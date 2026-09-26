;; 実行環境の丁寧な模擬の世界(本物の git の bare repo と file:// の URL・PATH の先頭の fake の uv・本物の EnvStore と ProcessHost)を組む道具。
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
(import doeff_cluster.runtime_env_model [RepoCheckout NativeWheel PythonProject RuntimeEnv EnvFailureKind
                                         runtime-env->json env-key current-platform])
(import doeff_cluster.runtime_env [LocalCheckout ProjectOfCheckout runtime-env-of-checkouts local-checkouts])
(import doeff_cluster.env_prepare [ENV-MARKER ROOTS-PTH])
(import doeff_cluster.handlers [EnvStore ProcessHost task-spec])
(import doeff_cluster.worker_model [CodeState CodeView StartJob ReapJob Outcome WorldView WorkerPolicy PrepareEnv WarmEnv
                                    code-key])
(import doeff_cluster.worker_policy [plan])
(import doeff_cluster.remote_model [encode-program decode-outcome current-versions TaskSucceeded TaskFailed])

(val FIXTURES (/ (. (Path __file__) (resolve) parent) "fixtures"))
(val HY (str (/ (. (Path sys.executable) parent) "hy")))
(val LOCK "httpx==0.28.1\nclick==8.1.8\n")
(val DEADLINE-SECONDS 180)
(val JOB-ENV "appjobs:env")


;; --- 検の世界 -----------------------------------------------------------------------------

(defk git [cwd #* args]
  {:pre [(: cwd Path) (: args tuple)] :post [(: % str)]}
  "検の repo を作るために git を 1 回呼ぶ。"
  (val done (subprocess.run ["git" "-C" (str cwd) "-c" "user.name=t" "-c" "user.email=t@example.invalid" #* args]
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
  "丁寧な模擬の 1 組: 検の dir・worker の state・fake の uv の dir・準備の EnvStore・子の ProcessHost・remote の checkout。"
  (#^ Path base)
  (#^ Path state)
  (#^ Path fake)
  (#^ EnvStore envs)
  (#^ ProcessHost host)
  (#^ Path app)
  (#^ Path lib))


(defk make-rig [base [min-free-bytes 0] [allowed None]]
  {:pre [(: base Path) (: min-free-bytes int) (: allowed (| tuple None))] :post [(: % Rig)]}
  "worker の組(EnvStore・ProcessHost)と、fake の uv を PATH の先頭に置く包みと、app と lib の remote を作る。"
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
  (Rig :base base :state state :fake fake :app app :lib lib
       :envs (EnvStore (str state) HY :repo-keys (str keys) :uv (str wrapper) :min-free-bytes min-free-bytes)
       ;; 検の子 process は checkout の中に bytecode を書かない(root の中に準備した bytecode は読むだけ)。
       :host (ProcessHost (str (/ state "logs")) HY {"DOEFF_WORKER_NAME" "careful" "PYTHONDONTWRITEBYTECODE" "1"}
                          :uv (str wrapper))))


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


(defk prepare [rig env]
  {:pre [(: rig Rig) (: env RuntimeEnv)] :post [(: % CodeView)]}
  "worker と同じ口(EnvStore.start)で root を準備し、READY か FAILED の観測(CodeView)まで待つ。"
  (<- key str (env-key env (current-platform)))
  (<- declared dict (runtime-env->json env))
  (.start rig.envs (+ "env-" key) (json.dumps declared :sort-keys True :ensure-ascii False))
  (val deadline (+ (time.monotonic) DEADLINE-SECONDS))
  (var found None)
  (while (is found None)
    (when (> (time.monotonic) deadline) (raise (AssertionError (.format "準備が {} 秒で終わらない" DEADLINE-SECONDS))))
    (for [view (.observe rig.envs)]
      (when (and (= view.revision (+ "env-" key)) (in view.state #(CodeState.READY CodeState.FAILED)))
        (:= found view)))
    (when (is found None) (time.sleep 0.2)))
  found)


(defk run-task [rig env task-id [versions None] [host None]]
  {:pre [(: rig Rig) (: env RuntimeEnv) (: task-id str) (: versions (| dict None)) (: host (| ProcessHost None))]
   :post [(: % (| TaskSucceeded TaskFailed))]}
  "準備済みの root で task を 1 本走らせる(worker の task-spec → ProcessHost の子 process → 結果の file)。答え = TaskSucceeded / TaskFailed。"
  (import appjobs)
  (<- declared dict (runtime-env->json env))
  (val tasks (/ rig.state "tasks"))
  (.mkdir tasks :parents True :exist-ok True)
  (.write-text (/ tasks (+ task-id ".blob")) (encode-program (appjobs.report)) :encoding "ascii")
  (val spec (task-spec {"id" task-id "env" JOB-ENV "revision" "" "versions" (or versions (current-versions)) "blob" ""
                        "runtimeEnv" declared}
                       tasks))
  (<- key str (env-key env (current-platform)))
  (val using (or host rig.host))
  (.start using (StartJob spec 1 (str (.root-of rig.envs (+ "env-" key)))))
  (val deadline (+ (time.monotonic) DEADLINE-SECONDS))
  (var ended None)
  (while (is ended None)
    (when (> (time.monotonic) deadline) (raise (AssertionError "task が終わらない")))
    (for [view (.observe using)]
      (when (and (= view.name spec.name) (is-not view.exit-code None)) (:= ended view)))
    (when (is ended None) (time.sleep 0.1)))
  (.reap using (ReapJob spec.name ended.pid Outcome.EXITED ended.exit-code))
  (val result (/ tasks (+ task-id ".result")))
  (assert (.is-file result) (.format "子が結果を書かなかった(終了 {})— log: {}" ended.exit-code
                                     (.read-text (next (.glob (/ rig.state "logs") (+ "task_" task-id "*"))) :errors "replace")))
  (decode-outcome (.read-text result :encoding "ascii")))


