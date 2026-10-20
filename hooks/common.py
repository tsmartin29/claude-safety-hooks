"""Shared helpers for the claude-safety-hooks. Every hook FAILS OPEN: if our own code errors, the tool call goes ahead."""
import fnmatch, json, os, sys, time

INSTALL_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(INSTALL_DIR, "config.default.json")

def user_config_path():
    return os.path.expanduser(os.environ.get("CLAUDE_SAFETY_HOOKS_CONFIG", "~/.claude/safety-hooks/config.json"))

def load_config():
    """Defaults from config.default.json (shipped), overridden key by key (per section) by the user's config.json."""
    cfg = {}
    for p in (DEFAULT_CONFIG, user_config_path()):
        try:
            with open(p) as f: data = json.load(f)
        except FileNotFoundError: continue
        except ValueError as e:
            sys.stderr.write(f"claude-safety-hooks: bad JSON in {p}: {e}\n"); continue
        for section, val in data.items():
            if isinstance(val, dict) and isinstance(cfg.get(section), dict): cfg[section].update(val)
            else: cfg[section] = val
    return cfg

def read_input():
    try: return json.load(sys.stdin)
    except Exception: return {}

def expand(p):
    return os.path.expandvars(os.path.expanduser(p))

def norm(path, cwd=None):
    """Absolute, normalised path; the parent folder is resolved through symlinks, the last part is not (rm removes the link itself)."""
    p = expand(path)
    if not os.path.isabs(p): p = os.path.join(cwd or os.getcwd(), p)
    p = os.path.normpath(p)
    head, tail = os.path.split(p)
    try: head = os.path.realpath(head)
    except OSError: pass
    return os.path.join(head, tail) if tail else head

def matches_any(path, patterns):
    """fnmatch against the path and the path with a trailing slash, so '*/build/*' matches both build/x and build itself."""
    for pat in patterns:
        pat = expand(pat)
        if fnmatch.fnmatchcase(path, pat) or fnmatch.fnmatchcase(path + "/", pat): return True
    return False

def fail_open(fn):
    """Run a hook's main(); any unexpected exception is logged and swallowed (exit 0). Deliberate sys.exit(2) still blocks."""
    try:
        fn()
    except SystemExit: raise
    except BaseException as e:
        try:
            sys.stderr.write(f"claude-safety-hooks: internal error, allowing the action: {e!r}\n")
            with open(os.path.join(os.path.expanduser("~/.claude/safety-hooks"), "errors.log"), "a") as f:
                f.write(f"{time.strftime('%F %T')} {os.path.basename(sys.argv[0])}: {e!r}\n")
        except Exception: pass
    sys.exit(0)
