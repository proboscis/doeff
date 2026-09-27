;; 検の口 faults.SetStoreOutage(置き場に届かない状態を起こす・戻す)を memory の置き場が答える形の検。
;; 使い手(記録の service の不達・一部の表の断りを筋書きにする業務の検と模擬)は、業務の effect に答える偽の handler を書かず、
;; 正典の memory の置き場をこの口で「届かない」にする(agora-redesign #783)。
;;   届かない間: 名に当たる公開 effect 7 つは Unreachable(detail)・置き場は変わらない / 名に当たらない表と列は今までどおり
;;   戻した後: 届かない間に撃った書きは 1 つも残っていない・読み書きは今までどおり
(require doeff-hy.macros [deftest defk <- val])
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [SimClock sim-time-handler])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.values [ExpectAbsent Written Missing Unreachable Appended WatchCursor])
(import doeff_records.effects [ReadRow ListRows PutRow PutRows RowWrite WatchChanges AppendEvent ReadEvents])
(import doeff_records.faults [SetStoreOutage])
(import doeff_records.memory [MemoryStore memory-records-handler])
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
  "公開 effect 7 つを parts と journal へ 1 つずつ撃ち、答えを並べる。"
  (<- p1 FrozenMap (part "p1"))
  (<- p2 FrozenMap (part "p2"))
  (<- read (ReadRow "parts" #("p1")))
  (<- listed (ListRows "parts"))
  (<- put (PutRow "parts" #("p1") p1 (ExpectAbsent)))
  (<- rows (PutRows #((RowWrite "parts" #("p2") p2 (ExpectAbsent)))))
  (<- watched (WatchChanges #("parts") (WatchCursor 1 0)))
  (<- appended (AppendEvent "journal" "k1" {"n" 1}))
  (<- events (ReadEvents "journal" 0))
  #(read listed put rows watched appended events))


(deftest test-an-outage-answers-unreachable-for-every-public-effect-and-changes-nothing
  (val store (MemoryStore LAW-SCHEMA))
  (in-store store (SetStoreOutage DETAIL))
  (val answers (in-store store (every-public-effect)))
  (assert (= answers (tuple (gfor _ answers (Unreachable DETAIL)))) answers)
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
