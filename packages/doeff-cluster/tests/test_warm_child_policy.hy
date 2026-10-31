;;; 待ちの子(#3646 の A4)の判断の表と、条 WC1〜WC3 の失敗ケース。判断(worker/core/policy の warm-child-step・plan)は純粋なので、観測の
;;; 組み合わせを並べて答えの action を比べる。条を判じる関数(worker/core/invariants)は、どの行の plan の答えにも破りが無い事を見る。
;;; 失敗ケースは、守りの 1 か所(warm_rules の warm-key-of・warm-mark-clean・policy の warm-child-actions)を壊すと条が赤になる事を見る。
(require doeff-hy.macros [deftest defk val var <-])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import dataclasses [replace])
(import doeff_cluster [CodeView CodeState WorldView WorkerPolicy ProcessView StopStage StartJob JobRecord SignalJob StartWarmChild StopWarmChild ForgetWarmChild]
        doeff_cluster._worker.intent.worker_model [StopProgress JobNotStarted Undeclared WarmEnv WarmChildMark WarmMarkUnreadable WarmChildView WarmLaunch]
        doeff_cluster [JobSpec JobPhase]
        doeff_cluster [RuntimeEnv]
        doeff_cluster._shared.intent.runtime_env_model [EnvFailure EnvFailureKind]
        doeff_cluster [runtime-env->json]
        doeff_cluster [code-key])
(import doeff_cluster [plan statuses records-after]
        doeff_cluster._worker.core.policy [warm-child-step warm-child-actions])
(import doeff_cluster._worker.core.worker_due [plan-due])
(import doeff_cluster._shared.intent.due_model [DueAt])
(import doeff_cluster._worker.core.warm_rules [refusals-after])
(import doeff_cluster._worker.protocol.heartbeat [status-row])
(import doeff_cluster._worker.core.policy :as policy-module)
(import doeff_cluster._worker.core.warm_rules :as warm-rules-module)
(import doeff_cluster._worker.core.invariants [warm-fork-uses-its-own-root warm-child-state-leaves-running-tasks
  warm-fork-only-before-any-vm])
(import tests.env_fixtures [LOCK env-of])

