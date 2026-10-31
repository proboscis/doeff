;; 表の行の一覧 ListRows の答えの分け方(ADR-DOE-CORE-EFFECTS-003 R5・R17 の追補 — card ki-61a3a1afc2a0):
;; defeffect が Page = 成功 / Reset = 値 / Unreachable = 失敗 / NotIndexed = 失敗(不在は無し)と宣言し、呼び手の <- は
;;   - Page を中身のまま束ねる
;;   - Reset を値のまま束ねる(Raise にも Absent にもしない — 呼び手が一覧の頭から読み直して続ける普通の答え)
;;   - Unreachable を Raise(Unreachable) として逃がす(on-raise で値へ戻せる。戻さなければ上へ出る)
;;   - NotIndexed を Raise(NotIndexed) として逃がす(索引の無い欄で絞った誤り — on-raise で値へ戻せる)
;; 答える memory の handler は今までどおり 4 つとも値で答える(R4 — 作る時の検めも変わらない)。
;; 表ごとの行の型の層 list-typed も同じ宣言を通る(TypedPage | Reset を返し、失敗は Raise で逃がす)。
(require doeff-hy.macros [deftest defk <- val on-raise])
(import dataclasses [dataclass])
(import types [NoneType])
(import pytest)
(import doeff [run with_handlers])
(import doeff [Err Ok UnhandledEffect])
(import doeff_hy._frozen [FrozenMap])
(import doeff_core_effects [result])
(import doeff_core_effects [scheduled])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records [ExpectAbsent ListCursor NotIndexed Page Reset Unreachable])
(import doeff_records [ListRows PutRow])
(import doeff_records [AdvanceStoreEpoch SetStoreOutage])
(import doeff_records._laws [LAW-SCHEMA MAKER])
(import doeff_records._typed [RowType TypedPage list-typed])
(import doeff_records [MemoryStore memory-records-handler])

(val DETAIL "記録の service が落ちている(筋書き)")


(defclass [(dataclass :frozen True)] Part []
  "表 parts の行の型(LAW-SCHEMA の欄の一部 — 知らない欄は読み捨てる)。"
  (#^ str id)
  (setv #^ (| str None) label None))

(val PARTS (RowType "parts" Part))


(defn #^ object in-store [#^ MemoryStore store program]  ; defk にできない: 検の入口で run を撃つ
  "仮想の時計と memory の置き場(書き手 MAKER)の下で program を回して答えを返す。"
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (memory-records-handler store MAKER)] program))))


(defk list-once [where cursor]
  {:pre [(: where FrozenMap) (: cursor (| ListCursor NoneType))] :post [(: % (| Page Reset))] :tags {:context "records" :role "program"}}
  "表 parts を 1 頁読み、答えを <- で束ねて返す(Unreachable と NotIndexed は束ねずに Raise として逃げる)。"
  (<- answer (ListRows "parts" :where where :cursor cursor))
  answer)


(defk list-once-as-value [where cursor]
  {:pre [(: where FrozenMap) (: cursor (| ListCursor NoneType))] :post [(: % (| Page Reset Unreachable NotIndexed))]
   :tags {:context "records" :role "program"}}
  "list-once の失敗を on-raise で Unreachable・NotIndexed の値へ戻して返す(値で受けたい呼び手の形)。"
  (<- answer (on-raise (list-once where cursor)
                       (Unreachable detail) (Unreachable detail)
                       (NotIndexed fields) (NotIndexed fields)))
  answer)


(defk list-typed-once [cursor]
  {:pre [(: cursor (| ListCursor NoneType))] :post [(: % (| TypedPage Reset))] :tags {:context "records" :role "program"}}
  "表 parts を行の型 Part で 1 頁読み、答えを <- で束ねて返す(list-typed の中で Unreachable と NotIndexed は Raise になる)。"
  (<- answer (list-typed PARTS :cursor cursor))
  answer)


(val NO-WHERE (FrozenMap))
(val NOT-INDEXED-WHERE (FrozenMap {"nofield" "x"}))


