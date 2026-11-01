;; memory の置き場の鍵の順の頁(ListRows の並べる欄の無い読み)は、cursor の位置の後から絞りに合う行を limit + 1 件見つけた所で判じを止める。
;; 出自 = 2026-10-31 の実測: 頁ごとに表の全部の鍵を並べ直して全部の行に絞りを当てていたので、頭からの頁送りの全読みが表の行の数の 2 乗に
;; なり、約 2.8 万行の表の読み込みで頁 1 枚 0.26 秒かかった。直した読みの頁と続きの cursor は、直す前の読み(下の reference-page)と同じ。
(require doeff-hy.macros [val])
(import copy)
(import pickle)
(import doeff_hy._frozen [FrozenMap])
(import doeff_records [ListCursor ListRows Page])
(import doeff_records._admission [key-text key-from-text row-matches? row-matches-any?])
(import doeff_records._memory :as memory)
(import doeff_records [MemoryStore])
(import doeff_records._laws [LAW-SCHEMA MAKER TICKET-KEEP-SECONDS])

(val KEEP-MS (* TICKET-KEEP-SECONDS 1000))
(val OWNERS #("a" "b" "c"))
(val NO-WHERE (FrozenMap))
(val NO-WHERE-ANY (FrozenMap))


(defn reference-page [store ask now-ms]  ; defk にできない: 錠の内の読み(memory-list-rows)と比べる同期の読みの写し
  "直す前の鍵の順の頁の読みを写した物 — 表の全部の鍵を並べ、全部の行に絞りと期限を当て、cursor の位置の後の limit 件を取る。続きの cursor は
   位置の後に絞りに合う行が limit 件より多い時だけ。直した読みが同じ頁を返す事を比べるため。"
  (setv decl (store.schema.table ask.table)
        table (get store.rows ask.table)
        after (if (is ask.cursor None) None ask.cursor.after-key)
        matching (lfor text (sorted table)
                       :setv value (. (get table text) row value)
                       :if (and (row-matches? ask.where value)
                                (row-matches-any? ask.where-any value)
                                (not (memory.stored-row-expired? decl (get table text) now-ms))
                                (or (is after None) (> text after)))
                       text)
        taken (cut matching ask.limit))
  (Page (tuple (gfor text taken (. (get table text) row)))
        (if (> (len matching) ask.limit) (ListCursor store.epoch (get taken -1)) None)
        store.epoch
        store.head))


(defn put-ticket [store group id owner state now-ms]  ; defk にできない: 検の種を置き場へ同期に置く(錠の内の書きと同じ道 memory-store-row)
  "表 tickets に 1 行(組 group・番号 id・持ち主 owner・状態 state)を刻 now-ms で書くため(閉じた行 done は保持の秒の後に読みから消える)。"
  (setv key #(group id)
        current (memory.memory-current-row store "tickets" key))
  (memory.memory-store-row store "tickets" key current
                           (FrozenMap {"group" group "id" id "owner" owner "state" state})
                           MAKER (FrozenMap {"owner" owner "state" state}) now-ms))


(defn seeded-store [count]  ; defk にできない: 検の種を同期に組む
  "表 tickets に count 行を鍵の順と違う順で置いた置き場を作るため(持ち主は 3 人に配り、5 行に 1 行を刻 0 で閉じる — 保持の秒の後の読みでは
   閉じた行が消える)。"
  (setv store (MemoryStore LAW-SCHEMA))
  (for [i (range count)]
    (setv n (% (* i 7919) count))
    (put-ticket store (.format "g{}" (% n 4)) (.format "t{:05d}" n) (get OWNERS (% n 3)) (if (= (% n 5) 0) "done" "open") 0))
  store)


