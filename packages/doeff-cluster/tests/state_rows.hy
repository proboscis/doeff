;;; test が ClusterState の表の欄を dict の行から組むための口(T3a の test 側・T4 以降の欄の移しも使う)。
;;;
;;; 欄が Table[V] になった後は、test が `:tasks {"t1" 行}` と dict を直に渡せない。行を鍵 → 行の dict で書き、ここで表に直して ClusterState に渡す。
(require doeff-hy.macros [defk])
(import doeff_hy._table [Table TableWrite table-of])


(defk table-from-rows [rows]
  {:pre [(: rows dict)] :post [(: % Table)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "鍵 → 行の dict rows から Table を作るため(行の並びは dict の挿入順 — 挿入順を変えた同じ集合を比べる検はここへ並べ替えた dict を渡す)。"
  (table-of (tuple (gfor #(key row) (.items rows) (TableWrite key row)))))


(defk rows-merged [table rows]
  {:pre [(: table Table) (: rows dict)] :post [(: % Table)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "表 table の上に鍵 → 行の dict rows を書き足した表を得るため(同じ鍵は rows の行が勝つ — dict の `(| 元 rows)` に当たる)。"
  (.with-writes table (tuple (gfor #(key row) (.items rows) (TableWrite key row)))))
