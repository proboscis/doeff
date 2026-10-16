# 再現: 止まり(3 分)の直後の歩で、worker の沈黙はずらされるが、
# clocks-shifted の一覧の外にある「頼み直しで延びる期限」(drain・盤の TTL の行・温める表)は止まりの長さを経過に数えて消える。
# 比べとして、一覧の中の盤の lease(semaphore の担い手)は残る。作業木は書き換えない(読むだけ)。
import hy  # noqa: F401
from doeff import run
from doeff_cluster.shared.intent.protocol import ClusterTiming
from doeff_cluster.shared.protocol.inbox import http_request
from doeff_cluster.coordinator.intent.cluster_model import ClusterState
from doeff_cluster.coordinator.intent.due_model import StepPace
from doeff_cluster.coordinator.core.api_policy import tick, tick_due, stall_of, clocks_shifted, resume_after_downtime, stamp_alive
from doeff_cluster.coordinator.core.cluster_policy import liveness_moves, sweep_due
from doeff_cluster.coordinator.protocol.request_bodies import responded

T = ClusterTiming()
V = {"python": "3.14.0", "doeff": "1"}
SEEN = 1000
JUDGED = SEEN + 2000
STALL = 180000

def req(state, method, path, body, now, actor="ops"):
    after, status, reply = responded(state, run(http_request(method, path, {}, body, actor=actor)), now, T)
    return after, status, reply

s = ClusterState()
s, st, _ = req(s, "POST", "/heartbeat", {"name": "w", "boot": "b1", "provides": ["net"], "capacity": 10, "taskReserve": 0, "versions": V, "statuses": []}, SEEN, actor="w")
print("heartbeat", st)
# 頼み直しで延びる期限 3 種(どれも頼み手が期限の内に頼み直す形)と、比べの semaphore の lease。
s, st, r = req(s, "POST", "/workers/w/drain", {"ttlSeconds": 60}, SEEN); print("drain", st)
s, st, r = req(s, "PUT", "/board/presence-x", {"value": {"pod": "p1"}, "ttlSeconds": 60}, SEEN); print("board ttl row", st, r)
s, st, r = req(s, "POST", "/leases/turn-x", {"op": "claim", "token": "tok1", "permits": 1, "ttlMs": 60000}, SEEN); print("semaphore lease", st, r)
# warm は runtimeEnv の形が要るので省く(読みの上では sweep-warms の until-ms も同じく一覧の外)

before = run(tick(s, JUDGED, T))
def show(label, st):
    print(f"  [{label}] workers.silent={sorted(st.silent)} drains={sorted(st.drains)} warms={len(st.warms)} "
          f"board={sorted(st.board)}")
show("止まる前の歩", before)
print("  tick-due(止まる前) =", run(tick_due(before, JUDGED, T)), " sweep-due =", run(sweep_due(before, JUDGED, T)))

now = JUDGED + STALL
stall = run(stall_of(StepPace(judged_ms=JUDGED, waited_ms=JUDGED + 5, planned_ms=SEEN + T.lease_ms + 1), now, T))
print("stall =", stall)
shifted = run(clocks_shifted(before, now, stall.gap_ms))
first = run(tick(shifted, now, T))
show("止まりの直後の歩(ずらし有り)", first)
print("  WorkerGone 等 =", run(liveness_moves(before, first, T)))
for k in sorted(first.board):
    print("  board row", k, "value=", first.board[k].value, "expires=", first.board[k].expires_ms)

# 起き直し(M4)も同じ一覧を使う: 止まる前の印の後に作り直した時も同じ形で消える。
resumed, gap = resume_after_downtime(run(stamp_alive(before, JUDGED)), now)
again = run(tick(resumed, now, T))
show(f"起き直し(gap={gap})の後の歩", again)

# 観測の口: GET /workers/w(ready = 生きていて drain 中でない)を、止まる前と止まりの直後の歩で読む。
for label, st_ in (("止まる前の歩", before), ("止まりの直後の歩", first)):
    _, code, view = req(st_, "GET", "/workers/w", None, JUDGED if st_ is before else now)
    print(f"  GET /workers/w [{label}] ->", code, {k: view.get(k) for k in ("live", "ready", "drain") if isinstance(view, dict)})
