;; 常駐の service を実行環境(runtime env)の root で起こす(2026-09-26・設計 worker-runtime-env.md 節 6 の順 3.5)。
;;
;; 前は runtimeEnv を運ぶのは task だけで、service の路(宣言 → coordinator の ClusterJob → heartbeat の返事 → worker → 入口の検め)には
;; 欄が無く、service は image の venv の doeff で動き続けた(doeff を変えるには image を作り直すしかない)。
;;
;; 速い検: 宣言の行が runtimeEnv を運ぶ・image の版を追う欄(baseFrom)を持つ行を断る・coordinator が spec と heartbeat の返事に載せる・worker が版を
;;        env のキーへ置き換える・入口の検めを root の venv で撃つ・子の文脈から自分の env を読む。
;; 丁寧な模擬(test_env_careful と同じ世界 — 本物の git・fake の uv・本物の root の言い換え env-host と、検めと子 process の言い換え〔probe-host・process-host〕): service を宣言から
;;        env の root で起こし、送り手の commit だけ変えた 2 回目の宣言で新しい root の source の値が返り、worker の process は同じ。
(require doeff-hy.macros [deftest defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import inspect)
(import json)
(import os)
(import sys)
(import time)
(import pathlib [Path])
(import pytest)
(import doeff [run])
(import dataclasses [replace])
(import doeff_cluster.shared.intent.runtime_env_model [RepoCheckout PythonProject RuntimeEnv EnvVar])
(import doeff_cluster.shared.core.runtime_env_rules [runtime-env->json env-key])
(import doeff_cluster.shared.core.native_wheel [current-platform])
(import doeff_cluster.shared.entry.service_build [job resolve system-of system-declaration])
(import doeff_cluster.shared.intent.service_model [CallShape System])
(import doeff_cluster.foundation.process_versions [process-versions])
(import doeff_cluster.coordinator.core.cluster_policy [job-from-json job-to-json])
(import doeff_cluster.coordinator.protocol.replies [spec-json])
(import doeff_cluster.worker.protocol.declared [declared-job-spec] doeff_cluster.worker.core.launch [program-file JobLaunch] doeff_cluster.worker.core.probe_rules [probe-targets probe-command])
(import doeff_cluster.worker.intent.worker_model [CodeLayout ProbeView WorkerPolicy])
(import doeff_cluster.worker.core.shim_timing [shim-spans])
(import doeff_cluster.worker.protocol.probes [ProbeSettings])
(import tests.probe_rig [probe-settings run-probes observed])
(import doeff_cluster.shared.intent.run_context [RunContext])
(import doeff_cluster.shared.core.run_context_rules [runtime-env-of-context])
(import doeff_cluster.worker.intent.worker_model [CodeView ProbeEntry ProbeState StartJob ReapJob Outcome CodeState] doeff_cluster.shared.intent.job_model [JobSpec] doeff_cluster.shared.core.job_rules [spec-hash] doeff_cluster.worker.core.worker_rules [ENV-KEY-PREFIX code-key])
(import tests.careful_rig [Rig make-rig push-commit app-files declare prepare LOCK HY DEADLINE-SECONDS])
(import tests.host_rig [job-ended run-on-host])
(import doeff_cluster.worker.protocol.process_host [job-work-dir])

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


(defk quiet-system [update readiness environ]
  {:pre [(: update str) (: readiness (| dict None)) (: environ dict)] :post [(: % System)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "速い検の系(job quiet 1 つ — defsystem の展開と同じ呼び方で作る)。"
  (system-of "lab" #((job "quiet" (quiet-program 1.0) :call (CallShape :function quiet-program :args [1.0] :kwargs {}) :replicas 1
                          :needs #{"net"} :update update :readiness readiness :environ environ))))


(deftest test-the-declaration-carries-the-runtime-env-and-one-source-of-child-env-vars
  ;; 宣言の行が runtimeEnv を運ぶ。Program の job は image の版を追う base-from を持たない(job に欄が無い・行の baseFrom は coordinator が
  ;; 断る — 下の検)。子の環境変数の足し口は 1 つ: 実行環境の env-vars と :environ に同じ名が在れば宣言の時点で断る(改訂 1 の G)。
  (<- declared-env RuntimeEnv (sample-env))
  (<- plain System (quiet-system "recreate" None {}))
  (val rows (. (system-declaration plain "rev-1" :versions (! (process-versions os.environ)) :runtime-env declared-env) rows))
  (<- env-json dict (runtime-env->json declared-env))
  (assert (= (get (get rows 0) "runtimeEnv") env-json) rows)
  (assert (not-in "runtimeEnv" (get (. (system-declaration plain "rev-1" :versions (! (process-versions os.environ))) rows) 0)) "env を渡さない宣言は今の形のまま")
  ;; job は image の版を追う base-from を受けない — 渡せる名の一覧に base_from が無いことを直に確かめる(わざと誤った呼び出しを書かない)。
  (assert (not-in "base_from" (. (inspect.signature job) parameters)) "job は base-from を受けない")
  (val with-vars (replace declared-env :env-vars #((EnvVar :name "POLL" :value "1"))))
  (<- clashing System (quiet-system "recreate" None {"POLL" "2"}))
  (with [raised (pytest.raises ValueError)]
    (system-declaration clashing "rev-1" :versions (! (process-versions os.environ)) :runtime-env with-vars))
  (assert (in "POLL" (str raised.value)))
  ;; coordinator も同じ重なりを行で断る(declare を通らない行 — 資源の口へ直に書かれた行)。
  (<- apart System (quiet-system "recreate" None {"POLL" "2"}))
  (val row (get (. (system-declaration apart "rev-1" :versions (! (process-versions os.environ))) rows) 0))
  (<- vars-json dict (runtime-env->json with-vars))
  (with [raised (pytest.raises ValueError)]
    (job-from-json (| row {"runtimeEnv" vars-json})))
  (assert (in "POLL" (str raised.value))))


(deftest test-the-coordinator-carries-the-runtime-env-to-the-worker
  ;; coordinator: 宣言の runtimeEnv を JobSpec の比べる欄に持ち、spec の JSON と heartbeat の返事に載せる。image の版を追う欄
  ;; (baseFrom — 係ごと消した)を持つ行と読めない宣言は断る。
  (<- declared-env RuntimeEnv (sample-env))
  (<- env-json dict (runtime-env->json declared-env))
  (<- plain System (quiet-system "recreate" None {}))
  (val row (get (. (system-declaration plain "rev-1" :versions (! (process-versions os.environ)) :runtime-env declared-env) rows) 0))
  (val job (job-from-json row))
  (assert (is-not job.spec.runtime-env None) job.spec)
  (assert (= (json.loads job.spec.runtime-env) env-json) job.spec)
  (assert (= (get (job-to-json job) "runtimeEnv") env-json) "coordinator の状態に残る(読み戻しで同じ宣言)")
  (assert (= (job-from-json (job-to-json job)) job) "読み戻しで同じ job")
  (val wire (! (spec-json job.spec)))
  (assert (= (get wire "runtimeEnv") env-json) wire)
  (assert (not-in "runtimeEnv" (! (spec-json (. (job-from-json (| row {"runtimeEnv" None})) spec)))) "env の無い job の返事は今の形")
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
  (val wire {"name" "quiet" "entry" "doeff_cluster.worker.entry.job_entry" "args" ["service"] "revision" "rev-1" "once" False
             "runtimeEnv" env-json})
  (<- spec (declared-job-spec wire False))
  ;; 版は宣言のまま(coordinator の版と同じ)・root の置き場の鍵は worker が計算した env のキー。
  (assert (= spec.revision "rev-1") spec)
  (assert (= spec.env-key key) spec)
  (assert (= (code-key spec) (+ ENV-KEY-PREFIX key)) spec)
  (assert (is-not spec.runtime-env None) spec)
  (assert (= (json.loads spec.runtime-env) env-json) spec)
  (<- plain (declared-job-spec (| wire {"runtimeEnv" None}) False))
  (assert (and (= plain.revision "rev-1") (is plain.runtime-env None)) plain))


(deftest test-the-coordinator-and-the-worker-agree-on-the-env-service
  ;; coordinator は今の宣言から計算した版と指紋(spec-hash)で、worker の報告が今の宣言の process の物かを判じる(readiness・handoff の
  ;; ready-instance)。worker が版を env のキーへ置き換えると両者が食い違い、env の service は Ready と数えられない(2026-09-26 の構成
  ;; レビューで見つけた欠陥)。版と指紋は両側で同じ・root の鍵だけが worker の中の値。
  (<- declared-env RuntimeEnv (sample-env))
  (<- handoff System (quiet-system "handoff" {"windowSeconds" 30} {"POLL" "5.0"}))
  (val row (get (. (system-declaration handoff "rev-1" :versions (! (process-versions os.environ)) :runtime-env declared-env) rows) 0))
  (val coordinator-spec (. (job-from-json row) spec))
  (<- worker-spec (declared-job-spec (! (spec-json coordinator-spec)) False))
  (assert (= worker-spec.revision coordinator-spec.revision) #(worker-spec coordinator-spec))
  (assert (= (spec-hash worker-spec) (spec-hash coordinator-spec)) "指紋が同じ(coordinator が worker の報告を今の宣言の物と数える)")
  (assert (= worker-spec coordinator-spec) "比べる欄が同じ(worker が同じ宣言で process を起こし直さない)"))


(deftest test-the-entry-probe-of-an-env-job-runs-in-the-root-venv [tmp-path]
  ;; 入口の検め: env の job は子と同じ起こし方(root の venv の uv run・PYTHONPATH を置かない・cwd は空の dir)で撃つ。worker の venv で
  ;; 検めると、root に無い module を worker の venv が読めて誤って通る。
  (<- declared-env RuntimeEnv (sample-env))
  (<- env-json dict (runtime-env->json declared-env))
  (val spec (JobSpec "quiet" "doeff_cluster.worker.entry.job_entry" #("service" "--identity" "0123456789abcdef")
                     "env-k" :runtime-env (json.dumps env-json :sort-keys True) :program (* "a" 64)))
  ;; 検めの子の起こし方の判断(probe-command — #2465)を、子を起こさずに見る。
  (<- plan JobLaunch (probe-command "/state/roots/env-k" spec.runtime-env (tuple (probe-targets spec)) :hy-command "/worker/bin/hy" :uv "/bin/uv"
                                    :layout (CodeLayout) :allowed-env {} :probe-dir (str tmp-path)))
  (val argv (list plan.argv))
  (val cwd plan.cwd)
  (val environment (dfor e plan.env e.name e.value))
  (assert (= (cut argv 0 6) ["/bin/uv" "run" "--no-sync" "--frozen" "--project" "/state/roots/env-k/app"]) argv)
  ;; Program の job の検めは入口の module の import だけ(詰めた Program の版と復元は起こした子が検める)。
  (assert (= (probe-targets spec) ["doeff_cluster.worker.entry.job_entry"]))
  (assert (in "doeff_cluster.worker.entry.job_entry" argv) argv)
  (assert (not-in "/worker/bin/hy" argv) "worker の hy では検めない")
  (assert (not-in "PYTHONPATH" environment) "PYTHONPATH を置かない")
  (assert (= cwd (str tmp-path)) cwd))


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


(defk probe-entered [spec code-path]
  {:pre [(: spec JobSpec) (: code-path str)] :post [(: % ProbeView)] :tags {:context "doeff-cluster-test" :role "program"}}
  "入口の検めを積み、答えを返すため。"
  (<- (ProbeEntry spec code-path))
  (<- view ProbeView (observed spec))
  view)


(defk run-service [rig env out probes]
  {:pre [(: rig Rig) (: env RuntimeEnv) (: out Path) (: probes ProbeSettings)] :post [(: % list)]}
  "service 1 本を宣言から env の root で起こして終わるまで待つ(宣言 → coordinator → heartbeat の返事 → worker の JobSpec → 準備 →
   入口の検め → 子)。答え = service が out に書いた行。"
  ;; appservice はこの検が env の root に文字列から書き出す利用者の app(型検査の時には無い module)なので、declare と同じ口 resolve で
  ;; `module:attr` の名から関数を引く。
  (val report-service (resolve "appservice:report_service"))
  (val declared (job "reporter" (report-service (str out))
                     :call (CallShape :function report-service :args [(str out)] :kwargs {}) :replicas 1
                     :needs #{"net"}))
  (val declaration (system-declaration (system-of "lab" #(declared)) "rev" :versions (! (process-versions os.environ)) :runtime-env env))
  (val row (get declaration.rows 0))
  (<- spec (declared-job-spec (! (spec-json (. (job-from-json row) spec))) False))
  ;; worker が /programs/<sha> から取って置くのと同じ file(coordinator への口の fetched-programs の形)を子 process の言い換えが読む cache に置く。
  (assert (is-not spec.program None) spec)
  (val cached (program-file (Path rig.host.program-dir) spec.program spec.versions))
  (.mkdir cached.parent :parents True :exist-ok True)
  (.write-text cached (json.dumps {"blob" (get declaration.programs spec.program) "versions" (get row "run" "versions")})
               :encoding "utf-8")
  (<- view CodeView (prepare rig env))
  (assert (= view.state CodeState.READY) view)
  (assert (is-not view.path None) view)
  (assert (= (code-key spec) view.revision) "worker の置き場の鍵は準備した root と同じ env のキー")
  (val deadline (+ (time.monotonic) DEADLINE-SECONDS))
  ;; 入口の検めを probe-host と本物の答え手の下で回す(この spec の答えだけを見る — observed は spec-hash で引く)。
  (<- probed ProbeView (run-probes probes (probe-entered spec view.path)))
  (assert (= probed.state ProbeState.PASSED) probed)
  (val ended (! (run-on-host rig.host (job-ended spec view.path (max 1.0 (- deadline (time.monotonic)))))))
  (assert (.is-file out) (.format "service が書かなかった(終了 {})— log: {}" ended.exit-code
                                  (.read-text (next (.glob (/ rig.state "logs") "reporter*")) :errors "replace")))
  (.splitlines (.read-text out :encoding "utf-8")))


(deftest test-careful-a-service-runs-in-the-env-root-and-follows-a-new-commit [tmp-path subprocess-bytecode job-child-code-store]
  (<- rig Rig (make-rig tmp-path))
  (<- a1 str (push-commit rig.app (! (service-files 1)) "app 1"))
  (<- l1 str (push-commit rig.lib {"native/core/lib.rs" "fn a() {}\n" "native/core/Cargo.toml" "[package]\n"} "lib 1"))
  (.insert sys.path 0 (str rig.app))
  (val probes (ProbeSettings :python sys.executable :hy-command HY :uv (str (/ rig.fake "uv")) :layout (CodeLayout)
                             :probe-dir (str (/ rig.state "probe")) :shim (! (shim-spans (WorkerPolicy)))))
  ;; 1 回目: 宣言の root で起こす
  (<- env-1 RuntimeEnv (declare rig a1 l1 LOCK))
  (<- lines-1 list (run-service rig env-1 (/ tmp-path "out-1") probes))
  (<- key-1 str (env-key env-1 (current-platform)))
  (assert (= (get lines-1 0) "1") lines-1)
  (assert (= (get lines-1 1) key-1) "service は宣言の env の root で走った")
  (assert (= (get lines-1 3) "None") "子に PYTHONPATH が無い")
  (<- work-1 str (job-work-dir rig.host "reporter"))
  (assert (= (get lines-1 4) work-1) "子の cwd は空の作業 dir")
  ;; 2 回目: 送り手の commit だけ変えた宣言 → 新しい root の source の値・worker の process は同じ
  (<- a2 str (push-commit rig.app (! (service-files 2)) "app 2"))
  (<- env-2 RuntimeEnv (declare rig a2 l1 LOCK))
  (<- lines-2 list (run-service rig env-2 (/ tmp-path "out-2") probes))
  (<- key-2 str (env-key env-2 (current-platform)))
  (assert (= (get lines-2 0) "2") "新しい commit の root の source の値")
  (assert (and (= (get lines-2 1) key-2) (!= key-2 key-1)) "新しい env のキーの root で走った")
  (assert (= (get lines-2 2) (get lines-1 2) (str (os.getpid))) "worker の process は同じ(再起動していない)"))
