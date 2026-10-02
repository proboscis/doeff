;;; 旧い path の子 process の入口(#2028 の 1 段目)。本体は worker/entry/job_entry。この版の worker が送る名 JOB-ENTRY は新しい入口の名
;;; (#2112)だが、配備してある coordinator・worker(送る名を切り替える前の版)は `hy -m doeff_cluster.job_entry …` を送り、job は宣言した
;;; doeff の commit の中で動く。だから旧い名は最後の配備まで残す。ここは新しい入口の main へ渡すだけで、名を再輸出しない(消すのは最後の
;;; 配備 — 旧い doeff の宣言を新旧両方を持つ doeff で宣言し直し、coordinator と worker を切り替えた版へ上げた後)。
(import doeff_cluster.worker.entry.job_entry [main])


(when (= __name__ "__main__")
  (main))
