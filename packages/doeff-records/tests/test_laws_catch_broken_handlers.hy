;; 反例: 法は壊れた handler で赤になる — 法ごとに、その法だけを破る包みを memory の handler の内側に被せて回し、LawBroken を確かめる
;; (法が何も確かめずに緑になる形を外す)。
(require doeff-hy.macros [deftest defhandler defk <- val])
(import dataclasses)
(import datetime [datetime timezone])
(import uuid)
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [SimClock sim-time-handler GetTimeEffect])
(import doeff_records.values [EachEvent ExpectAny Changes Reset Written WrittenRows Conflict Refused RowsConflict RowsRefused
                              StreamEnd StreamEmpty Event EventAbsent EventRetired])
(import doeff_records.effects [PutRow PutRows WatchChanges ListRows AppendEvent ReadEvents ReadStreamEnd ReadEventByKey])
(import doeff_records.memory :as memory)
(import doeff_records.memory [MemoryStore memory-records-handler])
(import doeff_records.laws [LAW-SCHEMA LawHarness LawBroken law-stale-put-conflicts law-committed-changes-appear-once-in-order
                            law-epoch-change-resets law-undeclared-writes-are-refused law-transient-rows-expire
                            law-indexed-list-equals-filtered-scan law-append-is-idempotent law-none-removes-a-field
                            law-maintenance-prunes-and-sweeps law-put-rows-is-all-or-nothing
                            law-grouped-events-expire-together law-stream-end-is-the-last-sequence
                            law-expired-keys-are-remembered law-expired-records-are-unseen-before-a-sweep
                            law-a-write-clears-the-expired-row-it-touches law-an-expired-key-answers-the-same-before-and-after-a-sweep
                            law-watch-tails-match-last-events law-event-by-key-reads-the-same-event
                            law-short-page-ends-the-stream])
(import doeff_records.maintenance [PruneChanges Pruned])


(defhandler ignore-expectation []
  (PutRow [table key value expect]
    (<- answer (PutRow table key value (ExpectAny)))
    (resume answer)))

(defhandler repeat-changes []
  (WatchChanges [tables cursor timeout limit streams]
    (<- answer (WatchChanges tables cursor :timeout timeout :limit limit :streams streams))
    (resume (if (isinstance answer Changes) (Changes (+ answer.items answer.items) answer.cursor answer.tails) answer))))

