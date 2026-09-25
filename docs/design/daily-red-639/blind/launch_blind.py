import subprocess, sys, os, time
role = sys.argv[1]
base = "/Users/kento/.worktrees/doeff-wt-639-verify/docs/design/daily-red-639/blind"
prompt = open(f"{base}/blind-{role.lower()}-input.md", encoding="utf-8").read()
work = f"/tmp/wt639-blind-{role}/tree"
out = f"/tmp/wt639/blind-{role}-last.md"
log = open(f"/tmp/wt639/blind-{role}-codex.log", "w", encoding="utf-8")
env = {k: v for k, v in os.environ.items() if not k.startswith("HERDR_")}
argv = ["cx", "personal", "exec", "-m", "gpt-6-astra", "-c", 'model_reasoning_effort="low"',
        "-C", work, "--skip-git-repo-check", "-o", out, "-"]
p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, env=env,
                     start_new_session=True, text=True, encoding="utf-8")
p.stdin.write(prompt); p.stdin.close()
print(role, "pid", p.pid, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
