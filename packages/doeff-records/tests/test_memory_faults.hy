;; 検の口 faults.AddStoreFault / ClearStoreFaults(置き場の故障を置く・外す)を memory の置き場が答える形の検。
;; 使い手(列への投函の断り・答えの切れ・照会だけの不達を筋書きにする業務の検と模擬)は、業務の effect に答える偽の handler を書かず、
;; 正典の memory の置き場にこの口で断らせる。
;;   当たる: 名(表・列)と操作(読み = ReadRow・ListRows・WatchChanges・ReadEvents / 書き = PutRow・PutRows・AppendEvent)と絞り(matching)
;;   lands = False: 置き場を変えずに故障の答え / lands = True: 書きを置き場に着けてから故障の答え
;;   PutRows: 束の中に当たる表が 1 つでもあれば束ごと(lands なら束ごと着く)
;;   外す: 名が重なる故障だけ / None で全部。置き場全体の不達(SetStoreOutage)が故障より先に答える
(require doeff-hy.macros [deftest defk <- val])
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [SimClock sim-time-handler])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.values [ExpectAbsent Written WrittenRows Missing Refused Unreachable Appended Changes Page Events WatchCursor])
(import doeff_records.effects [ReadRow ListRows PutRow PutRows RowWrite WatchChanges AppendEvent ReadEvents])
(import doeff_records.faults [SetStoreOutage StoreFault StoreOperation AddStoreFault ClearStoreFaults])
(import doeff_records.memory [MemoryStore memory-records-handler])
(import doeff_records.laws [LAW-SCHEMA MAKER])

(val REFUSED (Refused "断る(筋書き)"))
(val DOWN (Unreachable "届かない(筋書き)"))


(defn #^ object in-store [#^ MemoryStore store program]  ; defk にできない: 検の入口で run を撃つ
  "仮想の時計と memory の置き場(書き手 MAKER)の下で program を回して答えを返す。"
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (memory-records-handler store MAKER)] program))))


(defn #^ FrozenMap part [#^ str id]  ; defk にできない: 検の支度が同期に組む行の値
  (FrozenMap {"id" id "label" "a" "state" "open"}))


