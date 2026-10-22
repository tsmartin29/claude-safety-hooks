#!/bin/bash
# claude-safety-hooks installer.
#   ./install.sh                         copy the hooks to ~/.claude/safety-hooks and print the settings.json snippet
#   ./install.sh --config my.json        same, and use my.json as your config overlay (kept as config.json)
#   ./install.sh --merge-settings        same, and merge the hooks into ~/.claude/settings.json (dated .bak made first)
#   ./install.sh --print                 only print the snippet
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"; DEST="$HOME/.claude/safety-hooks"; SETTINGS="$HOME/.claude/settings.json"
PY=/usr/bin/python3; [ -x "$PY" ] || PY="$(command -v python3)"
MERGE=0; PRINT=0; CONFIG=""
while [ $# -gt 0 ]; do case "$1" in
  --merge-settings) MERGE=1;; --print) PRINT=1;; --config) shift; CONFIG="$1";;
  *) echo "unknown option $1"; exit 1;; esac; shift; done

snippet(){ "$PY" - "$PY" "$DEST" <<'PYS'
import json, sys
py, dest = sys.argv[1:3]
cmd = lambda f: {"type": "command", "command": f"{py} {dest}/{f}"}
print(json.dumps({"hooks": {
  "PreToolUse": [{"matcher": "Bash", "hooks": [cmd("no_delete.py"), cmd("no_mass_kill.py")]},
                 {"matcher": "Write|Edit|MultiEdit|NotebookEdit", "hooks": [cmd("backup_before_write.py")]}],
  "Stop": [{"hooks": [cmd("test_before_done.py")]}]}}, indent=2))
PYS
}
[ "$PRINT" = 1 ] && { snippet; exit 0; }

mkdir -p "$DEST"
cp "$HERE"/hooks/*.py "$HERE"/hooks/config.default.json "$DEST/"; chmod +x "$DEST"/*.py
if [ -n "$CONFIG" ]; then
  [ -e "$DEST/config.json" ] && cp "$DEST/config.json" "$DEST/config.json.$(date +%F)_before_install.bak"
  cp "$CONFIG" "$DEST/config.json"
elif [ ! -e "$DEST/config.json" ]; then echo '{}' > "$DEST/config.json"; fi
echo "Hooks installed in $DEST (config overlay: $DEST/config.json)"

if [ "$MERGE" = 1 ]; then
  mkdir -p "$(dirname "$SETTINGS")"; [ -e "$SETTINGS" ] || echo '{}' > "$SETTINGS"
  cp "$SETTINGS" "$SETTINGS.$(date +%F)_before_safety_hooks.bak"
  snippet > "$DEST/.snippet.json"
  "$PY" - "$SETTINGS" "$DEST/.snippet.json" <<'PYM'
import json, sys
path, snip = sys.argv[1:3]
cur, new = json.load(open(path)), json.load(open(snip))["hooks"]
hooks = cur.setdefault("hooks", {})
for event, entries in new.items():
    have = hooks.setdefault(event, [])
    for e in entries:
        mine = e["hooks"][0]["command"]
        if not any(mine == h.get("command") for g in have for h in g.get("hooks", [])): have.append(e)
json.dump(cur, open(path, "w"), indent=2); open(path, "a").write("\n")
PYM
  echo "Merged into $SETTINGS (backup: $SETTINGS.$(date +%F)_before_safety_hooks.bak). Restart Claude Code sessions to pick it up."
else
  echo; echo "Add this to $SETTINGS (or re-run with --merge-settings):"; snippet
fi
