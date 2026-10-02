"""検の変種の shim(条 C4b の失敗ケース — tests/test_shim_descendants.hy): 本物の shim(worker/entry/shim)に、子孫の引き取りを
「使えない」と答える部品を渡す。引き取りと片づけを外した shim として、別の session の孫が止め切りの後も残ることと、使えない理由の
1 行が stderr に出て process group への合図だけで止め続けることを確かめるため。

使い方は本物と同じ: python -m tests.fixtures.shim_without_adoption <猶予秒> -- <job の命令…>(検は process-host の StartProcess の
命令の module の名をこの名へ差し替える)
"""

from doeff_cluster.worker.entry.shim import NotAdopting, main

MODULE_TAGS = {"context": "doeff-cluster-test", "role": "main"}


def refused() -> NotAdopting:
    """子孫の引き取りを使えないと答える部品(検の変種が本物の shim へ渡すため)。"""
    return NotAdopting("検の変種: 子孫の引き取りを外した")


if __name__ == "__main__":
    main(adopt=refused)
