"""Tests for all three hooks. They run the real scripts as subprocesses, exactly as Claude Code does (JSON on stdin).
Run:  python3 -W ignore -m unittest discover -s tests -v
Each test uses a throw-away HOME, so nothing on your machine is touched."""
import glob, json, os, shutil, stat, subprocess, sys, tempfile, time, unittest, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
H = os.path.join(ROOT, "hooks")

class Base(unittest.TestCase):
    def setUp(self):
        # NOT under /tmp or /var/folders: those are allow-listed for deletes, which would hide the "blocked" cases.
        base = os.path.join(ROOT, "tests", ".tmp"); os.makedirs(base, exist_ok=True)
        self.home = os.path.realpath(tempfile.mkdtemp(prefix="home-", dir=base))
        os.makedirs(self.home + "/.claude/safety-hooks")
        self.env = dict(os.environ, HOME=self.home); self.env.pop("CLAUDE_SAFETY_HOOKS_CONFIG", None)
    def tearDown(self):
        for r, ds, fs in os.walk(self.home):
            for d in ds: os.chmod(os.path.join(r, d), 0o755)
        shutil.rmtree(self.home, ignore_errors=True)
    def run_hook(self, script, payload, env=None, raw=None):
        t = time.time()
        r = subprocess.run([sys.executable, os.path.join(H, script)], input=raw if raw is not None else json.dumps(payload),
                           capture_output=True, text=True, env=env or self.env, timeout=20)
        r.elapsed = time.time() - t
        return r
    def write_config(self, cfg):
        p = self.home + "/cfg.json"; json.dump(cfg, open(p, "w")); self.env["CLAUDE_SAFETY_HOOKS_CONFIG"] = p

