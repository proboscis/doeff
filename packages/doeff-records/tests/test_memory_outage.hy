;; 検の口 faults.SetStoreOutage(置き場に届かない状態を起こす・戻す)を memory の置き場が答える形の検。
;; 使い手(記録の service の不達・一部の表の断りを筋書きにする業務の検と模擬)は、業務の effect に答える偽の handler を書かず、
;; 正典の memory の置き場をこの口で「届かない」にする。
;;   届かない間: 名に当たる公開 effect 8 つは Unreachable(detail)・置き場は変わらない / 名に当たらない表と列は今までどおり
;;   戻した後: 届かない間に撃った書きは 1 つも残っていない・読み書きは今までどおり
;;   /readyz の問い(memory の置き場の選びの readiness): 置き場全体の止まりの間だけ届かない
(require doeff-hy.macros [deftest defk <- val])
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled Spawn Wait])
(import doeff_time [Delay GetTime])
(import doeff_time [SimClock sim-time-handler])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.values [ExpectAbsent Written Missing Unreachable Appended WatchCursor Changes EventsQuiet])
(import doeff_records.effects [ReadRow ListRows PutRow PutRows RowWrite WatchChanges WatchEvents AppendEvent ReadEvents ReadStreamEnd])
(import doeff_records.faults [SetStoreOutage])
(import doeff_records.memory [MemoryStore memory-records-handler memory-store-choice])
(import doeff_records.laws [LAW-SCHEMA MAKER])

(val DETAIL "記録の service が落ちている(筋書き)")


(defn #^ object in-store [#^ MemoryStore store program]  ; defk にできない: 検の入口で run を撃つ
  "仮想の時計と memory の置き場(書き手 MAKER)の下で program を回して答えを返す。"
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (memory-records-handler store MAKER)] program))))


(defk part [id]
  {:pre [(: id str)] :post [(: % FrozenMap)]}
  (FrozenMap {"id" id "label" "a" "state" "open"}))


