# Step 3 of docs/19-agent-tutorial.md: the outcomes where the agent does not simply finish. The agent asks a
# question and finishes after a follow-up; nothing comes back in time; the result fails the schema. Prints each
# effect the handler answers and what review_file returns or raises, to back the failure scene of the video.
from chapter import load

load()

from doeff import run  # noqa: E402
from doeff_agents import AwaitOutcome, AwaitStatus  # noqa: E402
from review import ReviewFailed, review_file, review_spec  # noqa: E402
from scripted import scripted_agent  # noqa: E402

approve = AwaitOutcome(status=AwaitStatus.EXITED, result={"verdict": "approve", "issues": []})
asks = AwaitOutcome(status=AwaitStatus.AWAITING_INPUT)
slow = AwaitOutcome(status=AwaitStatus.TIMED_OUT)
wrong = AwaitOutcome(
    status=AwaitStatus.EXITED,
    result={"verdict": "maybe", "issues": []},
    validation_error="'maybe' is not one of ['approve', 'request-changes']",
)

for path, outcomes in [("src/api.py", [asks, approve]), ("src/slow.py", [slow]), ("src/odd.py", [wrong])]:
    log = []
    agent = scripted_agent({review_spec(path).session_id: outcomes}, log)
    print(f"== {path}")
    try:
        print("returns:", run(agent(review_file(path))))
    except ReviewFailed as error:
        print("raises: ReviewFailed:", error)
    for line in log:
        print("  handler:", line)
