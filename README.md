# claude-safety-hooks

Three small [Claude Code hooks](https://docs.claude.com/en/docs/claude-code/hooks) that stop an AI agent from the three mistakes that hurt most: permanently deleting your files, overwriting a file with no way back, and saying "done" without having tried it.

Made by Tommy Martin. Built while running [ClubPlugins](https://clubplugins.com), where these hooks run on every Claude Code session that builds our plug-ins and apps (20+ at a time, since October 2026).

## Why it exists

When you run many Claude Code sessions, even a 0.1 % chance of a bad `rm -rf` per session adds up fast, and "I fixed it" without a test run wastes your afternoon. Rules written in a prompt get forgotten; a hook is code the harness runs on every tool call, so it cannot be forgotten. These three are the ones we ended up wanting everywhere.

| Hook | Event | What it does |
|---|---|---|
| `no_delete.py` | PreToolUse (Bash) | Blocks `rm`, `unlink`, `rmdir`, `shred`, `find -delete`, `find -exec rm`, `xargs rm`, `git clean -f`, also inside `bash -c`. Tells Claude: *Move it to ~/.Trash instead (mv <path> ~/.Trash/)*. Deleting is allowed in temp and build areas (`/tmp`, `node_modules`, `build/`, `.venv`, `__pycache__`, `DerivedData`, `*.o`, ... and anything already in `~/.Trash`). `git rm` is not blocked. |
| `backup_before_write.py` | PreToolUse (Write / Edit / MultiEdit / NotebookEdit) | If the file exists and matches your protected globs, copies it to `name.YYYY-MM-DD_before_<tool>.bak` first. Only the first change each day is backed up. Never blocks the edit: if the copy fails it warns and carries on. About 30-70 ms. |
| `test_before_done.py` | Stop | If Claude changed files this turn, claims it is done/fixed/built, and ran nothing that looks like a test or a try-it (test command, build check, browser/simulator tool), it gets one reminder to test like a user. At most once per turn, never loops (honours `stop_hook_active`). |

All three **fail open**: if a hook script itself crashes, the tool call or the stop goes ahead and the error is written to `~/.claude/safety-hooks/errors.log`. A safety hook must never be the reason your session is stuck.

## Requirements

macOS or Linux, Python 3.8+ (the `/usr/bin/python3` on macOS is fine), Claude Code with hooks support. No packages.

## Install

```bash
git clone <this repo> claude-safety-hooks && cd claude-safety-hooks
python3 -W ignore -m unittest discover -s tests -v     # 23 tests, throw-away HOME, touches nothing of yours
./install.sh --merge-settings                          # copies hooks to ~/.claude/safety-hooks and merges ~/.claude/settings.json
```

`--merge-settings` first saves `~/.claude/settings.json.YYYY-MM-DD_before_safety_hooks.bak`, keeps any hooks you already have, and is safe to run twice. Without it, `./install.sh` prints the snippet for you to paste. Restart Claude Code sessions afterwards.

The snippet it produces:

```json
{
  "hooks": {
    "PreToolUse": [
      {"matcher": "Bash", "hooks": [{"type": "command", "command": "/usr/bin/python3 ~/.claude/safety-hooks/no_delete.py"}]},
      {"matcher": "Write|Edit|MultiEdit|NotebookEdit", "hooks": [{"type": "command", "command": "/usr/bin/python3 ~/.claude/safety-hooks/backup_before_write.py"}]}
    ],
    "Stop": [
      {"hooks": [{"type": "command", "command": "/usr/bin/python3 ~/.claude/safety-hooks/test_before_done.py"}]}
    ]
  }
}
```

(The installer writes your real home path instead of `~`.)

## Configure

Defaults live in `hooks/config.default.json`. Put overrides in `~/.claude/safety-hooks/config.json` (or point `CLAUDE_SAFETY_HOOKS_CONFIG` at a file); each top-level section you give replaces the same key in the defaults.

```json
{
  "no_delete": {"allow_paths": ["/tmp/*", "*/build/*", "*/node_modules/*", "~/.Trash/*"]},
  "backup": {"rules": [{"roots": ["~/code"], "globs": ["*.py", "*.swift"], "exclude": ["*/build/*", "*/.venv/*"]}]},
  "stop_reminder": {"enabled": false}
}
```

Patterns are shell-style (`fnmatch`) against the absolute path; `*` also matches `/`. Add `*.bak` to your `.gitignore` so backups never get committed.

## How it behaves, honestly

- `no_delete` reads the command text. It handles quotes, `&&`/`;`/pipes, `sudo`/`env`/`nice`, `bash -c '...'`, heredocs, relative paths, `~`, `$HOME`, and `..` tricks. A path with a variable it cannot resolve (`rm -rf $SOMETHING/x`) is blocked. It does **not** catch deletes hidden inside a script, `python -c "shutil.rmtree(...)"`, `rsync --delete`, or `mv x /dev/null`. It is a seatbelt, not a sandbox.
- The Stop reminder is a heuristic on words and commands. It will miss some false "done" claims and will occasionally nudge when you already tested in a way it does not recognise; edit the patterns at the top of `hooks/test_before_done.py` to suit you.
- Backups are same-folder copies. They are not version control.

## Files

`hooks/` (three hooks, `common.py`, `config.default.json`) · `install.sh` · `tests/test_hooks.py`

## Licence

MIT. See `LICENSE`.
