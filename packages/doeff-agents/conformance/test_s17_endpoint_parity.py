"""S17: in-process result endpoint ↔ host endpoint parity — 器のみ(検査本体は C3)。

契約(README カバレッジ行列 S17、タグ X → C1 後に P 化する布石):
per-kind ゲート(直接束縛・in-process)と交代ゲート(agentd の公開 socket 越し)
の継ぎ目で、result チャネルの意味論が両束縛で一致しなければならない。

C3 で埋める検査本体の形(骨格として凍結しておく):

1. 同一シナリオ(valid 報告 / schema-invalid 報告 / 終端後の再報告)を
   (a) in-process result endpoint(C2 の per-kind defhandler 直接束縛)と
   (b) host endpoint(`session.report_result` daemon wire + report-result-mcp relay)
   の両方で駆動する。
2. 面ごとの assert(ハザード 5 を両束縛とも再現すること):
   - agent 可視面: `{"content":[{"text":...}],"isError":bool}` + schema 文言。
     数値エラーコードは現れない(relay が透過し始めたら parity break)。
   - wire 面: -32002 / -32003 / `already_reported:true` は daemon 制御 socket の
     `session.report_result` 応答にのみ現れる。
3. byte-faithful: await_result が返す payload は報告 JSON と byte 一致(S1/0035)。

in-process 側の束縛は C2(impls/ の per-kind defhandler)と C3(Hy session host)
が揃って初めて存在するため、本体はそこまで X(expected-red 相当の skip)。
"""

import pytest


@pytest.mark.skip(
    reason=(
        "S17 is the C1-scheduled skeleton: the in-process result endpoint "
        "(doeff_agents.sessionhost direct binding) gains its per-kind impls in "
        "C2 and the host endpoint swaps to the Hy session host in C3 — the "
        "parity body lands there (README matrix row S17, tag X until then)."
    )
)
def test_s17_inproc_and_host_result_endpoints_agree() -> None:
    """C3 で: 同一台本を直接束縛と host 束縛で駆動し、agent 面 / wire 面の両面で
    report_result / await_result の観測可能挙動が一致することを assert する。"""
    raise AssertionError("parity body is C3 scope — this skeleton must stay skipped until then")
