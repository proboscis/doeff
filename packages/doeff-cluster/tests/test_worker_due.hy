;; worker の期限の純粋な関数(worker/core/worker_due.hy — #3871 の単位 2)。どれも期限の答え(shared/intent/due_model の
;; DueAt・DueNow・DueNever)を返す。待ち方はまだ変えない(周の間の 0.5 秒の眠りに繋がない)。
;;
;; 期限の関数ごとの境: 返す刻 D(now より後)の 1 ms 前では判断が now と同じ答えで、D で答えを変える(1 ms でも早いと起きても何も
;; 変わらず、遅いと判断が遅れる)。判断は期限の関数が写す本物の判断そのもの(policy の plan・statuses・warm-child-step・
;; beat_policy の heartbeat-due・heartbeat_rules の desired-after-silence と kept-when-cut-off?・env_upkeep の prepare-overdue と sweep-due)。
;; 落ち着いた worker(何も時刻で変わらない・過ぎた刻しか持たない)には、どの関数も今すぐ(DueNow)を返さない。
(require doeff-hy.macros [deftest defk <- val])
(import dataclasses [replace])
(import collections.abc [Callable])
(import doeff [run])
(import doeff_cluster.shared.intent.due_model [DueAt DueNow DueNever])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.worker.intent.worker_model [CodeState CodeView ProcessView WorldView StopStage StopProgress JobStop Outcome JobRecord
                                                 WorkerPolicy ProbeState ProbeView WarmChildView WarmLaunch Undeclared])
(import doeff_cluster.shared.core.job_rules [spec-hash])
(import doeff_cluster.worker.core.policy [plan statuses warm-child-step kept-when-cut-off?])
(import doeff_cluster.worker.core.beat_policy [heartbeat-due])
(import doeff_cluster.worker.core.heartbeat_rules [desired-after-silence])
(import doeff_cluster.worker.core.env_upkeep [RootsTally PrepareLimits prepare-overdue sweep-due SWEEP-EVERY-MS])
(import doeff_cluster.worker.core.worker_due [plan-due beat-due fence-due prepare-stop-due sweep-interval-due])

