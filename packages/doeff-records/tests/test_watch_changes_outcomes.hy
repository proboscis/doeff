;; 表の変更の待ち WatchChanges の答えの分け方(ADR-DOE-CORE-EFFECTS-003 R5・R17 の追補 — card ki-9ef4c6a23811):
;; defeffect が Changes = 成功 / Reset = 値 / Unreachable = 失敗(不在は無し)と宣言し、呼び手の <- は
;;   - Changes を中身のまま束ねる
;;   - Reset を値のまま束ねる(Raise にも Absent にもしない — 呼び手が (epoch, floor) から読み直して続ける普通の答え)
;;   - Unreachable を Raise(Unreachable) として逃がす(on-raise で値へ戻せる。戻さなければ上へ出る)
;; 答える memory の handler は今までどおり 3 つとも値で答える(R4 — 作る時の検めも変わらない)。
(require doeff-hy.macros [deftest defk <- val on-raise])
(import pytest)
(import doeff [run with_handlers])
(import doeff [Err Ok UnhandledEffect])
(import doeff_hy._frozen [FrozenMap])
(import doeff_core_effects [result])
(import doeff_core_effects [scheduled])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records [Changes ExpectAbsent Page Reset Unreachable WatchCursor])
(import doeff_records [ListRows PutRow WatchChanges])
(import doeff_records [AdvanceStoreEpoch SetStoreOutage])
(import doeff_records._laws [LAW-SCHEMA MAKER])
(import doeff_records.memory [MemoryStore memory-records-handler])

(val DETAIL "記録の service が落ちている(筋書き)")


(defn #^ object in-store [#^ MemoryStore store program]  ; defk にできない: 検の入口で run を撃つ
  "仮想の時計と memory の置き場(書き手 MAKER)の下で program を回して答えを返す。"
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (memory-records-handler store MAKER)] program))))


