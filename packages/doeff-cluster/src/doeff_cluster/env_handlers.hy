;;; 旧い path の実行環境の準備の process の入口(#2028 の 1 段目)。本体は worker/entry/env_tool(翻訳 env-translation は
;;; worker/protocol/env_translation)。worker が送る名 ENV-TOOL(worker/protocol/env_store)は `hy -m doeff_cluster.env_handlers` の名のまま
;;; (本番の worker が名で読む入口は動かさない)。ここは新しい入口の main へ渡すだけで、名を再輸出しない(消すのは #2113)。
(import doeff_cluster.worker.entry.env_tool [main])


(when (= __name__ "__main__")
  (main))
