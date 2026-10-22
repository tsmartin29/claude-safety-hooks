#!/usr/bin/env python3
"""PreToolUse hook for Bash: no "signal everything I own" commands.

`kill -9 -1` (and kill(-1, SIGKILL) in a script) signals EVERY process the user owns: other Claude sessions, terminals,
tmux, the desktop. `kill 0` hits the whole process group. `pkill -u me` / `killall -u me` do the same by user, and a
pattern like `pkill -f .` matches everything. See anthropics/claude-code#104156.

Blocks, also inside `bash -c '...'`:
  kill ... -1 | 0          (any signal, with or without --)
  pkill / killall -u|-U|--uid|--euid ...
  pkill / killall with a match-everything pattern (., .*, ^, ^.*, '')
  inline code run with python -c / node -e / perl -e / ruby -e: os.kill(-1 / os.kill(0 / os.killpg(0 / process.kill(-1 / kill(-1,
  (text that only mentions those, in a heredoc, an echo or a file, is not blocked)
Killing specific PIDs, or a process group you started (kill -- -12345), is allowed.
Exit 2 + message on stderr = block (Claude sees the message). Any internal error = allow.
"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_config, read_input, fail_open
from no_delete import tokenize, segments, strip_prefix, strip_redirects

SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
EVERYTHING = {".", ".*", "^", "^.*", "", ".+", "^.+"}
USER_WIDE = {"-u", "-U", "--uid", "--euid"}
INLINE = re.compile(r"\b(?:os\.)?kill(?:pg)?\s*\(\s*(?:-\s*1|0)\s*[,)]|\bprocess\.kill\s*\(\s*-\s*1\b|\bkill\s*\(\s*-\s*1\s*,")
INTERPRETER = re.compile(r"^(python[0-9.]*|node|nodejs|deno|bun|perl|ruby)$")
MESSAGE = ("Blocked: this would signal every process you own (or your whole process group): other Claude sessions, "
           "terminals and the desktop would die. Kill the specific PIDs you started instead (kill <pid>), or a process "
           "group you created yourself (kill -- -<pgid>).")

def kill_targets(args):
    """The PID arguments of a `kill` command, after its signal option."""
    i = 0
    if i < len(args) and args[i] in ("-s", "-n"): i += 2
    elif i < len(args) and args[i].startswith("-") and args[i] != "--" and args[i] != "-l": i += 1
    if i < len(args) and args[i] == "--": i += 1
    return args[i:]

def bad_segment(words):
    words = strip_prefix(words)
    if not words: return None
    name, args = os.path.basename(words[0]), strip_redirects(words[1:])
    if name in SHELLS:
        for j, a in enumerate(args):
            if a == "-c" and j + 1 < len(args): return analyse(args[j + 1])
        return None
    if INTERPRETER.match(name):   # only code the interpreter will run (-c / -e), not text that merely mentions kill(-1)
        for j, a in enumerate(args):
            if a in ("-c", "-e", "--eval") and j + 1 < len(args):
                m = INLINE.search(args[j + 1])
                return m.group(0).strip() if m else None
        return None
    if name == "kill":
        hit = [t for t in kill_targets(args) if t in ("-1", "0", "-0")]
        return f"kill {hit[0]}" if hit else None
    if name in ("pkill", "killall", "pgrep"):
        if name == "pgrep": return None
        if any(a in USER_WIDE or a.startswith("--uid=") or a.startswith("--euid=") for a in args): return f"{name} by user"
        rest, skip = [], False
        for a in args:
            if skip: skip = False; continue
            if a in ("-s", "-g", "-G", "-P", "-t", "-F", "-c", "-n", "-o"): skip = True; continue
            if a.startswith("-"): continue
            rest.append(a)
        if rest and all(p in EVERYTHING for p in rest): return f"{name} {rest[0] or repr('')}"
    return None

def analyse(cmd):
    for words, _sep in segments(tokenize(cmd)):
        hit = bad_segment(words)
        if hit: return hit
    return None

def main():
    data = read_input()
    if data.get("tool_name") != "Bash": return
    cfg = load_config()
    if not cfg.get("no_mass_kill", {}).get("enabled", True): return
    hit = analyse(str((data.get("tool_input") or {}).get("command", "")))
    if hit:
        sys.stderr.write(f"{MESSAGE} (matched: {hit})\n")
        sys.exit(2)

if __name__ == "__main__":
    fail_open(main)
