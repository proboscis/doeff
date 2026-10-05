;;; 待ちの子(#3646 の A4)の判断の表と、条 WC1〜WC3 の失敗ケース。判断(worker/core/policy の warm-child-step・plan)は純粋なので、観測の
;;; 組み合わせを並べて答えの action を比べる。条を判じる関数(worker/core/invariants)は、どの行の plan の答えにも破りが無い事を見る。
;;; 失敗ケースは、守りの 1 か所(warm_rules の warm-key-of・warm-mark-clean・policy の warm-child-actions)を壊すと条が赤になる事を見る。
(require doeff-hy.macros [deftest defk val var <-])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import dataclasses [replace])
(import doeff_cluster.worker.intent.worker_model [CodeView CodeState WorldView WorkerPolicy ProcessView StopStage StopProgress
  StartJob SignalJob Undeclared WarmEnv WarmChildMark WarmMarkUnreadable WarmChildView WarmLaunch StartWarmChild StopWarmChild ForgetWarmChild]
        doeff_cluster.shared.intent.job_model [JobSpec JobPhase]
        doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv]
        doeff_cluster.shared.core.runtime_env_rules [runtime-env->json]
        doeff_cluster.worker.core.worker_rules [code-key])
(import doeff_cluster.worker.core.policy [plan statuses warm-child-step warm-child-actions])
(import doeff_cluster.worker.core.policy :as policy-module)
(import doeff_cluster.worker.core.warm_rules :as warm-rules-module)
(import doeff_cluster.worker.core.invariants [warm-fork-uses-its-own-root warm-child-state-leaves-running-tasks
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
  (JobSpec name "doeff_cluster.worker.entry.job_entry" #("task") "rev1" :once once
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


(defk stops-forked-tasks-with-their-child [now desired observed warm policy]
  {:pre [(: now int) (: desired tuple) (: observed WorldView) (: warm tuple) (: policy WorkerPolicy)] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "条 WC2 の失敗ケースのための壊れた判断: 待ちの子が終わったら、その root から分かれて走っている task も止める(本物の答えに足す)。"
  (<- base tuple (warm-child-actions now desired observed warm policy))
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
