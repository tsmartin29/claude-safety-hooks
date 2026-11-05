# Changelog

## 2026-11-05
- New `no_limit_raise.py` (PreToolUse on Write / Edit / MultiEdit): warns when an edit to a test file raises a limit or changes or removes an existing assert. Warn only: no permission decision, the edit goes ahead. Fails open. `{"no_limit_raise": {"enabled": false}}` turns it off. `install.sh` adds it as its own settings entry, so `--merge-settings` picks it up on an existing install.
- Tests: 39 (7 new for this hook, plus an installer check).

## 2026-10-21
- New `no_mass_kill.py`: blocks "signal everything I own" commands such as `kill -9 -1`.

## 2026-10-19
- First release: `no_delete.py`, `backup_before_write.py`, `test_before_done.py`.
