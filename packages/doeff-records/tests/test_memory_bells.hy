;; 置き場の外の待ち手の口 hang-bell / drop-bell(#3028)の検。置き場の外の待ち手(模擬の世界の落ち着きの見張りなど)は、呼び鈴の名の形
;; (#("table" 表)・#("stream" 列))と錠に触らずに、表と列の名を渡して書きを待つ。名の形は置き場の中の 1 か所(_bell-names)だけが決める。
;;   掛けた表への書き・掛けた列への追記で鳴る(鳴った呼び鈴は書きが置き場から外す)
;;   掛けていない表への書き・掛けていない列への追記では鳴らない
;;   外した呼び鈴は置き場に残らず、後の書きも 2 度目の外しも効かずに通る
(require doeff-hy.macros [deftest defk <- val])
(import doeff [run with_handlers EffectBase])
(import doeff_core_effects.scheduler [scheduled HANDLE-SWEEP-INTERVAL _SchedulerIntrospection])
(import doeff_time [SimClock sim-time-handler])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.values [ExpectAbsent])
(import doeff_records.effects [PutRow AppendEvent])
(import doeff_records.memory [MemoryStore memory-records-handler hang-bell drop-bell])
(import doeff_records.laws [LAW-SCHEMA MAKER])


(defn #^ object in-store [#^ MemoryStore store program]  ; defk にできない: 検の入口で run を撃つ
  "仮想の時計と memory の置き場(書き手 MAKER)の下で program を回して答えを返すため。"
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (memory-records-handler store MAKER)] program))))


(defk rung-after [store tables streams write]
  {:pre [(: store MemoryStore) (: tables tuple) (: streams tuple) (: write EffectBase)] :post [(: % bool)]}
  "置き場 store に表 tables・列 streams の呼び鈴を掛け、書きの effect write を撃った後に鳴ったか(鳴った呼び鈴は書きが置き場から外す)を答えるため。
   鳴らなかった呼び鈴は外して終える。"
  (<- bell (hang-bell store tables streams))
  (<- write)
  (val rung (not-in bell store.bells))
  (<- (drop-bell store bell))
  rung)


(val PART-WRITE (PutRow "parts" #("p1") (FrozenMap {"id" "p1" "label" "a" "state" "open"}) (ExpectAbsent)))
(val TICKET-WRITE (PutRow "tickets" #("g" "t1") (FrozenMap {"group" "g" "id" "t1" "state" "open"}) (ExpectAbsent)))
(val JOURNAL-APPEND (AppendEvent "journal" "k1" {"n" 1}))


(deftest test-a-hung-bell-rings-on-a-write-to-its-table
  (val store (MemoryStore LAW-SCHEMA))
  (assert (in-store store (rung-after store #("parts") #() PART-WRITE)) "parts への書きで parts の呼び鈴が鳴らない"))


(deftest test-a-hung-bell-rings-on-an-append-to-its-stream
  (val store (MemoryStore LAW-SCHEMA))
  (assert (in-store store (rung-after store #() #("journal") JOURNAL-APPEND)) "journal への追記で journal の呼び鈴が鳴らない"))


(deftest test-a-hung-bell-stays-on-writes-it-does-not-wait-for
  ;; 反例: 掛けていない表への書き・掛けていない列への追記では鳴らない(表と列は同じ綴りでも混ざらない)。
  (val store (MemoryStore LAW-SCHEMA))
  (assert (not (in-store store (rung-after store #("parts") #() TICKET-WRITE))) "tickets への書きで parts の呼び鈴が鳴った")
  (assert (not (in-store store (rung-after store #("parts") #() JOURNAL-APPEND))) "journal への追記で parts の呼び鈴が鳴った")
  (assert (not (in-store store (rung-after store #() #("pairs") JOURNAL-APPEND))) "journal への追記で pairs の呼び鈴が鳴った")
  (assert (= store.bells {}) store.bells))


(defk dropped-then-written [store]
  {:pre [(: store MemoryStore)] :post [(: % tuple)]}
  "parts の呼び鈴を掛けて外し、後で parts に書き、もう 1 度外すため。答え = #(外した直後に残る呼び鈴の数 書いた後に残る数)。"
  (<- bell (hang-bell store #("parts") #()))
  (<- (drop-bell store bell))
  (val after-drop (len store.bells))
  (<- PART-WRITE)
  (<- (drop-bell store bell))
  #(after-drop (len store.bells)))


(deftest test-a-dropped-bell-is-gone-and-later-writes-and-drops-pass
  (val store (MemoryStore LAW-SCHEMA))
  (assert (= (in-store store (dropped-then-written store)) #(0 0))))


;; 反例(#3508・#3494): 鳴らずに外した呼び鈴の外の promise が終わらないと、scheduler の promise の行が pending のまま残り、終わった物
;; だけを消す掃除の外になる — 外すたびに 1 行ずつ増える。
(val UNRUNG-CYCLES (* 4 HANDLE-SWEEP-INTERVAL))


(defk unrung-hangs [store]
  {:pre [(: store MemoryStore)] :post [(: % dict)]}
  "parts の呼び鈴を掛けて鳴らさずに外すのを UNRUNG-CYCLES 回繰り返し、scheduler の行の数を答えるため。"
  (for [_ (range UNRUNG-CYCLES)]
    (<- bell (hang-bell store #("parts") #()))
    (<- (drop-bell store bell)))
  (<- counts (_SchedulerIntrospection))
  counts)


(deftest test-unrung-dropped-bells-do-not-grow-scheduler-promises
  (val store (MemoryStore LAW-SCHEMA))
  (val counts (in-store store (unrung-hangs store)))
  ;; 直す前は外した呼び鈴が全部 pending のまま残る(UNRUNG-CYCLES 行 = 掃除の間隔の 4 倍)。直した後は、掃除と掃除の間に溜まる分(1 回の掛けで割り当てを数個使う)だけ。
  (assert (<= (get counts "promises") (* 2 HANDLE-SWEEP-INTERVAL)) counts))
