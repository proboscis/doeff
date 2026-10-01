"""旧い path の見張りの入口(#2028 の 1 段目)。本体は worker/entry/shim.py。

worker が子を起こす命令(worker/core/launch・worker/protocol/probes)は `python -m doeff_cluster.shim` の名のまま送る(本番の worker が
名で読む入口は動かさない)。ここは新しい入口の main へ渡すだけで、名を再輸出しない(消すのは #2113)。
"""

from doeff_cluster.worker.entry.shim import main

if __name__ == "__main__":
    main()
