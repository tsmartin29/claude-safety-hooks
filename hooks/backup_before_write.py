#!/usr/bin/env python3
"""PreToolUse hook for Write / Edit / MultiEdit / NotebookEdit: dated backup before overwriting a protected file.

If the file exists and matches a rule in config "backup.rules" (roots + globs, minus excludes), copy it to
<name>.YYYY-MM-DD_before_<tool>.bak next to it. Only the first change each day is backed up (so a day of edits gives one
backup, not hundreds). Never blocks: if the copy fails it only warns on stderr and the edit goes ahead.
"""
import datetime, fnmatch, glob, os, shutil, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_config, read_input, norm, matches_any, expand, fail_open

TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

def protected(path, rules):
    base = os.path.basename(path)
    for r in rules:
        roots = [norm(x) for x in r.get("roots", [])]
        if roots and not any(path == x or path.startswith(x.rstrip("/") + "/") for x in roots): continue
        if not any(fnmatch.fnmatchcase(base, g) for g in r.get("globs", ["*"])): continue
        if matches_any(path, r.get("exclude", [])): continue
        return True
    return False

def main():
    data = read_input()
    tool = data.get("tool_name")
    if tool not in TOOLS: return
    cfg = load_config().get("backup", {})
    if not cfg.get("enabled", True): return
    ti = data.get("tool_input") or {}
    raw = ti.get("file_path") or ti.get("notebook_path")
    if not raw: return
    path = norm(raw, data.get("cwd"))
    if not os.path.isfile(path) or path.endswith(".bak"): return
    if not protected(path, cfg.get("rules", [])): return
    if os.path.getsize(path) > cfg.get("max_file_mb", 20) * (1 << 20): return
    day = datetime.date.today().isoformat()
    if glob.glob(glob.escape(path) + f".{day}_before_*.bak"): return       # already backed up today
    dest = f"{path}.{day}_before_{tool.lower()}.bak"
    try:
        shutil.copy2(path, dest)
    except Exception as e:                                                   # never block the edit
        sys.stderr.write(f"claude-safety-hooks: could not back up {path}: {e!r} (edit allowed)\n")

if __name__ == "__main__":
    fail_open(main)
