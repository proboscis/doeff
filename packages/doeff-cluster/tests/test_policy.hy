(require doeff-hy.macros [deftest defk val var <-])

(import dataclasses [replace])
(import doeff_cluster.worker.intent.worker_model [CodeState CodeView ProcessView WorldView StopStage JobStop
  Outcome JobRecord WorkerPolicy PrepareCode StartJob SignalJob ReapJob SpecChanged Undeclared Retired CutOff WorkerStopping] doeff_cluster.shared.intent.job_model [JobSpec JobPhase])
(import doeff_cluster.worker.core.policy [plan ready-followups records-after statuses])

(setv POLICY (WorkerPolicy :stop-grace-ms 1000 :kill-grace-ms 500 :restart-backoff-ms 2000)
      A1 (JobSpec "a" "jobs.a" #() "rev1")
      A2 (replace A1 :revision "rev2")
      B1 (JobSpec "b" "jobs.b" #("x") "rev1")
      READY1 (CodeView "rev1" CodeState.READY "/c/rev1")
      READY2 (CodeView "rev2" CodeState.READY "/c/rev2"))

(defk world [#* processes [codes #(READY1)]]
  {:pre [(: processes tuple) (: codes tuple)] :post [(: % WorldView)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "観測した世界(コードの展開 codes と子 process の列)を、plan に渡す形で組むため。"
  (WorldView codes processes))

(defk running [spec [pid 10]]
  {:pre [(: spec JobSpec) (: pid int)] :post [(: % ProcessView)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "spec の job が pid で走っている子 process の観測を組むため(1 回目の起動・時刻 0)。"
  (ProcessView spec.name spec 1 pid 0))

(deftest test-start-waits-for-code
  (assert (= (! (plan 0 #(A1) (! (world :codes #())) {} POLICY)) #((PrepareCode "rev1"))))
  (assert (= (! (plan 0 #(A1) (! (world :codes #((CodeView "rev1" CodeState.PREPARING)))) {} POLICY)) #()))
  (assert (= (! (plan 0 #(A1) (! (world)) {} POLICY)) #((StartJob A1 1 "/c/rev1"))))
  ;; 展開に失敗した版は起動しない。状態表示に理由を出す。
  (setv failed (! (world :codes #((CodeView "rev1" CodeState.FAILED :detail "no such commit")))))
  (assert (= (! (plan 0 #(A1) failed {} POLICY)) #()))
  (assert (= (. (get (! (statuses 0 #(A1) failed {} POLICY)) 0) phase) JobPhase.CODE-FAILED)))

(deftest test-a-tree-ready-within-the-tick-starts-the-job-in-that-tick
  ;; 拍の頭で木が無く、準備の action の後の観測で揃った job は、その拍のうちに起こす(#2719)。揃っていなければ何もしない
  ;; (今までどおり後の拍で揃いを観測してから起こす)・拍の頭で既に揃っていた木は plan が扱ったので重ねない。
  (val cold (! (world :codes #())))
  (val preparing (! (world :codes #((CodeView "rev1" CodeState.PREPARING)))))
  (<- started tuple (ready-followups 0 #(A1) cold (! (world)) {} POLICY))
  (assert (= started #((StartJob A1 1 "/c/rev1"))))
  (<- waiting tuple (ready-followups 0 #(A1) cold preparing {} POLICY))
  (assert (= waiting #()))
  (<- already tuple (ready-followups 0 #(A1) (! (world)) (! (world)) {} POLICY))
  (assert (= already #())))

(deftest test-failed-code-is-prepared-again-after-the-retry-wait
  (setv policy (replace POLICY :code-retry-ms 30000)
        failed (! (world :codes #((CodeView "rev1" CodeState.FAILED :detail "準備に失敗" :failed-ms 1000)))))
  (assert (= (! (plan 30999 #(A1) failed {} policy)) #()))
  (setv #(status) (! (statuses 30999 #(A1) failed {} policy)))
  (assert (= status.phase JobPhase.CODE-FAILED))
  (assert (= status.detail "準備に失敗"))
  (assert (= (! (plan 31000 #(A1) failed {} policy)) #((PrepareCode "rev1")))))

(deftest test-jobs-are-independent
  ;; b だけ版を変えても a の process には何もしない。
  (setv w (! (world (! (running A1 10)) (! (running B1 11)))))
  (setv b2 (replace B1 :revision "rev2"))
  (assert (= (! (plan 0 #(A1 b2) w {} POLICY)) #((SignalJob "b" 11 StopStage.TERM (SpecChanged))))))

(deftest test-update-stops-old-before-starting-new
  (setv w (! (world (! (running A1)) :codes #(READY1 READY2))))
  (setv actions (! (plan 0 #(A2) w {} POLICY)))
  (assert (= actions #((SignalJob "a" 10 StopStage.TERM (SpecChanged)))))
  (var records (! (records-after 0 {"a" (JobRecord "a" :attempts 1)} actions)))
  ;; 猶予の間は待つ。新版は起動しない。
  (assert (= (! (plan 999 #(A2) w records POLICY)) #()))
  ;; 猶予切れで KILL。
  (setv kill (! (plan 1000 #(A2) w records POLICY)))
  (assert (= kill #((SignalJob "a" 10 StopStage.KILL (SpecChanged)))))
  (:= records (! (records-after 1000 records kill)))
  (assert (= (. (get records "a") stopping) (JobStop :requested-ms 0 :stage StopStage.KILL :signalled-ms 1000 :reason (SpecChanged))))
  ;; KILL の後も終了を観測できない → 置き換えを起動しない・停止未確認と表示する。
  (assert (= (! (plan 5000 #(A2) w records POLICY)) #()))
  (assert (= (. (get (! (statuses 5000 #(A2) w records POLICY)) 0) phase) JobPhase.STOP-UNCONFIRMED))
  ;; 終了を観測したら回収し、次の拍で新版を起動する。
  (setv exited (! (world (replace (! (running A1)) :exit-code -9) :codes #(READY1 READY2))))
  (setv reap (! (plan 5001 #(A2) exited records POLICY)))
  (assert (= reap #((ReapJob "a" 10 Outcome.STOPPED -9))))
  (:= records (! (records-after 5001 records reap)))
  (assert (= (! (plan 5002 #(A2) (! (world :codes #(READY1 READY2))) records POLICY))
             #((StartJob A2 2 "/c/rev2")))))

(deftest test-removed-job-is-stopped-not-forgotten
  (setv w (! (world (! (running A1)))))
  (assert (= (! (plan 0 #() w {} POLICY)) #((SignalJob "a" 10 StopStage.TERM (Undeclared)))))
  (assert (= (! (statuses 0 #() (! (world)) {} POLICY)) #())))

(deftest test-crash-restarts-after-backoff
  (setv crashed (! (world (replace (! (running A1)) :exit-code 3))))
  (setv reap (! (plan 0 #(A1) crashed {} POLICY)))
  (assert (= reap #((ReapJob "a" 10 Outcome.EXITED 3))))
  (setv records (! (records-after 0 {"a" (JobRecord "a" :attempts 1)} reap)))
  (assert (= (! (plan 1999 #(A1) (! (world)) records POLICY)) #()))
  (assert (= (. (get (! (statuses 1999 #(A1) (! (world)) records POLICY)) 0) phase) JobPhase.BACKOFF))
  (assert (= (! (plan 2000 #(A1) (! (world)) records POLICY)) #((StartJob A1 2 "/c/rev1")))))

(deftest test-requested-stop-exit-is-not-restarted-with-backoff
  ;; 停止を求めて終わった job は、宣言が残っていれば backoff なしで次版を起動する。
  (var records {"a" (JobRecord "a" :attempts 1 :stopping (JobStop :requested-ms 0 :stage StopStage.TERM :signalled-ms 0 :reason (SpecChanged)))})
  (setv reap (! (plan 10 #(A2) (! (world (replace (! (running A1)) :exit-code 0) :codes #(READY1 READY2))) records POLICY)))
  (:= records (! (records-after 10 records reap)))
  (assert (= (. (get records "a") last-outcome) Outcome.STOPPED))
  (assert (= (! (plan 11 #(A2) (! (world :codes #(READY1 READY2))) records POLICY)) #((StartJob A2 2 "/c/rev2")))))

(deftest test-task-runs-once-and-is-reported-finished
  (setv task (JobSpec "task/t1" "doeff_cluster.worker.entry.job_entry" #("task") "rev1" :once True))
  (assert (= (! (plan 0 #(task) (! (world)) {} POLICY)) #((StartJob task 1 "/c/rev1"))))
  ;; 終わった後は宣言に残っていても起動し直さない(worker の障害でも走らせ直さないのと同じ)
  (setv done {"task/t1" (JobRecord "task/t1" 1 100 Outcome.EXITED 0)})
  (assert (= (! (plan 5000 #(task) (! (world)) done POLICY)) #()))
  (assert (= (. (get (! (statuses 5000 #(task) (! (world)) done POLICY)) 0) phase) JobPhase.FINISHED)))


(deftest test-service-that-keeps-crashing-is-restarted-with-growing-backoff
  ;; Service は宣言がある限り起こし続ける。続けて落ちるたびに間を倍にし(2・4・8 秒…・上限 60 秒)、
  ;; 長く動いた後に落ちたら 1 回目として数え直す(k8s の CrashLoopBackOff と同じ形)。
  (setv policy (replace POLICY :restart-backoff-max-ms 8000 :stable-run-ms 60000))
  (var records {})
  (var now 0)
  (setv starts [])
  (for [round (range 5)]
    ;; 起動できる最初の時刻まで 1 ms ずつではなく、backoff の境目の前後だけを確かめる
    (setv start (! (plan now #(A1) (! (world)) records policy)))
    (assert (= (len start) 1))
    (.append starts now)
    (:= records (! (records-after now records start policy)))
    ;; 1 秒で落ちる
    (:= now (+ now 1000))
    (setv reap (! (plan now #(A1) (! (world (replace (! (running A1)) :exit-code 1))) records policy)))
    (:= records (! (records-after now records reap policy)))
    (setv wait (get [2000 4000 8000 8000 8000] round))
    (assert (= (! (plan (+ now wait -1) #(A1) (! (world)) records policy)) #()))
    (assert (= (. (get (! (statuses (+ now wait -1) #(A1) (! (world)) records policy)) 0) phase) JobPhase.BACKOFF))
    (:= now (+ now wait)))
  (assert (= (. (get records "a") failures) 5))
  (assert (= (. (get records "a") attempts) 5))
  ;; 長く(stable-run-ms 以上)動いてから落ちたら 1 回目に戻る
  (setv stable-start (! (plan now #(A1) (! (world)) records policy)))
  (:= records (! (records-after now records stable-start policy)))
  (:= now (+ now 60000))
  (:= records (! (records-after now records (! (plan now #(A1) (! (world (replace (! (running A1)) :exit-code 1))) records policy)) policy)))
  (assert (= (. (get records "a") failures) 1))
  (assert (= (! (plan (+ now 2000) #(A1) (! (world)) records policy)) #((StartJob A1 7 "/c/rev1")))))


;; --- 終わり方の数え方: exit code 0 は失敗に数えない。起こし直しの間は数え方と別に持つ -----------

(deftest test-task-that-exits-with-code-0-is-not-counted-as-a-failure
  ;; 1 回だけ走って exit code 0 で終わった task は失敗ではない。記録の failures は 0 で、状態に failures・backoff を出さない。
  (val task (JobSpec "task/t1" "doeff_cluster.worker.entry.job_entry" #("task") "rev1" :once True))
  (val start (! (plan 0 #(task) (! (world)) {} POLICY)))
  (val started (! (records-after 0 {} start POLICY)))
  (val reap (! (plan 1000 #(task) (! (world (replace (! (running task)) :exit-code 0))) started POLICY)))
  (val done (! (records-after 1000 started reap POLICY)))
  (val record (get done "task/t1"))
  (assert (= #(record.last-outcome record.last-exit-code record.failures) #(Outcome.EXITED 0 0)) record)
  (val status (get (! (statuses 5000 #(task) (! (world)) done POLICY)) 0))
  (assert (= status.phase JobPhase.FINISHED) status)
  (assert (= #(status.detail status.failures status.last-exit-code) #("last=exited" 0 0)) status)
  (assert (= (! (plan 5000 #(task) (! (world)) done POLICY)) #())))

(deftest test-task-that-exits-with-a-nonzero-code-is-still-counted
  ;; exit code 1 で終わった task は今までどおり失敗に数え、状態に回数を出す(起こし直しはしない)。
  (val task (JobSpec "task/t1" "doeff_cluster.worker.entry.job_entry" #("task") "rev1" :once True))
  (val started (! (records-after 0 {} (! (plan 0 #(task) (! (world)) {} POLICY)) POLICY)))
  (val reap (! (plan 1000 #(task) (! (world (replace (! (running task)) :exit-code 1))) started POLICY)))
  (val done (! (records-after 1000 started reap POLICY)))
  (assert (= (. (get done "task/t1") failures) 1))
  (val status (get (! (statuses 5000 #(task) (! (world)) done POLICY)) 0))
  (assert (= status.phase JobPhase.FINISHED) status)
  ;; 回数と code は欄で運ぶ(#3477)。文は終わり方と起こし直しの間だけ。
  (assert (.startswith status.detail "last=exited backoff=") status)
  (assert (= #(status.failures status.last-exit-code status.last-exit-at-ms) #(1 1 1000)) status)
  (assert (= (! (plan 5000 #(task) (! (world)) done POLICY)) #())))

(deftest test-service-that-keeps-exiting-with-code-0-keeps-its-growing-backoff
  ;; exit code 0 で終わったサービスも宣言がある限り起こし直し、続けて短く終わるたびに間を倍にする(今の振る舞いのまま)。
  ;; ただし失敗には数えない(failures は 0・状態に failures を出さない)。
  (val policy (replace POLICY :restart-backoff-max-ms 8000 :stable-run-ms 60000))
  (var records {})
  (var now 0)
  (for [wait [2000 4000 8000 8000]]
    (val start (! (plan now #(A1) (! (world)) records policy)))
    (assert (= (len start) 1) start)
    (:= records (! (records-after now records start policy)))
    (:= now (+ now 1000))
    (:= records (! (records-after now records (! (plan now #(A1) (! (world (replace (! (running A1)) :exit-code 0))) records policy)) policy)))
    (assert (= (! (plan (+ now wait -1) #(A1) (! (world)) records policy)) #()))
    (val status (get (! (statuses (+ now wait -1) #(A1) (! (world)) records policy)) 0))
    (assert (= status.phase JobPhase.BACKOFF) status)
    (assert (= #(status.detail status.failures status.last-exit-code) #("last=exited" 0 0)) status)
    (:= now (+ now wait)))
  (assert (= (. (get records "a") failures) 0))
  (assert (= (. (get records "a") attempts) 4))
  ;; 間が明けたら起こし直す
  (assert (= (! (plan now #(A1) (! (world)) records policy)) #((StartJob A1 5 "/c/rev1")))))

(deftest test-nonzero-exit-after-code-0-exits-counts-from-one
  ;; exit code 0 の終わりが続いた後の exit code 1 は、失敗の 1 回目として数える(起こし直しの間は続けて伸ばす)。
  (val policy (replace POLICY :restart-backoff-max-ms 8000 :stable-run-ms 60000))
  (val r1 (! (records-after 0 {} (! (plan 0 #(A1) (! (world)) {} policy)) policy)))
  (val r2 (! (records-after 1000 r1 (! (plan 1000 #(A1) (! (world (replace (! (running A1)) :exit-code 0))) r1 policy)) policy)))
  (val r3 (! (records-after 3000 r2 (! (plan 3000 #(A1) (! (world)) r2 policy)) policy)))
  (val r4 (! (records-after 4000 r3 (! (plan 4000 #(A1) (! (world (replace (! (running A1)) :exit-code 1))) r3 policy)) policy)))
  (assert (= (. (get r4 "a") failures) 1))
  (assert (= (! (plan 7999 #(A1) (! (world)) r4 policy)) #()))
  (val status (get (! (statuses 7999 #(A1) (! (world)) r4 policy)) 0))
  (assert (= status.detail "last=exited backoff=4000ms") status)
  (assert (= #(status.failures status.last-exit-code status.last-exit-at-ms) #(1 1 4000)) status))

(deftest test-status-carries-the-failure-streak-as-fields-and-reports-0-once-stable
  ;; 落ちた事実は文でなく欄で運ぶ(#3477): exit code 1 で 5 回続けて終わると failures = 5・最後の code と時刻が欄に入る。起こし直した
  ;; process が stable-run-ms 以上動いている間は 0 と報告する(記憶の failures は次の終わりまで 5 のまま — 数え直しは record-after)。
  (val policy (replace POLICY :restart-backoff-max-ms 8000 :stable-run-ms 60000))
  (var records {})
  (var now 0)
  (for [_ (range 5)]
    (:= now (+ now 10000))
    (val start (! (plan now #(A1) (! (world)) records policy)))
    (assert (= (len start) 1) start)
    (:= records (! (records-after now records start policy)))
    (:= now (+ now 1000))
    (:= records (! (records-after now records (! (plan now #(A1) (! (world (replace (! (running A1)) :exit-code 1))) records policy)) policy))))
  (val failing (get (! (statuses now #(A1) (! (world)) records policy)) 0))
  (assert (= #(failing.failures failing.last-exit-code failing.last-exit-at-ms) #(5 1 now)) failing)
  (:= now (+ now 10000))
  (:= records (! (records-after now records (! (plan now #(A1) (! (world)) records policy)) policy)))
  (val alive (! (world (! (running A1)))))
  (assert (= (. (get (! (statuses (+ now 59999) #(A1) alive records policy)) 0) failures) 5))
  (assert (= (. (get (! (statuses (+ now 60000) #(A1) alive records policy)) 0) failures) 0))
  (assert (= (. (get records "a") failures) 5)))


;; --- 入れ替え(handoff・2026-09-24): 新が Ready と数えられてから旧を止める ---------------------------------------

(import doeff_cluster.worker.intent.worker_model [RetireJob ReleaseLeases] doeff_cluster.worker.core.worker_rules [retired-name])

(setv H1 (replace A1 :handoff True)
      H2 (replace A1 :revision "rev2" :handoff True))

(defk proc [spec pid instance [retired-from None] [name None]]
  {:pre [(: spec JobSpec) (: pid int) (: instance str) (: retired-from (| str None)) (: name (| str None))] :post [(: % ProcessView)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "入れ替え(handoff)の検の子 process の観測を組むため(instance = 世代の名・retired-from = 退いた元の名・name = 観測の名)。"
  (ProcessView (or name spec.name) spec 1 pid 0 :instance instance :retired-from retired-from))

(deftest test-handoff-prepares-new-code-while-the-old-process-keeps-running
  ;; 新のコードが揃うまで旧は止めない(準備だけ進める)。状態には「入れ替えを待つ」を出す。
  (setv w (! (world (! (proc H1 10 "1-old")) :codes #(READY1))))
  (assert (= (! (plan 0 #(H2) w {} POLICY)) #((PrepareCode "rev2"))))
  (assert (= (! (plan 0 #(H2) (! (world (! (proc H1 10 "1-old")) :codes #(READY1 (CodeView "rev2" CodeState.PREPARING)))) {} POLICY)) #()))
  (setv #(status) (! (statuses 0 #(H2) w {} POLICY)))
  (assert (= status.phase JobPhase.RUNNING))
  (assert (in "入れ替えを待つ" status.detail) status.detail))

(deftest test-handoff-retires-the-old-process-then-starts-the-new-one-beside-it
  (setv w (! (world (! (proc H1 10 "1-old")) :codes #(READY1 READY2))))
  (setv retire (! (plan 0 #(H2) w {"a" (JobRecord "a" :attempts 1)} POLICY)))
  (assert (= retire #((RetireJob "a" 10 (retired-name "a" "1-old")))))
  ;; 退いた後: 名 a には process が無いので新を起こす。退いた旧は止めない(新がまだ Ready でない)。
  (setv old (! (proc H1 10 "1-old" :retired-from "a" :name (retired-name "a" "1-old"))))
  (setv w2 (! (world old :codes #(READY1 READY2))))
  (assert (= (! (plan 1 #(H2) w2 {"a" (JobRecord "a" :attempts 1)} POLICY)) #((StartJob H2 2 "/c/rev2"))))
  ;; 新が動いていても、coordinator が新を Ready と数えるまでは旧を止めない。
  (setv new (! (proc H2 11 "2-new")))
  (setv w3 (! (world old new :codes #(READY1 READY2))))
  (assert (= (! (plan 2 #(H2) w3 {} POLICY)) #()))
  (assert (= (! (plan 2 #((replace H2 :ready-instance "1-old")) w3 {} POLICY)) #()) "前の process の世代の名では止めない")
  ;; 新の世代の名が Ready と返ってきたら旧を止める(TERM → 猶予 → KILL の同じ手順)。
  (setv ready (replace H2 :ready-instance "2-new"))
  (assert (= (! (plan 3 #(ready) w3 {} POLICY)) #((SignalJob (retired-name "a" "1-old") 10 StopStage.TERM (Retired)))))
  ;; 状態の報告: 退いた旧は元の名つきで running の行に載る(coordinator はその job がまだ動いていると数える)。
  (setv rows (! (statuses 2 #(H2) w3 {} POLICY)))
  (setv by-name (dfor r rows r.name r))
  (assert (= (. (get by-name (retired-name "a" "1-old")) retired-from) "a"))
  (assert (= (. (get by-name "a") instance) "2-new")))

(deftest test-reaping-a-process-returns-its-leases-and-forgets-the-retired-record
  (setv name (retired-name "a" "1-old"))
  (setv dead (replace (! (proc H1 10 "1-old" :retired-from "a" :name name)) :exit-code -15))
  (setv records {name (JobRecord name :stopping (JobStop :requested-ms 0 :stage StopStage.TERM :signalled-ms 0 :reason (Retired)))})
  (setv actions (! (plan 5 #(H2) (! (world dead (! (proc H2 11 "2-new")) :codes #(READY1 READY2))) records POLICY)))
  ;; 担い手の job の名は子が名乗った名(起こした spec の名 "a" — 退いた後の名ではない)。
  (assert (= actions #((ReapJob name 10 Outcome.STOPPED -15) (ReleaseLeases "a" "1-old"))))
  (assert (not-in name (! (records-after 5 records actions))) "退いた process の記憶は回収で捨てる"))

(deftest test-handoff-never-runs-more-than-one-extra-process
  ;; 退いた旧が居る間に次の版が来たら、まだ Ready でない新は並べずに止める(並べるのは 1 つまで)。旧は動かし続ける。
  (setv H3 (replace A1 :revision "rev3" :handoff True))
  (setv old (! (proc H1 10 "1-old" :retired-from "a" :name (retired-name "a" "1-old"))))
  (setv w (! (world old (! (proc H2 11 "2-new")) :codes #(READY1 READY2 (CodeView "rev3" CodeState.READY "/c/rev3")))))
  (assert (= (! (plan 0 #(H3) w {} POLICY)) #((SignalJob "a" 11 StopStage.TERM (SpecChanged))))))

(deftest test-handoff-stops-the-retired-process-when-the-service-goes-away
  (setv old (! (proc H1 10 "1-old" :retired-from "a" :name (retired-name "a" "1-old"))))
  (assert (= (! (plan 0 #() (! (world old)) {} POLICY)) #((SignalJob (retired-name "a" "1-old") 10 StopStage.TERM (Retired))))))

(deftest test-recreate-jobs-keep-stopping-before-starting
  ;; handoff でない job は今までどおり(旧を止めてから新)。
  (assert (= (! (plan 0 #(A2) (! (world (! (proc A1 10 "1-old")) :codes #(READY1 READY2))) {} POLICY))
             #((SignalJob "a" 10 StopStage.TERM (SpecChanged))))))


;; --- 版の据え置き(#3684): drain 中の worker は、drain の間に宣言し直された新しい版を受けない ---------------------------------

(deftest test-a-held-job-takes-no-action-on-a-changed-spec
  ;; 失敗ケース(#3684): 返事の draining が真の拍(宣言が版を据え置く印 hold-version を持つ)では、spec が変わっても、入れ替えの job の
  ;; 新しい版の準備(PrepareCode)も退かせ(RetireJob)も、入れ替えでない job の止め(SignalJob)も出さず、旧い版を動かし続ける。直す前は
  ;; 印を見ずに、印の無い拍と同じ action を出していた。状態の行は「drain 中 — 新しい版は drain の後」。
  (val old-handoff (! (proc H1 10 "1-old")))
  (val held-handoff (replace H2 :hold-version True))
  (assert (= (! (plan 0 #(held-handoff) (! (world old-handoff :codes #(READY1))) {} POLICY)) #()))
  (assert (= (! (plan 0 #(held-handoff) (! (world old-handoff :codes #(READY1 READY2))) {"a" (JobRecord "a" :attempts 1)} POLICY)) #()))
  (val held-recreate (replace A2 :hold-version True))
  (val old-recreate (! (world (! (proc A1 10 "1-old")) :codes #(READY1 READY2))))
  (assert (= (! (plan 0 #(held-recreate) old-recreate {} POLICY)) #()))
  (val status (get (! (statuses 0 #(held-recreate) old-recreate {} POLICY)) 0))
  (assert (= status.phase JobPhase.RUNNING) status)
  (assert (= status.detail "drain 中 — 新しい版は drain の後") status)
  ;; 印は比べない欄: 印だけが違う宣言は同じ spec(据え置きの印で process を起こし直さない・指紋も同じ)。
  (assert (= (replace A1 :hold-version True) A1))
  ;; drain が解けた(印が偽に戻った)拍から、普通の入れ替えへ進む(溜めた物は無い)。
  (assert (= (! (plan 1 #(H2) (! (world old-handoff :codes #(READY1))) {} POLICY)) #((PrepareCode "rev2"))))
  (assert (= (! (plan 1 #(A2) old-recreate {} POLICY)) #((SignalJob "a" 10 StopStage.TERM (SpecChanged)))))
  ;; 印の前に止め始めた process は止め終える(据え置くのは止めていない process だけ)。
  (val stopping {"a" (JobRecord "a" :attempts 1 :stopping (JobStop :requested-ms 0 :stage StopStage.TERM :signalled-ms 0 :reason (SpecChanged)))})
  (assert (= (! (plan 1000 #(held-recreate) old-recreate stopping POLICY)) #((SignalJob "a" 10 StopStage.KILL (SpecChanged))))))

(deftest test-a-job-that-left-the-declaration-is-stopped-even-while-draining
  ;; 据え置くのは宣言に在る job の版だけ: drain 中の拍(残る job が印を持つ)でも、宣言から消えた job(移し先で新しい版が Ready になった
  ;; job など)は今までどおり止める。残る job には何もしない。
  (val w (! (world (! (running A1 10)) (! (running B1 11)))))
  (assert (= (! (plan 0 #((replace B1 :hold-version True)) w {} POLICY)) #((SignalJob "a" 10 StopStage.TERM (Undeclared))))))



(deftest test-a-job-left-out-by-the-cut-off-or-the-worker-stop-is-stopped-with-that-reason-through-the-kill
  ;; 宣言に無い job を止める訳は、拍の Program が渡す訳(#3713): 途絶で絞った宣言なら CutOff(最後の返事からの ms つき)・worker の停止なら
  ;; WorkerStopping。KILL も最初の TERM の訳を持ち回る(記憶の JobStop の訳)。
  (setv w (! (world (! (running A1)))))
  (val cut (CutOff :silent-ms 21000))
  (val term (! (plan 0 #() w {} POLICY :absent cut)))
  (assert (= term #((SignalJob "a" 10 StopStage.TERM cut))) term)
  (val records (! (records-after 0 {"a" (JobRecord "a" :attempts 1)} term)))
  ;; 猶予切れの KILL の拍には、宣言の読みが戻って(訳の既定 = 宣言から外れた)いても最初の訳を名乗る。
  (val kill (! (plan 1000 #() w records POLICY)))
  (assert (= kill #((SignalJob "a" 10 StopStage.KILL cut))) kill)
  (assert (= (! (plan 0 #() w {} POLICY :absent (WorkerStopping))) #((SignalJob "a" 10 StopStage.TERM (WorkerStopping))))))
