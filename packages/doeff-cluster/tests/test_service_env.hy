;; 常駐の service を実行環境(runtime env)の root で起こす(2026-09-26・設計 worker-runtime-env.md 節 6 の順 3.5)。
;;
;; 前は runtimeEnv を運ぶのは task だけで、service の路(宣言 → coordinator の ClusterJob → heartbeat の返事 → worker → 入口の検め)には
;; 欄が無く、service は image の venv の doeff で動き続けた(doeff を変えるには image を作り直すしかない)。
;;
;; 速い検: 宣言の行が runtimeEnv を運ぶ・baseFrom / overlay との併用を断る・coordinator が spec と heartbeat の返事に載せる・worker が版を
;;        env のキーへ置き換える・入口の検めを root の venv で撃つ・子の文脈から自分の env を読む。
;; 丁寧な模擬(test_env_careful と同じ世界 — 本物の git・fake の uv・本物の EnvStore / ProbeStore / ProcessHost): service を宣言から
;;        env の root で起こし、送り手の commit だけ変えた 2 回目の宣言で新しい root の source の値が返り、worker の process は同じ。
(require doeff-hy.macros [deftest defk <- val var])
(import json)
(import os)
(import sys)
(import time)
(import pathlib [Path])
(import pytest)
(import doeff [run])
(import doeff_cluster.runtime_env_model [RepoCheckout PythonProject RuntimeEnv runtime-env->json env-key current-platform])
(import doeff_cluster.service_model [service System system-declaration])
(import doeff_cluster.cluster_policy [job-from-json job-to-json spec-json])
(import doeff_cluster.handlers [declared-job-spec ProbeStore])
(import doeff_cluster.job_entry [RunContext runtime-env-of-context])
(import doeff_cluster.worker_model [JobSpec ProbeEntry ProbeState StartJob ReapJob Outcome CodeState ENV-KEY-PREFIX])
(import tests.test_env_careful [Rig make-rig push-commit app-files declare prepare LOCK HY DEADLINE-SECONDS])

(val SHA-A (* "a" 40))
(val SHA-B (* "b" 40))


