# Steps 1 and 2 of docs/19-agent-tutorial.md: review_file under the page's scripted_agent, with an agent that
# finishes and returns a valid review. Prints the Program before any handler runs it, the session id, every effect
# the handler answers in order, and the result, to back the Program and handler scenes of the video.
from chapter import load

load()

from doeff import run  # noqa: E402
from doeff_agents import AwaitOutcome, AwaitStatus  # noqa: E402
from review import review_file, review_spec  # noqa: E402
from scripted import scripted_agent  # noqa: E402

program = review_file("src/utils.py")
print("review_file('src/utils.py') is", type(program).__name__)
print("session id:", review_spec("src/utils.py").session_id)

approve = AwaitOutcome(status=AwaitStatus.EXITED, result={"verdict": "approve", "issues": []})
log = []
agent = scripted_agent({"review-src-utils.py-1": [approve]}, log)
result = run(agent(program))
for line in log:
    print("handler:", line)
print("result:", result)
