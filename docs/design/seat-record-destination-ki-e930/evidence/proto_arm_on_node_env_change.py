# 設計検証の最小実験(ki-e930・試作の差分 proto_arm_on_node_env_change.diff を当てた worktree で走らせる)。
# 本物の judgment.next-arm-for-job / charter-with-seat-env を呼ぶ。試作が足したのは
# (1) 帰属の nodeEnv(機体が席へ渡す env の指紋)と next-arm-for-job の比較、(2) node-seat-env-of(seat_env + 記録の宛先)だけ。
import sys

WT = "/home/kento/.worktrees/doeff-wt-ki-e930-plan/packages/doeff-agents"
sys.path[:0] = [WT + "/src"]
import hy  # noqa: F401,E402
import doeff_agents  # noqa: E402

assert doeff_agents.__file__.startswith(WT), doeff_agents.__file__
from doeff import run  # noqa: E402
from doeff_agents.sessionhost.acp import effects  # noqa: E402
from doeff_agents.sessionhost.acp.judgment import (  # noqa: E402
    charter_with_seat_env,
    next_arm_for_job,
    node_seat_env_digest_of,
    node_seat_env_of,
)

URL1, URL2 = "http://record-1.example:8874", "http://record-2.example:8874"
SEAT = (("ACP_BASE", "http://acp.example:8868"),)
HOME = {"account": "acct-1", "binding": None, "model": "claude-opus-5-5"}
d1 = run(node_seat_env_digest_of(run(node_seat_env_of(SEAT, URL1))))
d2 = run(node_seat_env_digest_of(run(node_seat_env_of(SEAT, URL2))))


def view(attr: dict | None) -> effects.SessionView:
    return effects.SessionView(
        session_id="s-1", agent_type="claude", status="running", work_dir="/w",
        lifecycle=effects.LIFECYCLE_MULTI_TURN, conversation={"id": "c-1"},
        effective_identity=None, result_payload=None, terminal_cause=None,
        turn_ended_at_ms=1, backend_kind="headless", backend_ref=None,
        launch_attribution=None if attr is None else {effects.ATTRIBUTION_AGENTD_KEY: attr},
        started_at_ms=0, backend_alive=True)


def attr(node_env: str | None) -> dict:
    a = {"conversationId": "c-1", "agentJobId": "aj-1", "account": "acct-1", "home": HOME, "arm": "launch", "effort": None}
    if node_env is not None:
        a["nodeEnv"] = node_env
    return a


cases = [
    ("同じ宛先(URL1 で生まれ・今も URL1)", view(attr(d1)), d1, effects.NEXT_ARM_SEND),
    ("宛先が変わった(URL1 で生まれ・今は URL2)", view(attr(d1)), d2, effects.NEXT_ARM_RESUME),
    ("指紋の無い古い session(配備前に生まれた)", view(attr(None)), d2, effects.NEXT_ARM_RESUME),
]
ok = True
for label, v, digest, want in cases:
    choice = run(next_arm_for_job("s-1", v, HOME, None, False, digest))
    good = choice.arm == want and (want != effects.NEXT_ARM_RESUME or choice.retire == "s-1")
    ok &= good
    print(f"{'OK ' if good else 'NG '} {label}: arm={choice.arm} retire={choice.retire} (期待 {want})")

charter = run(charter_with_seat_env({"session_env": {}}, run(node_seat_env_of(SEAT, URL2))))
got = charter["session_env"].get("RECORD_SERVICE_URL")
good = got == URL2
ok &= good
print(f"{'OK ' if good else 'NG '} resume で組み直した charter の RECORD_SERVICE_URL = {got}(期待 {URL2})")
charter0 = run(charter_with_seat_env({"session_env": {}}, run(node_seat_env_of(SEAT, None))))
good = "RECORD_SERVICE_URL" not in charter0["session_env"]
ok &= good
print(f"{'OK ' if good else 'NG '} 記録が無効な settings では置かない: {sorted(charter0['session_env'])}")
print("ALL OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
