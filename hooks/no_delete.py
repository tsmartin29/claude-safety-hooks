#!/usr/bin/env python3
"""PreToolUse hook for Bash: no permanent deletes of user files.

Blocks rm / unlink / rmdir / shred, `find ... -delete`, `find ... -exec rm`, `xargs rm`, `git clean -f`, also inside
`bash -c '...'`. Deletes are allowed only in the temp/build areas listed in config "no_delete.allow_paths" (and inside
~/.Trash). Everything else gets: "Move it to ~/.Trash instead (mv <path> ~/.Trash/)". `git rm` is not blocked.
Exit 2 + message on stderr = block (Claude sees the message). Any internal error = allow.
"""
import os, re, shlex, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_config, read_input, expand, norm, matches_any, fail_open

DELETERS = {"rm", "unlink", "rmdir", "shred"}
WRAPPERS = {"sudo", "command", "builtin", "exec", "nice", "nohup", "time", "env", "doas", "caffeinate"}
SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "|&"}
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\s*\2\b", re.S)

def tokenize(cmd):
    cmd = HEREDOC.sub(" ", cmd).replace("\n", " ; ")
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True); lex.whitespace_split = True
        return list(lex)
    except ValueError:
        return cmd.split()

def segments(tokens):
    """Yield (words, separator after them); the separator is None at the end."""
    seg = []
    for t in tokens:
        if t in SEPARATORS or (t and set(t) <= set(";&|()")):
            if seg: yield seg, t
            seg = []
        else: seg.append(t)
    if seg: yield seg, None

def strip_prefix(seg):
    """Drop VAR=value words and wrapper commands (sudo, nice -n 5, env X=1 ...) from the front."""
    i = 0
    while i < len(seg):
        t = seg[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t): i += 1; continue
        if os.path.basename(t) in WRAPPERS:
            i += 1
            while i < len(seg) and (seg[i].startswith("-") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", seg[i]) or seg[i].isdigit()): i += 1
            continue
        break
    return seg[i:]

REDIRECT = re.compile(r"^[<>&]*[<>][<>&]*$")   # >, >>, <, >&, &>, 2>&1 once the fd digit is split off

def strip_redirects(args):
    """Drop shell redirections (`2>/dev/null`, `> log`, `>&2`) and their targets: they are not arguments of the command."""
    out, i = [], 0
    while i < len(args):
        if REDIRECT.match(args[i]):
            if out and out[-1] in ("0", "1", "2"): out.pop()   # the fd number written in front: 2>/dev/null
            i += 2; continue
        out.append(args[i]); i += 1
    return out

def cd_dirs(name, args, dirs):
    """Folders the shell may be in after `cd`/`pushd` ARGS, one per folder in DIRS. None = cannot tell."""
    rest, opts = [], True
    for a in args:
        if opts and a == "--": opts = False; continue
        if opts and re.match(r"^-[A-Za-z@]+$", a): continue
        rest.append(a)
    if not rest: return [os.path.expanduser("~")] if name == "cd" else [None]   # bare pushd swaps the stack
    t = rest[0]
    if len(rest) > 1 or t == "-" or re.match(r"^[+-]\d+$", t) or "$" in expand(t) or "`" in t: return [None]
    if os.path.isabs(expand(t)): return [norm(t)]
    if os.environ.get("CDPATH") and not t.startswith(("./", "../")) and t not in (".", ".."): return [None]
    return [norm(t, d) if d else None for d in dirs]

def analyse(cmd, cwd, cfg, depth=0, dirs=None):
    """Return a list of (path, reason) that would be deleted outside the allowlist.
    Relative paths are checked against every folder the shell may be in: a `cd` replaces the folder only along an
    `&&` chain (a failed cd leaves the shell where it was); after ; || | & or a subshell any earlier folder is possible."""
    bad = []
    if depth > 3: return bad
    allow = cfg["no_delete"]["allow_paths"]
    trash = os.path.expanduser("~/.Trash")
    dirs = dirs or [cwd or os.getcwd()]
    seen, prev = list(dirs), "&&"
    def check(raw):
        if "$" in expand(raw) or "`" in raw:
            bad.append((raw, "path uses a variable or substitution I cannot verify")); return
        for d in ([None] if os.path.isabs(expand(raw)) else dict.fromkeys(dirs)):
            if d is None and not os.path.isabs(expand(raw)):
                bad.append((raw, "relative path after a cd whose folder I cannot verify")); continue
            p = norm(raw, d)
            if p == trash or p.startswith(trash + "/") or matches_any(p, allow): continue
            bad.append((p, "outside the temp/build allowlist"))
    for seg, sep in segments(tokenize(cmd)):
        if prev != "&&": dirs = list(seen)
        prev = sep
        seg = strip_prefix(seg)
        if not seg: continue
        name = os.path.basename(seg[0]); args = strip_redirects(seg[1:])
        if name in ("cd", "pushd", "popd"):
            dirs = [None] if name == "popd" else cd_dirs(name, args, dirs)
            seen += [d for d in dirs if d not in seen]
        if name in DELETERS:
            end_opts, targets = False, []
            for a in args:
                if not end_opts and a == "--": end_opts = True; continue
                if not end_opts and a.startswith("-") and a != "-": continue
                targets.append(a)
            for t in targets: check(t)
        elif name == "find":
            roots = []
            for a in args:
                if a.startswith("-") or a in ("(", "!", ")"): break
                roots.append(a)
            deletes = "-delete" in args or any(args[i] in ("-exec", "-execdir", "-ok", "-okdir") and i + 1 < len(args)
                                               and os.path.basename(args[i + 1]) in DELETERS for i in range(len(args)))
            if deletes:
                for r in (roots or ["."]): check(r)
        elif name == "xargs":
            rest = [a for a in args if not a.startswith("-")]
            if rest and os.path.basename(rest[0]) in DELETERS:
                bad.append(("(paths come from stdin)", "xargs rm: I cannot see which files"))
        elif name in ("bash", "sh", "zsh", "dash", "ksh") and "-c" in args:
            i = args.index("-c")
            if i + 1 < len(args): bad += analyse(args[i + 1], cwd, cfg, depth + 1, list(dirs))
        elif name == "git" and cfg["no_delete"].get("block_git_clean", True):
            sub = [a for a in args if not a.startswith("-")][:1]
            flags = [a for a in args if a.startswith("-")]
            if sub == ["clean"] and not any(f in ("-n", "--dry-run") or (re.match(r"^-[a-zA-Z]*n[a-zA-Z]*$", f)) for f in flags):
                bad.append(("(untracked files)", "git clean permanently deletes untracked files"))
    return bad

def main():
    data = read_input()
    if data.get("tool_name") != "Bash": return
    cfg = load_config()
    if not cfg["no_delete"].get("enabled", True): return
    cmd = (data.get("tool_input") or {}).get("command") or ""
    if not re.search(r"\b(rm|unlink|rmdir|shred|find|xargs|git|bash|sh|zsh)\b", cmd): return   # fast path
    bad = analyse(cmd, data.get("cwd"), cfg)
    if bad:
        shown = ", ".join(p for p, _ in bad)
        sys.stderr.write(f"BLOCKED: permanent delete of {shown} ({bad[0][1]}).\n"
                         "Move it to ~/.Trash instead (mv <path> ~/.Trash/). Deleting is only allowed in temp/build areas "
                         "(/tmp, build folders, node_modules, ...). Ask the user if you really need a permanent delete.\n")
        sys.exit(2)

if __name__ == "__main__":
    fail_open(main)
