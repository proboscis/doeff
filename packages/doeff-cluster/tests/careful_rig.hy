;; 実行環境の丁寧な模擬の世界(本物の git の bare repo と file:// の URL・PATH の先頭の fake の uv・本物の root の言い換え env-host と
;; 子 process の言い換え process-host)を組む道具。
;; test_env_careful.hy と test_service_env.hy が共有する(test の名でない module に置く — test の module を別の検から import すると、
;; pytest の書き換えの hook がそれを Python として読もうとして、走る順によって収集が落ちる)。
(require doeff-hy.macros [deftest defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import dataclasses [replace])
(import hashlib)
(import json)
(import os)
(import shutil)
(import subprocess)
(import sys)
(import time)
(import pathlib [Path])
(import doeff [run with-handlers])
(import doeff_core_effects.handlers [slog-handler state])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_time [sync-time-handler])
(import doeff_cluster.shared.intent.runtime_env_model [RepoCheckout NativeWheel PythonProject RuntimeEnv EnvFailureKind])
(import doeff_cluster.shared.core.runtime_env_rules [runtime-env->json env-key current-platform])
(import doeff_cluster.shared.intent.checkout_model [LocalCheckout ProjectOfCheckout])
(import doeff_cluster.shared.core.runtime_env [runtime-env-of-checkouts])
(import doeff_cluster.shared.protocol.checkout_reads [checkout-reads])
(import doeff_cluster.worker.intent.env_prepare_model [ROOTS-PTH] doeff_cluster.shared.intent.env_marker_model [ENV-MARKER])
(import doeff_cluster.shared.intent.service_model [resolve])
(import doeff_cluster.handlers [task-spec write-program-file])
(import doeff_cluster.worker.protocol.code_store [PREPARE-TOOL])
(import doeff_cluster.worker.protocol.env_store [EnvSettings env-host env-root])
(import doeff_cluster.worker.protocol.process_host [HostSettings])
(import tests.host_rig [host-settings job-ended run-on-host])
(import doeff_cluster.worker.intent.worker_model [CodeState CodeView StartJob ReapJob Outcome WorldView WorkerPolicy PrepareEnv WarmEnv
] doeff_cluster.worker.core.worker_rules [code-key])
(import doeff_cluster.worker.protocol.observations [ObserveEnvs])
(import doeff_cluster.worker.core.policy [plan])
(import doeff_cluster.shared.intent.remote_model [TaskSucceeded TaskFailed])
(import doeff_cluster.shared.protocol.program_codec [encode-program decode-outcome])
(import doeff_cluster.shared.core.remote_rules [program-sha])
(import doeff_cluster.foundation.process_versions [current-versions])

(val FIXTURES (/ (. (Path __file__) (resolve) parent) "fixtures"))
(val HY (str (/ (. (Path sys.executable) parent) "hy")))
(val LOCK "httpx==0.28.1\nclick==8.1.8\n")
(val DEADLINE-SECONDS 180)
(val JOB-ENV "appjobs:env")


;; --- 検の世界 -----------------------------------------------------------------------------

