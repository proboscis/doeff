;;; 旧い path の drain の入口(#2029 の 1 段目)。本体は worker/entry/drain_main。image に焼いた起動の script(deploy/boot.sh)と
;;; Pod の preStop・readinessProbe は `hy -m doeff_cluster.drain_main` の名で起こす(本番の worker が名で読む入口は動かさない)。ここは新しい
;;; 入口の main へ渡すだけで、名を再輸出しない(消すのは #2113 — 配備中の image と boot.sh が新しい名へ移った後)。
(import doeff_cluster.worker.entry.drain_main [main])


(when (= __name__ "__main__")
  (main))