(val POLICY (WorkerPolicy :stop-grace-ms 1000 :kill-grace-ms 500 :code-retry-ms 30000))
(val NOW 100000)
(val KEY-A "env-a")
(val KEY-B "env-b")
(val ROOT-A "/roots/env-a")
(val ROOT-B "/roots/env-b")
(val CLEAN (WarmChildMark :threads 1 :vm-live #(0 0 0)))
(val PRELOAD #("app.jobs" "app.models"))
;; root A の待ちの子の起こし方(宣言の project は repo app の根 — env_fixtures の env-of)。
(val LAUNCH-A (WarmLaunch :root ROOT-A :project (+ ROOT-A "/app") :preload PRELOAD))


(defk task-on [name key [entries PRELOAD] [once True]]
  {:pre [(: name str) (: key str) (: entries tuple) (: once bool)] :post [(: % JobSpec)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "root のキー key(\"env-\" を除いた所が宣言の env-key)の実行環境で走る job の宣言を組むため。entries = 宣言の bytecodeEntries。"
  (<- env RuntimeEnv (env-of "app" "lib" LOCK))
  (<- value dict (runtime-env->json (replace env :bytecode-entries entries)))
  (JobSpec name "doeff_cluster._worker.entry.job_entry" #("task") "rev1" :once once
           :runtime-env (json.dumps value :sort-keys True) :env-key (cut key 4 None)))


(defk child [key [mark CLEAN] [exit-code None] [ended-ms None] [stop None] [detail ""]]
  {:pre [(: key str) (: mark (| WarmChildMark WarmMarkUnreadable None)) (: exit-code (| int None)) (: ended-ms (| int None)) (: stop (| StopProgress None))
         (: detail str)]
   :post [(: % WarmChildView)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "root のキー key の待ちの子の観測を組むため(既定 = 走っていて、印が分かれる前の形)。"
  (WarmChildView :key key :pid 900 :started-ms 0 :mark mark :exit-code exit-code :ended-ms ended-ms :stop stop :detail detail))


(defk world [[children #()] [processes #()] [codes #((CodeView KEY-A CodeState.READY ROOT-A) (CodeView KEY-B CodeState.READY ROOT-B))]]
  {:pre [(: children tuple) (: processes tuple) (: codes tuple)] :post [(: % WorldView)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "root の準備(codes)・走っている process・待ちの子の観測を、plan に渡す形で組むため。"
  (WorldView codes processes :warm-children children))


(defk clauses-hold [actions observed]
  {:pre [(: actions tuple) (: observed WorldView)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "plan の答えに条 WC1 と WC3 の破りが無い事を見るため(破りの列を返す — 空なら緑)。"
  (<- wc1 tuple (warm-fork-uses-its-own-root actions))
  (<- wc3 tuple (warm-fork-only-before-any-vm actions observed))
  (+ wc1 wc3))


;; --- 判断の表 1: root のキー 1 つの待ちの子(warm-child-step)---------------------------------------------------------
;; 列 = 要る root(READY の path・要らなければ None)× 待ちの子の観測 × 時刻 → action。

(deftest test-the-warm-child-table-gives-one-action-per-observation
  (val term-now (StopProgress :requested-ms (- NOW 500) :stage StopStage.TERM :signalled-ms (- NOW 500)))
  (val term-old (StopProgress :requested-ms (- NOW 1000) :stage StopStage.TERM :signalled-ms (- NOW 1000)))
  (val killed (StopProgress :requested-ms (- NOW 5000) :stage StopStage.KILL :signalled-ms (- NOW 4000)))
  (val bad-threads (WarmChildMark :threads 2 :vm-live #(0 0 0)))
  (val bad-vm (WarmChildMark :threads 1 :vm-live #(1 0 0)))
  (val rows
    #(;; 要る root
      #("要る・無い → 起こす" LAUNCH-A None #((StartWarmChild KEY-A LAUNCH-A)))
      #("要る・起こし中(印なし)→ 待つ" LAUNCH-A (! (child KEY-A :mark None)) #())
      #("要る・準備済み → 何もしない" LAUNCH-A (! (child KEY-A)) #())
      #("要る・印の thread が 2 → 止める" LAUNCH-A (! (child KEY-A :mark bad-threads))
        #((StopWarmChild KEY-A StopStage.TERM "準備完了の印が分かれる前の形でない(threads=2 vmLive=[0, 0, 0])")))
      #("要る・印の VM が生きている → 止める" LAUNCH-A (! (child KEY-A :mark bad-vm))
        #((StopWarmChild KEY-A StopStage.TERM "準備完了の印が分かれる前の形でない(threads=1 vmLive=[1, 0, 0])")))
      #("要る・印が読めない → 止める" LAUNCH-A (! (child KEY-A :mark (WarmMarkUnreadable :detail "JSON でない: JSONDecodeError")))
        #((StopWarmChild KEY-A StopStage.TERM "準備完了の印が読めない: JSON でない: JSONDecodeError")))
      #("要る・終わったばかり → 待つ" LAUNCH-A (! (child KEY-A :exit-code 3 :ended-ms (- NOW 1))) #())
      #("要る・終わって間が過ぎた → 起こし直す" LAUNCH-A (! (child KEY-A :exit-code 3 :ended-ms (- NOW 30000)))
        #((StartWarmChild KEY-A LAUNCH-A)))
      #("要る・止めの合図の後・猶予の内 → 待つ" LAUNCH-A (! (child KEY-A :stop term-now)) #())
      #("要る・止めの合図の後・猶予を過ぎた → KILL" LAUNCH-A (! (child KEY-A :stop term-old))
        #((StopWarmChild KEY-A StopStage.KILL "止めの合図の後も終わらない")))
      #("要る・KILL の後 → 待つ" LAUNCH-A (! (child KEY-A :stop killed)) #())
      ;; 要らない root(分かれる task も温める表も無い・root が READY でない)
      #("要らない・無い → 何もしない" None None #())
      #("要らない・走っている → 止める" None (! (child KEY-A))
        #((StopWarmChild KEY-A StopStage.TERM "root の待ちの子が要らなくなった")))
      #("要らない・止めの合図の後・猶予を過ぎた → KILL" None (! (child KEY-A :stop term-old))
        #((StopWarmChild KEY-A StopStage.KILL "止めの合図の後も終わらない")))
      #("要らない・止めの合図の後・猶予の内 → 待つ" None (! (child KEY-A :stop term-now)) #())
      #("要らない・終わった → 観測から外す" None (! (child KEY-A :exit-code 0 :ended-ms (- NOW 1))) #((ForgetWarmChild KEY-A)))))
  (for [#(label root view expected) rows]
    (<- got tuple (warm-child-step NOW KEY-A root view POLICY))
    (assert (= got expected) (.format "{}: {}" label got))))


;; --- 判断の表 2: 分かれる task の起動の門(plan)---------------------------------------------------------------------

(deftest test-a-task-on-an-env-root-forks-only-from-its-own-ready-warm-child
  (<- ta JobSpec (task-on "ta" KEY-A))
  (<- tb JobSpec (task-on "tb" KEY-B))
  (<- service JobSpec (task-on "sa" KEY-A :once False))
  (<- bare WorldView (world))
  (<- starting WorldView (world :children #((! (child KEY-A :mark None)))))
  (<- ready WorldView (world :children #((! (child KEY-A)))))
  (<- dirty WorldView (world :children #((! (child KEY-A :mark (WarmChildMark :threads 1 :vm-live #(0 1 0)))))))
  (<- both WorldView (world :children #((! (child KEY-A)) (! (child KEY-B)))))
  (<- preparing WorldView (world :codes #((CodeView KEY-A CodeState.PREPARING))))
  (val rows
    #(#("待ちの子が無い → 待ちの子を起こし、task は起こさない" #(ta) bare #((StartWarmChild KEY-A LAUNCH-A)))
      #("起こし中 → 何もしない" #(ta) starting #())
      #("準備済み → 自分の root の待ちの子から分ける" #(ta) ready #((StartJob ta 1 ROOT-A :warm-key KEY-A)))
      #("印に VM → 起こさずに待ちの子を止める" #(ta) dirty
        #((StopWarmChild KEY-A StopStage.TERM "準備完了の印が分かれる前の形でない(threads=1 vmLive=[0, 1, 0])")))
      #("2 つの root → それぞれ自分の root から(WC1)" #(ta tb) both
        #((StartJob ta 1 ROOT-A :warm-key KEY-A) (StartJob tb 1 ROOT-B :warm-key KEY-B)))
      #("service → 入れ物 shim の道・待ちの子は起こさない" #(service) bare #((StartJob service 1 ROOT-A)))
      #("root が準備中 → 待ちの子も起こさない" #(ta) preparing #())))
  (for [#(label desired observed expected) rows]
    (<- got tuple (plan NOW desired observed {} POLICY))
    (assert (= got expected) (.format "{}: {}" label got))
    (<- broken tuple (clauses-hold got observed))
    (assert (= broken #()) (.format "{}: 条の破り {}" label broken))))


(deftest test-a-warm-table-root-gets-a-warm-child-without-a-task
  ;; 温める表の root(task はまだ無い)にも待ちの子を起こす(最初の task が読み込みを待たない)。
  (<- ta JobSpec (task-on "ta" KEY-A :entries #("app.warm")))
  (val warm #((WarmEnv KEY-A ta.runtime-env)))
  (<- got tuple (warm-child-actions NOW #() (! (world)) warm POLICY))
  (assert (= got #((StartWarmChild KEY-A (WarmLaunch :root ROOT-A :project (+ ROOT-A "/app") :preload #("app.warm")))))))


(deftest test-a-task-waiting-for-its-warm-child-is-preparing-with-the-reason
  (<- ta JobSpec (task-on "ta" KEY-A))
  (<- fresh tuple (statuses NOW #(ta) (! (world)) {} POLICY))
  (assert (= (tuple (gfor s fresh #(s.phase s.detail))) #(#(JobPhase.PREPARING "待ちの子の準備中"))) fresh)
  (val crashed (! (world :children #((! (child KEY-A :exit-code 3 :ended-ms NOW :detail "読めない module: app.jobs"))))))
  (<- after-crash tuple (statuses NOW #(ta) crashed {} POLICY))
  (assert (= (tuple (gfor s after-crash #(s.phase s.detail)))
             #(#(JobPhase.PREPARING "待ちの子の準備中 — 前の待ちの子が終わった: 読めない module: app.jobs")))
          after-crash)
  (<- ready tuple (statuses NOW #(ta) (! (world :children #((! (child KEY-A))))) {} POLICY))
  (assert (= (tuple (gfor s ready s.phase)) #(JobPhase.STARTING)) ready))


;; --- 条 WC2: 待ちの子の段階は、走っている task の止めと回収に届かない --------------------------------------------------

(deftest test-the-warm-child-state-does-not-reach-a-running-forked-task
  (<- ta JobSpec (task-on "ta" KEY-A))
  (val running (ProcessView "ta" ta 1 50 0))
  (val term-old (StopProgress :requested-ms (- NOW 1000) :stage StopStage.TERM :signalled-ms (- NOW 1000)))
  (val states #(#() #((! (child KEY-A :mark None))) #((! (child KEY-A :exit-code -9 :ended-ms NOW)))
                #((! (child KEY-A :stop term-old))) #((! (child KEY-A :mark (WarmChildMark :threads 3 :vm-live #(1 1 1)))))))
  ;; 宣言に残る task(止めない)と、宣言から消えた task(止める)の両方で、待ちの子の段階を入れ替えても止めと回収は同じ。
  (for [desired #(#(ta) #())]
    (<- baseline tuple (plan NOW desired (! (world :children #((! (child KEY-A))) :processes #(running))) {} POLICY))
    (for [children states]
      (<- variant tuple (plan NOW desired (! (world :children children :processes #(running))) {} POLICY))
      (<- broken tuple (warm-child-state-leaves-running-tasks baseline variant))
      (assert (= broken #()) (.format "宣言 {} 待ちの子 {}: {}" (len desired) children broken)))))


;; --- 失敗ケース(守りの 1 か所を壊すと条が赤)---------------------------------------------------------------------

(deftest test-a-fork-key-from-another-root-breaks-wc1 [monkeypatch]
  ;; 分かれ元のキーを決める 1 か所を「隣の root のキー」を返す形に壊すと、task は別の root の待ちの子から分かれ、WC1 が名指す。
  (<- ta JobSpec (task-on "ta" KEY-A))
  (<- tb JobSpec (task-on "tb" KEY-B))
  (val observed (! (world :children #((! (child KEY-A)) (! (child KEY-B))))))
  (monkeypatch.setattr policy-module "warm_key_of" (fn [spec] (if (= (code-key spec) KEY-A) KEY-B KEY-A)))
  (<- got tuple (plan NOW #(ta tb) observed {} POLICY))
  (<- broken tuple (warm-fork-uses-its-own-root got))
  (assert (= (frozenset (gfor action broken action.spec.name)) #{"ta" "tb"}) broken))


(defk stops-forked-tasks-with-their-child [now desired observed warm policy [records {}]]
  {:pre [(: now int) (: desired tuple) (: observed WorldView) (: warm tuple) (: policy WorkerPolicy) (: records dict)] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "条 WC2 の失敗ケースのための壊れた判断: 待ちの子が終わったら、その root から分かれて走っている task も止める(本物の答えに足す)。"
  (<- base tuple (warm-child-actions now desired observed warm policy :records records))
  (+ base (tuple (gfor view observed.warm-children :if (is-not view.exit-code None)
                       p observed.processes :if (and (is p.exit-code None) (= (code-key p.spec) view.key))
                       (SignalJob p.name p.pid StopStage.TERM (Undeclared))))))


(deftest test-stopping-forked-tasks-with-their-warm-child-breaks-wc2 [monkeypatch]
  (<- ta JobSpec (task-on "ta" KEY-A))
  (val running (ProcessView "ta" ta 1 50 0))
  (monkeypatch.setattr policy-module "warm_child_actions" stops-forked-tasks-with-their-child)
  (<- baseline tuple (plan NOW #(ta) (! (world :children #((! (child KEY-A))) :processes #(running))) {} POLICY))
  (<- variant tuple (plan NOW #(ta) (! (world :children #((! (child KEY-A :exit-code -9 :ended-ms NOW))) :processes #(running)))
                          {} POLICY))
  (<- broken tuple (warm-child-state-leaves-running-tasks baseline variant))
  (assert (= broken #((SignalJob "ta" 50 StopStage.TERM (Undeclared)))) broken))


(deftest test-counting-a-mark-with-a-live-vm-as-ready-breaks-wc3 [monkeypatch]
  ;; 準備済みに数える印の判じを「thread だけ見る」形に壊すと、VM を起こした待ちの子から task が分かれ、WC3 が名指す。
  (<- ta JobSpec (task-on "ta" KEY-A))
  (val observed (! (world :children #((! (child KEY-A :mark (WarmChildMark :threads 1 :vm-live #(1 0 0))))))))
  (monkeypatch.setattr warm-rules-module "warm_mark_clean" (fn [mark] (= mark.threads 1)))
  (<- got tuple (plan NOW #(ta) observed {} POLICY))
  (<- broken tuple (warm-fork-only-before-any-vm got observed))
  (assert (= (tuple (gfor action broken action.spec.name)) #("ta")) broken))


;; --- 起動を断り続ける待ちの子-------------------------------------------------
;; 待ちの子が準備完了の印を書く前に終わる(起動の断り — 例 import の失敗)事が、同じ root で WorkerPolicy.warm-refusal-limit 回続いたら、
;; worker は待ちの子を起こし直さず、その root から分かれる task を env-failed(種類 env-incompatible・やり直さない・訳 = 断りの 1 行)で
;; 終える(coordinator の absorb-env-failure が task を終わりの phase にし、枠を返す)。失敗ケース: 上限が無いと、待ちの子は code-retry-ms
;; ごとに起こし直され、task は PREPARING のまま枠を取り続ける(2026-10-10 に 1,092 回・10 時間)。

(val REFUSAL "warm_child: 起動を断る: 前もって読む module app.jobs を読めない: ImportError: cannot import name 'X' from 'lib'")


(deftest test-refusals-count-up-across-restarts-and-reset-after-a-ready-child
  ;; 起こし直す時の観測の数え: 印を書く前に終わった子の後は 1 つ増え、準備済みになった子・worker が止めた子の後は 0 に戻る。
  (val first (refusals-after None))
  (val once (refusals-after (! (child KEY-A :mark None :exit-code 1 :ended-ms NOW :detail REFUSAL))))
  (val twice (refusals-after (replace (! (child KEY-A :mark None :exit-code 1 :ended-ms NOW :detail REFUSAL)) :refusals 1)))
  (val after-ready (refusals-after (replace (! (child KEY-A :exit-code 0 :ended-ms NOW)) :refusals 2)))
  (val stopped (StopProgress :requested-ms NOW :stage StopStage.TERM :signalled-ms NOW))
  (val after-stop (refusals-after (replace (! (child KEY-A :mark None :exit-code -15 :ended-ms NOW :stop stopped)) :refusals 2)))
  (assert (= #(first once twice after-ready after-stop) #(0 1 2 0 0)) #(first once twice after-ready after-stop)))


(deftest test-a-warm-child-refusing-up-to-the-limit-is-not-restarted-and-its-task-ends-env-failed
  (<- ta JobSpec (task-on "ta" KEY-A))
  (val limit POLICY.warm-refusal-limit)
  ;; 上限の手前(この終わりで limit - 1 回目)は、今までどおり間を置いて起こし直し、task は準備中のまま。
  (val below (replace (! (child KEY-A :mark None :exit-code 1 :ended-ms (- NOW 30000) :detail REFUSAL)) :refusals (- limit 2)))
  (<- restart tuple (warm-child-step NOW KEY-A LAUNCH-A below POLICY))
  (assert (= restart #((StartWarmChild KEY-A LAUNCH-A))) restart)
  (<- waiting tuple (statuses NOW #(ta) (! (world :children #(below))) {} POLICY))
  (assert (= (tuple (gfor s waiting s.phase)) #(JobPhase.PREPARING)) waiting)
  ;; 上限に達した(この終わりで limit 回目): 起こし直さず、task は env-failed で終わる。
  (val refused (replace below :refusals (- limit 1)))
  (<- held tuple (warm-child-step NOW KEY-A LAUNCH-A refused POLICY))
  (assert (= held #()) held)
  (<- ended tuple (statuses NOW #(ta) (! (world :children #(refused))) {} POLICY))
  (assert (= (tuple (gfor s ended #(s.phase s.failure s.detail)))
             #(#(JobPhase.ENV-FAILED (EnvFailure :kind EnvFailureKind.ENV-INCOMPATIBLE :detail REFUSAL :retryable False)
                 (.format "待ちの子が {} 回続けて断った: {}" limit REFUSAL))))
          ended)
  ;; coordinator へ運ぶ行は、やり直さない env-failed(absorb-env-failure が task を終える)。
  (<- row dict (status-row (get ended 0)))
  (assert (= #((get row "phase") (get row "failureKind") (get row "retryable")) #("env-failed" "env-incompatible" False)) row))


;; --- task を分けるのを断った待ちの子(card ki-e1612a12d0c3)-----------------------------------------------------------
;; 待ちの子が task を分けるのを断った(混んだ機体で期限の内に答えない — 10-24 02:42 JST の本番)事は、StartJob の答え JobNotStarted で
;; 届き、記憶の not-started に残る。判断は task だけを落ちた時と同じ failures と backoff に数え、その待ちの子を止めて断りに数える
;; (起こし直しと上限で task を env-failed で終える道は、起動の断りと同じ)。前は宿が OSError に変え、worker の process ごと落ちた。

(val LATE "待ちの子が分けるのを断った: 待ちの子が 5.0 秒のうちに答えない: /state/warm/env-a/sock")


(deftest test-a-start-that-was-not-started-counts-as-the-jobs-failure-and-backs-off
  (<- ta JobSpec (task-on "ta" KEY-A))
  (val refusal (JobNotStarted :name "ta" :warm-key KEY-A :detail LATE :at-ms NOW))
  (<- after dict (records-after NOW {} #((StartJob ta 1 ROOT-A :warm-key KEY-A)) POLICY #(refusal)))
  (val record (get after "ta"))
  ;; 落ちた時と同じ数え(failures・unexpected-exits)。起こした刻は書かず、task を終えた事にしない(last-outcome は None のまま)。
  (assert (= #(record.attempts record.failures record.unexpected-exits record.not-started record.last-outcome record.last-start-ms)
             #(1 1 1 refusal None None))
          record)
  ;; 断りの後に起こし直した待ちの子(断りより後に起こした子 — 止めの対象でない)の上で、backoff の間は起こさず、明けたら起こし直す。
  (<- restarted WorldView (world :children #((replace (! (child KEY-A)) :started-ms (+ NOW 1)))))
  (<- waiting tuple (plan (+ NOW 1999) #(ta) restarted after POLICY))
  (assert (= waiting #()) waiting)
  (<- due (plan-due NOW restarted after POLICY))
  (assert (= due (DueAt :at (+ NOW 2000))) due)
  (<- again tuple (plan (+ NOW 2000) #(ta) restarted after POLICY))
  (assert (= again #((StartJob ta 2 ROOT-A :warm-key KEY-A))) again)
  ;; 起こせた StartJob は not-started を消す。
  (<- started dict (records-after (+ NOW 2000) after again POLICY))
  (assert (is (. (get started "ta") not-started) None) started))


(deftest test-a-task-refused-by-its-warm-child-backs-off-alone-and-the-child-is-stopped
  (<- ta JobSpec (task-on "ta" KEY-A))
  (<- tb JobSpec (task-on "tb" KEY-B))
  (val refusal (JobNotStarted :name "ta" :warm-key KEY-A :detail LATE :at-ms NOW))
  (val records {"ta" (JobRecord "ta" :attempts 1 :failures 1 :unexpected-exits 1 :not-started refusal)})
  (<- observed WorldView (world :children #((! (child KEY-A)) (! (child KEY-B)))))
  (<- got tuple (plan NOW #(ta tb) observed records POLICY))
  ;; ta は backoff の間は起こさず、断った env-a の待ちの子を断りとして止める。tb は自分の待ちの子から今までどおり分かれる。
  (assert (= got #((StartJob tb 1 ROOT-B :warm-key KEY-B) (StopWarmChild KEY-A StopStage.TERM LATE :refused True))) got)
  (<- report tuple (statuses NOW #(ta) observed records POLICY))
  (assert (= (tuple (gfor s report #(s.phase s.failures s.detail)))
             #(#(JobPhase.BACKOFF 1 (.format "起こせなかった(warm:{}): {} — backoff=2000ms" KEY-A LATE))))
          report))


(deftest test-the-warm-child-table-stops-a-child-that-refused-a-fork-once
  (val refusal (JobNotStarted :name "ta" :warm-key KEY-A :detail LATE :at-ms NOW))
  (val term-now (StopProgress :requested-ms NOW :stage StopStage.TERM :signalled-ms NOW))
  (val rows
    #(#("断りの前に起こした子 → 断りとして止める" (! (child KEY-A)) #((StopWarmChild KEY-A StopStage.TERM LATE :refused True)))
      #("断りの後に起こし直した子 → 何もしない" (replace (! (child KEY-A)) :started-ms (+ NOW 1)) #())
      #("断りで止め始めた子・猶予の内 → 待つ(止めを出し直さない)" (replace (! (child KEY-A :stop term-now)) :fork-refused True) #())))
  (for [#(label view expected) rows]
    (<- got tuple (warm-child-step NOW KEY-A LAUNCH-A view POLICY :refusal refusal))
    (assert (= got expected) (.format "{}: {}" label got))))


(deftest test-fork-refusals-count-toward-the-warm-refusal-limit
  (<- ta JobSpec (task-on "ta" KEY-A))
  (val stopped (StopProgress :requested-ms (- NOW 30000) :stage StopStage.TERM :signalled-ms (- NOW 30000)))
  (val ended (replace (! (child KEY-A :exit-code -15 :ended-ms (- NOW 30000) :stop stopped :detail LATE)) :fork-refused True))
  ;; 断りで止めた子の終わりは、起こし直す時に断りの数えを 1 つ進める(断りの外で止めた子の後は 0 — 上の表の after-stop)。
  (assert (= #((refusals-after ended) (refusals-after (replace ended :refusals 1))) #(1 2)))
  ;; 上限の手前は間を置いて起こし直す。
  (<- restart tuple (warm-child-step NOW KEY-A LAUNCH-A ended POLICY))
  (assert (= restart #((StartWarmChild KEY-A LAUNCH-A))) restart)
  ;; 上限に達したら起こし直さず、task は断りの訳で env-failed に終わる。
  (val at-limit (replace ended :refusals (- POLICY.warm-refusal-limit 1)))
  (<- held tuple (warm-child-step NOW KEY-A LAUNCH-A at-limit POLICY))
  (assert (= held #()) held)
  (<- report tuple (statuses NOW #(ta) (! (world :children #(at-limit))) {} POLICY))
  (assert (= (tuple (gfor s report #(s.phase s.failure)))
             #(#(JobPhase.ENV-FAILED (EnvFailure :kind EnvFailureKind.ENV-INCOMPATIBLE :detail LATE :retryable False))))
          report))


;; --- 判断の表 2b: service を待ちの子から分ける道(card ki-de44efdb9aa4 — warm_rules の warm-start)------------------------------

(deftest test-a-service-forks-only-from-a-warm-child-that-names-service-forks
  ;; service を分ける方策(warm-services・下限 2 本)。分けるのは service が 2 本以上か分かれる task も在る root の service だけ。待ちの子の
  ;; 準備を待ち、印が service を受けると名乗る時だけ分ける。名乗らない古い版の待ちの子と、断りの上限に届いた待ちの子の root は入れ物 shim。
  (val on (replace POLICY :warm-services True :warm-services-min 2))
  (val serving (WarmChildMark :threads 1 :vm-live #(0 0 0) :services True))
  (<- s1 JobSpec (task-on "s1" KEY-A :once False))
  (<- s2 JobSpec (task-on "s2" KEY-A :once False))
  (<- lone JobSpec (task-on "lone" KEY-B :once False))
  (<- ta JobSpec (task-on "ta" KEY-A))
  (<- bare WorldView (world))
  (<- starting WorldView (world :children #((! (child KEY-A :mark None)))))
  (<- ready WorldView (world :children #((! (child KEY-A :mark serving)))))
  (<- old WorldView (world :children #((! (child KEY-A)))))
  (<- refused-child WarmChildView (child KEY-A :mark None :exit-code 3 :ended-ms (- NOW 30000)))
  (<- refused WorldView (world :children #((replace refused-child :refusals (- on.warm-refusal-limit 1)))))
  (val rows
    #(#("方策が偽 → 2 本でも入れ物 shim・待ちの子は起こさない" POLICY #(s1 s2) bare #((StartJob s1 1 ROOT-A) (StartJob s2 1 ROOT-A)))
      #("下限に満たない root → 入れ物 shim・待ちの子は起こさない" on #(lone) bare #((StartJob lone 1 ROOT-B)))
      #("下限の root・待ちの子が無い → 待ちの子を起こし、service は待つ" on #(s1 s2) bare #((StartWarmChild KEY-A LAUNCH-A)))
      #("下限の root・起こし中 → 何もしない" on #(s1 s2) starting #())
      #("下限の root・service を受ける待ちの子 → 分ける" on #(s1 s2) ready
        #((StartJob s1 1 ROOT-A :warm-key KEY-A) (StartJob s2 1 ROOT-A :warm-key KEY-A)))
      #("下限の root・印が service を名乗らない待ちの子 → 入れ物 shim" on #(s1 s2) old
        #((StartJob s1 1 ROOT-A) (StartJob s2 1 ROOT-A)))
      #("分かれる task の在る root の 1 本の service → task も service も分ける" on #(s1 ta) ready
        #((StartJob s1 1 ROOT-A :warm-key KEY-A) (StartJob ta 1 ROOT-A :warm-key KEY-A)))
      #("断りの上限に届いた待ちの子の root → service は入れ物 shim(env-failed にしない)" on #(s1 s2) refused
        #((StartJob s1 1 ROOT-A) (StartJob s2 1 ROOT-A)))))
  (for [#(label policy desired observed expected) rows]
    (<- got tuple (plan NOW desired observed {} policy))
    (assert (= got expected) (.format "{}: {}" label got))
    (<- broken tuple (clauses-hold got observed))
    (assert (= broken #()) (.format "{}: 条の破り {}" label broken)))
  ;; 待ちの子を待つ service の状態は準備の段階(理由は待ちの子の準備中)。shim で起こす service は待たない。
  (<- waiting tuple (statuses NOW #(s1 s2) starting {} on))
  (assert (= (tuple (gfor s waiting s.phase)) #(JobPhase.PREPARING JobPhase.PREPARING)) waiting)
  (<- cold tuple (statuses NOW #(s1 s2) starting {} POLICY))
  (assert (= (tuple (gfor s cold s.phase)) #(JobPhase.STARTING JobPhase.STARTING)) cold))


(deftest test-a-service-outliving-the-worker-forks-only-from-a-warm-child-that-names-orphan-forks
  ;; worker より長く生きる service(退きの寿命を宣言した handoff — launch の outlives-worker・card ki-f69f2a6869aa)は、頼みの
  ;; orphan-seconds を受けると印 orphanForks で名乗る待ちの子にだけ分ける。名乗らない版の待ちの子は頼みの知らない欄を断るので、入れ物 shim
  ;; (--orphan-seconds)で起こす。寿命を宣言しない service は今まで通り分ける。
  (val on (replace POLICY :warm-services True :warm-services-min 2))
  (val serving (WarmChildMark :threads 1 :vm-live #(0 0 0) :services True))
  (val orphaning (WarmChildMark :threads 1 :vm-live #(0 0 0) :services True :orphans True))
  (<- s1 JobSpec (task-on "s1" KEY-A :once False))
  (<- s2 JobSpec (task-on "s2" KEY-A :once False))
  (val lasting (replace s1 :handoff True :retired-ms 18000000))
  (<- old WorldView (world :children #((! (child KEY-A :mark serving)))))
  (<- new WorldView (world :children #((! (child KEY-A :mark orphaning)))))
  (val rows
    #(#("印が orphanForks を名乗らない待ちの子 → 長く生きる service は入れ物 shim・他は分ける" old
        #((StartJob lasting 1 ROOT-A) (StartJob s2 1 ROOT-A :warm-key KEY-A)))
      #("印が orphanForks を名乗る待ちの子 → どちらも分ける" new
        #((StartJob lasting 1 ROOT-A :warm-key KEY-A) (StartJob s2 1 ROOT-A :warm-key KEY-A)))))
  (for [#(label observed expected) rows]
    (<- got tuple (plan NOW #(lasting s2) observed {} on))
    (assert (= got expected) (.format "{}: {}" label got))))


;; --- 判断の表 2c: 分ける root の recreate の service の入れ替え(card ki-d8cc8191472e)------------------------------------------
;; 失敗ケース: 新しい版の木が揃い入口の検めも通った所で旧を止めると、止め終えた後の新は新しい root の待ちの子の準備(本番 zeus の 10-28〜29 の
;; 30 回のうち 23 回・回収から起動まで 5.8〜23.3 秒)を待ち、その間 job の仕事が止まった。待ちの子が準備済みになるまで旧を動かしたままにし、
;; 準備済みになった周期に旧を止め、止め終えた次の周期に待ちの子から分けて起こす。直す前は検めの後すぐ旧を止めていた。

(import doeff_cluster [ProbeView ProbeState ReapJob]
        doeff_cluster._worker.intent.worker_model [StartHold SpecChanged Outcome]
        doeff_cluster [spec-hash]
        doeff_cluster._worker.core.policy [start-holds JobHold])

(defk service-on [name key revision]
  {:pre [(: name str) (: key str) (: revision str)] :post [(: % JobSpec)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "root のキー key の実行環境で走る service(入口の検めの対象 — args の頭が service)の宣言を組むため。"
  (<- spec JobSpec (task-on name key :once False))
  (replace spec :args #("service") :revision revision))


(deftest test-a-recreate-service-on-a-forking-root-keeps-the-old-process-until-the-new-warm-child-is-ready
  (val on (replace POLICY :warm-services True :warm-services-min 2))
  (val serving (WarmChildMark :threads 1 :vm-live #(0 0 0) :services True))
  ;; 旧い版は root B の上で走り、宣言は root A の新しい版(2 本 — 分ける root)。新しい版の入口の検めはどちらも通っている。
  (<- old1 JobSpec (service-on "s1" KEY-B "rev1"))
  (<- old2 JobSpec (service-on "s2" KEY-B "rev1"))
  (<- new1 JobSpec (service-on "s1" KEY-A "rev2"))
  (<- new2 JobSpec (service-on "s2" KEY-A "rev2"))
  (val desired #(new1 new2))
  (val running #((ProcessView "s1" old1 1 50 0) (ProcessView "s2" old2 1 51 0)))
  (val probes #((ProbeView (spec-hash new1) ProbeState.PASSED) (ProbeView (spec-hash new2) ProbeState.PASSED)))
  (val codes #((CodeView KEY-A CodeState.READY ROOT-A) (CodeView KEY-B CodeState.READY ROOT-B)))
  (defn observed [children [processes running]]
    "旧の process と新しい root の待ちの子の観測を、plan に渡す形で組むため。"
    (WorldView codes processes :probes probes :warm-children children))
  (val records {"s1" (JobRecord "s1" :attempts 1) "s2" (JobRecord "s2" :attempts 1)})
  ;; 新しい root の待ちの子が無い → 待ちの子を起こすだけ・旧は止めない。起こし中(印なし)→ 何もしない。
  (<- bare tuple (plan NOW desired (observed #()) records on))
  (assert (= bare #((StartWarmChild KEY-A LAUNCH-A))) bare)
  (<- starting tuple (plan NOW desired (observed #((! (child KEY-A :mark None)))) records on))
  (assert (= starting #()) starting)
  ;; 待っている間の見送りの訳は WARM-CHILD・状態は旧い版が走っている事と待ちの子の準備中。
  (<- holds tuple (start-holds NOW desired (observed #((! (child KEY-A :mark None)))) records on))
  (assert (= holds #((JobHold :name "s1" :hold StartHold.WARM-CHILD) (JobHold :name "s2" :hold StartHold.WARM-CHILD))) holds)
  (<- report tuple (statuses NOW desired (observed #((! (child KEY-A :mark None)))) records on))
  (assert (= (tuple (gfor s report #(s.phase s.running-revision s.detail)))
             (* #(#(JobPhase.RUNNING "rev1" "旧い版を動かしたまま — 新しい版の待ちの子の準備中")) 2))
          report)
  ;; 待ちの子が準備済み → 旧を止める。
  (val ready-child #((! (child KEY-A :mark serving))))
  (<- stop tuple (plan NOW desired (observed ready-child) records on))
  (assert (= stop #((SignalJob "s1" 50 StopStage.TERM (SpecChanged)) (SignalJob "s2" 51 StopStage.TERM (SpecChanged)))) stop)
  ;; 止め終えて回収した次の周期に、待たずに待ちの子から分けて起こす。
  (<- stopping dict (records-after NOW records stop on))
  (val exited (tuple (gfor p running (replace p :exit-code -15))))
  (<- reap tuple (plan (+ NOW 10) desired (observed ready-child exited) stopping on))
  (assert (= reap #((ReapJob "s1" 50 Outcome.STOPPED -15 None) (ReapJob "s2" 51 Outcome.STOPPED -15 None))) reap)
  (<- reaped dict (records-after (+ NOW 10) stopping reap on))
  (<- start tuple (plan (+ NOW 11) desired (observed ready-child #()) reaped on))
  (assert (= start #((StartJob new1 2 ROOT-A :warm-key KEY-A) (StartJob new2 2 ROOT-A :warm-key KEY-A))) start))


(deftest test-a-recreate-service-on-a-shim-root-does-not-wait-for-a-warm-child
  ;; 分けない root(方策が偽・印が service を名乗らない古い版の待ちの子・断りの上限に届いた待ちの子)の service は、今までどおり検めの後に
  ;; 旧を止める(待ちの子を待つと、来ない準備を待って旧い版に留まる)。
  (val on (replace POLICY :warm-services True :warm-services-min 2))
  (<- old1 JobSpec (service-on "s1" KEY-B "rev1"))
  (<- old2 JobSpec (service-on "s2" KEY-B "rev1"))
  (<- new1 JobSpec (service-on "s1" KEY-A "rev2"))
  (<- new2 JobSpec (service-on "s2" KEY-A "rev2"))
  (val running #((ProcessView "s1" old1 1 50 0) (ProcessView "s2" old2 1 51 0)))
  (val probes #((ProbeView (spec-hash new1) ProbeState.PASSED) (ProbeView (spec-hash new2) ProbeState.PASSED)))
  (val codes #((CodeView KEY-A CodeState.READY ROOT-A) (CodeView KEY-B CodeState.READY ROOT-B)))
  (<- refused-child WarmChildView (child KEY-A :mark None :exit-code 3 :ended-ms (- NOW 1)))
  (val stop #((SignalJob "s1" 50 StopStage.TERM (SpecChanged)) (SignalJob "s2" 51 StopStage.TERM (SpecChanged))))
  (val rows
    #(#("方策が偽 → 待ちの子を待たない" POLICY #())
      #("印が service を名乗らない待ちの子 → 待たない" on #((! (child KEY-A))))
      #("断りの上限に届いた待ちの子 → 待たない" on #((replace refused-child :refusals (- on.warm-refusal-limit 1))))))
  (for [#(label policy children) rows]
    (<- got tuple (plan NOW #(new1 new2) (WorldView codes running :probes probes :warm-children children) {} policy))
    (assert (= got stop) (.format "{}: {}" label got))))
