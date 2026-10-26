# Step 5 of docs/19-agent-tutorial.md: the same review_file under the packaged test handler that
# scenario_agent_handler makes. Prints the result, to back the handler-swap scene of the video.
from chapter import load

load()

from doeff import run  # noqa: E402
from doeff_agents import ScenarioStep, scenario_agent_handler  # noqa: E402
from review import review_file  # noqa: E402

scenario = scenario_agent_handler(
    scripts={
        "review-src-utils.py-1": [
            ScenarioStep.success({"verdict": "request-changes", "issues": ["unused import"]}),
        ],
    },
)
print("result:", run(scenario(review_file("src/utils.py"))))