(defn pages-agree [store where where-any limit now-ms]  ; defk にできない: 錠の内の読みを同期に呼んで比べる
  "頭から頁を送り、頁ごとに直した読みと直す前の読みが同じ答え(行・続きの cursor・epoch・head)を返す事を確かめ、読んだ行の鍵を返すため。"
  (setv cursor None keys [])
  (while True
    (setv ask (ListRows "tickets" :where where :where-any where-any :cursor cursor :limit limit)
          got (memory.memory-list-rows store ask now-ms)
          want (reference-page store ask now-ms))
    (assert (= got want) #(limit now-ms cursor got want))
    (.extend keys (gfor row got.rows row.key))
    (setv cursor got.next-cursor)
    (when (is cursor None)
      (return keys))))


(defn test-pages-and-cursors-match-the-full-scan-at-every-boundary []  ; defk にできない: 錠の内の読みを同期に呼んで比べる検
  ;; 絞り(無し・where・where-any・両方)× limit(1・割り切れる・割り切れない・行の数ちょうど・行の数より多い)× 期限(前・後)。
  (setv store (seeded-store 60))
  (for [#(where where-any) #(#(NO-WHERE NO-WHERE-ANY)
                             #((FrozenMap {"owner" "a"}) NO-WHERE-ANY)
                             #(NO-WHERE (FrozenMap {"owner" #("a" "c")}))
                             #((FrozenMap {"group" "g1"}) (FrozenMap {"owner" #("b" "c")}))
                             #((FrozenMap {"owner" "nobody"}) NO-WHERE-ANY))
        limit #(1 4 7 20 40 60 61 500)
        now-ms #(0 (+ KEEP-MS 1))]
    (setv keys (pages-agree store where where-any limit now-ms))
    (assert (= keys (sorted keys :key key-text)) keys))
  ;; 期限の後の読みは閉じた行を出さない(頁の上限の前に除くので、頁の行の数は limit のまま)。
  (setv later (pages-agree store NO-WHERE NO-WHERE-ANY 7 (+ KEEP-MS 1)))
  (assert (= (len later) 48) later))


(defn test-rows-written-and-removed-between-pages-are-seen-like-the-full-scan []  ; defk にできない: 錠の内の読みと書きを同期に混ぜる検
  ;; 頁の間に cursor の位置より前と後へ行を足し、cursor の位置の行そのものを消しても、次の頁は直す前の読みと同じ(並べた鍵の覚えが書きで
  ;; 直る)。
  (setv store (seeded-store 30)
        first-ask (ListRows "tickets" :limit 10)
        first (memory.memory-list-rows store first-ask 0))
  (assert (= first (reference-page store first-ask 0)) first)
  (setv cursor first.next-cursor)
  (put-ticket store "g0" "a-before" "a" "open" 0)
  (put-ticket store "g9" "z-after" "b" "open" 0)
  (memory.remove-stored-row store "tickets" cursor.after-key 0)
  (setv next-ask (ListRows "tickets" :cursor cursor :limit 10)
        next (memory.memory-list-rows store next-ask 0))
  (assert (= next (reference-page store next-ask 0)) next)
  (assert (not-in #("g0" "a-before") (lfor row next.rows row.key)) next)
  (setv rest (pages-agree store NO-WHERE NO-WHERE-ANY 10 0))
  (assert (in #("g9" "z-after") rest) rest)
  (assert (not-in (key-from-text cursor.after-key) rest) rest)
  (assert (= (len rest) 31) rest))


(defn test-a-copied-store-pages-on-its-own-keys []  ; defk にできない: pickle と copy の後の置き場を同期に読む検
  ;; 並べた鍵の覚えは置き場の中身から導ける物 — copy と pickle は運ばず、写しへの書きは元の置き場の頁に出ない。
  (setv store (seeded-store 20))
  (pages-agree store NO-WHERE NO-WHERE-ANY 6 0)
  (for [copied #((copy.deepcopy store) (pickle.loads (pickle.dumps store)))]
    (put-ticket copied "g0" "copied" "a" "open" 0)
    (assert (= (len (pages-agree copied NO-WHERE NO-WHERE-ANY 6 0)) 21) copied))
  (assert (= (len (pages-agree store NO-WHERE NO-WHERE-ANY 6 0)) 20) store))


(defn test-a-page-judges-only-the-rows-it-needs [monkeypatch]  ; defk にできない: pytest の fixture を受ける検
  ;; 2,000 行の表の頁 1 枚は、cursor の位置の後から limit + 1 行だけ絞りを当てる(表の全部の行に当てない)。
  (setv store (seeded-store 2000) calls [] original memory.memory-row-visible?)
  (defn counted [#* args]
    (.append calls 1)
    (original #* args))
  (.setattr monkeypatch memory (hy.mangle "memory-row-visible?") counted)
  (setv head (memory.memory-list-rows store (ListRows "tickets" :limit 10) 0))
  (assert (= (len calls) 11) (len calls))
  (.clear calls)
  (setv middle (ListCursor store.epoch (key-text #("g1" "t01001"))))
  (memory.memory-list-rows store (ListRows "tickets" :cursor middle :limit 10) 0)
  (assert (= (len calls) 11) (len calls))
  (assert (is-not head.next-cursor None) head))
