;; 追記 AppendEvent の答えの分け方(ADR-DOE-CORE-EFFECTS-003 R5・R17 — card ki-c23e1bcfff1b):
;; defeffect が Appended = 成功 / Refused = 失敗 / Unreachable = 失敗(不在と値は無し)と宣言し、呼び手の <- は
;;   - Appended を中身のまま束ねる
;;   - Refused を Raise(Refused) として逃がす(同じ冪等キーの別の本文・本文の上限超え — on-raise で値へ戻せる)
;;   - Unreachable を Raise(Unreachable) として逃がす(置き場に届かない — on-raise で値へ戻せる)
;; 答える memory の handler は今までどおり 3 つとも値で答える(R4 — 作る時の検めと本文を深く凍らせる事も変わらない)。
(require doeff-hy.macros [deftest defk <- val on-raise])
(import pytest)
(import doeff [run with_handlers])
(import doeff [Err UnhandledEffect])
(import doeff_hy.frozen [FrozenMap])
(import doeff_core_effects.outcomes [result])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records.values [Appended Refused Unreachable])
(import doeff_records.effects [AppendEvent])
(import doeff_records.faults [SetStoreOutage])
(import doeff_records.laws [LAW-SCHEMA MAKER])
(import doeff_records.memory [MemoryStore memory-records-handler])

(val DETAIL "記録の service が落ちている(筋書き)")


(defn #^ object in-store [#^ MemoryStore store program]  ; defk にできない: 検の入口で run を撃つ
  "仮想の時計と memory の置き場(書き手 MAKER)の下で program を回して答えを返す。"
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (memory-records-handler store MAKER)] program))))


(defk append-once [key body]
  {:pre [(: key str) (: body dict)] :post [(: % Appended)] :tags {:context "records" :role "program"}}
  "列 journal へ追記 1 つを積み、答えを <- で束ねて返す(Refused・Unreachable は束ねずに Raise として逃げる)。"
  (<- answer (AppendEvent "journal" key body))
  answer)


(defk append-once-as-value [key body]
  {:pre [(: key str) (: body dict)] :post [(: % (| Appended Refused Unreachable))] :tags {:context "records" :role "program"}}
  "append-once の失敗を on-raise で Refused・Unreachable の値へ戻して返す(値で受けたい呼び手の形)。"
  (<- answer (on-raise (append-once key body)
                       (Refused reason) (Refused reason)
                       (Unreachable detail) (Unreachable detail)))
  answer)


(deftest test-an-append-is-bound-as-success-and-a-replay-answers-the-same-sequence
  (val store (MemoryStore LAW-SCHEMA))
  (val first (in-store store (append-once "k1" {"n" 1})))
  (assert (isinstance first Appended) first)
  ;; 同じ冪等キー・同じ本文の再送は前の番号 — 失敗にも値の分けにもならず、成功のまま束ねられる。
  (val again (in-store store (append-once "k1" {"n" 1})))
  (assert (= again.sequence first.sequence) again)
  (assert (= (len store.events) 1) store.events))


(deftest test-a-refused-append-escapes-as-a-raise
  (val store (MemoryStore LAW-SCHEMA))
  (in-store store (append-once "k1" {"n" 1}))
  ;; 同じ冪等キーの別の本文は断られる。束ねずに逃がす: result で畳むと Err(Refused) — 値として束ねた名には Refused が来ない。
  (val settled (in-store store (result (append-once "k1" {"n" 9}))))
  ;; Ok / Err は値で比べられない(Rust の型)ので、直の撃ちの答え(値)と repr で比べる。
  (val refused (in-store store (AppendEvent "journal" "k1" {"n" 9})))
  (assert (isinstance refused Refused) refused)
  (assert (isinstance settled Err) settled)
  (assert (= (repr settled) (.format "Err({!r})" refused)) settled)
  ;; 逃がしたままだと、受け手の無い Raise として止まる(黙って値にしない)。
  (with [(pytest.raises UnhandledEffect)]
    (in-store store (append-once "k1" {"n" 9})))
  (assert (= (len store.events) 1) "断られた追記が列に積まれた"))


(deftest test-an-unreachable-append-escapes-as-a-raise
  (val store (MemoryStore LAW-SCHEMA))
  (in-store store (SetStoreOutage DETAIL))
  (val settled (in-store store (result (append-once "k1" {"n" 1}))))
  (assert (isinstance settled Err) settled)
  (assert (= (repr settled) (.format "Err({!r})" (Unreachable DETAIL))) settled)
  (with [(pytest.raises UnhandledEffect)]
    (in-store store (append-once "k1" {"n" 1})))
  (assert (= store.events []) "届かない間の追記が列に積まれた"))


(deftest test-on-raise-brings-the-failures-back-to-the-values
  (val store (MemoryStore LAW-SCHEMA))
  (assert (isinstance (in-store store (append-once-as-value "k1" {"n" 1})) Appended))
  (assert (= (in-store store (append-once-as-value "k1" {"n" 9})) (in-store store (AppendEvent "journal" "k1" {"n" 9}))))
  (assert (isinstance (in-store store (append-once-as-value "k1" {"n" 9})) Refused))
  (in-store store (SetStoreOutage DETAIL))
  (assert (= (in-store store (append-once-as-value "k2" {"n" 2})) (Unreachable DETAIL)))
  ;; 戻すと今までどおり — 値で受けた呼び手は、届く間は Appended をそのまま受ける。
  (in-store store (SetStoreOutage None))
  (assert (isinstance (in-store store (append-once-as-value "k2" {"n" 2})) Appended)))


(deftest test-the-handler-still-answers-the-failures-as-values
  ;; R4: 答える側は値で答えたまま — <- を通さない撃ち方(in-store の直の撃ち)では Raise にならず、Refused・Unreachable の値が返る。
  (val store (MemoryStore LAW-SCHEMA))
  (assert (isinstance (in-store store (AppendEvent "journal" "k1" {"n" 1})) Appended))
  (assert (isinstance (in-store store (AppendEvent "journal" "k1" {"n" 9})) Refused))
  (in-store store (SetStoreOutage DETAIL))
  (assert (= (in-store store (AppendEvent "journal" "k2" {"n" 2})) (Unreachable DETAIL))))


(deftest test-an-append-is-still-checked-at-creation-and-freezes-its-body
  ;; 作る時の検め(列の名・冪等キー)は defclass の時と同じ ValueError で断る。
  (for [bad [{"stream" "Bad Name" "idempotency_key" "k" "body" {}} {"stream" "journal" "idempotency_key" "" "body" {}}
             {"stream" "journal" "idempotency_key" 3 "body" {}}]]
    (with [(pytest.raises ValueError)]
      (AppendEvent #** bad)))
  ;; 本文は作る時に深く凍らせる(object は FrozenMap・array は tuple)。
  (val append (AppendEvent "journal" "k" {"a" [1 {"b" 2}]}))
  (assert (isinstance append.body FrozenMap) append.body)
  (assert (isinstance (get append.body "a") tuple) append.body)
  (assert (= append (AppendEvent "journal" "k" {"a" [1 {"b" 2}]})))
  (assert (= (hash append) (hash (AppendEvent "journal" "k" {"a" [1 {"b" 2}]})))))
