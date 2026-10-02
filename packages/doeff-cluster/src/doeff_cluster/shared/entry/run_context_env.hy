;;; この process の環境変数(worker が子へ渡した名)から実行先の文脈 RunContext を読む、入口の読み — 根の job_context から移した
;;; (#2981・#2167 の子)。読むのは子の入口(worker/entry/job_entry)と宿の答え手(shared/entry/host_reader の host-reader)。
;;; 名の綴りと読みの規則は doeff_cluster.shared.core.run_context_rules の context-of-environ(sim の宿は同じ規則を自分の dict に当てる)。
;;;
;;; 宿の契約(foundation/host_contract)を import しない: 旧い入口 doeff_cluster.job_context がここを再輸出し、foundation/host_contract に
;;; 1 版残す旧い host-reader が旧い入口を読むので、ここが宿の契約を読むと import が輪になる(旧い入口を消すのは #2981 の 2 段目)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "main"})
(import os)
(import doeff [run])
(import doeff_cluster.shared.intent.run_context [RunContext])
(import doeff_cluster.shared.core.run_context_rules [context-of-environ])


(defn #^ RunContext context-from-env []
  "この process の文脈を os.environ から読むため(子の入口が Program の外で 1 回読み、宿の答え手 host-reader が session で 1 回読む)。"
  (run (context-of-environ os.environ)))
