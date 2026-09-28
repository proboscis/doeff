;;; sim の外の世界(SimOutside)の検 — service は外の系(業務の store の模擬)を effect を通してだけ共有し、柵は SimOutside の effects に
;;; 載った型だけを外へ通す(載っていなければ本番の子と同じ未処理で落ちる)。
(require doeff-hy.macros [deftest defk <- val])
(import doeff_time [Delay])
(import doeff_cluster.local [sim-cluster SimOutside SimWorker ProcessOutside ProcessesOf KillWorker])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.outside_programs [shared-store last-words memory-store signed-puts StorePut StoreGet])


(defk wait-seconds [seconds]
  {:pre [(: seconds float)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きが仮想の時計で待つ。"
  (<- (Delay seconds))
  None)


(deftest test-services-share-the-outside-store-only-through-effects
  (val rows {})
  (<- answer (sim-cluster (shared-store sim-foundation) (wait-seconds 30.0)
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
  (<- processes tuple (sim-cluster (shared-store sim-foundation) (crashed-processes "writer")))
  (assert processes)
  (assert (any (gfor p processes (and (is-not p.exit-code None) (!= p.exit-code 0) (in "StorePut" p.detail)))) processes))


(deftest test-per-process-outside-handlers-answer-before-the-shared-world-with-the-job-name
  ;; process ごとの外の handler の組(SimOutside.per-process — job の名と worker の名で作る)は、柵の外側・共有の外の世界の手前で
  ;; 答える: 書きは job の名つきの行になり、共有の store の "count" には届かない(読み手は数を見ないので "seen" も書かない)。
  (val rows {})
  (<- answer (sim-cluster (shared-store sim-foundation) (wait-seconds 30.0)
                          :outside (SimOutside :handlers [(memory-store rows)] :effects #(StorePut StoreGet)
                                               :per-process (fn [job worker] (ProcessOutside :handlers #((signed-puts rows job)))))))
  (assert (>= (.get rows "writer/count" 0) 10) rows)
  (assert (not-in "count" rows) rows)
  (assert (not-in "reader/seen" rows) rows))


(deftest test-an-effect-let-through-for-one-process-is-not-let-through-for-another
  ;; process ごとの柵の許し: 書き手の process にだけ StorePut・StoreGet を通す(共有の外の世界の型は空)。読み手の process は同じ
  ;; effect を出しても柵に止められ、本番の子と同じく未処理で落ちる — 別の job の外の口が sim で黙って答えない(構成のレビューの A)。
  (val rows {})
  (<- processes tuple (sim-cluster (shared-store sim-foundation) (crashed-processes "reader")
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
  ;; (finally の StorePut)は外の世界に届かない(dead-process-gate)。
  (val rows {})
  (<- processes tuple (sim-cluster (last-words sim-foundation) (kill-the-speaker "w1")
                                   :workers #((SimWorker :name "w1" :provides (frozenset ["cluster-net"])))
                                   :outside (SimOutside :handlers [(memory-store rows)] :effects #(StorePut StoreGet))))
  (assert (>= (.get rows "count" 0) 5) rows)
  (assert (not-in "last-words" rows) rows)
  (assert (any (gfor p processes (= p.exit-code -9))) processes))
