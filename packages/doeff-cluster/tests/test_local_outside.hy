;;; sim の外の世界(SimOutside)の検 — service は外の系(業務の store の模擬)を effect を通してだけ共有し、柵は SimOutside の effects に
;;; 載った型だけを外へ通す(載っていなければ本番の子と同じ未処理で落ちる)。
(require doeff-hy.macros [deftest defk <- val])
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff [EffectBase])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_core_effects.scheduler [Spawn Task])
(import doeff_cluster.sim.local [sim-cluster SimOutside SimWorker ProcessOutside ProcessesOf KillWorker Crash KeepChild])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.outside_programs [shared-store last-words slow-last-words spawning-last-words memory-store signed-puts
                                         StorePut StoreGet])


(defk wait-seconds [seconds]
  {:pre [(: seconds float)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きが仮想の時計で待つ。"
  (<- (Delay seconds))
  None)


(deftest test-services-share-the-outside-store-only-through-effects
  (val rows {})
  (<- answer (sim-cluster :notice-broker (MemoryBroker) (shared-store sim-foundation) (wait-seconds 30.0)
                          :outside (SimOutside :handlers [(memory-store rows)] :effects #(StorePut StoreGet))))
  (assert (>= (.get rows "count" 0) 10) rows)
  (assert (>= (.get rows "seen" 0) 5) rows))


(defk crashed-processes [name]
  {:pre [(: name str)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "30 秒待ってから、job の process の終わりを読む。"
  (<- (Delay 30.0))
  (<- processes tuple (ProcessesOf name))
  processes)


(deftest test-without-the-outside-world-the-store-effect-is-unanswered
  (<- processes tuple (sim-cluster :notice-broker (MemoryBroker) (shared-store sim-foundation) (crashed-processes "writer")))
  (assert processes)
  (assert (any (gfor p processes (and (is-not p.exit-code None) (!= p.exit-code 0) (in "StorePut" p.detail)))) processes))


(deftest test-per-process-outside-handlers-answer-before-the-shared-world-with-the-job-name
  ;; process ごとの外の handler の組(SimOutside.per-process — job の名と worker の名で作る)は、柵の外側・共有の外の世界の手前で
  ;; 答える: 書きは job の名つきの行になり、共有の store の "count" には届かない(読み手は数を見ないので "seen" も書かない)。
  (val rows {})
  (<- answer (sim-cluster :notice-broker (MemoryBroker) (shared-store sim-foundation) (wait-seconds 30.0)
                          :outside (SimOutside :handlers [(memory-store rows)] :effects #(StorePut StoreGet)
                                               :per-process (fn [job worker] (ProcessOutside :handlers #((signed-puts rows job)))))))
  (assert (>= (.get rows "writer/count" 0) 10) rows)
  (assert (not-in "count" rows) rows)
  (assert (not-in "reader/seen" rows) rows))


(deftest test-an-effect-let-through-for-one-process-is-not-let-through-for-another
  ;; process ごとの柵の許し: 書き手の process にだけ StorePut・StoreGet を通す(共有の外の世界の型は空)。読み手の process は同じ
  ;; effect を出しても柵に止められ、本番の子と同じく未処理で落ちる — 別の job の外の口が sim で黙って答えない(構成のレビューの A)。
  (val rows {})
  (<- processes tuple (sim-cluster :notice-broker (MemoryBroker) (shared-store sim-foundation) (crashed-processes "reader")
                                   :outside (SimOutside :handlers [(memory-store rows)] :effects #()
                                                        :per-process (fn [job worker]
                                                                       (if (= job "writer")
                                                                           (ProcessOutside :handlers #() :effects #(StorePut StoreGet))
                                                                           (ProcessOutside :handlers #()))))))
  (assert (>= (.get rows "count" 0) 10) rows)
  (assert (any (gfor p processes (and (is-not p.exit-code None) (!= p.exit-code 0) (in "StoreGet" p.detail)))) processes))


(defk kill-the-speaker [worker]
  {:pre [(: worker str)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "10 秒待って worker を殺し、さらに 10 秒待って speaker の process を読む。"
  (<- (Delay 10.0))
  (<- (KillWorker worker))
  (<- (Delay 10.0))
  (<- processes tuple (ProcessesOf "speaker"))
  processes)


(deftest test-a-process-killed-with-its-worker-reaches-nothing-while-it-unwinds
  ;; 本物の機体の死では、落ちた process の後始末から何も届かない。sim でも、殺された process の取り消しの巻き戻しの中の effect
  ;; (finally の StorePut)は外の世界に届かない(殺された process は巻き戻さずに捨てる — Discard)。
  (val rows {})
  (<- processes tuple (sim-cluster :notice-broker (MemoryBroker) (last-words sim-foundation) (kill-the-speaker "w1")
                                   :workers #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :task-reserve 0))
                                   :outside (SimOutside :handlers [(memory-store rows)] :effects #(StorePut StoreGet))))
  (assert (>= (.get rows "count" 0) 5) rows)
  (assert (not-in "last-words" rows) rows)
  (assert (any (gfor p processes (= p.exit-code -9))) processes))


(defk end-lag-after [kill]
  {:pre [(: kill EffectBase)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "10 秒待って kill(KillWorker か Crash)を出した刻を控え、さらに 10 秒待ってから、殺された speaker の process の終わりの刻が殺した
   刻からどれだけ遅れて記録されたかを返す。"
  (<- (Delay 10.0))
  (<- killed-ms int (now-epoch-ms))
  (<- kill)
  (<- (Delay 10.0))
  (<- processes tuple (ProcessesOf "speaker"))
  (val ended (next (gfor p processes :if (is-not p.ended-ms None) p)))
  (- ended.ended-ms killed-ms))


(deftest test-a-process-killed-with-its-worker-ends-when-it-is-killed
  ;; 殺された process の終わりは殺した刻で記録する(本物の process は殺された時点で終わる — 巻き戻しの長さに依らない)。終わりを
  ;; 書く 3 つの道(EndProcess・Crash・KillWorker)が同じ記録を書く(#3057)。前の形では、KillWorker が書いた刻を巻き戻しの後の
  ;; EndProcess が 5 秒後で書き直していた。
  (val rows {})
  (<- lag int (sim-cluster :notice-broker (MemoryBroker) (slow-last-words sim-foundation) (end-lag-after (KillWorker "w1"))
                           :workers #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :task-reserve 0))
                           :outside (SimOutside :handlers [(memory-store rows)] :effects #(StorePut StoreGet))))
  (assert (= lag 0) lag)
  (assert (not-in "last-words" rows) rows))


(deftest test-a-crashed-process-ends-when-it-is-crashed
  ;; Crash も同じ: 前の形では、終わりを巻き戻しの後の EndProcess だけが 5 秒後で書いていた(Crash の後は worker が job を起こし
  ;; 直し、その process は筋書きの終わりの優雅な停止で最後の言葉を書くので、ここでは書きの有無を問わない)。
  (val rows {})
  (<- lag int (sim-cluster :notice-broker (MemoryBroker) (slow-last-words sim-foundation) (end-lag-after (Crash "speaker"))
                           :workers #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :task-reserve 0))
                           :outside (SimOutside :handlers [(memory-store rows)] :effects #(StorePut StoreGet))))
  (assert (= lag 0) lag))


(deftest test-the-tasks-a-killed-process-spawned-leave-nothing-either
  ;; 殺された process の中で Spawn した task も一緒に捨てる(本番は子 process ごと消える)— 子の task の後始末(finally の StorePut)も
  ;; 外の世界に届かない(#3057)。
  (val rows {})
  (<- processes tuple (sim-cluster :notice-broker (MemoryBroker) (spawning-last-words sim-foundation) (kill-the-speaker "w1")
                                   :workers #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :task-reserve 0))
                                   :outside (SimOutside :handlers [(memory-store rows)] :effects #(StorePut StoreGet))))
  (assert (>= (.get rows "count" 0) 5) rows)
  (assert (not-in "last-words" rows) rows)
  (assert (not-in "child-last-words" rows) rows)
  (assert (any (gfor p processes (= p.exit-code -9))) processes))


(defk late-words []
  {:pre [] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "待ち続け、取り消されると巻き戻しの中で書こうとする task の本体(登録の隙間に居た task の後始末が外へ届くかを測る)。"
  (try
    (while True
      (<- (Delay 1.0)))
    (finally
      (<- (StorePut "late-words" True)))))


(defk register-after-the-kill [worker]
  {:pre [(: worker str)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "10 秒待って worker を殺し、その後で、殺された speaker の process の task として、finally で書こうとする task を世界に覚えさせ、
   さらに 5 秒待つ(process の中で Spawn した task が先に走り、把手を覚える KeepChild の前に process が殺された隙間と同じ形)。"
  (<- (Delay 10.0))
  (<- (KillWorker worker))
  (<- processes tuple (ProcessesOf "speaker"))
  (<- late Task (Spawn (late-words)))
  (<- (KeepChild (. (get processes 0) pid) late))
  (<- (Delay 5.0))
  None)


(deftest test-a-task-registered-after-its-process-was-killed-is-dropped
  ;; 登録の隙間: 殺された process の task が後から世界に覚えられたら、その場で捨てる(取り消して巻き戻させない)— 後始末
  ;; (finally の StorePut)は外の世界に届かない(#3057 — 前は門が effect ごとに殺されたかを問うて断っていた)。
  (val rows {})
  (<- (sim-cluster :notice-broker (MemoryBroker) (last-words sim-foundation) (register-after-the-kill "w1")
                   :workers #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :task-reserve 0))
                   :outside (SimOutside :handlers [(memory-store rows)] :effects #(StorePut StoreGet))))
  (assert (not-in "late-words" rows) rows))