(defk sample-env []
  {:pre [] :post [(: % RuntimeEnv)]}
  "速い検の宣言(repo 1 つ・project はその根)。"
  (RuntimeEnv :repos #((RepoCheckout :name "app" :url "https://example.invalid/app.git" :commit SHA-A))
              :project (PythonProject :repo "app" :path "." :lock-sha256 (* "0" 64) :python "3.14")
              :import-roots #("app/.")))


(defk quiet-program [interval]
  {:pre [(: interval float)] :post [(: % float)]}
  "速い検の service の本体(宣言の組み立てだけに使う — 走らせない)。"
  interval)


(defn env [config ctx] [])  ; defk にできない: job_entry が Program の外で呼ぶ組み立ての関数(速い検の宣言の参照先)


(deftest test-the-declaration-carries-the-runtime-env-and-refuses-base-from
  ;; 宣言の行が runtimeEnv を運ぶ。image の版を追う base-from の service を含む宣言は、commit が 2 つになるので断る。
  (<- declared-env RuntimeEnv (sample-env))
  (val plain (service "quiet" quiet-program :env "tests.test_service_env:env" :config {"interval" 1.0}))
  (val rows (system-declaration (System "lab" #(plain)) "rev-1" :runtime-env declared-env))
  (<- env-json dict (runtime-env->json declared-env))
  (assert (= (get (get rows 0) "runtimeEnv") env-json) rows)
  (assert (not-in "runtimeEnv" (get (system-declaration (System "lab" #(plain)) "rev-1") 0)) "env を渡さない宣言は今の形のまま")
  (val following (service "follow" quiet-program :env "tests.test_service_env:env" :config {"interval" 1.0}
                          :base-from {"kind" "Deployment" "namespace" "n" "name" "d"}))
  (with [(pytest.raises ValueError)]
    (system-declaration (System "lab" #(plain following)) "rev-1" :runtime-env declared-env)))


(deftest test-the-coordinator-carries-the-runtime-env-to-the-worker
  ;; coordinator: 宣言の runtimeEnv を JobSpec の比べる欄に持ち、spec の JSON と heartbeat の返事に載せる。baseFrom・overlay との併用と
  ;; 読めない宣言は断る。
  (<- declared-env RuntimeEnv (sample-env))
  (<- env-json dict (runtime-env->json declared-env))
  (val row (get (system-declaration (System "lab" #((service "quiet" quiet-program :env "tests.test_service_env:env"
                                                             :config {"interval" 1.0})))
                                    "rev-1" :runtime-env declared-env)
                0))
  (val job (job-from-json row))
  (assert (= (json.loads job.spec.runtime-env) env-json) job.spec)
  (assert (= (get (job-to-json job) "runtimeEnv") env-json) "coordinator の状態に残る(読み戻しで同じ宣言)")
  (assert (= (job-from-json (job-to-json job)) job) "読み戻しで同じ job")
  (val wire (spec-json job.spec))
  (assert (= (get wire "runtimeEnv") env-json) wire)
  (assert (not-in "runtimeEnv" (spec-json (. (job-from-json (| row {"runtimeEnv" None})) spec))) "env の無い job の返事は今の形")
  (with [(pytest.raises ValueError)]
    (job-from-json (| row {"baseFrom" {"kind" "Deployment" "namespace" "n" "name" "d"}})))
  (with [(pytest.raises ValueError)]
    (job-from-json (| row {"runtimeEnv" (| env-json {"repos" []})}))))


(deftest test-the-worker-places-an-env-service-by-the-env-key
  ;; worker: heartbeat の返事の service の job に runtimeEnv があれば、版をこの worker の platform の env のキーに置き換え、宣言を比べる
  ;; 欄に持つ(worker の方針が PrepareEnv で root を準備し、子を root の venv で起こす)。無ければ今の形。
  (<- declared-env RuntimeEnv (sample-env))
  (<- env-json dict (runtime-env->json declared-env))
  (<- key str (env-key declared-env (current-platform)))
  (val wire {"name" "quiet" "entry" "doeff_cluster.job_entry" "args" ["service"] "revision" "rev-1" "once" False
             "runtimeEnv" env-json})
  (val spec (declared-job-spec wire))
  (assert (= spec.revision (+ ENV-KEY-PREFIX key)) spec)
  (assert (= (json.loads spec.runtime-env) env-json) spec)
  (val plain (declared-job-spec (| wire {"runtimeEnv" None})))
  (assert (and (= plain.revision "rev-1") (is plain.runtime-env None)) plain))


(deftest test-the-entry-probe-of-an-env-job-runs-in-the-root-venv [tmp-path]
  ;; 入口の検め: env の job は子と同じ起こし方(root の venv の uv run・PYTHONPATH を置かない・cwd は空の dir)で撃つ。worker の venv で
  ;; 検めると、root に無い module を worker の venv が読めて誤って通る。
  (<- declared-env RuntimeEnv (sample-env))
  (<- env-json dict (runtime-env->json declared-env))
  (val spec (JobSpec "quiet" "doeff_cluster.job_entry" #("service" "--factory" "app.jobs:program" "--env" "app.jobs:env")
                     "env-k" :runtime-env (json.dumps env-json :sort-keys True)))
  ;; probe-dir は既に在る dir(mkdir が何も作らない — argv の形だけを見る)。
  (val probes (ProbeStore "/worker/bin/hy" :uv "/bin/uv" :probe-dir (str tmp-path)))
  (val command (.command probes (ProbeEntry spec "/state/roots/env-k")))
  (val argv (get command 0))
  (val cwd (get command 1))
  (val environment (get command 2))
  (assert (= (cut argv 0 6) ["/bin/uv" "run" "--no-sync" "--frozen" "--project" "/state/roots/env-k/app"]) argv)
  (assert (in "app.jobs:program" argv) argv)
  (assert (not-in "/worker/bin/hy" argv) "worker の hy では検めない")
  (assert (not-in "PYTHONPATH" environment) "PYTHONPATH を置かない")
  (assert (= cwd (str probes.probe-dir)) cwd))


(deftest test-a-job-reads-its-own-runtime-env-from-the-context
  ;; 子がさらに送る task の既定の env: 文脈の宣言を読む(無ければ None — 今の revision の形)。
  (<- declared-env RuntimeEnv (sample-env))
  (<- env-json dict (runtime-env->json declared-env))
  (<- own (| RuntimeEnv None) (runtime-env-of-context (RunContext "http://c" "w" "env-k" "quiet"
                                                                  :runtime-env (json.dumps env-json))))
  (assert (= own declared-env) own)
  (<- none (| RuntimeEnv None) (runtime-env-of-context (RunContext "http://c" "w" "rev" "quiet")))
  (assert (is none None)))


;; --- 丁寧な模擬 ---------------------------------------------------------------------------------

(val SERVICE-MODULE
  (+ "(require doeff-hy.macros [defk <-])\n(import os)\n(import pathlib [Path])\n(import appjobs [current-value])\n"
     ";; service の本体: この commit の値(参照で呼ぶ関数)と env のキー・worker の pid・venv の prefix を out に書いて終わる。\n"
     "(defk report-service [out]\n  {:pre [(: out str)] :post [(: % int)]}\n"
     "  (.write-text (Path out) (.join \"\\n\" [(str (current-value)) (os.environ.get \"DOEFF_RUNTIME_ENV_KEY\" \"\")\n"
     "                                      (os.environ.get \"DOEFF_WORKER_PID\" \"\") (str (os.environ.get \"PYTHONPATH\"))\n"
     "                                      (os.getcwd)]) :encoding \"utf-8\")\n"
     "  0)\n"))


(defk service-files [value]
  {:pre [(: value int)] :post [(: % dict)]}
  "project の repo の中身: test_env_careful の app に service の本体の module を足した物。"
  (<- files dict (app-files value LOCK))
  (| files {"appservice.hy" SERVICE-MODULE}))


(defk run-service [rig env out probes]
  {:pre [(: rig Rig) (: env RuntimeEnv) (: out Path) (: probes ProbeStore)] :post [(: % list)]}
  "service 1 本を宣言から env の root で起こして終わるまで待つ(宣言 → coordinator → heartbeat の返事 → worker の JobSpec → 準備 →
   入口の検め → 子)。答え = service が out に書いた行。"
  (import appservice)
  (val declared (service "reporter" appservice.report-service :env "appjobs:env" :config {"out" (str out)}))
  (val row (get (system-declaration (System "lab" #(declared)) "rev" :runtime-env env) 0))
  (val spec (declared-job-spec (spec-json (. (job-from-json row) spec))))
  (<- view (prepare rig env))
  (assert (= view.state CodeState.READY) view)
  (assert (= spec.revision view.revision) "worker の置き場の鍵は準備した root と同じ env のキー")
  (.start probes (ProbeEntry spec view.path))
  (val deadline (+ (time.monotonic) DEADLINE-SECONDS))
  (var probed None)
  (while (is probed None)
    (when (> (time.monotonic) deadline) (raise (AssertionError "入口の検めが終わらない")))
    (for [p (.observe probes)]
      (when (!= p.state ProbeState.RUNNING) (:= probed p)))
    (when (is probed None) (time.sleep 0.1)))
  (assert (= probed.state ProbeState.PASSED) probed)
  (.start rig.host (StartJob spec 1 view.path))
  (var ended None)
  (while (is ended None)
    (when (> (time.monotonic) deadline) (raise (AssertionError "service が終わらない")))
    (for [p (.observe rig.host)]
      (when (and (= p.name spec.name) (is-not p.exit-code None)) (:= ended p)))
    (when (is ended None) (time.sleep 0.1)))
  (.reap rig.host (ReapJob spec.name ended.pid Outcome.EXITED ended.exit-code))
  (assert (.is-file out) (.format "service が書かなかった(終了 {})— log: {}" ended.exit-code
                                  (.read-text (next (.glob (/ rig.state "logs") "reporter*")) :errors "replace")))
  (.splitlines (.read-text out :encoding "utf-8")))


(deftest test-careful-a-service-runs-in-the-env-root-and-follows-a-new-commit [tmp-path monkeypatch]
  (.setenv monkeypatch "PYTHONDONTWRITEBYTECODE" "1")
  (<- rig Rig (make-rig tmp-path))
  (<- a1 str (push-commit rig.app (! (service-files 1)) "app 1"))
  (<- l1 str (push-commit rig.lib {"native/core/lib.rs" "fn a() {}\n" "native/core/Cargo.toml" "[package]\n"} "lib 1"))
  (.insert sys.path 0 (str rig.app))
  (val probes (ProbeStore HY :uv (str (/ rig.fake "uv")) :probe-dir (str (/ rig.state "probe"))))
  ;; 1 回目: 宣言の root で起こす
  (<- env-1 RuntimeEnv (declare rig a1 l1 LOCK))
  (<- lines-1 list (run-service rig env-1 (/ tmp-path "out-1") probes))
  (<- key-1 str (env-key env-1 (current-platform)))
  (assert (= (get lines-1 0) "1") lines-1)
  (assert (= (get lines-1 1) key-1) "service は宣言の env の root で走った")
  (assert (= (get lines-1 3) "None") "子に PYTHONPATH が無い")
  (assert (= (get lines-1 4) (str (.work-dir rig.host "reporter"))) "子の cwd は空の作業 dir")
  ;; 2 回目: 送り手の commit だけ変えた宣言 → 新しい root の source の値・worker の process は同じ
  (<- a2 str (push-commit rig.app (! (service-files 2)) "app 2"))
  (<- env-2 RuntimeEnv (declare rig a2 l1 LOCK))
  (<- lines-2 list (run-service rig env-2 (/ tmp-path "out-2") probes))
  (<- key-2 str (env-key env-2 (current-platform)))
  (assert (= (get lines-2 0) "2") "新しい commit の root の source の値")
  (assert (and (= (get lines-2 1) key-2) (!= key-2 key-1)) "新しい env のキーの root で走った")
  (assert (= (get lines-2 2) (get lines-1 2) (str (os.getpid))) "worker の process は同じ(再起動していない)"))
