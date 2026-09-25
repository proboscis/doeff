"""盲検 B の反例の再現(設計者の実測): 一覧の後・鍵での読み直しの前に別の書き手の ended が着地する競合で、
巡回が ended の記録へ 2 度目を書くか・手番の終わりが書いた usage を消すかを数える。
S1 = 別の書き手が usage つきの ended(手番の終わりの書き)・S2 = usage なしの ended(別の agentd の巡回)。"""

import sys
from pathlib import Path

import doeff_hy  # noqa: F401  # Hy の import hook

TESTS = Path("packages/doeff-agents/tests").resolve()
sys.path.insert(0, str(TESTS))
t = __import__("sessionhost_acp_turn_events_deftests")
from doeff_agents.sessionhost.acp.fake import FakeAcp  # noqa: E402


class RacingAcp(FakeAcp):
    def __init__(self, births, other_status):
        super().__init__(births)
        self.other_status = other_status
        self.raced = False

    def _running_turn_records(self):
        listed = super()._running_turn_records()
        if not self.raced and t.record_key_of("j-2") in self.rows:
            self.raced = True
            self._put_status(self.rows[t.record_key_of("j-2")], self.other_status)
        return listed


def scenario(label, other_status):
    world = t.World()
    racing = RacingAcp(world.acp.births, other_status)
    for row in world.acp.rows.values():
        racing.put_row(row)
    world.acp = racing
    racing.put_row(t.running_record_row("j-2", t.NODE))
    racing.put_row(t.job_row_in_phase("j-2", t.PHASE_ENDED, t.NODE))
    world.tick(0)
    writes = t.record_writes_of(world, "j-2")
    final = racing.rows[t.record_key_of("j-2")]
    swept = [m for m in world.local.metrics if m["metric"] == "agentd_turn_record_sweep_ended"]
    print(f"{label}: 記録への書き {len(writes)} 回・最終 status {final.status}・generation {final.generation}・"
          f"巡回の ended の計器 {len(swept)}・usage が残る {'usage' in final.status}")


scenario("S1(usage つきの ended が先に着地)", {"state": "ended", "usage": {"input": 10, "output": 5, "cacheWrite": 0, "cacheRead": 0}})
scenario("S2(usage なしの ended が先に着地)", {"state": "ended"})