(defk watch-once [cursor]
  {:pre [(: cursor WatchCursor)] :post [(: % (| Changes Reset))] :tags {:context "records" :role "program"}}
  "表 parts の変更を待たずに 1 回だけ見て、答えを <- で束ねて返す(Unreachable は束ねずに Raise として逃げる)。"
  (<- answer (WatchChanges #("parts") cursor :timeout 0.0))
  answer)


(defk watch-once-as-value [cursor]
  {:pre [(: cursor WatchCursor)] :post [(: % (| Changes Reset Unreachable))] :tags {:context "records" :role "program"}}
  "watch-once の失敗を on-raise で Unreachable の値へ戻して返す(値で受けたい呼び手の形)。"
  (<- answer (on-raise (watch-once cursor) (Unreachable detail) (Unreachable detail)))
  answer)


(defn #^ WatchCursor head-of [#^ MemoryStore store]  ; defk にできない: 検の支度が同期に読む置き場の今の位置
  "置き場の今の位置(表 parts の最初の頁の epoch と sequence)— 変更はこの位置より後から数える。"
  (setv page (in-store store (ListRows "parts")))
  (assert (isinstance page Page) page)
  (WatchCursor page.epoch page.sequence))


(deftest test-the-declaration-names-changes-as-success-reset-as-a-value-and-unreachable-as-a-failure
  ;; 割り当ての表: 成功 = Changes / 不在 = 無し / 失敗 = Unreachable / 値 = Reset。
  (val outcomes WatchChanges.__doeff_outcomes__)
  (assert (= outcomes.success #(Changes)) outcomes)
  (assert (= outcomes.absent #()) outcomes)
  (assert (= outcomes.failure #(Unreachable)) outcomes)
  (assert (= outcomes.value #(Reset)) outcomes))


(deftest test-changes-are-bound-as-success-and-a-reset-as-a-value
  (val store (MemoryStore LAW-SCHEMA))
  (val cursor (head-of store))
  ;; 変更が無い: 空の Changes が成功として束ねられる。
  (val quiet (in-store store (watch-once cursor)))
  (assert (and (isinstance quiet Changes) (= quiet.items #())) quiet)
  ;; 書いた後: 変更が成功として束ねられる。
  (in-store store (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent)))
  (val moved (in-store store (watch-once cursor)))
  (assert (and (isinstance moved Changes) (= (len moved.items) 1)) moved)
  ;; 置き場の版が変わった後の古い位置: Reset は Raise にも Absent にもならず、値のまま束ねられる。
  (val epoch (in-store store (AdvanceStoreEpoch)))
  (val reset (in-store store (watch-once cursor)))
  (assert (isinstance reset Reset) reset)
  (assert (= reset.epoch epoch) reset)
  ;; result で畳んでも失敗にならない(Ok のまま)。
  (assert (isinstance (in-store store (result (watch-once cursor))) Ok))
  ;; Reset の (epoch, floor) から読み直せば続けられる。
  (val resumed (in-store store (watch-once (WatchCursor reset.epoch reset.floor))))
  (assert (isinstance resumed Changes) resumed))


(deftest test-an-unreachable-watch-escapes-as-a-raise
  (val store (MemoryStore LAW-SCHEMA))
  (val cursor (head-of store))
  (in-store store (SetStoreOutage DETAIL))
  ;; 束ねずに逃がす: result で畳むと Err(Unreachable) — 値として束ねた名には Unreachable が来ない。
  (val settled (in-store store (result (watch-once cursor))))
  ;; Ok / Err は値で比べられない(Rust の型)ので形を repr で比べる。
  (assert (isinstance settled Err) settled)
  (assert (= (repr settled) (.format "Err({!r})" (Unreachable DETAIL))) settled)
  ;; 逃がしたままだと、受け手の無い Raise として止まる(黙って値にしない)。
  (with [(pytest.raises UnhandledEffect)]
    (in-store store (watch-once cursor))))


(deftest test-on-raise-brings-an-unreachable-watch-back-to-the-value
  (val store (MemoryStore LAW-SCHEMA))
  (val cursor (head-of store))
  (in-store store (SetStoreOutage DETAIL))
  (assert (= (in-store store (watch-once-as-value cursor)) (Unreachable DETAIL)))
  ;; 戻すと今までどおり — 値で受けた呼び手は、届く間は Changes をそのまま受ける。
  (in-store store (SetStoreOutage None))
  (val quiet (in-store store (watch-once-as-value cursor)))
  (assert (isinstance quiet Changes) quiet))


(deftest test-the-handler-still-answers-unreachable-as-a-value
  ;; R4: 答える側は値で答えたまま — <- を通さない撃ち方(in-store の直の撃ち)では Raise にならず、Unreachable の値が返る。
  (val store (MemoryStore LAW-SCHEMA))
  (val cursor (head-of store))
  (in-store store (SetStoreOutage DETAIL))
  (assert (= (in-store store (WatchChanges #("parts") cursor :timeout 0.0)) (Unreachable DETAIL)))
  (assert (= (in-store store (WatchChanges #("parts") cursor :timeout 30.0)) (Unreachable DETAIL))))


(deftest test-a-watch-is-still-refused-at-creation-for-a-bad-field
  ;; 作る時の検め(表・位置・待つ秒・上限・列)は defclass の時と同じ型(TypeError / ValueError)で断る。
  (val cursor (WatchCursor 1 0))
  (for [#(error bad) [#(TypeError {"tables" #()})
                      #(TypeError {"tables" ["parts"]})
                      #(ValueError {"tables" #("Bad Name")})
                      #(TypeError {"cursor" 1})
                      #(ValueError {"timeout" -1.0})
                      #(ValueError {"timeout" True})
                      #(ValueError {"timeout" "x"})
                      #(ValueError {"limit" 0})
                      #(ValueError {"limit" True})
                      #(TypeError {"streams" ["journal"]})
                      #(ValueError {"streams" #("Bad Name")})
                      #(ValueError {"streams" #("journal" "journal")})]]
    (with [(pytest.raises error)]
      (WatchChanges #** (| {"tables" #("parts") "cursor" cursor} bad))))
  ;; 境の値は通る(timeout = 0 は待たずに 1 回だけ見る・limit = 1 は最小)。
  (assert (= (WatchChanges #("parts") cursor :timeout 0 :limit 1) (WatchChanges #("parts") cursor :timeout 0.0 :limit 1))))
