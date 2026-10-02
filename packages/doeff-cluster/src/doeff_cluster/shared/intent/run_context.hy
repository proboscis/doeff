;;; 実行先の子 process の文脈の型 RunContext(worker が環境変数で渡す)— 根の job_context から移した(#2981・#2167 の子)。
;;; 環境変数の名との綴りと読み(worker-context-environ・process-context-environ・context-of-environ・runtime-env-of-context)は
;;; doeff_cluster.shared.core.run_context_rules。この process の環境変数そのものの読み(context-from-env)と、宿の契約の
;;; Ask HOST-CONTRACT.run-context-key への答え手(host-reader)は doeff_cluster.shared.entry.host_reader。
;;;
;;; 入口(job_entry)でない module に置く理由: 子は `hy -m doeff_cluster.worker.entry.job_entry` で起き、入口の module は __main__ として読まれる。
;;; 型を入口に置くと、env の組み立て(業務の側の関数)が入口から import した時に同じ file がもう 1 回読まれて class が 2 つになり、
;;; __main__ の RunContext を渡された :pre の型の検めが必ず落ちた(実験用の namespace で再現・2026-09-26)。この module の class は 1 つだけ読まれる。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "type"})
(import dataclasses [dataclass])


(defclass [(dataclass :frozen True)] RunContext []
  "実行先の文脈(worker が環境変数で渡す)。env の組み立てだけが読む。"
  (#^ str coordinator-url)
  (#^ str worker)
  (#^ str revision)
  (#^ str job)
  ;; この process の世代(worker が起こした時に振った名・試行の番号・起こした spec の指紋・割り当ての世代)。
  ;; readiness と計器の報告に載せ、coordinator は今の宣言で今動いている process の報告だけを数える。
  (setv #^ str instance "")
  (setv #^ str attempt "")
  (setv #^ str spec-hash "")
  (setv #^ str placement "")
  ;; 実行環境の宣言(JSON の文字列)とキー。env の task でなければ空。子がさらに task を送る時の既定の env になる。
  (setv #^ str runtime-env "")
  (setv #^ str env-key "")

  (defn #^ (get dict #(str (| str int None))) identity [self]
    "報告に載せる process の世代(coordinator の resource_policy.report-matches が比べる欄)。"
    {"instance" self.instance "attempt" self.attempt "specHash" self.spec-hash
     "placement" (if self.placement (int self.placement) None)}))
