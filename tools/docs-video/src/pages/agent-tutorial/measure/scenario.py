# Step 5 of docs/19-agent-tutorial.md: the same review_file under the packaged test handler ScenarioAgentHandler.
# Prints the result and the sessions it stopped, to back the handler-swap scene of the video.
from chapter import load

load()

from doeff import run  # noqa: E402
from doeff_agents import ScenarioAgentHandler, ScenarioStep  # noqa: E402
from review import review_file  # noqa: E402

scenario = ScenarioAgentHandler(
    scripts={
        "review-src-utils.py-1": [
            ScenarioStep.success({"verdict": "request-changes", "issues": ["unused import"]}),
        ],
    },
)
print("result:", run(scenario.wrap(review_file("src/utils.py"))))
print("stopped:", scenario.stopped_sessions)