(defk git [cwd #* args]
  {:pre [(: cwd Path) (: args tuple)] :post [(: % str)]}
  "検の repo を作るために git を 1 回呼ぶ。"
  (val words (lfor a args :if (isinstance a str) a))
  (assert (= (len words) (len args)) #("子 process の引数は文字列だけ" args))
  (val done (subprocess.run ["git" "-C" (str cwd) "-c" "user.name=t" "-c" "user.email=t@example.invalid" #* words]
                            :capture-output True :text True :check True))
  (.strip done.stdout))


(defk push-commit [work files message]
  {:pre [(: work Path) (: files dict) (: message str)] :post [(: % str)]}
  "work の checkout に files(相対 path → 中身)を書いて commit し、remote へ push する。答え = commit の sha。"
  (for [#(rel text) (.items files)]
    (val path (/ work rel))
    (.mkdir path.parent :parents True :exist-ok True)
    (.write-text path text :encoding "utf-8"))
  (<- (git work "add" "-A"))
  (<- (git work "commit" "-q" "--allow-empty" "-m" message))
  (<- (git work "push" "-q" "origin" "HEAD:main"))
  (<- sha str (git work "rev-parse" "HEAD"))
  sha)


(defk remote-repo [base name]
  {:pre [(: base Path) (: name str)] :post [(: % Path)]}
  "bare の remote と、それを clone した作業の checkout を作る。答え = checkout の path(remote の URL は file:// + <name>.git)。"
  (val remote (/ base "remotes" (+ name ".git")))
  (val work (/ base "work" name))
  (.mkdir remote.parent :parents True :exist-ok True)
  (.mkdir work.parent :parents True :exist-ok True)
  (<- (git base "init" "-q" "--bare" (str remote)))
  (<- (git base "clone" "-q" (+ "file://" (str remote)) (str work)))
  work)


(defk url-of [base name]
  {:pre [(: base Path) (: name str)] :post [(: % str)]}
  "検の remote の URL。"
  (+ "file://" (str (/ base "remotes" (+ name ".git")))))


(defk app-files [value lock]
  {:pre [(: value int) (: lock str)] :post [(: % dict)]}
  "project の repo の中身: uv の project(pyproject・uv.lock)と、送る Program の module(返す値が commit で違う)。"
  {"pyproject.toml" "[project]\nname = \"app\"\nversion = \"0\"\n"
   "uv.lock" lock
   "appjobs.hy" (+ "(require doeff-hy.macros [defk <-])\n(import os sys)\n"
                   (.format "(setv VALUE {})\n" value)
                   ";; この commit の値。cloudpickle は module の属性の関数を参照で運ぶので、子は root の中のこの関数を呼ぶ\n"
                   ";; (defk の本体は値で運ばれ、本体が直に読む module の定数は送り手の値で固まる — だから本体は VALUE を直に読まない)。\n"
                   "(defn current-value [] VALUE)  ; defk にできない: cloudpickle が参照で運ぶ素の関数の見本\n"
                   ";; 送る Program: この commit の値と、子の環境(PYTHONPATH・cwd・env のキー・worker の pid・interpreter)を返す。\n"
                   "(defk report []\n  {:pre [] :post [(: % tuple)]}\n"
                   "  #((current-value) (os.environ.get \"PYTHONPATH\") (os.getcwd) (os.environ.get \"DOEFF_RUNTIME_ENV_KEY\")\n"
                   "    (os.environ.get \"DOEFF_WORKER_PID\") sys.prefix))\n"
                   ";; 実行先の handler の組(空)。\n"
                   "(defn env [config ctx] [])  ; defk にできない: job_entry が Program の外で呼ぶ組み立ての関数\n")
   "docs/readme.md" "doc\n"})


(defrecord Rig
  "丁寧な模擬の 1 組: 検の dir・worker の state・fake の uv の dir・root の準備の設定(envs — env-host・#2467)・子 process の言い換えの
   設定(host — process-host・#2464)・remote の checkout。"
  (#^ Path base)
  (#^ Path state)
  (#^ Path fake)
  (#^ EnvSettings envs)
  (#^ HostSettings host)
  (#^ Path app)
  (#^ Path lib))


(defk make-rig [base [min-free-bytes 0] [allowed None]]
  {:pre [(: base Path) (: min-free-bytes int) (: allowed (| tuple None))] :post [(: % Rig)]}
  "worker の組(root の準備と子 process の言い換えの設定)と、fake の uv を PATH の先頭に置く包みと、app と lib の remote を作る。"
  (val fake (/ base "fake-uv"))
  (.mkdir fake :parents True)
  (val site (next (gfor p sys.path :if (.endswith p "site-packages") p)))
  (val wrapper (/ fake "uv"))
  (.write-text wrapper (+ "#!/bin/sh\n"
                          (.format "export FAKE_UV_PYTHON={!r} FAKE_UV_SITE={!r} FAKE_UV_DIR={!r} PYTHONDONTWRITEBYTECODE=1\n"
                                   sys.executable site (str fake))
                          (.format "exec {!r} {!r} \"$@\"\n" HY (str (/ FIXTURES "fake_uv.hy")))))
  (os.chmod wrapper 0o755)
  (<- app Path (remote-repo base "app"))
  (<- lib Path (remote-repo base "lib"))
  (<- (remote-repo base "tools"))
  (val keys (/ base "repo-keys.json"))
  (<- app-url str (url-of base "app"))
  (<- lib-url str (url-of base "lib"))
  (<- tools-url str (url-of base "tools"))
  (.write-text keys (json.dumps (dfor u (or allowed #(app-url lib-url tools-url)) u "")))
  (val state (/ base "state"))
  ;; 検の子 process は checkout の中に bytecode を書かない(root の中に準備した bytecode は読むだけ)。
  (<- host (host-settings state :hy-command HY :extra-env {"DOEFF_WORKER_NAME" "careful" "PYTHONDONTWRITEBYTECODE" "1"}
                         :uv (str wrapper)))
  (Rig :base base :state state :fake fake :app app :lib lib
       :envs (EnvSettings :state (str state) :hy-command HY :platform (current-platform) :code-prepare PREPARE-TOOL :repo-keys (str keys)
                          :uv (str wrapper) :min-free-bytes min-free-bytes)
       :host host))


(defk declare [rig app-sha lib-sha lock [roots #("app/.")] [extra-repos #()]]
  {:pre [(: rig Rig) (: app-sha str) (: lib-sha str) (: lock str) (: roots tuple) (: extra-repos tuple)]
   :post [(: % RuntimeEnv)]}
  "app(project)と lib(native の source)の宣言。"
  (<- app-url str (url-of rig.base "app"))
  (<- lib-url str (url-of rig.base "lib"))
  (RuntimeEnv :repos (+ #((RepoCheckout :name "app" :url app-url :commit app-sha)
                          (RepoCheckout :name "lib" :url lib-url :commit lib-sha))
                        extra-repos)
              :project (PythonProject :repo "app" :path "." :lock-sha256 (.hexdigest (hashlib.sha256 (.encode lock)))
                                      :python "3.14"
                                      :native #((NativeWheel :package "lib-native" :repo "lib" :paths #("native/core"))))
              :import-roots roots))


(defk fake-log [rig]
  {:pre [(: rig Rig)] :post [(: % tuple)]}
  "fake の uv の log の行。"
  (val path (/ rig.fake "log"))
  (tuple (if (.is-file path) (.splitlines (.read-text path :encoding "utf-8")) [])))


(defk count-log [rig verb]
  {:pre [(: rig Rig) (: verb str)] :post [(: % int)]}
  "fake の uv の log のうち verb で始まる行の数(sync・build)。"
  (<- lines tuple (fake-log rig))
  (len (lfor line lines :if (.startswith line verb) line)))


(defk downloads [rig]
  {:pre [(: rig Rig)] :post [(: % int)]}
  "fake の uv が数えた download の合計。"
  (<- lines tuple (fake-log rig))
  (sum (gfor line lines :if (.startswith line "sync ") (int (get (.split line "downloads=") 1)))))


(defn #^ object run-envs [#^ EnvSettings settings #^ object program]  ; defk にできない: 検が Program の外から本物の答え手の組で 1 回走らせる入口
  "筋書きの Program を env-host と本物の答え手の下で 1 回の run で回す(with-handlers の並びは先頭が外側 — 準備の記録は外側の state が持つ)。"
  (run (with-handlers [(state) (sync-time-handler) slog-handler os-file-handler subprocess-handler (env-host settings)] program)))


(defk settled-env [key]
  {:pre [(: key str)] :post [(: % CodeView)]}
  "root の観測で key が READY か FAILED になるまで待つため(準備は起こさない・上限 DEADLINE-SECONDS)。"
  (val deadline (+ (time.monotonic) DEADLINE-SECONDS))
  (var found None)
  (while (is found None)
    (when (> (time.monotonic) deadline) (raise (AssertionError (.format "準備が {} 秒で終わらない" DEADLINE-SECONDS))))
    (<- views tuple (ObserveEnvs))
    (for [view views]
      (when (and (= view.revision key) (in view.state #(CodeState.READY CodeState.FAILED)))
        (:= found view)))
    (when (is found None) (time.sleep 0.2)))
  found)


(defk prepared-env [key text [times 1]]
  {:pre [(: key str) (: text str) (: times int)] :post [(: % CodeView)]}
  "worker と同じ口(PrepareEnv)で root の準備を times 回頼み、READY か FAILED の観測まで待つため。"
  (for [_ (range times)]
    (<- (PrepareEnv key text)))
  (<- view CodeView (settled-env key))
  view)


(defk prepare [rig env]
  {:pre [(: rig Rig) (: env RuntimeEnv)] :post [(: % CodeView)]}
  "worker と同じ口(PrepareEnv)で root を準備し、READY か FAILED の観測(CodeView)まで待つ。"
  (<- key str (env-key env (current-platform)))
  (<- declared dict (runtime-env->json env))
  (run-envs rig.envs (prepared-env (+ "env-" key) (json.dumps declared :sort-keys True :ensure-ascii False))))


(defk run-task [rig env task-id [versions None] [around #()]]
  {:pre [(: rig Rig) (: env RuntimeEnv) (: task-id str) (: versions (| dict None)) (: around tuple)]
   :post [(: % (| TaskSucceeded TaskFailed))]}
  "準備済みの root で task を 1 本走らせる(worker の task-spec → 子 process の言い換え process-host と本物の答え手 → 結果の file)。
   答え = TaskSucceeded / TaskFailed。詰めた Program は worker が置き場から取った cache と同じ形の file(programs の dir)に、送り手の版
   versions と一緒に置く。around = process-host と本物の答え手の間に置く handler(反例の壊した handler)。"
  ;; appjobs は rig が env の root に文字列から書き出す利用者の app(型検査の時には無い module)なので、declare と同じ口 resolve で
  ;; `module:attr` の名から関数を引く。
  (val report (resolve "appjobs:report"))
  (<- declared dict (runtime-env->json env))
  (val tasks (/ rig.state "tasks"))
  (.mkdir tasks :parents True :exist-ok True)
  (val blob (encode-program (report)))
  (val sha (program-sha blob))
  (write-program-file (Path rig.host.program-dir) sha blob (or versions (current-versions)))
  (val spec (task-spec {"id" task-id "revision" "" "versions" (or versions (current-versions)) "program" sha
                        "runtimeEnv" declared}
                       tasks))
  (<- key str (env-key env (current-platform)))
  (<- root str (env-root rig.envs (+ "env-" key)))
  (val ended (run-on-host rig.host (job-ended spec root DEADLINE-SECONDS) :around around))
  (val result (/ tasks (+ task-id ".result")))
  (assert (.is-file result) (.format "子が結果を書かなかった(終了 {})— log: {}" ended.exit-code
                                     (.read-text (next (.glob (/ rig.state "logs") (+ "task_" task-id "*"))) :errors "replace")))
  (decode-outcome (.read-text result :encoding "ascii")))