class NoDelete(Base):
    def bash(self, cmd, cwd=None):
        return self.run_hook("no_delete.py", {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": cwd or self.home})
    def assertBlocked(self, cmd, **kw):
        r = self.bash(cmd, **kw)
        self.assertEqual(r.returncode, 2, f"should block: {cmd}\n{r.stderr}")
        self.assertIn("Move it to ~/.Trash instead (mv <path> ~/.Trash/)", r.stderr)
    def assertAllowed(self, cmd, **kw):
        r = self.bash(cmd, **kw); self.assertEqual(r.returncode, 0, f"should allow: {cmd}\n{r.stderr}")

    def test_blocks_user_file_deletes(self):
        for c in ["rm ~/Documents/report.docx", "rm -rf ~/Documents/project", "rm -f notes.txt", "unlink ~/a.txt", "rmdir ~/stuff",
                  "shred -u ~/secret.txt", "sudo rm -rf ~/Desktop/x", r"\rm ~/x", "/bin/rm ~/x", "ls && rm ~/x", "echo hi; rm ~/x",
                  'rm -rf "$HOME/Documents/x"', "rm /tmp/ok.txt ~/Documents/not-ok.txt", "rm -rf /tmp/../Users/someone/x",
                  "rm -- -weird", "bash -c 'rm ~/Documents/x'", "sh -c \"cd ~ && rm -rf Documents\"", "nice rm ~/x", "env FOO=1 rm ~/x",
                  "rm -rf $SOME_UNKNOWN_VAR/x", "rm -rf ~", "rm -rf /"]:
            with self.subTest(c): self.assertBlocked(c)

    def test_blocks_find_xargs_and_git_clean(self):
        for c in ["find ~/Documents -name '*.tmp' -delete", "find . -name '*.log' -delete", "find ~/x -exec rm {} \;",
                  "find ~/x -type f | xargs rm", "ls | xargs -n1 rm -f", "git clean -fd", "git clean -fdx", "cd ~/proj && git clean -f"]:
            with self.subTest(c): self.assertBlocked(c)

    def test_allows_temp_and_build_areas(self):
        for c in ["rm /tmp/foo", "rm -rf /tmp/x/y", "rm -rf /private/tmp/x", "rm -rf /private/var/folders/ab/cd/T/job",
                  "rm -rf ~/proj/build", "rm -rf ~/proj/build_debug/obj", "rm -rf ~/proj/_build", "rm -rf node_modules",
                  "rm -rf ~/proj/node_modules/.cache", "rm -rf .venv", "rm -rf ~/p/venv", "rm -rf ~/p/__pycache__", "rm -rf ~/p/.pytest_cache",
                  "rm -rf ~/p/CMakeFiles", "rm -rf ~/Library/Developer/Xcode/DerivedData/App-abc", "rm ~/p/main.o",
                  "rm -rf /tmp/claude-1/x/scratchpad/work", "rm -rf ~/.Trash/old.zip", "find /tmp -name '*.x' -delete",
                  "find ~/p/build -delete", "rm -rf ~/p/build /tmp/y"]:
            with self.subTest(c): self.assertAllowed(c)
        self.assertAllowed("rm -rf build", cwd=self.home + "/proj")         # relative path resolved against cwd
        self.assertAllowed('rm -rf "$HOME/proj/build"')

    def test_follows_cd_in_the_same_command(self):
        for c in ["cd /tmp/x && rm y", "cd /private/tmp/x && rm file.html", "cd /tmp && cd x && rm -rf y", "pushd /tmp/x && rm y",
                  "cd -P /tmp/x && rm y", "cd ~/proj && rm -rf build", "cd /tmp/x &&\nrm y", "(cd /tmp/x && rm y)",
                  "cd $SOMEWHERE && rm /tmp/y", "bash -c 'cd /tmp/x && rm y'", "cd /tmp/x && bash -c 'rm y'"]:
            with self.subTest(c): self.assertAllowed(c)
        for c in ["cd ~/Documents && rm y", "cd /tmp/x && cd ~/Documents && rm y", "cd ~ && rm -rf Documents", "cd && rm y",
                  "cd /tmp/x; rm y", "cd /tmp/x || rm y", "(cd /tmp/x) && rm y", "cd /tmp/x | rm y", "cd /tmp/x & rm y",
                  "cd /tmp/x && true; rm y", "cd $SOMEWHERE && rm y", "cd `pwd`/x && rm y", "cd - && rm y",
                  "pushd /tmp/x && popd && rm y", "cd /tmp/x && rm ../../Users/someone/y", "cd /tmp && cd ~ && find . -delete"]:
            with self.subTest(c): self.assertBlocked(c)
        self.assertAllowed("cd .. && rm y", cwd="/tmp/a/b")                           # relative cd from the session folder
        self.assertBlocked("cd ../.. && rm y", cwd=self.home + "/a")

    def test_redirections_are_not_delete_targets(self):
        for c in ["rm -rf ~/proj/build 2>/dev/null", "rm /tmp/x 2>&1", "rm -f /tmp/x >/dev/null 2>&1", "rm -f /tmp/x &>/dev/null",
                  "rm -rf /tmp/x > /tmp/log", "rmdir /tmp/x 2> /dev/null", "rm -rf node_modules >> /tmp/out.txt 2>&1",
                  "bash -c 'rm /tmp/x 2>/dev/null'", "cd /tmp/x && rm y 2>/dev/null"]:
            with self.subTest(c): self.assertAllowed(c)
        self.assertAllowed("rm -rf build 2>/dev/null", cwd=self.home + "/proj")
        for c in ["rm ~/Documents/x 2>/dev/null", "rm ~/x >/dev/null 2>&1", "rm ~/x > /tmp/log", "rm -rf ~/Desktop/y 2>&1 | cat",
                  "rm /tmp/ok ~/Documents/no 2>/dev/null", "rm 2>/dev/null ~/Documents/x"]:
            with self.subTest(c): self.assertBlocked(c)

    def test_allows_things_that_are_not_deletes(self):
        for c in ["git rm old.py", "git rm -r --cached dist", "mv ~/x ~/.Trash/", "ls -la", "echo rm -rf ~/x", "git clean -n", "git clean -nd",
                  "grep -r 'rm -rf' .", "cat <<'EOF'\nrm -rf ~/Documents\nEOF", "python3 build.py", "pytest -q", "npm run build"]:
            with self.subTest(c): self.assertAllowed(c)

    def test_allowlist_is_configurable(self):
        self.write_config({"no_delete": {"allow_paths": ["/tmp/*", "*/myscratch/*"]}})
        self.assertAllowed("rm -rf ~/work/myscratch/a"); self.assertBlocked("rm -rf ~/p/build")     # build no longer allowed

    def test_can_be_disabled(self):
        self.write_config({"no_delete": {"enabled": False}}); self.assertAllowed("rm -rf ~/Documents")

    def test_ignores_other_tools_and_bad_input(self):
        self.assertEqual(self.run_hook("no_delete.py", {"tool_name": "Write", "tool_input": {"command": "rm -rf ~"}}).returncode, 0)
        self.assertEqual(self.run_hook("no_delete.py", None, raw="not json").returncode, 0)
        self.assertEqual(self.run_hook("no_delete.py", None, raw="").returncode, 0)

    def test_fails_open_on_internal_error(self):
        self.write_config({"no_delete": 5})            # config the code cannot use -> internal error -> allowed
        r = self.bash("rm -rf ~/Documents"); self.assertEqual(r.returncode, 0); self.assertIn("internal error", r.stderr)
        open(self.home + "/cfg.json", "w").write("{broken")     # unreadable config -> defaults still protect
        self.assertBlocked("rm -rf ~/Documents")

class NoMassKill(Base):
    """anthropics/claude-code#104156: kill(-1, SIGKILL) from an agent's script took down every session and the desktop."""
    def bash(self, cmd):
        return self.run_hook("no_mass_kill.py", {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": self.home})
    def assertBlocked(self, cmd):
        r = self.bash(cmd); self.assertEqual(r.returncode, 2, f"should block: {cmd}\n{r.stderr}"); self.assertIn("every process you own", r.stderr)
    def assertAllowed(self, cmd):
        r = self.bash(cmd); self.assertEqual(r.returncode, 0, f"should allow: {cmd}\n{r.stderr}")
    def test_blocks_kill_minus_one_and_zero(self):
        for c in ["kill -9 -1", "kill -KILL -1", "kill -s KILL -1", "kill -- -1", "kill 0", "kill -TERM 0", "sudo kill -9 -1",
                  "cd /tmp && kill -9 -1", "sleep 1; kill -1 -1", "bash -c 'kill -9 -1'", "zsh -c \"kill 0\""]:
            self.assertBlocked(c)
    def test_blocks_user_wide_and_match_all(self):
        for c in ["pkill -u tommy", "pkill -9 -U 501", "killall -u tommy", "pkill --euid=501", "pkill -f .", "pkill '.*'", "killall -m '.*'"]:
            self.assertBlocked(c)
    def test_blocks_inline_code(self):
        for c in ["python3 -c 'import os,signal; os.kill(-1, signal.SIGKILL)'", "python3 -c 'import os; os.killpg(0, 9)'",
                  "node -e 'process.kill(-1)'", "perl -e 'kill(-1, 9)'"]:
            self.assertBlocked(c)
    def test_allows_specific_targets(self):
        for c in ["kill 12345", "kill -9 12345 23456", "kill -- -12345", "kill -TERM %1", "kill -l", "kill -0 4242",
                  "pkill -f my_server.py", "killall Finder", "pgrep -u tommy python", "pkill -9 -f 'node dev'",
                  "python3 -c 'import os; os.kill(4242, 15)'", "echo kill -9 -1 is dangerous > notes.txt"]:
            self.assertAllowed(c)
    def test_text_that_only_mentions_it_is_allowed(self):
        # found the first time the hook ran for real: a README edit quoting os.kill(-1, ...) was blocked
        for c in ["python3 - <<'EOF'\nopen('README.md','a').write('inline os.kill(-1, ...) is blocked')\nEOF",
                  "echo 'never run os.kill(-1, 9)' >> notes.md", "grep -rn 'killpg(0' src/", "git commit -m 'block kill -9 -1'"]:
            self.assertAllowed(c)
    def test_can_be_disabled(self):
        self.write_config({"no_mass_kill": {"enabled": False}}); self.assertAllowed("kill -9 -1")
    def test_ignores_other_tools_and_bad_input(self):
        r = self.run_hook("no_mass_kill.py", {"tool_name": "Write", "tool_input": {"command": "kill -9 -1"}}); self.assertEqual(r.returncode, 0)
        r = self.run_hook("no_mass_kill.py", None, raw="not json"); self.assertEqual(r.returncode, 0)

class Backup(Base):
    def setUp(self):
        super().setUp()
        self.proj = self.home + "/code/app"; os.makedirs(self.proj + "/src"); os.makedirs(self.proj + "/node_modules/x"); os.makedirs(self.proj + "/build")
        self.write_config({"backup": {"rules": [{"roots": [self.home + "/code"], "globs": ["*.py", "*.json", "CMakeLists.txt"],
                                                 "exclude": ["*/build/*", "*/node_modules/*"]}]}})
        self.today = datetime.date.today().isoformat()
    def edit(self, path, tool="Edit"):
        return self.run_hook("backup_before_write.py", {"tool_name": tool, "tool_input": {"file_path": path}, "cwd": self.home})
    def mk(self, rel, text="original"):
        p = os.path.join(self.proj, rel); open(p, "w").write(text); return p

    def test_backup_made_with_dated_name(self):
        f = self.mk("src/app.py")
        r = self.edit(f); self.assertEqual(r.returncode, 0)
        b = f"{f}.{self.today}_before_edit.bak"
        self.assertEqual(open(b).read(), "original")
        r = self.edit(self.mk("CMakeLists.txt"), tool="Write")
        self.assertTrue(os.path.exists(f"{self.proj}/CMakeLists.txt.{self.today}_before_write.bak"))

    def test_only_first_edit_each_day(self):
        f = self.mk("src/app.py"); self.edit(f); open(f, "w").write("changed once"); self.edit(f); self.edit(f, "Write")
        self.assertEqual(len(glob.glob(f + ".*.bak")), 1)
        self.assertEqual(open(f"{f}.{self.today}_before_edit.bak").read(), "original")     # the pre-day version is kept

    def test_old_backup_does_not_stop_todays(self):
        f = self.mk("src/app.py"); open(f + ".2020-01-01_before_edit.bak", "w").write("ancient"); self.edit(f)
        self.assertEqual(len(glob.glob(f + ".*.bak")), 2)

    def test_skips_unprotected_new_excluded_and_bak(self):
        self.edit(self.mk("notes.txt")); self.edit(self.proj + "/src/brand_new.py")
        self.edit(self.mk("node_modules/x/i.py")); self.edit(self.mk("build/gen.py")); self.edit(self.mk("src/old.py.bak"))
        self.edit(self.home + "/elsewhere.py") if open(self.home + "/elsewhere.py", "w").write("x") else None
        self.assertEqual(glob.glob(self.home + "/**/*_before_*.bak", recursive=True), [])

    def test_never_blocks_when_copy_fails(self):
        f = self.mk("src/app.py"); os.chmod(self.proj + "/src", stat.S_IRUSR | stat.S_IXUSR)
        r = self.edit(f); os.chmod(self.proj + "/src", 0o755)
        if os.geteuid() == 0: self.skipTest("root ignores directory permissions")
        self.assertEqual(r.returncode, 0); self.assertIn("could not back up", r.stderr)

    def test_fast(self):
        f = self.mk("src/app.py"); self.edit(f)           # warm file cache
        times = [self.edit(self.mk(f"src/f{i}.py")).elapsed for i in range(3)]
        print(f"\n  backup hook wall time incl. Python start: {min(times) * 1000:.0f} ms")
        self.assertLess(min(times), 0.3)

    def test_fails_open_on_bad_config_and_input(self):
        self.write_config({"backup": 7}); self.assertEqual(self.edit(self.mk("src/z.py")).returncode, 0)
        self.assertEqual(self.run_hook("backup_before_write.py", None, raw="oops").returncode, 0)

class StopHook(Base):
    def transcript(self, *entries):
        p = self.home + "/t.jsonl"; open(p, "w").write("\n".join(json.dumps(e) for e in entries) + "\n"); return p
    def user(self, text="please fix the bug", uid="u1"): return {"type": "user", "uuid": uid, "message": {"content": text}}
    def tool(self, name, **inp): return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}
    def say(self, text): return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}
    def result(self): return {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}}
    def stop(self, path, active=False, sid="s1"):
        return self.run_hook("test_before_done.py", {"hook_event_name": "Stop", "transcript_path": path, "stop_hook_active": active, "session_id": sid})
    def blocked(self, r):
        self.assertEqual(r.returncode, 0); return bool(r.stdout.strip()) and json.loads(r.stdout).get("decision") == "block"

    def test_reminds_when_done_claimed_without_test(self):
        t = self.transcript(self.user(), self.tool("Edit", file_path="a.py"), self.result(), self.say("All done, I've fixed the bug."))
        r = self.stop(t); self.assertTrue(self.blocked(r)); self.assertIn("test", json.loads(r.stdout)["reason"].lower())

    def test_only_once_per_turn_and_never_loops(self):
        t = self.transcript(self.user(), self.tool("Write", file_path="a.py"), self.say("Done."))
        self.assertTrue(self.blocked(self.stop(t)))
        self.assertFalse(self.blocked(self.stop(t, active=True)))     # Claude Code's loop guard
        self.assertFalse(self.blocked(self.stop(t)))                  # even if the flag were missing: same turn, already reminded
        t2 = self.transcript(self.user("next thing", "u2"), self.tool("Edit", file_path="b.py"), self.say("Fixed it."))
        self.assertTrue(self.blocked(self.stop(t2)))                  # a new turn can get its own single reminder

    def test_no_reminder_when_tests_were_run(self):
        for ev in [self.tool("Bash", command="python3 -m pytest -q"), self.tool("Bash", command="npm test"),
                   self.tool("Bash", command="./build.sh --selftest"), self.tool("mcp__Claude_Browser__navigate", url="http://localhost:3000"),
                   self.tool("mcp__Claude_Code_iOS_Simulator__control", action="screenshot")]:
            t = self.transcript(self.user(), self.tool("Edit", file_path="a.py"), ev, self.say("Done, it's fixed and working."))
            with self.subTest(ev["message"]["content"][0]["name"]): self.assertFalse(self.blocked(self.stop(t)))

    def test_no_reminder_without_edits_or_claims(self):
        self.assertFalse(self.blocked(self.stop(self.transcript(self.user("what is 2+2"), self.say("Done thinking: it is 4.")))))   # no edits
        self.assertFalse(self.blocked(self.stop(self.transcript(self.user(), self.tool("Edit", file_path="a"), self.say("I changed the title; want me to test it?")))))
        self.assertFalse(self.blocked(self.stop(self.transcript(self.user(), self.tool("Edit", file_path="a"), self.say("Here is the plan for tomorrow.")))))

    def test_only_this_turn_counts(self):
        t = self.transcript(self.user("first", "u1"), self.tool("Edit", file_path="a.py"), self.say("Done."),
                            self.user("question only", "u2"), self.say("Done, that is the answer."))
        self.assertFalse(self.blocked(self.stop(t)))                  # the earlier turn's edit does not count for this turn
        t = self.transcript(self.user("first", "u1"), self.tool("Bash", command="pytest"), self.user("second", "u2"),
                            self.tool("Edit", file_path="a.py"), self.say("Done."))
        self.assertTrue(self.blocked(self.stop(t)))                   # the earlier turn's test does not count either

    def test_fails_open(self):
        for r in (self.stop(self.home + "/missing.jsonl"), self.run_hook("test_before_done.py", None, raw="junk"),
                  self.run_hook("test_before_done.py", {"transcript_path": self.transcript()})):
            self.assertFalse(self.blocked(r))
        open(self.home + "/bad.jsonl", "w").write("{not json\n\x00\x01\n")
        self.assertFalse(self.blocked(self.stop(self.home + "/bad.jsonl")))
        self.write_config({"stop_reminder": 3})
        self.assertFalse(self.blocked(self.stop(self.transcript(self.user(), self.tool("Edit", file_path="a"), self.say("Done.")))))

    def test_can_be_disabled(self):
        self.write_config({"stop_reminder": {"enabled": False}})
        self.assertFalse(self.blocked(self.stop(self.transcript(self.user(), self.tool("Edit", file_path="a"), self.say("Done.")))))

class InstallTest(Base):
    def test_install_merges_settings_and_keeps_existing_hooks(self):
        s = self.home + "/.claude/settings.json"
        json.dump({"model": "x", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}, open(s, "w"))
        r = subprocess.run([ROOT + "/install.sh", "--merge-settings"], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.load(open(s)); self.assertEqual(cfg["model"], "x")
        self.assertEqual(len(cfg["hooks"]["Stop"]), 2); self.assertTrue(cfg["hooks"]["PreToolUse"])
        self.assertTrue(glob.glob(s + ".*_before_safety_hooks.bak"))                    # dated backup of settings.json
        r = subprocess.run([ROOT + "/install.sh", "--merge-settings"], env=self.env, capture_output=True, text=True)
        self.assertEqual(len(json.load(open(s))["hooks"]["Stop"]), 2)                    # idempotent
        self.assertTrue(os.path.exists(self.home + "/.claude/safety-hooks/no_delete.py"))

if __name__ == "__main__":
    unittest.main()
