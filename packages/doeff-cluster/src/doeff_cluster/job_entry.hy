;;; 旧い path の子 process の入口(#2028 の 1 段目)。本体は worker/entry/job_entry。worker が送る名 JOB-ENTRY(coordinator/core/cluster_policy・
;;; worker/protocol/declared)は、job が宣言した doeff の commit の中で `hy -m doeff_cluster.job_entry …` として動くので、宣言した全 service の
;;; doeff がこの commit 以上になるまで旧い名のまま送る(切り替え = #2112)。ここは新しい入口の main へ渡すだけで、名を再輸出しない
;;; (消すのは #2113 — 本番の coordinator・worker と全 service の doeff が切り替えの後になってから)。
(import doeff_cluster.worker.entry.job_entry [main])


(when (= __name__ "__main__")
  (main))
