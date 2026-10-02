;;; この process の環境変数(worker が子へ渡した名)から実行先の文脈 RunContext を読む、入口の読み — 根の job_context から移した
;;; (#2981・#2167 の子)。読むのは子の入口(worker/entry/job_entry)と宿の答え手(shared/entry/host_reader の host-reader)。
;;; 名の綴りと読みの規則は doeff_cluster.shared.core.run_context_rules の context-of-environ(sim の宿は同じ規則を自分の dict に当てる)。
;;;
;;; 旧い入口 doeff_cluster.job_context(ここを再輸出していた)と、それを読んでいた foundation/host_contract の古い host-reader は
;;; 2026-10-03 に消した(利用者の決め・#2167)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "main"})
(import doeff [run])
(import doeff_cluster.foundation.process_versions [this-process-environ])
(import doeff_cluster.shared.intent.run_context [RunContext])
(import doeff_cluster.shared.core.run_context_rules [context-of-environ])


(defn #^ RunContext context-from-env []
  "この process の文脈を環境変数から読むため(子の入口が Program の外で 1 回読み、宿の答え手 host-reader が session で 1 回読む)。環境変数の
   写像は foundation の this-process-environ が渡す(入口の層は os.environ に触らない — DOEFF106・#3014)。"
  (run (context-of-environ (run (this-process-environ)))))