(deftest test-the-declaration-names-page-as-success-reset-as-a-value-and-unreachable-and-not-indexed-as-failures
  ;; 割り当ての表: 成功 = Page / 不在 = 無し / 失敗 = Unreachable・NotIndexed / 値 = Reset。
  (val outcomes ListRows.__doeff_outcomes__)
  (assert (= outcomes.success #(Page)) outcomes)
  (assert (= outcomes.absent #()) outcomes)
  (assert (= outcomes.failure #(Unreachable NotIndexed)) outcomes)
  (assert (= outcomes.value #(Reset)) outcomes))


(deftest test-a-page-is-bound-as-success-and-a-reset-as-a-value
  (val store (MemoryStore LAW-SCHEMA))
  ;; 行が無い: 空の Page が成功として束ねられる。
  (val empty (in-store store (list-once NO-WHERE None)))
  (assert (and (isinstance empty Page) (= empty.rows #())) empty)
  ;; 書いた後: 行が成功として束ねられる。
  (in-store store (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent)))
  (val listed (in-store store (list-once NO-WHERE None)))
  (assert (and (isinstance listed Page) (= (len listed.rows) 1)) listed)
  ;; 置き場の版が変わった後の古い位置: Reset は Raise にも Absent にもならず、値のまま束ねられる。
  (val stale (ListCursor listed.epoch "[\"p0\"]"))
  (val epoch (in-store store (AdvanceStoreEpoch)))
  (val reset (in-store store (list-once NO-WHERE stale)))
  (assert (isinstance reset Reset) reset)
  (assert (= reset.epoch epoch) reset)
  ;; result で畳んでも失敗にならない(Ok のまま)。
  (assert (isinstance (in-store store (result (list-once NO-WHERE stale))) Ok))
  ;; 一覧の頭から読み直せば続けられる。
  (val again (in-store store (list-once NO-WHERE None)))
  (assert (and (isinstance again Page) (= (len again.rows) 1)) again))


(deftest test-an-unreachable-list-escapes-as-a-raise
  (val store (MemoryStore LAW-SCHEMA))
  (in-store store (SetStoreOutage DETAIL))
  ;; 束ねずに逃がす: result で畳むと Err(Unreachable) — 値として束ねた名には Unreachable が来ない。
  (val settled (in-store store (result (list-once NO-WHERE None))))
  ;; Ok / Err は値で比べられない(Rust の型)ので形を repr で比べる。
  (assert (isinstance settled Err) settled)
  (assert (= (repr settled) (.format "Err({!r})" (Unreachable DETAIL))) settled)
  ;; 逃がしたままだと、受け手の無い Raise として止まる(黙って値にしない)。
  (with [(pytest.raises UnhandledEffect)]
    (in-store store (list-once NO-WHERE None))))


(deftest test-a-list-by-an-unindexed-field-escapes-as-a-raise
  (val store (MemoryStore LAW-SCHEMA))
  ;; 索引の無い欄で絞った読みの NotIndexed は失敗 — 読み直しても同じ答えになり続きが無い。
  (val settled (in-store store (result (list-once NOT-INDEXED-WHERE None))))
  (assert (isinstance settled Err) settled)
  (assert (= (repr settled) (.format "Err({!r})" (NotIndexed #("nofield")))) settled)
  (with [(pytest.raises UnhandledEffect)]
    (in-store store (list-once NOT-INDEXED-WHERE None))))


(deftest test-on-raise-brings-the-failures-back-to-the-value
  (val store (MemoryStore LAW-SCHEMA))
  (assert (= (in-store store (list-once-as-value NOT-INDEXED-WHERE None)) (NotIndexed #("nofield"))))
  (in-store store (SetStoreOutage DETAIL))
  (assert (= (in-store store (list-once-as-value NO-WHERE None)) (Unreachable DETAIL)))
  ;; 戻すと今までどおり — 値で受けた呼び手は、届く間は Page をそのまま受ける。
  (in-store store (SetStoreOutage None))
  (assert (isinstance (in-store store (list-once-as-value NO-WHERE None)) Page)))


(deftest test-the-handler-still-answers-the-failures-as-values
  ;; R4: 答える側は値で答えたまま — <- を通さない撃ち方(in-store の直の撃ち)では Raise にならず、Unreachable・NotIndexed の値が返る。
  (val store (MemoryStore LAW-SCHEMA))
  (assert (= (in-store store (ListRows "parts" :where NOT-INDEXED-WHERE)) (NotIndexed #("nofield"))))
  (in-store store (SetStoreOutage DETAIL))
  (assert (= (in-store store (ListRows "parts")) (Unreachable DETAIL))))


(deftest test-the-typed-layer-returns-the-typed-page-and-lets-the-failures-escape
  (val store (MemoryStore LAW-SCHEMA))
  (in-store store (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent)))
  (val page (in-store store (list-typed-once None)))
  (assert (and (isinstance page TypedPage) (= (lfor row page.rows row.value) [(Part "p1" "a")])) page)
  ;; 世代の違う位置の Reset は値のまま返る(行の型には写さない)。
  (val stale (ListCursor page.epoch "[\"p0\"]"))
  (in-store store (AdvanceStoreEpoch))
  (assert (isinstance (in-store store (list-typed-once stale)) Reset))
  ;; 届かない間は list-typed の中の <- が Raise にして上へ逃がす。
  (in-store store (SetStoreOutage DETAIL))
  (val settled (in-store store (result (list-typed-once None))))
  (assert (isinstance settled Err) settled)
  (assert (= (repr settled) (.format "Err({!r})" (Unreachable DETAIL))) settled))


(deftest test-a-list-is-still-refused-at-creation-for-a-bad-field
  ;; 作る時の検め(表・絞り・値の組・欄・位置・上限・向き)は defclass の時と同じ型(TypeError / ValueError)で断る。
  (for [#(error bad) [#(ValueError {"table" "Bad Name"})
                      #(ValueError {"limit" 0})
                      #(ValueError {"limit" True})
                      #(ValueError {"limit" 5001})
                      #(TypeError {"cursor" 1})
                      #(TypeError {"where_any" {"color" "red"}})
                      #(ValueError {"where_any" {"color" #()}})
                      #(ValueError {"where" {"color" "red"} "where_any" {"color" #("red")}})
                      #(ValueError {"ascending_by" "note" "descending_by" "note"})
                      #(ValueError {"descending_by" "note" "cursor" (ListCursor 1 "[\"p1\"]")})
                      #(ValueError {"cursor" (ListCursor 1 "[\"p1\"]" 30)})]]
    (with [(pytest.raises error)]
      (ListRows #** (| {"table" "parts"} bad))))
  ;; 境の値は通る(limit の上限ちょうど・向きを 1 つだけ)。凍らせた写像は dict を渡しても FrozenMap になる。
  (assert (= (. (ListRows "parts" :limit 5000) limit) 5000))
  (assert (isinstance (. (ListRows "parts" :where {"color" "red"}) where) FrozenMap))
  (assert (= (ListRows "parts") (ListRows "parts" :where {} :limit 500))))
