;; 反例: 法は壊れた handler で赤になる — 法ごとに、その法だけを破る包みを memory の handler の内側に被せて回し、LawBroken を確かめる
;; (法が何も確かめずに緑になる形を外す)。
(require doeff-hy.macros [deftest defhandler <-])
(import dataclasses)
(import datetime [datetime timezone])
(import uuid)
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [SimClock sim-time-handler GetTimeEffect])
(import doeff_records.values [ExpectAny Changes Reset])
(import doeff_records.effects [PutRow WatchChanges ListRows AppendEvent])
(import doeff_records.memory [MemoryStore memory-records-handler])
(import doeff_records.laws [LAW-SCHEMA LawHarness LawBroken law-stale-put-conflicts law-committed-changes-appear-once-in-order
                            law-epoch-change-resets law-undeclared-writes-are-refused law-operator-paths-need-an-operator
                            law-transient-rows-expire
                            law-indexed-list-equals-filtered-scan law-append-is-idempotent law-none-removes-a-field
                            law-maintenance-prunes-and-sweeps])
(import doeff_records.maintenance [PruneChanges Pruned])


(defhandler ignore-expectation []
  (PutRow [table key value expect approval]
    (<- answer (PutRow table key value (ExpectAny) :approval approval))
    (resume answer)))

(defhandler repeat-changes []
  (WatchChanges [tables cursor timeout limit]
    (<- answer (WatchChanges tables cursor :timeout timeout :limit limit))
    (resume (if (isinstance answer Changes) (Changes (+ answer.items answer.items) answer.cursor) answer))))

(defhandler hide-reset []
  (WatchChanges [tables cursor timeout limit]
    (<- answer (WatchChanges tables cursor :timeout timeout :limit limit))
    ;; 置き場の版を見ない handler の顔: Reset の代わりに「変更なし」を返す。
    (resume (if (isinstance answer Reset) (Changes #() cursor) answer))))

(defhandler frozen-clock []
  (GetTimeEffect []
    (resume (datetime 1970 1 1 :tzinfo timezone.utc))))

(defhandler ignore-where []
  (ListRows [table where fields cursor limit]
    (<- answer (ListRows table :fields fields :cursor cursor :limit limit))
    (resume answer)))

(defhandler ignore-removals []
  ;; 欄を消せない handler の顔: 差分の None の欄を捨てて書く。
  (PutRow [table key value expect approval]
    (<- answer (PutRow table key (dfor #(k v) (.items value) :if (is-not v None) k v) expect :approval approval))
    (resume answer)))

(defhandler skip-pruning []
  ;; 刈り取りをしない handler の顔: 変更の列を消さず floor も上げないまま「刈った」と答える。
  (PruneChanges [keep-seconds]
    (resume (Pruned 0 0))))

(defhandler forget-idempotency []
  (AppendEvent [stream idempotency-key body]
    (<- answer (AppendEvent stream (. (uuid.uuid4) hex) body))
    (resume answer)))


(defn broken-harness [store inner [writer-of None]]
  "writer-of = 書き手の名の替え方(None = そのまま)。inner = memory の handler の内側に被せる包み(None = 無し)。"
  (LawHarness (fn [writer program]
                (with_handlers (+ [(memory-records-handler store (if writer-of (writer-of writer) writer))]
                                  (if inner [inner] []))
                               program))))


(defn breaks? [law harness [outer None]]
  (try
    (run (scheduled (with_handlers (+ [(sim-time-handler :clock (SimClock))] (if outer [outer] [])) (law harness))))
    False
    (except [LawBroken] True)))


(deftest test-each-law-turns-red-on-the-handler-that-breaks-it
  (for [#(law inner) [#(law-stale-put-conflicts (ignore-expectation))
                      #(law-committed-changes-appear-once-in-order (repeat-changes))
                      #(law-epoch-change-resets (hide-reset))
                      #(law-indexed-list-equals-filtered-scan (ignore-where))
                      #(law-append-is-idempotent (forget-idempotency))
                      #(law-none-removes-a-field (ignore-removals))
                      #(law-maintenance-prunes-and-sweeps (skip-pruning))]]
    (assert (breaks? law (broken-harness (MemoryStore LAW-SCHEMA) inner)) law.__name__))
  ;; 書き手を問わない handler(誰の書きも maker として通す)。
  (assert (breaks? law-undeclared-writes-are-refused (broken-harness (MemoryStore LAW-SCHEMA) None (fn [_] "maker"))))
  ;; operator の主体を問わない置き場: agent(maker)も operator の一覧に入れた宣言 = agent が operator の欄を書ける。
  (assert (breaks? law-operator-paths-need-an-operator
                   (broken-harness (MemoryStore (dataclasses.replace LAW-SCHEMA :operators #("overseer" "maker"))) None)))
  ;; 誰の書きも operator の主体として通す handler(身元を operator にすり替える)。
  (assert (breaks? law-operator-paths-need-an-operator (broken-harness (MemoryStore LAW-SCHEMA) None (fn [_] "overseer"))))
  ;; 時計の進まない handler(保持の期限が来ない)— memory の handler の GetTime だけを止め、法の Delay は仮想の時計が進める。
  (setv store (MemoryStore LAW-SCHEMA))
  (assert (breaks? law-transient-rows-expire
                   (LawHarness (fn [writer program] (with_handlers [(frozen-clock) (memory-records-handler store writer)] program)))))
  ;; 壊していない handler では同じ法が緑(反例の包みが無ければ通る — 比べの基準)。
  (assert (not (breaks? law-stale-put-conflicts (broken-harness (MemoryStore LAW-SCHEMA) None)))))
