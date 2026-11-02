;; memory の置き場は、宣言の index の欄の等しさの絞り(ListRows・CountRows の where・where-any)を、書きで保つ欄の値の覚え(欄の値 → 鍵の綴り)で
;; 引き、合い得る行だけに絞りと期限を当てる。出自 = 2026-11-02 の実測: 使い手の模擬の入れ替えの台を本番の量(約 18.5 万行の表)で回すと、
;; 一覧の読みの stack の 8 割が表の全部の鍵を頭から照らす読み(memory-row-visible? → json-equal?)の中にあり、起動から同期まで 411 秒
;; (index で引く PostgreSQL の置き場の本番は 148 秒)。引いた答えは表の全部の行を照らした読み(直す前の読み)と同じ。
(require doeff-hy.macros [val])
(import copy)
(import math)
(import pickle)
(import doeff_hy._frozen [FrozenMap])
(import doeff_records [CountRows ListRows MemoryStore])
(import doeff_records._admission [key-text row-matches? row-matches-any? json-equal? json-equal-token])
(import doeff_records._memory :as memory)

(import doeff_records._laws [LAW-SCHEMA MAKER TICKET-KEEP-SECONDS])

(val KEEP-MS (* TICKET-KEEP-SECONDS 1000))
(val NO-WHERE (FrozenMap))
;; 色の欄に置く値: json-equal? の等しさが Python の等しさと違う値(真偽と数・整数と浮動小数・写像と配列・NaN・null)を混ぜる。
(val COLORS #("red" "blue" "green" 1 1.0 True False None (FrozenMap {"tone" "red"}) #("red") math.nan))


(defn put-part [store id color state]  ; defk にできない: 検の種を置き場へ同期に置く(錠の内の書きと同じ道 memory-store-row)
  "表 parts に 1 行(番号 id・色 color・状態 state)を刻 0 で置くため(色は宣言の index の欄・ラベルは id ごとに違う index の欄)。"
  (setv key #(id)
        value (FrozenMap {"id" id "label" (+ "l-" id) "color" color "note" (len id) "state" state}))
  (memory.memory-store-row store "parts" key (memory.memory-current-row store "parts" key) value MAKER value 0))


(defn seeded-parts [count]  ; defk にできない: 検の種を同期に組む
  "表 parts に count 行を鍵の順と違う順で置いた置き場を作るため(色は COLORS を順に配り、7 行に 1 行は色の欄を持たない)。"
  (setv store (MemoryStore LAW-SCHEMA))
  (for [i (range count)]
    (setv n (% (* i 7919) count) id (.format "p{:04d}" n))
    (if (= (% n 7) 0)
        (memory.memory-store-row store "parts" #(id) None (FrozenMap {"id" id "label" (+ "l-" id) "state" "open"}) MAKER
                                 (FrozenMap {"id" id}) 0)
        (put-part store id (get COLORS (% n (len COLORS))) "open")))
  store)


(defn scanned-texts [store table-name where where-any now-ms]  ; defk にできない: 錠の内の読みと比べる同期の読みの写し
  "表の全部の行に絞りと期限を当てた鍵の綴り(綴りの順)— 直す前の読み。"
  (setv decl (store.schema.table table-name)
        table (get store.rows table-name))
  (lfor text (sorted table)
        :setv stored (get table text)
        :if (and (row-matches? where stored.row.value)
                 (row-matches-any? where-any stored.row.value)
                 (not (memory.stored-row-expired? decl stored now-ms)))
        text))


(defn paged-texts [store table-name where where-any limit now-ms]  ; defk にできない: 錠の内の読みを同期に呼ぶ
  "鍵の順の頁を頭から送り、出た行の鍵の綴りを並べて返すため。"
  (setv cursor None keys [])
  (while True
    (setv page (memory.memory-list-rows store (ListRows table-name :where where :where-any where-any :cursor cursor :limit limit) now-ms))
    (.extend keys (gfor row page.rows (key-text row.key)))
    (setv cursor page.next-cursor)
    (when (is cursor None)
      (return keys))))


(defn reads-agree [store table-name filters now-ms]  ; defk にできない: 錠の内の読みを同期に呼んで比べる
  "絞りの組 filters ごとに、一覧・数え・鍵の順の頁が表の全部の行を照らした読みと同じ行を答える事を確かめるため。"
  (setv decl (store.schema.table table-name))
  (for [#(where where-any) filters]
    (setv want (scanned-texts store table-name where where-any now-ms))
    (assert (= (memory.memory-matching-texts store decl table-name where where-any now-ms) want) #(where where-any))
    (assert (= (. (memory.memory-count-rows store (CountRows table-name :where where :where-any where-any) now-ms) count) (len want))
            #(where where-any))
    (for [limit #(1 3 50)]
      (assert (= (paged-texts store table-name where where-any limit now-ms) want) #(where where-any limit)))))


(val PART-FILTERS
  (tuple (+ (tuple (gfor color COLORS #((FrozenMap {"color" color}) (FrozenMap))))
            #(#(NO-WHERE (FrozenMap {"color" #("red" 1 None)}))
              #(NO-WHERE (FrozenMap {"color" #(True 1.0 (FrozenMap {"tone" "red"}))}))
              #(NO-WHERE (FrozenMap {"color" #(["red"] {"tone" "red"})}))
              #((FrozenMap {"color" "red"}) (FrozenMap {"label" #("l-p0001" "l-p0012" "l-p0101")}))
              #((FrozenMap {"color" "blue" "label" "l-p0002"}) (FrozenMap))
              #((FrozenMap {"color" "nobody"}) (FrozenMap))
              #(NO-WHERE (FrozenMap {"color" #("red") "id" #("p0011" "p0022" "p0033")}))))))


(defn test-a-token-is-equal-exactly-when-the-values-are-json-equal []  ; defk にできない: 純関数の印を同期に比べる検
  ;; 印の作れる 2 つの値の印が等しい事と json-equal? で等しい事は同じ(NaN と hash できない値は印を作らない)。
  (setv values (+ COLORS #(0 0.0 -0.0 "1" "" #() (FrozenMap) [] {} ["red"] {"tone" "red"} #(1 True) #(1.0 True) #(True 1)
                           (FrozenMap {"a" #(1 None)}) {"a" [1.0 None]} (* 2 (** 10 20)) (float (* 2 (** 10 20))))))
  (for [a values b values]
    (setv ta (json-equal-token a) tb (json-equal-token b))
    (when (and (is-not ta None) (is-not tb None))
      (assert (= (= ta tb) (json-equal? a b)) #(a b ta tb))))
  (assert (is (json-equal-token math.nan) None))
  (assert (is (json-equal-token #(1 math.nan)) None))
  (assert (is (json-equal-token (bytearray b"x")) None)))


(defn test-indexed-reads-answer-the-rows-of-the-full-scan []  ; defk にできない: 錠の内の読みを同期に呼んで比べる検
  ;; 欄の値の種類(真偽と数・整数と浮動小数・写像と配列・NaN・null・欄の無い行)× 絞り(where・where-any・両方・組の重なり・空の組・鍵の組と
  ;; 一緒)× limit: 一覧・数え・頁が、表の全部の行を照らした読みと同じ行を答える。
  (reads-agree (seeded-parts 400) "parts" PART-FILTERS 0))


(defn test-indexed-reads-follow-writes-removals-and-copies []  ; defk にできない: 錠の内の読みと書きを同期に混ぜる検
  ;; 覚えを作った後の書き(色を替える・欄を外す・新しい行)と消し・copy と pickle の写しへの書きの後も、読みは全部の行を照らした読みと同じ。
  (setv store (seeded-parts 200))
  (reads-agree store "parts" PART-FILTERS 0)
  (put-part store "p0001" "blue" "open")
  (put-part store "p0002" 1.0 "open")
  (put-part store "p9999" "red" "open")
  (memory.memory-store-row store "parts" #("p0003") (memory.memory-current-row store "parts" #("p0003"))
                           (FrozenMap {"id" "p0003" "label" "l-p0003" "state" "open"}) MAKER (FrozenMap {"id" "p0003"}) 0)
  (memory.remove-stored-row store "parts" (key-text #("p0004")) 0)
  (memory.remove-stored-row store "parts" (key-text #("p0005")) 0)
  (reads-agree store "parts" PART-FILTERS 0)
  (for [copied #((copy.deepcopy store) (pickle.loads (pickle.dumps store)))]
    (put-part copied "p0006" "green" "open")
    (put-part copied "p8888" "red" "open")
    (reads-agree copied "parts" PART-FILTERS 0))
  (reads-agree store "parts" PART-FILTERS 0))


(defn test-rows-placed-directly-are-seen-after-the-sorted-keys-are-dropped []  ; defk にできない: 置き場の行を直に置く使い手の作法を同期に写す検
  ;; 置き場の行を直に置く使い手(使い手の模擬の、前の版の状態の種まき)は、並べた鍵の覚え(sorted-texts の表の覚え)を外す — 欄の値の
  ;; 覚えもそれで外れ、次の読みが作り直す(古い覚えで答えず、直に置いた行の書き換えの後の書きも崩れない)。
  (setv store (seeded-parts 100))
  (reads-agree store "parts" PART-FILTERS 0)
  (for [id #("p0001" "p0002" "p7777")]
    (setv value (FrozenMap {"id" id "label" (+ "l-" id) "color" "red" "note" 1 "state" "open"}))
    (setv (get (get store.rows "parts") (key-text #(id))) (memory.StoredRow (memory.spelled-row #(id) value (memory.canonical-json value) 1) 0))
    (.pop store.sorted-texts "parts" None))
  (put-part store "p0002" "green" "open")
  (reads-agree store "parts" PART-FILTERS 0))


(defn test-indexed-reads-hide-expired-rows-like-the-full-scan []  ; defk にできない: 錠の内の読みを同期に呼んで比べる検
  ;; 保持の期限の後の読みは、覚えに残る閉じた行を出さない(回収の前でも)— 全部の行を照らした読みと同じ。
  (setv store (MemoryStore LAW-SCHEMA))
  (for [n (range 90)]
    (setv key #((.format "g{}" (% n 3)) (.format "t{:03d}" n)) owner (get #("a" "b" "c") (% n 3)))
    (memory.memory-store-row store "tickets" key None (FrozenMap {"group" (get key 0) "id" (get key 1) "owner" owner
                                                                  "state" (if (= (% n 4) 0) "done" "open")})
                             MAKER (FrozenMap {"owner" owner}) 0))
  (setv filters #(#((FrozenMap {"owner" "a"}) (FrozenMap)) #(NO-WHERE (FrozenMap {"owner" #("b" "c")}))
                  #((FrozenMap {"group" "g1"}) (FrozenMap {"owner" #("a" "b")}))))
  (reads-agree store "tickets" filters 0)
  (reads-agree store "tickets" filters (+ KEEP-MS 1)))


(defn test-an-indexed-read-judges-only-the-rows-of-the-value [monkeypatch]  ; defk にできない: pytest の fixture を受ける検
  ;; 3,000 行の表(色 3 つ)の色の絞りの読みは、その色の行だけに絞りを当てる(表の全部の行を照らす読みに戻すと 3,000 回になり赤)。
  (setv store (MemoryStore LAW-SCHEMA))
  (for [n (range 3000)]
    (put-part store (.format "p{:04d}" n) (get #("red" "blue" "green") (% n 3)) "open"))
  (setv decl (store.schema.table "parts") calls [] original memory.memory-row-visible?)
  (defn counted [#* args]
    (.append calls 1)
    (original #* args))
  (.setattr monkeypatch memory (hy.mangle "memory-row-visible?") counted)
  (assert (= (len (memory.memory-matching-texts store decl "parts" (FrozenMap {"color" "red"}) NO-WHERE 0)) 1000))
  (assert (= (len calls) 1000) (len calls))
  (.clear calls)
  (assert (= (. (memory.memory-count-rows store (CountRows "parts" :where-any (FrozenMap {"color" #("blue" "nobody")})) 0) count) 1000))
  (assert (= (len calls) 1000) (len calls))
  (.clear calls)
  ;; ラベルは行ごとに違う — 1 行の読みは 1 行だけを照らす。
  (setv page (memory.memory-list-rows store (ListRows "parts" :where (FrozenMap {"color" "red" "label" "l-p0300"}) :limit 10) 0))
  (assert (= (lfor row page.rows row.key) [#("p0300")]) page)
  (assert (= (len calls) 1) (len calls)))