(defk reads []
  {:pre [] :post [(: % tuple)]}
  "読みの 4 つ(parts の ReadRow・ListRows・WatchChanges と journal の ReadEvents)の答え。"
  (<- read (ReadRow "parts" #("p1")))
  (<- listed (ListRows "parts"))
  (<- watched (WatchChanges #("parts") (WatchCursor 1 0) :timeout 0.0))
  (<- events (ReadEvents "journal" 0))
  #(read listed watched events))


(defk writes []
  {:pre [] :post [(: % tuple)]}
  "書きの 3 つ(parts の PutRow・PutRows と journal の AppendEvent)の答え。"
  (<- put (PutRow "parts" #("p1") (part "p1") (ExpectAbsent)))
  (<- rows (PutRows #((RowWrite "parts" #("p2") (part "p2") (ExpectAbsent)))))
  (<- appended (AppendEvent "journal" "k1" {"n" 1}))
  #(put rows appended))


(defn #^ StoreFault fault-on [#^ StoreOperation operation answer * [lands False] [matching None]]  ; defk にできない: 検の支度が同期に組む値
  "parts と journal に当たる故障 1 つ。"
  (StoreFault (frozenset #("parts" "journal")) operation answer :lands lands :matching matching))


(deftest test-a-read-fault-answers-every-read-and-leaves-the-writes-alone
  (val store (MemoryStore LAW-SCHEMA))
  (in-store store (AddStoreFault (fault-on StoreOperation.READ DOWN)))
  (assert (= (in-store store (reads)) #(DOWN DOWN DOWN DOWN)))
  ;; 書きは当たらない — 置き場に着く。
  (val written (in-store store (writes)))
  (assert (isinstance (get written 0) Written) written)
  (assert (isinstance (get written 1) WrittenRows) written)
  (assert (isinstance (get written 2) Appended) written)
  (assert (= store.head 2) store.head)
  (assert (= (len store.events) 1) store.events))


(deftest test-a-write-fault-that-does-not-land-answers-every-write-and-changes-nothing
  (val store (MemoryStore LAW-SCHEMA))
  (in-store store (AddStoreFault (fault-on StoreOperation.WRITE REFUSED)))
  (assert (= (in-store store (writes)) #(REFUSED REFUSED REFUSED)))
  (assert (= store.head 0) "当たった書きが変更の列に積まれた")
  (assert (= store.events []) "当たった追記が列に積まれた")
  ;; 読みは当たらない。
  (val answers (in-store store (reads)))
  (assert (= (get answers 0) (Missing)) answers)
  (assert (= (lfor answer (cut answers 1 None) (type answer)) [Page Changes Events]) answers))


(deftest test-a-write-fault-that-lands-writes-and-then-answers-the-fault
  (val store (MemoryStore LAW-SCHEMA))
  (in-store store (AddStoreFault (fault-on StoreOperation.WRITE DOWN :lands True)))
  (assert (= (in-store store (writes)) #(DOWN DOWN DOWN)))
  ;; 答えは切れたが書きは着いている。
  (in-store store (ClearStoreFaults))
  (assert (= (. (in-store store (ReadRow "parts" #("p1"))) value) (part "p1")))
  (assert (= (. (in-store store (ReadRow "parts" #("p2"))) value) (part "p2")))
  (assert (= (lfor event store.events event.idempotency-key) ["k1"]) store.events))


(deftest test-matching-narrows-a-fault-to-the-effects-it-names
  (val store (MemoryStore LAW-SCHEMA))
  (in-store store (AddStoreFault (StoreFault (frozenset #("journal")) StoreOperation.WRITE REFUSED
                                             :matching (fn [ask] (= (.get ask.body "name") "blocked")))))
  (assert (= (in-store store (AppendEvent "journal" "k1" {"name" "blocked"})) REFUSED))
  (assert (isinstance (in-store store (AppendEvent "journal" "k2" {"name" "open"})) Appended))
  (assert (= (lfor event store.events event.idempotency-key) ["k2"]) store.events))


(deftest test-put-rows-is-faulted-as-a-whole-when-one-table-in-the-bundle-is-named
  (val store (MemoryStore LAW-SCHEMA))
  (val bundle (PutRows #((RowWrite "charters" #("c1") (FrozenMap {"name" "c1"}) (ExpectAbsent))
                         (RowWrite "parts" #("p1") (part "p1") (ExpectAbsent)))))
  (in-store store (AddStoreFault (StoreFault (frozenset #("parts")) StoreOperation.WRITE REFUSED)))
  (assert (= (in-store store bundle) REFUSED))
  (assert (= (in-store store (ReadRow "charters" #("c1"))) (Missing)) "束の 1 行が書かれた")
  ;; lands なら束ごと着いてから故障の答え。
  (in-store store (ClearStoreFaults (frozenset #("parts"))))
  (in-store store (AddStoreFault (StoreFault (frozenset #("parts")) StoreOperation.WRITE DOWN :lands True)))
  (assert (= (in-store store bundle) DOWN))
  (assert (= store.head 2) "束の 2 行が着いていない"))


(deftest test-clear-removes-the-faults-by-name-or-all-of-them
  (val store (MemoryStore LAW-SCHEMA))
  (in-store store (AddStoreFault (StoreFault (frozenset #("parts")) StoreOperation.READ DOWN)))
  (in-store store (AddStoreFault (StoreFault (frozenset #("journal")) StoreOperation.READ DOWN)))
  ;; 名の重なる故障だけを外す。
  (in-store store (ClearStoreFaults (frozenset #("parts"))))
  (assert (= (in-store store (ReadRow "parts" #("p1"))) (Missing)))
  (assert (= (in-store store (ReadEvents "journal" 0)) DOWN))
  ;; None で全部。
  (in-store store (ClearStoreFaults None))
  (assert (isinstance (in-store store (ReadEvents "journal" 0)) Events))
  (assert (= store.faults #()) store.faults))


(deftest test-the-first-placed-fault-answers-and-the-outage-answers-before-any-fault
  (val store (MemoryStore LAW-SCHEMA))
  (in-store store (AddStoreFault (StoreFault (frozenset #("parts")) StoreOperation.READ REFUSED)))
  (in-store store (AddStoreFault (StoreFault (frozenset #("parts")) StoreOperation.READ DOWN)))
  (assert (= (in-store store (ReadRow "parts" #("p1"))) REFUSED))
  (in-store store (SetStoreOutage "置き場ごと落ちた"))
  (assert (= (in-store store (ReadRow "parts" #("p1"))) (Unreachable "置き場ごと落ちた")))
  (in-store store (SetStoreOutage None))
  (assert (= (in-store store (ReadRow "parts" #("p1"))) REFUSED)))


(deftest test-a-fault-refuses-malformed-values-at-construction
  (for [build [(fn [] (StoreFault "parts" StoreOperation.READ DOWN))
               (fn [] (StoreFault (frozenset #("parts")) "read" DOWN))
               (fn [] (StoreFault (frozenset #("parts")) StoreOperation.READ "down"))
               (fn [] (StoreFault (frozenset #("parts")) StoreOperation.READ DOWN :lands 1))
               (fn [] (StoreFault (frozenset #("parts")) StoreOperation.READ DOWN :matching "name"))]]
    (assert (try (build) False (except [TypeError] True)) build)))
