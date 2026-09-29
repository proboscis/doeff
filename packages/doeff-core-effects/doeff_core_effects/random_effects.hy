;;; 乱数の effect — 決まった長さの乱数の byte を求める(agora-redesign #1544)。業務の語を持たない土台の語彙。
;;;
;;;   RandomBytes  count 個の乱数の byte を求める。答え = 長さ count の bytes。
;;;
;;; 何のためか: 起動ごとの名乗り(札の頭・requestId の衝突よけ)を os.urandom で直に作ると、模擬で決まった値に替えられず、検が実の乱数に
;;; 届く。program は乱数の出どころを知らず、この effect で求めるだけ。16 進などの綴りへの変換は求める側がする。
;;;
;;; 答え手: os-random-handler(os_random.hy — 本物・os.urandom)と seeded-random-handler(seeded_random.hy — 種と呼びの順で決まった値)。
(require doeff-hy.macros [defeffect])


(defeffect RandomBytes
  "count 個の乱数の byte を求める(頭の註)。答え = 長さ count の bytes。"
  {:fields [(: count int)]
   :answer bytes
   :tags {:context "random" :role "foundation"}})
