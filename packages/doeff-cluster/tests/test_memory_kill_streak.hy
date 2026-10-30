;; 機体の memory の見張りに続けて止められた常駐の job の起こし直しを止める(WorkerPolicy.memory-kill-limit)— 模擬の検。
;;
;; 実例(2026-10-19・本番の worker): 1 つの常駐の job が起動の読みで memory の山を作り、見張り(worker/protocol/memory_watch)に起動から
;; 約 1 分で止められ、backoff の後に起こし直されてまた止められた — 06:48〜10:01 に 125 回・11:27〜12:10 に 37 回。どの回も要求を 1 件も
;; 回していない。worker は見張りの止めを普通の終わりと同じく backoff で起こし直し、止められた回数を数えず、失敗の数は約 1 分の走り
;; (stable-run-ms)で 1 に戻ったので、表に出なかった。
;;   - 模擬の worker(本物の見張りの 1 回・本物の観測のまとめ local-host と見張りの観測の答え手・本物の判断 plan / records-after / statuses)
;;     で、起動のたびに山を作る job を回す: 見張りが止めた job の根に印を残し、判断は回収でそれを数え、memory-kill-limit 回目で起こし直しを
;;     止め、状態の行が memory-killed(失敗の数も載る)になる。
;;   - memory-kill-reset-ms 以上動いてから止められた job は 1 から数え直す(仕事を回せた job は止めない)。
;;   - 宣言の spec が変われば、新しい spec は起こす。見張りに止められていない終わりは数えない(今までどおり backoff で起こし直す)。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import pytest)
(import dataclasses [replace])
(import doeff [with-handlers])
(import doeff_core_effects [SlogEffect])
(import doeff_core_effects [state])
(import doeff_core_effects [MemoryFiles])
(import doeff_core_effects [memory-file-handler])
(import doeff_core_effects [ProcRow])
(import doeff_core_effects [scripted-proc-table-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_cluster.shared.core.clock [datetime-of-epoch-ms])
(import doeff_cluster.shared.core.job_rules [spec-hash])
(import doeff_cluster.shared.intent.job_model [JobSpec JobPhase])
(import doeff_cluster._worker.core.memory_rules [JobRoot])
(import doeff_cluster [CodeState CodeView JobRecord ObserveWorld ProcessView ReapJob StartJob WorkerPolicy WorldView]
        doeff_cluster._worker.intent.worker_model [EnvDisk Outcome StartHold])
(import doeff_cluster [plan records-after statuses]
        doeff_cluster._worker.core.policy [start-holds])
(import doeff_cluster [ObserveProcesses ObserveCode ObserveEnvs ObserveEnvDisk ObserveProbes ObserveWarmChildren ObserveLeaving ObserveJobRoots])
(import doeff_cluster._worker.protocol.memory_watch [MemoryWatchSettings MemoryCell memory-watch-step])
(import doeff_cluster [memory-observer])
(import doeff_cluster [local-host])
(import tests.test_memory_watch [node-files row])

(val POLICY (WorkerPolicy :stop-grace-ms 1000 :kill-grace-ms 500 :restart-backoff-ms 2000 :restart-backoff-max-ms 60000
                          :stable-run-ms 60000 :memory-kill-limit 3 :memory-kill-reset-ms 600000))
(val SVC (JobSpec "svc" "jobs.svc" #() "rev1"))
(val READY1 (CodeView "rev1" CodeState.READY "/c/rev1"))
(val READY2 (CodeView "rev2" CodeState.READY "/c/rev2"))
;; 起動から見張りに止められるまで(本番の約 1 分 — stable-run-ms と同じ長さで、以前はここで失敗の数が 1 に戻った)。
(val KILLED-AFTER-MS 60000)
;; 止めた後、調整ループが終わりを観測して回収するまで。
(val REAPED-AFTER-MS 1000)


(defhandler only-this-process [#^ ProcessView process #^ tuple codes]
  "観測のまとめ(local-host)の言い換えの代わり: 子 process は process だけ・コードの木は codes・ほかの観測は空。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 回ごとに違う process(根の pid と終わりの code)を観測させる台本の値。
  (ObserveCode [] (resume codes))
  (ObserveEnvs [] (resume #()))
  (ObserveProcesses [] (resume #(process)))
  (ObserveProbes [] (resume #()))
  (ObserveEnvDisk [] (resume (EnvDisk :free 10 :sweep-wanted False :pinned (frozenset))))
  (ObserveWarmChildren [] (resume #()))
  (ObserveLeaving [] (resume False)))


(defhandler job-root-and-quiet-log [#^ JobRoot root]
  "見張りの外側の偽の世界: job の根は root 1 つ・log の行は捨てる(行の中身は test_memory_watch.hy が見る)。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 回ごとに違う根の pid(起こすたびに新しい process)。
  (ObserveJobRoots [] (resume #(root)))
  (SlogEffect [] (resume None)))


(defk piled-rows [root-pid]
  {:pre [(: root-pid int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "起動のたびに memory の山を作る常駐の job の process の表を作るため: shim(根 root-pid)→ 本体(2000 MiB の山)。"
  (<- tini ProcRow (row 1 0 1 "tini"))
  (<- shim ProcRow (row root-pid 1 10 "python"))
  (<- body ProcRow (row (+ root-pid 1) root-pid 2000 "python"))
  #(tini shim body))


(defk watched-once [cell root-pid now-ms available-gib]
  {:pre [(: cell MemoryCell) (: root-pid int) (: now-ms int) (: available-gib float)] :post [(: % None)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の見張りの 1 回を、刻 now-ms・機体の空き available-gib・山を作った job の process の表の下で回すため。"
  (<- files MemoryFiles (node-files available-gib))
  (<- rows tuple (piled-rows root-pid))
  (! (with-handlers [(state) (sim-time-handler :clock (SimClock :current-time (datetime-of-epoch-ms now-ms))) (memory-file-handler files)
                     (scripted-proc-table-handler rows) (job-root-and-quiet-log (JobRoot :name "svc" :pid root-pid))]
       (memory-watch-step (MemoryWatchSettings) cell)))
  None)


(defk observed-world [cell process codes]
  {:pre [(: cell MemoryCell) (: process ProcessView) (: codes tuple)] :post [(: % WorldView)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "本番と同じ観測のまとめ(local-host)と見張りの観測の答え手(memory-observer)で、終わった process を含む世界を観測するため。"
  (! (with-handlers [(memory-observer cell) (only-this-process process codes) local-host] (ObserveWorld))))


(defk next-start [now records world]
  {:pre [(: now int) (: records dict) (: world WorldView)] :post [(: % (| int None))] :tags {:context "doeff-cluster-test" :role "program"}}
  "回収の後、判断が起こし直す最初の刻を求めるため(backoff の上限 + 1 秒まで 1 秒ずつ問う — 起こさなければ None)。"
  (for [step (range 1 (+ (// POLICY.restart-backoff-max-ms 1000) 2))]
    (val at (+ now (* step 1000)))
    (<- actions tuple (plan at #(SVC) world records POLICY))
    (when (any (gfor a actions (isinstance a StartJob)))
      (return at)))
  None)


(deftest test-a-job-killed-by-the-memory-watch-on-every-start-stops-restarting-at-the-limit-and-reports-a-failure
  ;; 失敗ケース(直す前): 見張りの止めは普通の終わりと同じく backoff で起こし直され、止まらなかった。失敗の数は約 1 分の走りで 1 に戻った。
  (val cell (MemoryCell))
  (val empty (WorldView #(READY1) #()))
  (var records {})
  (var now 1000000)
  (var restarts #())
  (for [round (range POLICY.memory-kill-limit)]
    (<- started tuple (plan now #(SVC) empty records POLICY))
    (assert (= started #((StartJob SVC (+ round 1) "/c/rev1"))) #(round started))
    (:= records (! (records-after now records started POLICY)))
    ;; 起動から約 1 分で山が機体の空きを食い、本物の見張りが山(本体)を止めて、job の根に印を残す。
    (val root-pid (+ 100 (* 10 round)))
    (val killed-at (+ now KILLED-AFTER-MS))
    (<- (watched-once cell root-pid killed-at 1.6))
    (assert (= (tuple (gfor k cell.kills #(k.job k.pid))) (tuple (gfor r (range (+ round 1)) #("svc" (+ 100 (* 10 r)))))) cell.kills)
    ;; 本体が止められて job の process が終わる。調整ループの観測(local-host)は見張りの印を載せ、判断は回収で数える。
    (val reaped-at (+ killed-at REAPED-AFTER-MS))
    (val ended (ProcessView "svc" SVC (+ round 1) root-pid now :exit-code 137))
    (<- world WorldView (observed-world cell ended #(READY1)))
    (assert (in #("svc" root-pid) world.memory-killed) world)
    (<- reaped tuple (plan reaped-at #(SVC) world records POLICY))
    (assert (= reaped #((ReapJob "svc" root-pid Outcome.EXITED 137 (spec-hash SVC)))) reaped)
    (:= records (! (records-after reaped-at records reaped POLICY)))
    (assert (= (. (get records "svc") memory-kills) (+ round 1)) (get records "svc"))
    (<- again (| int None) (next-start reaped-at records empty))
    (:= restarts (+ restarts #(again)))
    (:= now (if (is again None) reaped-at again)))
  ;; 1 回目と 2 回目の後は backoff(2 秒・4 秒 — 見張りに止められた終わりは約 1 分の走りで数え直さない)で起こし直し、3 回目の後は
  ;; backoff の上限を過ぎても起こさない。
  (assert (= (tuple (gfor r restarts (if (is r None) None 1))) #(1 1 None)) restarts)
  (val record (get records "svc"))
  (assert (= #(record.memory-kills record.failures record.unexpected-exits) #(3 3 3)) record)
  ;; 失敗として表に出す: 状態の行は memory-killed(coordinator は版の判定を Blocked・待ち手は落ちたと数える)、失敗の数 3、理由の文。
  (val later (+ now 3600000))
  (<- reported tuple (statuses later #(SVC) empty records POLICY))
  (val status (get reported 0))
  (assert (= status.phase JobPhase.MEMORY-KILLED) status)
  (assert (= status.failures 3) status)
  (assert (= status.detail "機体の memory の見張りに 3 回続けて止められた — 起こし直さない(宣言の spec が変わるまで)") status)
  (<- holds tuple (start-holds later #(SVC) empty records POLICY))
  (assert (= (. (get holds 0) hold) StartHold.MEMORY-KILLED) holds)
  (<- idle tuple (plan later #(SVC) empty records POLICY))
  (assert (= idle #()) "1 時間後も起こさない")
  ;; 宣言の spec が変われば(新しい版)、新しい spec は起こす。
  (val svc2 (replace SVC :revision "rev2"))
  (<- fresh tuple (plan later #(svc2) (WorldView #(READY1 READY2) #()) records POLICY))
  (assert (= fresh #((StartJob svc2 4 "/c/rev2"))) fresh)
  ;; 印は kill-mark-seconds(600 秒)を過ぎた見張りの読みで除く(余裕の戻った機体 — 何も止めない)。
  (<- (watched-once cell 999 (+ later 1) 16.0))
  (assert (= cell.kills #()) cell.kills))


(defk killed-run [records start-ms ran-ms]
  {:pre [(: records dict) (: start-ms int) (: ran-ms int)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "SVC を start-ms に起こし、ran-ms 走った所で見張りに止められて回収した後の記憶を求めるため(判断だけ — 観測の印は手で組む)。"
  (<- started tuple (plan start-ms #(SVC) (WorldView #(READY1) #()) records POLICY))
  (assert (= (len started) 1) started)
  (<- after-start dict (records-after start-ms records started POLICY))
  (val ended (ProcessView "svc" SVC 1 50 start-ms :exit-code 137))
  (val world (WorldView #(READY1) #(ended) :memory-killed (frozenset #(#("svc" 50)))))
  (val end-ms (+ start-ms ran-ms))
  (<- reaped tuple (plan end-ms #(SVC) world after-start POLICY))
  (<- after-reap dict (records-after end-ms after-start reaped POLICY))
  after-reap)


(deftest test-a-job-that-ran-long-before-the-kill-counts-again-from-one
  ;; 2 回続けて約 1 分で止められた後、memory-kill-reset-ms(10 分)以上動いてから止められた job は、仕事を回せた job — 1 から数え直し、
  ;; 起こし直す。
  (<- one dict (killed-run {} 0 KILLED-AFTER-MS))
  (<- two dict (killed-run one 100000 KILLED-AFTER-MS))
  (assert (= (. (get two "svc") memory-kills) 2) two)
  (<- long dict (killed-run two 300000 POLICY.memory-kill-reset-ms))
  (val record (get long "svc"))
  (assert (= #(record.memory-kills record.failures) #(1 1)) record)
  (<- again tuple (plan (+ 300000 POLICY.memory-kill-reset-ms 2000) #(SVC) (WorldView #(READY1) #()) long POLICY))
  (assert (= (len again) 1) again))


(deftest test-exits-the-memory-watch-did-not-cause-are-not-counted
  ;; 見張りの印の無い終わり(落ちた・自分で終わった)は数えない — 今までどおり backoff で起こし直し続ける。
  (var records {})
  (var now 0)
  (for [_ (range 5)]
    (<- started tuple (plan now #(SVC) (WorldView #(READY1) #()) records POLICY))
    (assert (= (len started) 1) started)
    (:= records (! (records-after now records started POLICY)))
    (:= now (+ now 1000))
    (<- reaped tuple (plan now #(SVC) (WorldView #(READY1) #((ProcessView "svc" SVC 1 50 0 :exit-code 1))) records POLICY))
    (assert (= reaped #((ReapJob "svc" 50 Outcome.EXITED 1 None))) reaped)
    (:= records (! (records-after now records reaped POLICY)))
    (:= now (+ now POLICY.restart-backoff-max-ms)))
  (assert (= (. (get records "svc") memory-kills) 0) records)
  (<- reported tuple (statuses now #(SVC) (WorldView #(READY1) #()) records POLICY))
  (assert (= (. (get reported 0) phase) JobPhase.STARTING) reported))


(deftest test-the-limit-must-be-positive
  ;; 0 回の上限は 1 度も起こさずに失敗にしてしまう・負の数え直しの境は意味を持たない — 方策を作る時に断る。
  (with [(pytest.raises ValueError)] (WorkerPolicy :memory-kill-limit 0))
  (with [(pytest.raises ValueError)] (WorkerPolicy :memory-kill-reset-ms -1)))


(deftest test-the-default-limit-stops-at-the-fifth-kill-and-reports-five-failures-even-when-the-root-exits-with-0
  ;; 既定の方策(memory-kill-limit 5): 起こし直しを止めた job の失敗の数は 5 — 上に載る系の運転の見張りが「落ち続け」と数える閾
  ;; (status.failures が 5 以上)に届く。見張りが木の単位を止めた後に根が exit code 0 で終わっても、止められた終わりは失敗に数える
  ;; (失敗ケース: exit code 0 を失敗に数えないままだと、失敗の数は 0 で表に出ない)。
  (val policy (WorkerPolicy))
  (val empty (WorldView #(READY1) #()))
  (var records {})
  (var now 0)
  (var held-at None)
  (for [round (range 6)]
    (<- started tuple (plan now #(SVC) empty records policy))
    (when (= started #())
      (:= held-at round)
      (break))
    (:= records (! (records-after now records started policy)))
    (val end-ms (+ now KILLED-AFTER-MS))
    (val ended (ProcessView "svc" SVC (+ round 1) 50 now :exit-code 0))
    (<- reaped tuple (plan end-ms #(SVC) (WorldView #(READY1) #(ended) :memory-killed (frozenset #(#("svc" 50)))) records policy))
    (:= records (! (records-after end-ms records reaped policy)))
    (:= now (+ end-ms policy.restart-backoff-max-ms)))
  (assert (= held-at 5) #(held-at records))
  (<- reported tuple (statuses now #(SVC) empty records policy))
  (val status (get reported 0))
  (assert (= #(status.phase status.failures) #(JobPhase.MEMORY-KILLED 5)) status))
