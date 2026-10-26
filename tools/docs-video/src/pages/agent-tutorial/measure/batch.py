# Step 4 of docs/19-agent-tutorial.md: review_all over a.py, b.py and c.py with at most 2 sessions at a time, b's
# agent timing out. Prints every effect the session handler answers in the order the scheduler ran them, the most
# sessions open at once, and the three results, to back the batch scene of the video.
from chapter import load

load()

from doeff import run  # noqa: E402
from doeff_agents import AwaitOutcome, AwaitStatus  # noqa: E402
from doeff_core_effects.handlers import try_handler  # noqa: E402
from doeff_core_effects.scheduler import scheduled  # noqa: E402
from review_all import review_all  # noqa: E402
from scripted import scripted_agent  # noqa: E402

approve = AwaitOutcome(status=AwaitStatus.EXITED, result={"verdict": "approve", "issues": []})
slow = AwaitOutcome(status=AwaitStatus.TIMED_OUT)
outcomes = {
    "review-a.py-1": [approve],
    "review-b.py-1": [slow],
    "review-c.py-1": [approve],
}

log = []
program = try_handler(scripted_agent(outcomes, log)(review_all(["a.py", "b.py", "c.py"], 2)))
results = run(scheduled(program))
open_now, most = set(), 0
for line in log:
    verb, session = line.split(" ", 1)
    session = session.split(":")[0]
    if verb == "launch":
        open_now.add(session)
    if verb == "stop":
        open_now.discard(session)
    most = max(most, len(open_now))
    print(f"handler: {line:<40} open: {' '.join(sorted(s[7:8] for s in open_now)) or '-'}")
print("most sessions open at once:", most)
for name, result in zip("abc", results):
    print(f"{name}:", result)
