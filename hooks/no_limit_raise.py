#!/usr/bin/env python3
"""PreToolUse hook for Write / Edit / MultiEdit: warn when an edit to a test file loosens a check.

Agents under pressure to get green raise the limit instead of fixing the code. This looks at the lines an edit changes in a test file
and warns when an upper bound goes up, a lower bound goes down, or an existing assert is changed or removed. Tightening a bound, new
asserts and new tests are fine.

It only warns: the edit goes ahead, because sometimes a limit really is wrong and a person decided so. The warning reaches Claude as
additionalContext and you as a system message. No permission decision is made. Any internal error = allow, silently.
"""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_config, read_input, norm, fail_open

TOOLS = {"Write", "Edit", "MultiEdit"}
TEST_PATH = re.compile(r"(^|/)(tests?|spec|__tests__)/|(^|/)test_[^/]*$|_test\.[a-z]+$|\.(test|spec)\.[a-z]+$", re.I)
ASSERT = re.compile(r"\bassert\w*\b|\bexpect\s*\(|\bself\.assert|\bXCTAssert", re.I)
UPPER = re.compile(r"max|limit|budget|ceiling|cap\b|timeout|threshold|tolerance|\btol\b|allowed|less|_ms\b|_kb\b|_mb\b|<=?", re.I)
LOWER = re.compile(r"\bmin|floor|at_least|greater|(?<![-=])>=?", re.I)
NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?:e-?\d+)?(?![\w.])", re.I)
MAX_BYTES = 2_000_000

def nums(s): return [float(x) for x in NUM.findall(s)]

def loosened(old, new):
    """The lines of `old` that `new` loosens: [(kind, old line, new line)]."""
    keep = {l.strip() for l in new.splitlines()}
    shape = {NUM.sub("#", l.strip()): l.strip() for l in new.splitlines() if l.strip()}
    found = []
    for line in old.splitlines():
        s = line.strip()
        if not s or s in keep or s.startswith(("#", "//")): continue
        twin = shape.get(NUM.sub("#", s))
        if twin is not None:   # same line, only the numbers changed
            pairs = list(zip(nums(s), nums(twin)))
            up, down = any(n > o for o, n in pairs), any(n < o for o, n in pairs)
            hi, lo = UPPER.search(s), LOWER.search(s)
            if (hi and up) or (lo and down): found.append(("limit raised", s, twin))
            elif not hi and not lo and ASSERT.search(s): found.append(("assert changed", s, twin))
        elif ASSERT.search(s):
            found.append(("assert removed or rewritten", s, ""))
    return found

def edits(tool, ti, path):
    """(old text, new text) pairs for this tool call."""
    if tool == "Edit": return [(ti.get("old_string") or "", ti.get("new_string") or "")]
    if tool == "MultiEdit": return [(e.get("old_string") or "", e.get("new_string") or "") for e in ti.get("edits") or []]
    if not os.path.isfile(path) or os.path.getsize(path) > MAX_BYTES: return []    # Write: a new file loosens nothing
    with open(path, encoding="utf-8", errors="replace") as f: return [(f.read(), ti.get("content") or "")]

def project_path(path, cwd):
    """The path inside the project, so a project that happens to live under a folder called test/ isn't all test files."""
    root = norm(cwd).rstrip("/") + "/" if cwd else ""
    return path[len(root):] if root and path.startswith(root) else path

def main():
    data = read_input()
    tool = data.get("tool_name")
    if tool not in TOOLS: return
    if not load_config().get("no_limit_raise", {}).get("enabled", True): return
    ti = data.get("tool_input") or {}
    raw = ti.get("file_path")
    if not raw: return
    path = norm(raw, data.get("cwd"))
    if not TEST_PATH.search(project_path(path, data.get("cwd"))): return
    found = [f for o, n in edits(tool, ti, path) for f in loosened(o, n)]
    if not found: return
    lines = ["%s: %s%s" % (k, o, " -> " + n if n else "") for k, o, n in found[:5]]
    msg = ("This edit loosens a check in %s:\n  %s\nDon't raise a limit or change an assert to get green: fix the code. If the limit really "
           "is wrong, say so and ask a person." % (os.path.basename(path), "\n  ".join(lines)))
    print(json.dumps({"systemMessage": msg, "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": msg}}))

if __name__ == "__main__":
    fail_open(main)
