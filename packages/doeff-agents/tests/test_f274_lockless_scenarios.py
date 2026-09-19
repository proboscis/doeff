"""錠(1 認証 1 宿)の無い預かり所に対して B2 / B3 が落ちないかの確認(会話 c-TF2KG7HW・着地はしない検体)。

預かり所の wt/nolock の形: 別の借り手でも 409 で断らず新しい貸与 id を出す / 同じ借り手の再要求は同じ id を再具現 /
revoke は持ち主なら 200(released は true/false・2 度目も 200)。
"""

import dataclasses
import sys
from pathlib import Path

import doeff_hy  # noqa: F401

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import sessionhost_acp_lease_deftests as L  # noqa: E402
from doeff import Pass, Resume  # noqa: E402
from doeff_agents.sessionhost.acp.effects import (  # noqa: E402
    PHASE_ENDED,
    PHASE_RUNNING,
    CustodyLeaseBorrow,
    CustodyLeaseRevoke,
    LeaseGrant,
)
from doeff_agents.sessionhost.acp.fake import FakeCustody  # noqa: E402


class SameIdCustody(FakeCustody):
    """同じ借り手の再要求に同じ貸与 id を返す(HolderMine の再具現)— 錠なしでもこの形は残る。"""

    def __init__(self, *args, second_revoke_ok: bool = True, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.second_revoke_ok = second_revoke_ok
        self.released: set[str] = set()

    def dispatch(self, effect, k):
        if isinstance(effect, CustodyLeaseBorrow) and self.refuse_with is None and effect.account in self.tokens:
            self.borrowed.append((effect.kind, effect.account, effect.purpose))
            self.released.discard("lease-A")
            return Resume(
                k,
                LeaseGrant(
                    lease_id="lease-A",
                    kind=effect.kind,
                    renewed=len(self.borrowed) > 1,
                    hold_expires_at_ms=self.hold_ms,
                    access_token=self.tokens[effect.account],
                    auth_json=None,
                ),
            )
        if isinstance(effect, CustodyLeaseRevoke):
            self.revoked.append(effect.lease_id)
            first = effect.lease_id not in self.released
            self.released.add(effect.lease_id)
            return Resume(k, True if first else self.second_revoke_ok)
        return super().dispatch(effect, k)


def _two_jobs(world: L.World) -> None:
    world.settings = dataclasses.replace(world.settings, node_capacity=2)
    world.acp.put_row(L.bound_job("s-1"))
    world.acp.put_row(L.bound_job("s-2"))
    world.tick(0)
    for job in ("s-1", "s-2"):
        assert world.job(job).status["phase"] == PHASE_RUNNING, world.job(job).status


def test_two_turns_of_one_account_on_one_node_return_their_own_lease():
    # 錠なし = 同じ口座を同じ機体の 2 手番が同時に借りられる(預かり所は借りごとに別の id)。
    world = L.World()
    _two_jobs(world)
    assert world.journal() == {"s-1": "lease-1", "s-2": "lease-2"}
    world.sessions.finish(world.sid("s-1"), "done", {"ok": True})
    world.tick(500)
    assert world.job("s-1").status["phase"] == PHASE_ENDED
    assert world.job("s-2").status["phase"] == PHASE_RUNNING, "隣の手番は動き続ける"
    assert world.custody.revoked == ["lease-1"], "返すのは閉じた手番の 1 つだけ(sweep しない)"
    assert world.journal() == {"s-2": "lease-2"}
    world.sessions.finish(world.sid("s-2"), "done", {"ok": True})
    world.tick(500)
    assert world.custody.revoked == ["lease-1", "lease-2"]
    assert world.journal() == {}
    assert not [line for line in world.local.logs if "not returned" in line]


def test_shared_lease_id_is_returned_once_per_turn_and_a_200_second_revoke_is_silent():
    # HolderMine の再具現: 同じ機体の 2 手番が同じ貸与 id を持つ。預かり所(nolock)は 2 度目の revoke にも 200。
    world = L.World()
    world.custody = SameIdCustody(tokens={L.ACCOUNT: L.TOKEN})
    _two_jobs(world)
    assert world.journal() == {"s-1": "lease-A", "s-2": "lease-A"}
    world.sessions.finish(world.sid("s-1"), "done", {"ok": True})
    world.tick(500)
    assert world.custody.revoked == ["lease-A"]
    assert world.job("s-2").status["phase"] == PHASE_RUNNING
    assert world.journal() == {"s-2": "lease-A"}, "隣の手番の記録は残る(job 単位)"
    world.sessions.finish(world.sid("s-2"), "done", {"ok": True})
    world.tick(500)
    assert world.job("s-2").status["phase"] == PHASE_ENDED
    assert world.custody.revoked == ["lease-A", "lease-A"]
    assert world.journal() == {}
    assert not [line for line in world.local.logs if "not returned" in line], world.local.logs


def test_shared_lease_id_with_a_non_200_second_revoke_logs_once_and_still_ends_the_turn():
    # 2 度目の revoke が 200 で答えない預かり所でも、手番は閉じ、B3 の log が 1 行出るだけ。
    world = L.World()
    world.custody = SameIdCustody(tokens={L.ACCOUNT: L.TOKEN}, second_revoke_ok=False)
    _two_jobs(world)
    world.sessions.finish(world.sid("s-1"), "done", {"ok": True})
    world.tick(500)
    world.sessions.finish(world.sid("s-2"), "done", {"ok": True})
    world.tick(500)
    assert world.job("s-1").status["phase"] == PHASE_ENDED
    assert world.job("s-2").status["phase"] == PHASE_ENDED
    lines = [line for line in world.local.logs if "not returned" in line]
    assert len(lines) == 1 and "lease-A" in lines[0] and "s-2" in lines[0], world.local.logs
    assert world.journal() == {}


def test_drain_returns_both_turns_of_a_shared_account():
    world = L.World()
    _two_jobs(world)
    world.close_for_stop("SIGTERM")
    assert sorted(world.custody.revoked) == ["lease-1", "lease-2"]
    assert world.journal() == {}


def test_a_replaced_agentd_returns_both_journalled_leases_without_a_lock():
    world = L.World()
    _two_jobs(world)
    sids = [world.sid("s-1"), world.sid("s-2")]
    world.restart()
    for sid in sids:
        del world.sessions.views[sid]
    world.tick(1000)
    assert world.job("s-1").status["phase"] == PHASE_ENDED
    assert world.job("s-2").status["phase"] == PHASE_ENDED
    assert sorted(world.custody.revoked) == ["lease-1", "lease-2"]
    assert world.journal() == {}
