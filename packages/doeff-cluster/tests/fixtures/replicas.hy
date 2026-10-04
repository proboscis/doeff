;;; 取り下げ・起こしの筋書きの部品 — 系の job の望む台数(:replicas)を替えた同じ系(#3487 — 台数は Redeclare の引数ではなく系の値の
;;; job が持つ。取り下げ = 台数 0 の系の宣言し直し)。
(require doeff-hy.macros [defk])
(import dataclasses [replace])
(import doeff_cluster.shared.intent.service_model [System])


(defk with-replicas [system replicas]
  {:pre [(: system System) (: replicas int)] :post [(: % System)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "系の全部の job の望む台数を replicas にした同じ系を作るため — 取り下げ・起こしの筋書きと、台数を運ばない答え手の代役が宣言し直す系。"
  (System :name system.name :jobs (tuple (gfor j system.jobs (replace j :replicas replicas)))))