(defk every-public-effect []
  {:pre [] :post [(: % tuple)]}
  "公開 effect 8 つを parts と journal へ 1 つずつ撃ち、答えを並べる。"
  (<- p1 FrozenMap (part "p1"))
  (<- p2 FrozenMap (part "p2"))
  (<- read (ReadRow "parts" #("p1")))
  (<- listed (ListRows "parts"))
  (<- put (PutRow "parts" #("p1") p1 (ExpectAbsent)))
  (<- rows (PutRows #((RowWrite "parts" #("p2") p2 (ExpectAbsent)))))
  (<- watched (WatchChanges #("parts") (WatchCursor 1 0)))
  (<- appended (AppendEvent "journal" "k1" {"n" 1}))
  (<- events (ReadEvents "journal" 0))
  (<- end (ReadStreamEnd "journal"))
  #(read listed put rows watched appended events end))


(deftest test-an-outage-answers-unreachable-for-every-public-effect-and-changes-nothing
  (val store (MemoryStore LAW-SCHEMA))
  (in-store store (SetStoreOutage DETAIL))
  (val answers (in-store store (every-public-effect)))
  (assert (= answers (tuple (gfor _ answers (Unreachable DETAIL)))) answers)
  ;; 列の待ち WatchEvents(公開 effect の外)も届かない間は Unreachable を待たずに答える。
  (assert (= (in-store store (WatchEvents "journal" :timeout 30.0)) (Unreachable DETAIL)))
  (assert (= store.head 0) "届かない間の書きが変更の列に積まれた")
  (assert (= store.events []) "届かない間の追記が列に積まれた")
  ;; 戻すと今までどおり — 届かない間の書きは残っていない。
  (in-store store (SetStoreOutage None))
  (assert (= (in-store store (ReadRow "parts" #("p1"))) (Missing)))
  (val put (in-store store (PutRow "parts" #("p1") (FrozenMap {"id" "p1" "label" "a" "state" "open"}) (ExpectAbsent))))
  (assert (isinstance put Written) put)
  (assert (isinstance (in-store store (AppendEvent "journal" "k1" {"n" 1})) Appended)))


(deftest test-an-outage-of-named-tables-leaves-the-other-tables-and-streams-reachable
  (val store (MemoryStore LAW-SCHEMA))
  (in-store store (SetStoreOutage DETAIL :names (frozenset #("parts"))))
  (assert (= (in-store store (ReadRow "parts" #("p1"))) (Unreachable DETAIL)))
  (assert (= (in-store store (ReadRow "tickets" #("g" "t1"))) (Missing)))
  (assert (isinstance (in-store store (AppendEvent "journal" "k1" {"n" 1})) Appended))
  ;; 束の中に 1 つでも届かない表があれば束ごと届かない(1 行も書かない)。
  (val mixed (in-store store (PutRows #((RowWrite "charters" #("c1") (FrozenMap {"name" "c1"}) (ExpectAbsent))
                                        (RowWrite "parts" #("p1") (FrozenMap {"id" "p1" "label" "a" "state" "open"}) (ExpectAbsent))))))
  (assert (= mixed (Unreachable DETAIL)) mixed)
  (assert (= (in-store store (ReadRow "charters" #("c1"))) (Missing))))


(defk outage-after [seconds names]
  {:pre [(: seconds float) (: names (| frozenset None))] :post [(: % None)]}
  "seconds 秒後に置き場を届かない状態にする筋書きの手(待ち手とは別の task — 検の口 SetStoreOutage を handler 越しに撃つ)。"
  (<- (Delay seconds))
  (<- (SetStoreOutage DETAIL :names names))
  None)


(defk waits-through-an-outage [names]
  {:pre [(: names (| frozenset None))] :post [(: % tuple)]}
  "parts の変更の待ちと journal の追記の待ちを上限 30 秒で並べ、2 秒後に届かない状態を置く。答え = #(表の待ちの答え 列の待ちの答え 起きた刻の秒)。"
  (<- start (GetTime))
  (<- table-wait (Spawn (WatchChanges #("parts") (WatchCursor 1 0) :timeout 30.0)))
  (<- event-wait (Spawn (WatchEvents "journal" :timeout 30.0)))
  (<- _outage (Spawn (outage-after 2.0 names)))
  (<- changes (Wait table-wait))
  (<- events (Wait event-wait))
  (<- now (GetTime))
  #(changes events (.total-seconds (- now start))))


(deftest test-an-outage-set-during-a-wait-wakes-the-waiters-with-unreachable
  ;; 待ちの最中に置いた届かない状態は、眠っている待ち手を鳴らし、起きた回の走査が Unreachable で返す(上限 30 秒まで眠らない)—
  ;; HTTP と PostgreSQL の口の待ちが読み直しの次の問いで不達を知るのと同じ(出自の issue は #1020)。
  (val store (MemoryStore LAW-SCHEMA))
  (val answers (in-store store (waits-through-an-outage None)))
  (assert (= (get answers 0) (Unreachable DETAIL)) answers)
  (assert (= (get answers 1) (Unreachable DETAIL)) answers)
  (assert (= (get answers 2) 2.0) answers))


(deftest test-an-outage-of-other-names-set-during-a-wait-leaves-the-waiters-waiting
  ;; 待つ名に当たらない届かない状態(表 tickets だけ)では、鳴らされた待ち手も走査して待ち続け、上限で静かな答えを返す。
  (val store (MemoryStore LAW-SCHEMA))
  (val answers (in-store store (waits-through-an-outage (frozenset #("tickets")))))
  (assert (and (isinstance (get answers 0) Changes) (= (. (get answers 0) items) #())) answers)
  (assert (isinstance (get answers 1) EventsQuiet) answers)
  (assert (= (get answers 2) 30.0) answers))


(deftest test-the-memory-choice-answers-the-readiness-from-the-outage
  ;; /readyz の問い(置き場の選び memory-store-choice の readiness — #3733): 置き場全体の止まり(名の無い SetStoreOutage — 記録の
  ;; service に届かない状態)を置いた間は届かない(False)、外せば届く(True)。名を限った止まり(一部の表の断り)は置き場そのものには
  ;; 届くので届く(PostgreSQL の置き場の問い SELECT 1 が表ごとの断りでは落ちないのと同じ)。
  (val store (MemoryStore LAW-SCHEMA))
  (val choice (run (memory-store-choice store)))
  (assert (is (in-store store (choice.readiness)) True))
  (in-store store (SetStoreOutage DETAIL))
  (assert (is (in-store store (choice.readiness)) False))
  (in-store store (SetStoreOutage None))
  (assert (is (in-store store (choice.readiness)) True))
  (in-store store (SetStoreOutage DETAIL :names (frozenset #("parts"))))
  (assert (is (in-store store (choice.readiness)) True)))
