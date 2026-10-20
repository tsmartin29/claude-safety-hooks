#!/usr/bin/env python3
"""Stop hook: gentle "test it like a user before you say done" reminder.

When Claude is about to finish a turn we look at the transcript for THIS turn only. We remind (once) only if ALL of:
  - Claude changed files this turn (Write / Edit / MultiEdit / NotebookEdit),
  - its last message claims completion ("done", "fixed", "built", "implemented", ...),
  - there is no evidence of a test or try-it run this turn (a Bash command that looks like a test/run/build check, or a
    browser / simulator / computer-use tool call).
Never loops: if `stop_hook_active` is set (we already blocked this stop) we exit at once, and we also remember which turn we
reminded about in a small state file, so at most one reminder per turn. Any internal error = allow the stop.
"""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_config, read_input, fail_open

EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
CLAIM = re.compile(r"(\ball done\b|\bdone[.!]|\b(is|are|now|all) (done|fixed|built|finished|complete|completed|working|ready)\b|"
                   r"\bi('ve| have)? (just )?(built|fixed|implemented|finished|completed|added|updated|created|written)\b|"
                   r"\b(fixed|implemented|completed|finished) (it|that|this|the)\b|\bshould (now )?work\b|\bready to (use|ship|go)\b)", re.I)
EVIDENCE = re.compile(r"(\btest(s|ing)?\b|pytest|unittest|jest|vitest|mocha|rspec|cargo (test|run|check)|go (test|run|vet)|swift (test|run|build)|"
                      r"xcodebuild|npm (test|run)|yarn (test|run)|make( |$)|cmake --build|--selftest|--self-test|smoke|\bcheck\b|\bverify\b|"
                      r"\blint\b|\bcurl\b|\bnode \S+|\bpython3? \S+|\./\S+|\bopen -a\b|\bauval\b)", re.I)
UI_TOOLS = re.compile(r"(browser|simulator|computer|chrome|playwright|puppeteer)", re.I)

def state_path():
    return os.path.expanduser("~/.claude/safety-hooks/stop_state.json")

def real_user(entry):
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain"): return False
    c = (entry.get("message") or {}).get("content")
    if isinstance(c, str): return bool(c.strip())
    return any(isinstance(b, dict) and b.get("type") == "text" for b in (c or []))

def read_turn(path):
    """Entries since the last real user message (reads only the tail of the file)."""
    with open(path, "rb") as f:
        f.seek(max(0, os.path.getsize(path) - 3_000_000)); lines = f.read().decode("utf-8", "ignore").splitlines()
    entries = []
    for l in lines:
        try: entries.append(json.loads(l))
        except ValueError: pass
    start = 0
    for i, e in enumerate(entries):
        if real_user(e): start = i
    return entries[start:]

def analyse(turn):
    edited, evidence, last_text, turn_id = False, False, "", ""
    for e in turn:
        if e.get("type") == "user" and real_user(e): turn_id = e.get("uuid") or turn_id
        if e.get("type") != "assistant": continue
        c = (e.get("message") or {}).get("content")
        if isinstance(c, str): last_text = c; continue
        text = ""
        for b in c or []:
            if not isinstance(b, dict): continue
            if b.get("type") == "text": text += b.get("text", "")
            elif b.get("type") == "tool_use":
                name, inp = b.get("name", ""), b.get("input") or {}
                if name in EDIT_TOOLS: edited = True
                elif name == "Bash" and EVIDENCE.search(inp.get("command", "")): evidence = True
                elif UI_TOOLS.search(name): evidence = True
        if text.strip(): last_text = text
    return edited, evidence, last_text, turn_id

def main():
    data = read_input()
    if data.get("stop_hook_active"): return                       # we already blocked once: let the session end
    cfg = load_config().get("stop_reminder", {})
    if not cfg.get("enabled", True): return
    path = data.get("transcript_path")
    if not path or not os.path.isfile(path): return
    edited, evidence, last_text, turn_id = analyse(read_turn(path))
    if not (edited and not evidence and CLAIM.search(last_text)): return
    key = f"{data.get('session_id', '')}:{turn_id}"
    try: state = json.load(open(state_path()))
    except Exception: state = {}
    if state.get("last") == key: return                           # already reminded about this turn
    try:
        os.makedirs(os.path.dirname(state_path()), exist_ok=True)
        json.dump({"last": key}, open(state_path(), "w"))
    except OSError: return                                       # can't remember => don't risk a loop
    print(json.dumps({"decision": "block", "reason": cfg.get("message", "Test it like a user before saying done.")}))

if __name__ == "__main__":
    fail_open(main)
