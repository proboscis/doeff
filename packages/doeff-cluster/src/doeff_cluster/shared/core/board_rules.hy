;;; 盤(共有の保存)の書きの規則 — coordinator の /board(cluster_policy.board-write)とテストの fake の盤(tests/board_fake.hy — 本番と
;;; 同じ shared-http が話す HTTP の層の fake)が同じ定義で判じる(coordinator の core から移した・#2107。以前は shared_model が
;;; coordinator の cluster_policy を import していた)。型と effect(ReadShared・WriteShared・ANY)は doeff_cluster.shared.intent.shared_model。
(require doeff-hy.macros [defk val])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})

;; 期限つきの行の期限の上限(秒)。盤のほかの容量の上限は coordinator の状態の上限なので cluster_policy に残す。
(setv BOARD-MAX-TTL-SECONDS (* 30 24 3600))


;; current / expect は盤の値そのもの(どの JSON の値にもなる — 等しいかだけを見る)。
(defn #^ bool board-allows [#^ object current #^ bool present #^ bool has-expect #^ object expect]
  "compare-and-set: expect が無ければ無条件・None なら行が無い時だけ・値ならいまの値がそれと等しい時だけ書いてよい。"
  (cond
    (not has-expect) True
    (is expect None) (not present)
    True (and present (= current expect))))


(defk board-ttl-refusal [ttl]
  ;; ttl は要求の本文の欄の素の JSON の値 — 数かどうかを検めるのがこの関数の役目なので、型は JSON の値の全部。
  {:pre [(: ttl (| dict list str int float bool None))] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "盤の行の期限(秒・None = 期限なし)が書けない値なら理由の文、書けるなら None。coordinator の盤(board-write の 400)と
   テストの fake の盤が同じ規則で断るため(定義点はここ 1 つ — 契約テスト tests/test_shared_contract.hy)。"
  (if (or (is ttl None) (and (isinstance ttl #(int float)) (< 0 ttl (+ BOARD-MAX-TTL-SECONDS 1))))
      None
      (.format "ttlSeconds は 0 より大きく {} 以下: {!r}" BOARD-MAX-TTL-SECONDS ttl)))
