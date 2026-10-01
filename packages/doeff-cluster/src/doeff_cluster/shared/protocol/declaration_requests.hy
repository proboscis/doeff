;;; coordinator の資源の口へ宣言の行を書く要求の本文の形(declare の CLI と手元の sim-cluster で同じ形 — declare から分けた・agora-redesign #2346)。
(require doeff-hy.macros [deff val])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})


(defn #^ dict spec-for-update [#^ dict row #^ dict current #^ (| int None) [replicas None]]  ; defk にできない: CLI の入口(Program の外)が呼ぶ純粋な判断
  "宣言の行 → PUT の spec。所有者と replicas と readiness の無い行の readiness はいまの資源の値を保つ。"
  (setv spec (dfor #(k v) (.items row) :if (!= k "name") k v))
  (| {"readiness" (.get current "readiness")}
     spec
     {"owner" (.get current "owner")
      "replicas" (if (is replicas None) (.get current "replicas" 1) replicas)}))


(deff create-body [#^ dict row #^ (| int None) replicas]  ; defk にできない: CLI の入口(Program の外)と sim-cluster の宣言が同じ形を作る純粋な判断
  {:pre [(: row dict) (: replicas (| int None))] :post [(: % dict)] :tags {:context "doeff-cluster" :role "protocol"}}
  "まだ無い Service を作る POST /resources/Service の本文を作るため(declare の CLI と手元の sim-cluster で同じ形)。replicas を付けなければ 1。"
  {"name" (get row "name")
   "spec" (| (dfor #(k v) (.items row) :if (!= k "name") k v)
             {"replicas" (if (is replicas None) 1 replicas)})})
