# 既存の器(基準 commit・無改変)の続きの腕が、席の env を「行の launch_overlay」から組むことの実測。
# agentd A(宛先 URL1)が起こした手番の行を置き、agentd B(宛先 URL2)が送りの腕で次の手番を送る形を作る。
import sys
WT = "/home/kento/.worktrees/doeff-wt-ki-e930-plan/packages/doeff-agents"
sys.path[:0] = [WT + "/src", WT + "/tests"]
import hy  # noqa: F401
import doeff_agents
assert doeff_agents.__file__.startswith(WT), doeff_agents.__file__
from doeff import run
from doeff_agents.sessionhost.acp.judgment import turn_session_env_of
from doeff_agents.sessionhost.policy import overlay_without_turn_auth, carry_launch_flags
import sessionhost_charter_reaches_the_seat_deftests as t

URL1, URL2 = "http://record-1.example:8874", "http://record-2.example:8874"
# 起こす腕(agentd A)が charter.session_env に置いたと仮定する値(差分の後の形)を、器が launch で行へ写す形で写す
launch_session_env = {"AGORA_CONVERSATION_ID": "c-1", "AGORA_SEAT_OPENER": "operator", "RECORD_SERVICE_URL": URL1}
overlay = carry_launch_flags({}, {"session_env": run(overlay_without_turn_auth(launch_session_env)),
                                  "model": None, "effort": None, "mcp_servers": {}})
# agentd B(URL2 で再起動)の送りの腕が運ぶ手番の env(預かり所の札なし = lease None)
turn_env = run(turn_session_env_of(None))
world = t.ContinueWorld()
run(t.run_continue(world, t.continued_row(overlay), {}))
spawned_env = world.spawns[0][1]
print("turn_env from agentd B:", turn_env)
print("row overlay session_env:", overlay["session_env"])
print("spawned RECORD_SERVICE_URL:", spawned_env.get("RECORD_SERVICE_URL"))
print("agentd B destination     :", URL2)
print("byte-identical to agentd B:", spawned_env.get("RECORD_SERVICE_URL") == URL2)