(defhandler hide-reset []
  (WatchChanges [tables cursor timeout limit streams]
    (<- answer (WatchChanges tables cursor :timeout timeout :limit limit :streams streams))
    ;; 置き場の版を見ない handler の顔: Reset の代わりに「変更なし」を返す。
    (resume (if (isinstance answer Reset) (Changes #() cursor #()) answer))))

(defhandler drop-tails
  ;; 列の末尾を運ばない handler の顔(#3718): 名指した列の tails を捨て、名指さない答えと同じ形で返す。
  (WatchChanges [tables cursor timeout limit streams]
    (<- answer (WatchChanges tables cursor :timeout timeout :limit limit :streams streams))
    (resume (match answer
              (Changes :items items :cursor moved) (Changes items moved #())
              _ answer))))

(defhandler tails-in-name-order
  ;; 名指した順を守らない handler の顔(#3718): tails を列の名の順に並べ替えて返す(名指した順で照らす使い手が列を取り違える)。
  (WatchChanges [tables cursor timeout limit streams]
    (<- answer (WatchChanges tables cursor :timeout timeout :limit limit :streams streams))
    (resume (match answer
              (Changes :items items :cursor moved :tails tails) (Changes items moved (tuple (sorted tails :key (fn [tail] tail.stream))))
              _ answer))))

(defhandler frozen-clock []
  (GetTimeEffect []
    (resume (datetime 1970 1 1 :tzinfo timezone.utc))))

(defhandler ignore-where []
  (ListRows [table where fields cursor limit]
    (<- answer (ListRows table :fields fields :cursor cursor :limit limit))
    (resume answer)))

(defhandler ignore-removals []
  ;; 欄を消せない handler の顔: 差分の None の欄を捨てて書く。
  (PutRow [table key value expect]
    (<- answer (PutRow table key (dfor #(k v) (.items value) :if (is-not v None) k v) expect))
    (resume answer)))

(defhandler skip-pruning []
  ;; 刈り取りをしない handler の顔: 変更の列を消さず floor も上げないまま「刈った」と答える。
  (PruneChanges [keep-seconds]
    (resume (Pruned 0 0))))

(defk put-each-row [writes]
  {:pre [(: writes tuple)] :post [(: % (| WrittenRows RowsConflict RowsRefused))]}
  "束を 1 行ずつの PutRow で書く(1 transaction でない handler の顔)— 途中の行が通らなくても、前の行は書いたまま残る。"
  (val written [])
  (for [#(index write) (enumerate writes)]
    (.append written (! (PutRow write.table write.key write.value write.expect)))
    (match (get written -1)
      (Conflict :current current) (return (RowsConflict index write.table write.key current))
      (Written) None
      other (return (RowsRefused index write.table write.key other.reason))))
  (WrittenRows (tuple written)))

(defhandler put-rows-one-by-one []
  (PutRows [writes]
    (<- answer (put-each-row writes))
    (resume answer)))

(defhandler refuse-the-stranger [writer]
  ;; 書き手の名で断る handler の顔: 欄の書き手の宣言に無い stranger の PutRow を断る。
  ;; 引数に残す理由: 書き手の名は LawHarness の呼びごとに違い、置き場の effect の欄には無い。
  (PutRow [table key value expect]
    (if (= writer "stranger")
        (resume (Refused "書き手の名で断る置き場"))
        (do (<- answer (PutRow table key value expect))
            (resume answer)))))

(defhandler forget-idempotency []
  (AppendEvent [stream idempotency-key body]
    (<- answer (AppendEvent stream (. (uuid.uuid4) hex) body))
    (resume answer)))

(defk end-over-every-stream []
  {:pre [] :post [(: % (| StreamEnd StreamEmpty))]}
  "置き場の全部の列(journal・pairs)の末尾の最大 — 列を問わない handler の顔の答え。"
  (<- journal (ReadStreamEnd "journal"))
  (<- pairs (ReadStreamEnd "pairs"))
  (val ends (lfor end #(journal pairs) :if (isinstance end StreamEnd) end.sequence))
  (if ends (StreamEnd (max ends)) (StreamEmpty)))

(defhandler end-of-every-stream []
  ;; 列を問わない handler の顔: どの列の末尾にも、置き場の全部の列の最後の番号を答える。
  (ReadStreamEnd [stream]
    (<- answer (end-over-every-stream))
    (resume answer)))

(defk event-in-any-stream [idempotency-key]
  {:pre [(: idempotency-key str)] :post [(: % (| Event EventAbsent))]}
  "置き場の全部の列(journal・pulses・pairs)から鍵の出来事を探した最初の答え — 列を問わない鍵の読みの handler の顔の答え。"
  (for [stream #("journal" "pulses" "pairs")]
    (<- answer (ReadEventByKey stream idempotency-key))
    (when (isinstance answer Event)
      (return answer)))
  (EventAbsent))

(defhandler event-of-any-stream []
  ;; 列を問わない handler の顔(#3750): どの列の鍵の読みにも、置き場の全部の列からその鍵の出来事を探して答える。
  (ReadEventByKey [stream idempotency-key]
    (<- answer (event-in-any-stream idempotency-key))
    (resume answer)))

(defhandler retired-as-absent []
  ;; 消えた鍵と来ていない鍵を混ぜる handler の顔(#3750): EventRetired を EventAbsent で答える。
  (ReadEventByKey [stream idempotency-key]
    (<- answer (ReadEventByKey stream idempotency-key))
    (resume (if (isinstance answer EventRetired) (EventAbsent) answer))))


(defhandler cap-below-limit []
  ;; 1 頁を内部で上限より 1 つ少なく切る handler の顔(#3986): 残りの出来事を隠したまま、上限より短い頁を返す。
  (ReadEvents [stream after limit]
    (<- answer (ReadEvents stream :after after :limit (max 1 (- limit 1))))
    (resume answer)))


(defclass Forgetful [dict]
  "書いても覚えない dict — 保持の期限で消した冪等キーの覚え(MemoryStore.retired-keys)をこれにした置き場は、#3022 の前の置き場の形
   (出来事を消すと鍵も忘れ、消した後の同じ鍵が新しい出来事になる)の代役になる。"
  (defn __setitem__ [self key value]  ; defk にできない: dict の書きの口(置き場が錠の内で同期に呼ぶ)を塞ぐ
    None))


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
                      #(law-maintenance-prunes-and-sweeps (skip-pruning))
                      #(law-put-rows-is-all-or-nothing (put-rows-one-by-one))
                      #(law-stream-end-is-the-last-sequence (end-of-every-stream))
                      #(law-event-by-key-reads-the-same-event (event-of-any-stream))
                      #(law-event-by-key-reads-the-same-event (retired-as-absent))
                      #(law-watch-tails-match-last-events drop-tails)
                      #(law-watch-tails-match-last-events tails-in-name-order)
                      #(law-short-page-ends-the-stream (cap-below-limit))]]
    (assert (breaks? law (broken-harness (MemoryStore LAW-SCHEMA) inner)) law.__name__))
  ;; 書き手の名で断る置き場(欄の書き手でない stranger の書きを断る)— #2994 の前の置き場の形。
  (setv strict-store (MemoryStore LAW-SCHEMA))
  (assert (breaks? law-undeclared-writes-are-refused
                   (LawHarness (fn [writer program]
                                 (with_handlers [(memory-records-handler strict-store writer) (refuse-the-stranger writer)] program)))))
  ;; 時計の進まない handler(保持の期限が来ない)— memory の handler の GetTime だけを止め、法の Delay は仮想の時計が進める。
  (setv store (MemoryStore LAW-SCHEMA))
  (assert (breaks? law-transient-rows-expire
                   (LawHarness (fn [writer program] (with_handlers [(frozen-clock) (memory-records-handler store writer)] program)))))
  ;; 出来事ごとに数える置き場(保持の組を読まない)— 組の後の出来事が残るのに前の出来事が消える。
  (setv ungrouped (dataclasses.replace (get LAW-SCHEMA.streams "pairs") :retention-group (EachEvent)))
  (assert (breaks? law-grouped-events-expire-together
                   (broken-harness (MemoryStore (dataclasses.replace LAW-SCHEMA :streams (| (dict LAW-SCHEMA.streams) {"pairs" ungrouped})))
                                   None)))
  ;; 保持の期限で消した冪等キーを覚えない置き場(#3022 の前の形)— 消した後の同じ鍵の別の本文が新しい出来事になる。
  (setv forgetful (MemoryStore LAW-SCHEMA))
  (setv forgetful.retired-keys (Forgetful))
  (assert (breaks? law-expired-keys-are-remembered (broken-harness forgetful None)))
  (assert (not (breaks? law-expired-keys-are-remembered (broken-harness (MemoryStore LAW-SCHEMA) None))))
  ;; 同じ置き場の代役で、回収で消した鍵を鍵の読みが EventAbsent と答える(#3750)。
  (setv forgetful-keys (MemoryStore LAW-SCHEMA))
  (setv forgetful-keys.retired-keys (Forgetful))
  (assert (breaks? law-event-by-key-reads-the-same-event (broken-harness forgetful-keys None)))
  ;; 壊していない handler では同じ法が緑(反例の包みが無ければ通る — 比べの基準)。
  (assert (not (breaks? law-stale-put-conflicts (broken-harness (MemoryStore LAW-SCHEMA) None)))))


(defn test-the-unseen-law-turns-red-when-reads-stop-judging-expiry [monkeypatch]  ; defk にできない: pytest の fixture を受ける検
  ;; #3561: 読みの側の期限の判定を外した memory の置き場(読みの関数が使う判定 stored-row-expired? / stored-event-expired? を「期限を
  ;; 過ぎていない」に差し替えた源)では法 15 が赤になる。読みは回収しないので、回収の前の期限を過ぎた行と出来事を読みから隠すのは読みの判定
  ;; 1 か所だけ — 回収を読みの前に重ねて、判定を外しても破れない形にしない。行の判定と出来事の判定を 1 つずつ外し、どちらでも赤。
  (assert (not (breaks? law-expired-records-are-unseen-before-a-sweep (broken-harness (MemoryStore LAW-SCHEMA) None))))
  (with [patched (.context monkeypatch)]
    (.setattr patched memory "hyx_stored_row_expiredXquestion_markX" (fn [decl stored now-ms] False))
    (assert (breaks? law-expired-records-are-unseen-before-a-sweep (broken-harness (MemoryStore LAW-SCHEMA) None))
            "行の期限の判定を外した置き場"))
  (with [patched (.context monkeypatch)]
    (.setattr patched memory "hyx_stored_event_expiredXquestion_markX" (fn [store decl event now-ms] False))
    (assert (breaks? law-expired-records-are-unseen-before-a-sweep (broken-harness (MemoryStore LAW-SCHEMA) None))
            "出来事の期限の判定を外した置き場")))


(defn test-the-touched-write-laws-turn-red-when-writes-leave-expired-records-in-place [monkeypatch]  ; defk にできない: pytest の fixture を受ける検
  ;; #3605 の D: 書きの前の回収を外した memory の置き場で、書きが触る期限を過ぎた物を片付けない形は法 16・17 で赤になる — 行の書きが
  ;; 期限を過ぎた行をそのまま判定に渡す(writable-row を素の行の引き memory-current-row に差し替える)と、ExpectAbsent が Conflict になる。
  ;; 追記が期限を過ぎた出来事を片付けない(retire-touched-events を何もしない関数に差し替える)と、回収の前の断りの文が回収の後と違い、期限を
  ;; 過ぎた組の古い出来事が新しい鍵の追記で読みに戻る。片付けを外さない置き場では緑(比べの基準)。
  (assert (not (breaks? law-a-write-clears-the-expired-row-it-touches (broken-harness (MemoryStore LAW-SCHEMA) None))))
  (assert (not (breaks? law-an-expired-key-answers-the-same-before-and-after-a-sweep (broken-harness (MemoryStore LAW-SCHEMA) None))))
  (with [patched (.context monkeypatch)]
    (.setattr patched memory "writable_row" (fn [store decl key now-ms] (memory.memory-current-row store decl.name key)))
    (assert (breaks? law-a-write-clears-the-expired-row-it-touches (broken-harness (MemoryStore LAW-SCHEMA) None))
            "期限を過ぎた行を片付けない書き"))
  (with [patched (.context monkeypatch)]
    (.setattr patched memory "retire_touched_events" (fn [store decl idempotency-key now-ms] None))
    (assert (breaks? law-an-expired-key-answers-the-same-before-and-after-a-sweep (broken-harness (MemoryStore LAW-SCHEMA) None))
            "期限を過ぎた出来事を片付けない追記")))
