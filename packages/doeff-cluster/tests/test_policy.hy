(require doeff-hy.macros [deftest])

(import dataclasses [replace])
(import doeff_cluster.worker_model [JobSpec CodeState CodeView ProcessView WorldView StopStage StopProgress
  Outcome JobRecord WorkerPolicy JobPhase PrepareCode StartJob SignalJob ReapJob])
(import doeff_cluster.worker_policy [plan records-after statuses])

(setv POLICY (WorkerPolicy :stop-grace-ms 1000 :kill-grace-ms 500 :restart-backoff-ms 2000)
      A1 (JobSpec "a" "jobs.a" #() "rev1")
      A2 (replace A1 :revision "rev2")
      B1 (JobSpec "b" "jobs.b" #("x") "rev1")
      READY1 (CodeView "rev1" CodeState.READY "/c/rev1")
      READY2 (CodeView "rev2" CodeState.READY "/c/rev2"))

(defn world [#* processes [codes #(READY1)]] (WorldView codes processes))
(defn running [spec [pid 10]] (ProcessView spec.name spec 1 pid 0))

(deftest test-start-waits-for-code
  (assert (= (plan 0 #(A1) (world :codes #()) {} POLICY) #((PrepareCode "rev1"))))
  (assert (= (plan 0 #(A1) (world :codes #((CodeView "rev1" CodeState.PREPARING))) {} POLICY) #()))
  (assert (= (plan 0 #(A1) (world) {} POLICY) #((StartJob A1 1 "/c/rev1"))))
  ;; 展開に失敗した版は起動しない。状態表示に理由を出す。
  (setv failed (world :codes #((CodeView "rev1" CodeState.FAILED :detail "no such commit"))))
  (assert (= (plan 0 #(A1) failed {} POLICY) #()))
  (assert (= (. (get (statuses 0 #(A1) failed {} POLICY) 0) phase) JobPhase.CODE-FAILED)))

(deftest test-failed-code-is-prepared-again-after-the-retry-wait
  (setv policy (replace POLICY :code-retry-ms 30000)
        failed (world :codes #((CodeView "rev1" CodeState.FAILED :detail "準備に失敗" :failed-ms 1000))))
  (assert (= (plan 30999 #(A1) failed {} policy) #()))
  (setv #(status) (statuses 30999 #(A1) failed {} policy))
  (assert (= status.phase JobPhase.CODE-FAILED))
  (assert (= status.detail "準備に失敗"))
  (assert (= (plan 31000 #(A1) failed {} policy) #((PrepareCode "rev1")))))

(deftest test-jobs-are-independent
  ;; b だけ版を変えても a の process には何もしない。
  (setv w (world (running A1 10) (running B1 11)))
  (setv b2 (replace B1 :revision "rev2"))
  (assert (= (plan 0 #(A1 b2) w {} POLICY) #((SignalJob "b" 11 StopStage.TERM)))))

(deftest test-update-stops-old-before-starting-new
  (setv w (world (running A1) :codes #(READY1 READY2)))
  (setv actions (plan 0 #(A2) w {} POLICY))
  (assert (= actions #((SignalJob "a" 10 StopStage.TERM))))
  (setv records (records-after 0 {"a" (JobRecord "a" :attempts 1)} actions))
  ;; 猶予の間は待つ。新版は起動しない。
  (assert (= (plan 999 #(A2) w records POLICY) #()))
  ;; 猶予切れで KILL。
  (setv kill (plan 1000 #(A2) w records POLICY))
  (assert (= kill #((SignalJob "a" 10 StopStage.KILL))))
  (setv records (records-after 1000 records kill))
  (assert (= (. (get records "a") stopping) (StopProgress 0 StopStage.KILL 1000)))
  ;; KILL の後も終了を観測できない → 置き換えを起動しない・停止未確認と表示する。
  (assert (= (plan 5000 #(A2) w records POLICY) #()))
  (assert (= (. (get (statuses 5000 #(A2) w records POLICY) 0) phase) JobPhase.STOP-UNCONFIRMED))
  ;; 終了を観測したら回収し、次の拍で新版を起動する。
  (setv exited (world (replace (running A1) :exit-code -9) :codes #(READY1 READY2)))
  (setv reap (plan 5001 #(A2) exited records POLICY))
  (assert (= reap #((ReapJob "a" 10 Outcome.STOPPED -9))))
  (setv records (records-after 5001 records reap))
  (assert (= (plan 5002 #(A2) (world :codes #(READY1 READY2)) records POLICY)
             #((StartJob A2 2 "/c/rev2")))))

(deftest test-removed-job-is-stopped-not-forgotten
  (setv w (world (running A1)))
  (assert (= (plan 0 #() w {} POLICY) #((SignalJob "a" 10 StopStage.TERM))))
  (assert (= (statuses 0 #() (world) {} POLICY) #())))

(deftest test-crash-restarts-after-backoff
  (setv crashed (world (replace (running A1) :exit-code 3)))
  (setv reap (plan 0 #(A1) crashed {} POLICY))
  (assert (= reap #((ReapJob "a" 10 Outcome.EXITED 3))))
  (setv records (records-after 0 {"a" (JobRecord "a" :attempts 1)} reap))
  (assert (= (plan 1999 #(A1) (world) records POLICY) #()))
  (assert (= (. (get (statuses 1999 #(A1) (world) records POLICY) 0) phase) JobPhase.BACKOFF))
  (assert (= (plan 2000 #(A1) (world) records POLICY) #((StartJob A1 2 "/c/rev1")))))

(deftest test-requested-stop-exit-is-not-restarted-with-backoff
  ;; 停止を求めて終わった job は、宣言が残っていれば backoff なしで次版を起動する。
  (setv records {"a" (JobRecord "a" :attempts 1 :stopping (StopProgress 0 StopStage.TERM 0))})
  (setv reap (plan 10 #(A2) (world (replace (running A1) :exit-code 0) :codes #(READY1 READY2)) records POLICY))
  (setv records (records-after 10 records reap))
  (assert (= (. (get records "a") last-outcome) Outcome.STOPPED))
  (assert (= (plan 11 #(A2) (world :codes #(READY1 READY2)) records POLICY) #((StartJob A2 2 "/c/rev2")))))

(deftest test-task-runs-once-and-is-reported-finished
  (setv task (JobSpec "task/t1" "doeff_cluster.job_entry" #("task") "rev1" :once True))
  (assert (= (plan 0 #(task) (world) {} POLICY) #((StartJob task 1 "/c/rev1"))))
  ;; 終わった後は宣言に残っていても起動し直さない(worker の障害でも走らせ直さないのと同じ)
  (setv done {"task/t1" (JobRecord "task/t1" 1 100 Outcome.EXITED 0)})
  (assert (= (plan 5000 #(task) (world) done POLICY) #()))
  (assert (= (. (get (statuses 5000 #(task) (world) done POLICY) 0) phase) JobPhase.FINISHED)))


(deftest test-service-that-keeps-crashing-is-restarted-with-growing-backoff
  ;; Service は宣言がある限り起こし続ける。続けて落ちるたびに間を倍にし(2・4・8 秒…・上限 60 秒)、
  ;; 長く動いた後に落ちたら 1 回目として数え直す(k8s の CrashLoopBackOff と同じ形)。
  (setv policy (replace POLICY :restart-backoff-max-ms 8000 :stable-run-ms 60000))
  (setv records {} now 0 starts [])
  (for [round (range 5)]
    ;; 起動できる最初の時刻まで 1 ms ずつではなく、backoff の境目の前後だけを確かめる
    (setv start (plan now #(A1) (world) records policy))
    (assert (= (len start) 1))
    (.append starts now)
    (setv records (records-after now records start policy))
    ;; 1 秒で落ちる
    (+= now 1000)
    (setv reap (plan now #(A1) (world (replace (running A1) :exit-code 1)) records policy))
    (setv records (records-after now records reap policy))
    (setv wait (get [2000 4000 8000 8000 8000] round))
    (assert (= (plan (+ now wait -1) #(A1) (world) records policy) #()))
    (assert (= (. (get (statuses (+ now wait -1) #(A1) (world) records policy) 0) phase) JobPhase.BACKOFF))
    (+= now wait))
  (assert (= (. (get records "a") failures) 5))
  (assert (= (. (get records "a") attempts) 5))
  ;; 長く(stable-run-ms 以上)動いてから落ちたら 1 回目に戻る
  (setv start (plan now #(A1) (world) records policy))
  (setv records (records-after now records start policy))
  (+= now 60000)
  (setv records (records-after now records (plan now #(A1) (world (replace (running A1) :exit-code 1)) records policy) policy))
  (assert (= (. (get records "a") failures) 1))
  (assert (= (plan (+ now 2000) #(A1) (world) records policy) #((StartJob A1 7 "/c/rev1")))))


;; --- 入れ替え(handoff・2026-09-24): 新が Ready と数えられてから旧を止める ---------------------------------------

(import doeff_cluster.worker_model [RetireJob ReleaseLeases code-key retired-name])

(setv H1 (replace A1 :handoff True)
      H2 (replace A1 :revision "rev2" :handoff True))

(defn proc [spec pid instance [retired-from None] [name None]]
  (ProcessView (or name spec.name) spec 1 pid 0 :instance instance :retired-from retired-from))

(deftest test-handoff-prepares-new-code-while-the-old-process-keeps-running
  ;; 新のコードが揃うまで旧は止めない(準備だけ進める)。状態には「入れ替えを待つ」を出す。
  (setv w (world (proc H1 10 "1-old") :codes #(READY1)))
  (assert (= (plan 0 #(H2) w {} POLICY) #((PrepareCode "rev2"))))
  (assert (= (plan 0 #(H2) (world (proc H1 10 "1-old") :codes #(READY1 (CodeView "rev2" CodeState.PREPARING))) {} POLICY) #()))
  (setv #(status) (statuses 0 #(H2) w {} POLICY))
  (assert (= status.phase JobPhase.RUNNING))
  (assert (in "入れ替えを待つ" status.detail) status.detail))

(deftest test-handoff-retires-the-old-process-then-starts-the-new-one-beside-it
  (setv w (world (proc H1 10 "1-old") :codes #(READY1 READY2)))
  (setv retire (plan 0 #(H2) w {"a" (JobRecord "a" :attempts 1)} POLICY))
  (assert (= retire #((RetireJob "a" 10 (retired-name "a" "1-old")))))
  ;; 退いた後: 名 a には process が無いので新を起こす。退いた旧は止めない(新がまだ Ready でない)。
  (setv old (proc H1 10 "1-old" :retired-from "a" :name (retired-name "a" "1-old")))
  (setv w2 (world old :codes #(READY1 READY2)))
  (assert (= (plan 1 #(H2) w2 {"a" (JobRecord "a" :attempts 1)} POLICY) #((StartJob H2 2 "/c/rev2"))))
  ;; 新が動いていても、coordinator が新を Ready と数えるまでは旧を止めない。
  (setv new (proc H2 11 "2-new"))
  (setv w3 (world old new :codes #(READY1 READY2)))
  (assert (= (plan 2 #(H2) w3 {} POLICY) #()))
  (assert (= (plan 2 #((replace H2 :ready-instance "1-old")) w3 {} POLICY) #()) "前の process の世代の名では止めない")
  ;; 新の世代の名が Ready と返ってきたら旧を止める(TERM → 猶予 → KILL の同じ手順)。
  (setv ready (replace H2 :ready-instance "2-new"))
  (assert (= (plan 3 #(ready) w3 {} POLICY) #((SignalJob (retired-name "a" "1-old") 10 StopStage.TERM))))
  ;; 状態の報告: 退いた旧は元の名つきで running の行に載る(coordinator はその job がまだ動いていると数える)。
  (setv rows (statuses 2 #(H2) w3 {} POLICY))
  (setv by-name (dfor r rows r.name r))
  (assert (= (. (get by-name (retired-name "a" "1-old")) retired-from) "a"))
  (assert (= (. (get by-name "a") instance) "2-new")))

(deftest test-reaping-a-process-returns-its-leases-and-forgets-the-retired-record
  (setv name (retired-name "a" "1-old"))
  (setv dead (replace (proc H1 10 "1-old" :retired-from "a" :name name) :exit-code -15))
  (setv records {name (JobRecord name :stopping (StopProgress 0 StopStage.TERM 0))})
  (setv actions (plan 5 #(H2) (world dead (proc H2 11 "2-new") :codes #(READY1 READY2)) records POLICY))
  (assert (= actions #((ReapJob name 10 Outcome.STOPPED -15) (ReleaseLeases "1-old"))))
  (assert (not-in name (records-after 5 records actions)) "退いた process の記憶は回収で捨てる"))

(deftest test-handoff-never-runs-more-than-one-extra-process
  ;; 退いた旧が居る間に次の版が来たら、まだ Ready でない新は並べずに止める(並べるのは 1 つまで)。旧は動かし続ける。
  (setv H3 (replace A1 :revision "rev3" :handoff True))
  (setv old (proc H1 10 "1-old" :retired-from "a" :name (retired-name "a" "1-old")))
  (setv w (world old (proc H2 11 "2-new") :codes #(READY1 READY2 (CodeView "rev3" CodeState.READY "/c/rev3"))))
  (assert (= (plan 0 #(H3) w {} POLICY) #((SignalJob "a" 11 StopStage.TERM)))))

(deftest test-handoff-stops-the-retired-process-when-the-service-goes-away
  (setv old (proc H1 10 "1-old" :retired-from "a" :name (retired-name "a" "1-old")))
  (assert (= (plan 0 #() (world old) {} POLICY) #((SignalJob (retired-name "a" "1-old") 10 StopStage.TERM)))))

(deftest test-recreate-jobs-keep-stopping-before-starting
  ;; handoff でない job は今までどおり(旧を止めてから新)。
  (assert (= (plan 0 #(A2) (world (proc A1 10 "1-old") :codes #(READY1 READY2)) {} POLICY)
             #((SignalJob "a" 10 StopStage.TERM)))))

(deftest test-layered-code-is-prepared-under-its-own-key
  ;; base の在る job は「base~revision」の木を使う(業務コード = 本番の commit・包み = 宣言の commit)。
  (setv L (replace A1 :base "base1"))
  (assert (= (code-key L) "base1~rev1"))
  (assert (= (plan 0 #(L) (world :codes #(READY1)) {} POLICY) #((PrepareCode "base1~rev1"))))
  (setv ready (CodeView "base1~rev1" CodeState.READY "/c/base1~rev1"))
  (assert (= (plan 0 #(L) (world :codes #(READY1 ready)) {} POLICY) #((StartJob L 1 "/c/base1~rev1"))))
  ;; base が変われば別の spec(process を入れ替える)。
  (assert (!= L (replace L :base "base2"))))