(val POLICY (WorkerPolicy))
(val A1 (JobSpec "a" "jobs.a" #() "rev1"))
(val SERVICE (JobSpec "s" "jobs.s" #("service") "rev1"))
(val READY1 (CodeView "rev1" CodeState.READY "/c/rev1"))
(val NOW 2000)


(defk boundary [due judged now]
  {:pre [(: due (| DueAt DueNow DueNever)) (: judged Callable) (: now int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "期限の答え due が刻 D(now より後)で、判断 judged(刻 → 答え)の答えが now と D − 1 で同じ・D で違う事を確かめ、D を返すため。"
  (assert (isinstance due DueAt) due)
  (val at due.at)
  (assert (> at now) #(at now))
  (assert (= (judged (- at 1)) (judged now)) #("D − 1 で答えが変わった" at))
  (assert (!= (judged at) (judged now)) #("D で答えが変わらない" at))
  at)


(defk policy-judged [desired world records]
  {:pre [(: desired tuple) (: world WorldView) (: records dict)] :post [(: % Callable)] :tags {:context "doeff-cluster-test" :role "program"}}
  "policy の判断(plan の action と statuses の報告の組)を、刻の関数にするため。"
  (fn [at] #((run (plan at desired world records POLICY)) (run (statuses at desired world records POLICY)))))


;; --- policy.hy の時刻の比べ ----------------------------------------------------------------------------

(deftest test-plan-due-is-the-code-retry-boundary
  ;; 準備に失敗した木を作り直す刻(failed-ms + code-retry-ms — prepare-actions)。
  (val world (WorldView #((CodeView "rev1" CodeState.FAILED :detail "失敗" :failed-ms 1000)) #()))
  (<- due (plan-due NOW world {} POLICY))
  (<- at int (boundary due (! (policy-judged #(A1) world {})) NOW))
  (assert (= at (+ 1000 POLICY.code-retry-ms)) at))


(deftest test-plan-due-is-the-probe-retry-boundary
  ;; 入口の検めに失敗した service を検め直す刻(probe の failed-ms + code-retry-ms — probe-actions)。
  (val world (WorldView #(READY1) #() :probes #((ProbeView (spec-hash SERVICE) ProbeState.FAILED :detail "読めない" :failed-ms 1000))))
  (<- due (plan-due NOW world {} POLICY))
  (<- at int (boundary due (! (policy-judged #(SERVICE) world {})) NOW))
  (assert (= at (+ 1000 POLICY.code-retry-ms)) at))


(deftest test-plan-due-is-the-backoff-boundary
  ;; 落ちた job を起こし直す刻(last-exit-ms + backoff-ms — in-backoff)。2 回続けて落ちたので間は 2 倍。
  (val world (WorldView #(READY1) #()))
  (val records {"a" (JobRecord "a" :attempts 2 :last-exit-ms 1000 :last-outcome Outcome.EXITED :last-exit-code 1 :failures 2
                                :unexpected-exits 2)})
  (<- due (plan-due NOW world records POLICY))
  (<- at int (boundary due (! (policy-judged #(A1) world records)) NOW))
  (assert (= at (+ 1000 (* 2 POLICY.restart-backoff-ms))) at))


(deftest test-plan-due-is-the-term-to-kill-boundary
  ;; TERM を送った job に KILL を送る刻(signalled-ms + stop-grace-ms — stop-actions)。
  (val world (WorldView #(READY1) #((ProcessView "a" A1 1 10 0))))
  (val records {"a" (JobRecord "a" :attempts 1 :stopping (JobStop :requested-ms 1000 :stage StopStage.TERM :signalled-ms 1000 :reason (Undeclared)))})
  (<- due (plan-due NOW world records POLICY))
  (<- at int (boundary due (! (policy-judged #() world records)) NOW))
  (assert (= at (+ 1000 POLICY.stop-grace-ms)) at))


(deftest test-plan-due-is-the-stop-unconfirmed-boundary
  ;; KILL を送った job を「止まりを確かめられない」と報告する刻(signalled-ms + kill-grace-ms — phase-of)。
  (val world (WorldView #(READY1) #((ProcessView "a" A1 1 10 0))))
  (val records {"a" (JobRecord "a" :attempts 1 :stopping (JobStop :requested-ms 1000 :stage StopStage.KILL :signalled-ms 1000 :reason (Undeclared)))})
  (<- due (plan-due NOW world records POLICY))
  (<- at int (boundary due (! (policy-judged #() world records)) NOW))
  (assert (= at (+ 1000 POLICY.kill-grace-ms)) at))


(deftest test-plan-due-is-the-warm-child-kill-boundary
  ;; TERM を送った待ちの子に KILL を送る刻(stop の signalled-ms + stop-grace-ms — warm-stop-step)。
  (val world (WorldView #() #() :warm-children #((WarmChildView :key "env-k" :pid 20 :started-ms 0 :stop (StopProgress 1000 StopStage.TERM 1000)))))
  (<- due (plan-due NOW world {} POLICY))
  (<- at int (boundary due (! (policy-judged #() world {})) NOW))
  (assert (= at (+ 1000 POLICY.stop-grace-ms)) at))


(deftest test-plan-due-is-the-warm-child-restart-boundary
  ;; 終わった待ちの子を起こし直す刻(ended-ms + code-retry-ms — warm-child-step)。判断は要る root の起こし方 launch を持つ warm-child-step。
  (val view (WarmChildView :key "env-k" :pid 20 :started-ms 0 :exit-code 1 :ended-ms 1000))
  (val launch (WarmLaunch :root "/roots/k" :project "/roots/k" :preload #()))
  (<- due (plan-due NOW (WorldView #() #() :warm-children #(view)) {} POLICY))
  (<- at int (boundary due (fn [at] (run (warm-child-step at "env-k" launch view POLICY))) NOW))
  (assert (= at (+ 1000 POLICY.code-retry-ms)) at))


(deftest test-plan-due-is-the-stable-run-boundary
  ;; 長く動いた process の続けて落ちた回数を 0 と報告する刻(last-start-ms + stable-run-ms — statuses)。
  (val world (WorldView #(READY1) #((ProcessView "a" A1 3 10 1000))))
  (val records {"a" (JobRecord "a" :attempts 3 :failures 2 :unexpected-exits 2 :last-start-ms 1000)})
  (<- due (plan-due NOW world records POLICY))
  (<- at int (boundary due (! (policy-judged #(A1) world records)) NOW))
  (assert (= at (+ 1000 POLICY.stable-run-ms)) at))


(deftest test-plan-due-is-the-earliest-of-the-boundaries
  ;; 期限が 2 つ在れば早い方(KILL の 1000 + 10000 と、落ち着きの 500 + 60000)。
  (val world (WorldView #(READY1) #((ProcessView "a" A1 1 10 500))))
  (val records {"a" (JobRecord "a" :attempts 1 :last-start-ms 500 :failures 1 :stopping (JobStop :requested-ms 1000 :stage StopStage.TERM :signalled-ms 1000 :reason (Undeclared)))})
  (<- due (plan-due NOW world records POLICY))
  (assert (= due (DueAt :at (+ 1000 POLICY.stop-grace-ms))) due))


;; --- heartbeat・途絶の柵 --------------------------------------------------------------------------

(deftest test-beat-due-is-the-heartbeat-interval-boundary
  ;; 待ちの口を使え、前の heartbeat が届き、報告も変わらない worker が次に heartbeat を送る刻(last-ok-ms + interval-ms)。
  (<- due (beat-due NOW 1000 2500))
  (<- at int (boundary due (fn [at] (heartbeat-due True True False False (- at 1000) 2500)) NOW))
  (assert (= at 3500) at))


(deftest test-fence-due-is-the-fence-boundary
  ;; 最後に届いた返事から fence を越えた刻(> なので last-ok-ms + fence-ms + 1 — desired-after-silence)。
  (<- due (fence-due NOW 1000 20000 240000))
  (<- at int (boundary due (fn [at] (desired-after-silence (- at 1000) 20000 240000 True #(A1) #())) NOW))
  (assert (= at 21001) at))


(deftest test-fence-due-is-the-keep-fence-boundary-after-the-fence
  ;; fence を越えた後は、動かし続けてよい印の在る job を止める刻(last-ok-ms + keep-fence-ms + 1 — kept-when-cut-off?)。
  (val kept (replace A1 :keep-when-cut-off True))
  (val now 30000)
  (<- due (fence-due now 1000 20000 240000))
  (<- at int (boundary due (fn [at] (run (kept-when-cut-off? kept (- at 1000) 240000))) now))
  (assert (= at 241001) at))


;; --- 実行環境の準備と掃除 --------------------------------------------------------------------------

(deftest test-prepare-stop-due-is-the-stall-boundary
  ;; 進みの印が stall-seconds 動かない準備を止める刻(> なので印の刻 + stall + 1 ms — prepare-overdue。呼び手と同じく秒の float で判じる)。
  (val limits (PrepareLimits :stall-seconds 600.0))
  (<- due (prepare-stop-due NOW 1250 limits))
  (<- at int (boundary due (fn [at] (run (prepare-overdue (/ 1250 1000.0) (/ at 1000.0) limits))) NOW))
  (assert (= at 601251) at))


(deftest test-sweep-interval-due-is-the-sweep-boundary
  ;; 上限を越えたままの roots を数え直す刻(前の掃除の終わり + SWEEP-EVERY-MS — sweep-due)。
  (val tally (RootsTally :ready (frozenset #("env-a")) :bytes 200 :below-min-free False))
  (<- due (sweep-interval-due NOW tally 100 1000))
  (<- at int (boundary due (fn [at] (run (sweep-due tally (frozenset #("env-a")) 100 False False at 1000))) NOW))
  (assert (= at (+ 1000 SWEEP-EVERY-MS)) at))


;; --- 落ち着いた worker --------------------------------------------------------------------------

(deftest test-a-settled-worker-is-never-due-now
  ;; 長く動いている job・過ぎた刻しか持たない記憶(昔の失敗・昔の止め)・上限の内の roots: どの関数も今すぐを返さない。時刻で何も
  ;; 変わらない物は無し、heartbeat と柵は先の刻。
  (val now 10000000)
  (val world (WorldView #(READY1 (CodeView "rev0" CodeState.FAILED :detail "昔" :failed-ms 1000)) #((ProcessView "a" A1 1 10 0))))
  (val records {"a" (JobRecord "a" :attempts 1 :last-start-ms 0) "b" (JobRecord "b" :last-exit-ms 1000 :last-outcome Outcome.EXITED
                                                                                 :unexpected-exits 1)})
  (<- planned (plan-due now world records POLICY))
  (assert (= planned (DueNever)) planned)
  (<- beat (beat-due now (- now 100) 2500))
  (assert (= beat (DueAt :at (+ (- now 100) 2500))) beat)
  (<- fenced (fence-due now (- now 100) 20000 240000))
  (assert (isinstance fenced DueAt) fenced)
  (<- swept (sweep-interval-due now (RootsTally :ready (frozenset) :bytes 50 :below-min-free False) 100 1000))
  (assert (= swept (DueNever)) swept)
  (<- stalled (prepare-stop-due now (- now 700000) (PrepareLimits :stall-seconds 600.0)))
  (assert (= stalled (DueNever)) stalled))


(deftest test-the-due-types-live-only-in-the-shared-layer
  ;; 期限の型と合わせ方は worker と coordinator が共用する層に 1 つ(移した後の旧い置き場に残さない)。
  (import doeff_cluster.coordinator.intent.due_model :as old-model)
  (import importlib.util [find-spec])
  (assert (not (hasattr old-model "DueAt")))
  (assert (is (find-spec "doeff_cluster.coordinator.core.due_policy") None)))
