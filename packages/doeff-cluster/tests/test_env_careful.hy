;; 実行環境(runtime env)の丁寧な模擬 — 本物の EnvStore(準備の process = env_handlers の local-env)・手元の bare repo と file:// の URL・
;; PATH の先頭の fake の uv(tests/fixtures/fake_uv.hy)・本物の ProcessHost(子 process と shim)・実時間。
;;
;; 筋書き(設計 worker-runtime-env.md 節 5):
;;   1 宣言 → 準備 → 実行: 結果が返る・子の環境変数に PYTHONPATH が無い・子の cwd が作業 dir・bytecode は root の venv の interpreter で作った
;;   2 送り手の repo の commit だけ変えて再送(返す値が commit で違う関数): 新しい値が返る・worker の pid が同じ・download は 0
;;   3 lock を変えて再送: 新しい env のキー・download が増える
;;   4 同じ lock・別の project の commit: 新しい root・download 0・native の build 0
;;   5 native の source を変える: native の build が 1 回だけ増え、次の root は wheel を使い回す
;;   6 同じ env の task を 2 本同時に: 準備は 1 本・2 本とも走る
;;   7 repo を 3 つに: 3 つのツリーが兄弟に並ぶ・import の根の順が宣言どおり
;;   8 先読み: 温める表の env を worker が job の前に準備し、task が来た最初の拍で子を起こす(準備を待たない)
;;   9 固定された root がある時に空きが下限を切る: 固定された root・project ごとの最新・worker が作っていない dir は残り、
;;     固定されていない古い root が消える
;; 反例: 子に PYTHONPATH を残す / worker の再起動で走らせる実装 / 根と同じ最上位の名の第三者の package / 送り手の版の doeff をずらす /
;;       節 3.6 の失敗の組(許可表に無い URL・push していない commit・lock の hash の 1 文字・fake の uv の失敗・空きが足りない)。
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


;; --- 筋書き ---------------------------------------------------------------------------------

(deftest test-careful-scenarios-1-to-5 [tmp-path monkeypatch]
  (.setenv monkeypatch "PYTHONDONTWRITEBYTECODE" "1")
  (<- rig Rig (make-rig tmp-path))
  (<- a1 str (push-commit rig.app (! (app-files 1 LOCK)) "app 1"))
  (<- l1 str (push-commit rig.lib {"native/core/lib.rs" "fn a() {}\n" "native/core/Cargo.toml" "[package]\n"} "lib 1"))
  (.insert sys.path 0 (str rig.app))
  ;; 1 宣言(送り手の組み立て — 本物の git の checkout から)→ 準備 → 実行
  (<- env-1 RuntimeEnv (local-checkouts (runtime-env-of-checkouts #((LocalCheckout :name "app" :path (str rig.app))
                                                                    (LocalCheckout :name "lib" :path (str rig.lib)))
                                                                  (ProjectOfCheckout :repo "app" :path "." :python "3.14"
                                                                                     :native #((NativeWheel :package "lib-native" :repo "lib" :paths #("native/core"))))
                                                                  #("app/."))))
  (<- view-1 (prepare rig env-1))
  (assert (= view-1.state CodeState.READY) view-1)
  (<- outcome-1 (run-task rig env-1 "t1"))
  (assert (isinstance outcome-1 TaskSucceeded) outcome-1)
  ;; 子の答え = #(値 PYTHONPATH cwd env のキー worker の pid venv の prefix)
  (val value-1 (get outcome-1.value 0))
  (val pythonpath (get outcome-1.value 1))
  (val cwd (get outcome-1.value 2))
  (val key-1 (get outcome-1.value 3))
  (val pid-1 (get outcome-1.value 4))
  (val prefix-1 (get outcome-1.value 5))
  (assert (= value-1 1))
  (assert (is pythonpath None) "子の環境変数に PYTHONPATH が無い")
  (assert (= cwd (str (.work-dir rig.host "task/t1"))) "子の cwd は空の作業 dir")
  (assert (not (.exists (.work-dir rig.host "task/t1"))) "作業 dir は回収の後に消す")
  (<- key-1-want str (env-key env-1 (current-platform)))
  (assert (= key-1 key-1-want))
  (assert (= prefix-1 (str (/ (Path view-1.path) "app" ".venv"))) "子は root の venv で走った")
  (val marker (json.loads (.read-text (/ (Path view-1.path) ENV-MARKER))))
  (assert (= (get marker "interpreter") (os.path.realpath (/ (Path view-1.path) "app" ".venv" "bin" "python")))
          "bytecode を作った interpreter は root の venv の物")
  (assert (list (.glob (/ (Path view-1.path) "app") "__pycache__/appjobs.*.pyc")) "root の中に bytecode を作った")
  ;; 2 project の commit だけ変えて再送(同じ lock・同じ native)
  (<- downloads-1 int (downloads rig))
  (<- builds-1 int (count-log rig "build "))
  (<- a2 str (push-commit rig.app (! (app-files 2 LOCK)) "app 2"))
  (<- env-2 RuntimeEnv (declare rig a2 l1 LOCK))
  (<- view-2 (prepare rig env-2))
  (assert (= view-2.state CodeState.READY) view-2)
  (assert (!= view-2.path view-1.path) "新しい commit は新しい root")
  (<- outcome-2 (run-task rig env-2 "t2"))
  (val value-2 (get outcome-2.value 0))
  (val pid-2 (get outcome-2.value 4))
  (assert (= value-2 2) "新しい root の source の値が返る")
  (assert (= pid-2 pid-1 (str (os.getpid))) "worker の process は同じ(再起動していない)")
  (<- downloads-2 int (downloads rig))
  (<- builds-2 int (count-log rig "build "))
  (assert (= downloads-2 downloads-1) "同じ lock なので download は 0")
  ;; 4 同じ lock・別の project の commit → 新しい root・download 0・native の build 0
  (assert (= builds-2 builds-1 1) "native の source が同じなので build は 0(最初の 1 回だけ)")
  ;; 3 lock を変えて再送 → 新しいキー・download が増える
  (val lock-3 (+ LOCK "rich==13.9.4\n"))
  (<- a3 str (push-commit rig.app (! (app-files 3 lock-3)) "app 3"))
  (<- env-3 RuntimeEnv (declare rig a3 l1 lock-3))
  (<- view-3 (prepare rig env-3))
  (assert (= view-3.state CodeState.READY) view-3)
  (<- downloads-3 int (downloads rig))
  (assert (= (- downloads-3 downloads-2) 1) "増えた package だけ download")
  ;; 5 native の source を変える → build が 1 回増え、同じ source の次の root は wheel を使い回す
  (<- l2 str (push-commit rig.lib {"native/core/lib.rs" "fn b() {}\n"} "lib 2"))
  (<- view-5 (prepare rig (! (declare rig a2 l2 LOCK))))
  (assert (= view-5.state CodeState.READY) view-5)
  (<- builds-5 int (count-log rig "build "))
  (assert (= builds-5 2))
  (<- view-5b (prepare rig (! (declare rig a1 l2 LOCK))))
  (assert (= view-5b.state CodeState.READY) view-5b)
  (<- builds-5b int (count-log rig "build "))
  (assert (= builds-5b 2) "同じ native の source の root は wheel を使い回す"))


(deftest test-careful-scenarios-6-and-7 [tmp-path monkeypatch]
  (.setenv monkeypatch "PYTHONDONTWRITEBYTECODE" "1")
  (<- rig Rig (make-rig tmp-path))
  (<- a1 str (push-commit rig.app (! (app-files 1 LOCK)) "app 1"))
  (<- l1 str (push-commit rig.lib {"native/core/lib.rs" "fn a() {}\n" "lib/__init__.py" "Y = 2\n"} "lib 1"))
  (<- t1 str (push-commit (/ rig.base "work" "tools") {"src/toolmod.py" "Z = 3\n"} "tools 1"))
  (.insert sys.path 0 (str rig.app))
  ;; 6 同じ env の task を 2 本同時に: 準備は 1 本・2 本とも走る
  (<- env RuntimeEnv (declare rig a1 l1 LOCK))
  (<- key str (env-key env (current-platform)))
  (<- declared dict (runtime-env->json env))
  (val text (json.dumps declared :sort-keys True :ensure-ascii False))
  (.start rig.envs (+ "env-" key) text)
  (.start rig.envs (+ "env-" key) text)
  (<- view (prepare rig env))
  (assert (= view.state CodeState.READY) view)
  (assert (= rig.envs.started 1) "同じキーの準備は 1 本")
  (<- first (run-task rig env "t1"))
  (<- second (run-task rig env "t2"))
  (assert (= (lfor o #(first second) (get o.value 0)) [1 1]) #(first second))
  ;; 7 repo を 3 つに: 3 つのツリーが root の下に兄弟で並び、import の根が宣言の順で .pth に並ぶ
  (<- tools-url str (url-of rig.base "tools"))
  (<- env-7 RuntimeEnv (declare rig a1 l1 LOCK :roots #("tools/src" "app/." "lib/.")
                                :extra-repos #((RepoCheckout :name "tools" :url tools-url :commit t1))))
  (<- view-7 (prepare rig env-7))
  (assert (= view-7.state CodeState.READY) view-7)
  (val root (Path view-7.path))
  (assert (= (sorted (lfor e (.iterdir root) :if (and (.is-dir e) (not (.startswith e.name "."))) e.name)) ["app" "lib" "tools"]))
  (val pth (next (.glob root "app/.venv/lib/*/site-packages/_doeff_cluster_roots.pth")))
  (assert (= (.splitlines (.read-text pth)) [(str (/ root "tools" "src")) (str (/ root "app")) (str (/ root "lib"))])))


(defk failure-kind [rig env]
  {:pre [(: rig Rig) (: env RuntimeEnv)] :post [(: % EnvFailureKind)]}
  "準備が失敗し、完成マーカーを置かなかったことを確かめて kind を返す。"
  (<- view CodeView (prepare rig env))
  (assert (= view.state CodeState.FAILED) view)
  (<- key str (env-key env (current-platform)))
  (assert (not (.exists (/ (.root-of rig.envs (+ "env-" key)) ENV-MARKER))) "失敗した root に完成マーカーは無い")
  view.failure.kind)


(deftest test-careful-failures-come-back-as-their-kind [tmp-path monkeypatch]
  (.setenv monkeypatch "PYTHONDONTWRITEBYTECODE" "1")
  (<- rig Rig (make-rig tmp-path))
  (<- a1 str (push-commit rig.app (! (app-files 1 LOCK)) "app 1"))
  (<- l1 str (push-commit rig.lib {"native/core/lib.rs" "fn a() {}\n"} "lib 1"))
  ;; push していない commit
  (.write-text (/ rig.app "local.txt") "x\n")
  (<- (git rig.app "add" "-A"))
  (<- (git rig.app "commit" "-q" "-m" "local only"))
  (<- unpushed str (git rig.app "rev-parse" "HEAD"))
  (<- kind-1 EnvFailureKind (failure-kind rig (! (declare rig unpushed l1 LOCK))))
  (assert (= kind-1 EnvFailureKind.COMMIT-MISSING) kind-1)
  ;; lock の hash を 1 文字変える
  (<- env RuntimeEnv (declare rig a1 l1 LOCK))
  (val h env.project.lock-sha256)
  (val wrong (replace env :project (replace env.project :lock-sha256 (+ (if (= (get h 0) "0") "1" "0") (cut h 1 None)))))
  (<- kind-2 EnvFailureKind (failure-kind rig wrong))
  (assert (= kind-2 EnvFailureKind.LOCK-MISMATCH) kind-2)
  ;; fake の uv を失敗させる(lock が古い・build できない sdist・Python を取れない)
  (for [#(fail want) [#("lock-stale" EnvFailureKind.LOCK-STALE) #("sync-failed" EnvFailureKind.SYNC-FAILED)
                      #("python-unavailable" EnvFailureKind.PYTHON-UNAVAILABLE)]]
    (.write-text (/ rig.fake "fail") fail)
    (<- got EnvFailureKind (failure-kind rig env))
    (assert (= got want) #(fail got)))
  ;; native の build の失敗(新しい native の source で build が要る時)
  (.write-text (/ rig.fake "fail") "native-build-failed")
  (<- l2 str (push-commit rig.lib {"native/core/lib.rs" "fn broken( {}\n"} "lib 2"))
  (<- kind-3 EnvFailureKind (failure-kind rig (! (declare rig a1 l2 LOCK))))
  (assert (= kind-3 EnvFailureKind.NATIVE-BUILD-FAILED) kind-3)
  (.unlink (/ rig.fake "fail"))
  ;; 根と同じ最上位の名(appjobs)を第三者の package が持つ(名前の影)
  (val shadow-lock (+ LOCK "vendor-shadow==1.0 fake-top=appjobs\n"))
  (<- a2 str (push-commit rig.app (! (app-files 2 shadow-lock)) "app shadow"))
  (<- kind-4 EnvFailureKind (failure-kind rig (! (declare rig a2 l1 shadow-lock))))
  (assert (= kind-4 EnvFailureKind.ENV-INCOMPATIBLE) kind-4)
  ;; URL を許可表から外す
  (<- app-url str (url-of rig.base "app"))
  (.write-text (/ rig.base "repo-keys.json") (json.dumps {app-url ""}))
  (<- kind-5 EnvFailureKind (failure-kind rig env))
  (assert (= kind-5 EnvFailureKind.REPO-DENIED) kind-5)
  ;; 空きが下限を切る
  (.write-text (/ rig.base "repo-keys.json") (json.dumps {app-url "" (! (url-of rig.base "lib")) ""}))
  (val full (EnvStore (str rig.state) HY :repo-keys (str (/ rig.base "repo-keys.json")) :uv (str (/ rig.fake "uv"))
                      :min-free-bytes (** 10 18)))
  (<- kind-6 EnvFailureKind (failure-kind (replace rig :envs full) env))
  (assert (= kind-6 EnvFailureKind.DISK-FULL) kind-6))


;; --- 反例: 子の環境と worker の process ----------------------------------------------------

(defclass LeakyHost [ProcessHost]
  "反例: 実行環境の job の子に PYTHONPATH を残す実装。"
  (defn launch [self spec code-path instance attempt]  ; defk にできない: ProcessHost の method の差し替え
    (setv #(argv cwd env) (.launch (super) spec code-path instance attempt))
    #(argv cwd (| env {"PYTHONPATH" code-path}))))


(defclass RestartingHost [ProcessHost]
  "反例: task ごとに worker の process を作り直して走らせる実装(子が名乗る worker の pid が task ごとに変わる)。"
  (defn launch [self spec code-path instance attempt]  ; defk にできない: ProcessHost の method の差し替え
    (setv #(argv cwd env) (.launch (super) spec code-path instance attempt))
    #(argv cwd (| env {"DOEFF_WORKER_PID" (.format "restarted-{}" instance)}))))


(defk child-problems [outcomes host]
  {:pre [(: outcomes tuple) (: host ProcessHost)] :post [(: % list)]}
  "筋書き 1・2 の子の確かめ: PYTHONPATH が無い・cwd が作業 dir・どの task も同じ worker の process から起きた。破れた所の列。"
  (var problems [])
  (for [#(name outcome) outcomes]
    (when (is-not (get outcome.value 1) None) (.append problems (.format "{}: 子に PYTHONPATH が在る" name)))
    (when (!= (get outcome.value 2) (str (.work-dir host name))) (.append problems (.format "{}: cwd が作業 dir でない" name))))
  (when (!= (len (set (gfor #(_ o) outcomes (get o.value 4)))) 1)
    (.append problems "task ごとに worker の pid が違う(worker の process を作り直した)"))
  problems)


(deftest test-careful-counterexamples-are-caught [tmp-path monkeypatch]
  (.setenv monkeypatch "PYTHONDONTWRITEBYTECODE" "1")
  (<- rig Rig (make-rig tmp-path))
  (<- a1 str (push-commit rig.app (! (app-files 1 LOCK)) "app 1"))
  (<- l1 str (push-commit rig.lib {"native/core/lib.rs" "fn a() {}\n"} "lib 1"))
  (.insert sys.path 0 (str rig.app))
  (<- env RuntimeEnv (declare rig a1 l1 LOCK))
  (<- view CodeView (prepare rig env))
  (assert (= view.state CodeState.READY) view)
  (val extra {"DOEFF_WORKER_NAME" "careful" "PYTHONDONTWRITEBYTECODE" "1"})
  (val uv (str (/ rig.fake "uv")))
  (var results {})
  (for [#(label host) [#("real" rig.host)
                       #("leaky" (LeakyHost (str (/ rig.state "logs")) HY extra :uv uv))
                       #("restarting" (RestartingHost (str (/ rig.state "logs")) HY extra :uv uv))]]
    (var outcomes [])
    (for [n [1 2]]
      (val task-id (.format "{}-{}" label n))
      (<- outcome (run-task rig env task-id :host host))
      (assert (isinstance outcome TaskSucceeded) outcome)
      (.append outcomes #(f"task/{task-id}" outcome)))
    (<- problems list (child-problems (tuple outcomes) host))
    (setv (get results label) problems))
  (assert (= (get results "real") []) (get results "real"))
  (assert (any (gfor p (get results "leaky") (in "PYTHONPATH" p))) "子に PYTHONPATH を残すと筋書き 1 が赤")
  (assert (any (gfor p (get results "restarting") (in "worker の pid" p))) "worker の再起動で走らせると筋書き 2 が赤")
  ;; 送り手の版の doeff を 1 つずらす → 子が復元を断り、欄の名と env のキーが載る
  (import doeff_cluster.detached_model [outcome-from-task-outcome DetachedVersionMismatch])
  (<- key str (env-key env (current-platform)))
  (val shifted (| (current-versions) {"doeff" "0.0.0-shifted"}))
  (<- refused (run-task rig env "shifted" :versions shifted))
  (assert (isinstance refused TaskFailed) refused)
  (val answer (outcome-from-task-outcome refused))
  (assert (isinstance answer DetachedVersionMismatch) answer)
  (assert (= (lfor d answer.diffs d.field) ["doeff"]) answer.diffs)
  (assert (= answer.env-key key) answer))


;; --- 筋書き 8・9(先読みと掃除) --------------------------------------------------------------------

(defk wait-ready [rig key]
  {:pre [(: rig Rig) (: key str)] :post [(: % CodeView)]}
  "EnvStore の観測で key が READY か FAILED になるまで待つ(準備は起こさない)。"
  (val deadline (+ (time.monotonic) DEADLINE-SECONDS))
  (var found None)
  (while (is found None)
    (when (> (time.monotonic) deadline) (raise (AssertionError (.format "準備が {} 秒で終わらない" DEADLINE-SECONDS))))
    (for [view (.observe rig.envs)]
      (when (and (= view.revision key) (in view.state #(CodeState.READY CodeState.FAILED)))
        (:= found view)))
    (when (is found None) (time.sleep 0.2)))
  found)


(deftest test-careful-scenario-8-a-warmed-root-starts-the-task-on-the-first-tick [tmp-path monkeypatch]
  (.setenv monkeypatch "PYTHONDONTWRITEBYTECODE" "1")
  (<- rig Rig (make-rig tmp-path))
  (<- a1 str (push-commit rig.app (! (app-files 1 LOCK)) "app 1"))
  (<- l1 str (push-commit rig.lib {"native/core/lib.rs" "fn a() {}\n"} "lib 1"))
  (.insert sys.path 0 (str rig.app))
  (<- env RuntimeEnv (declare rig a1 l1 LOCK))
  (<- key str (env-key env (current-platform)))
  (<- declared dict (runtime-env->json env))
  (val warm (WarmEnv :key (+ "env-" key) :runtime-env (json.dumps declared :sort-keys True :ensure-ascii False)))
  (val policy (WorkerPolicy))
  ;; 温める表を受けた worker は、job が無くても準備を起こす(先読み)
  (val warming (plan 0 #() (WorldView (.observe rig.envs) #()) {} policy :warm #(warm)))
  (assert (= warming #((PrepareEnv warm.key warm.runtime-env :warm True))) warming)
  (.start rig.envs warm.key warm.runtime-env :warm True)
  (<- view (wait-ready rig warm.key))
  (assert (= view.state CodeState.READY) view)
  ;; task が来た最初の拍で子を起こす(PrepareEnv を挟まない = 準備が task の待ちに入らない)
  (val tasks (/ rig.state "tasks"))
  (val spec (task-spec {"id" "t8" "env" JOB-ENV "revision" "" "versions" (current-versions) "blob" "" "runtimeEnv" declared}
                       tasks))
  (val first (plan 1 #(spec) (WorldView (.observe rig.envs) #()) {} policy :warm #(warm)))
  (assert (= first #((StartJob spec 1 view.path))) first)
  (<- outcome (run-task rig env "t8"))
  (assert (= (get outcome.value 0) 1) outcome)
  ;; 反例: 温めていない env の task は、最初の拍で準備を起こす(準備が task の待ちに入る)
  (<- a2 str (push-commit rig.app (! (app-files 2 LOCK)) "app 2"))
  (<- cold RuntimeEnv (declare rig a2 l1 LOCK))
  (<- cold-declared dict (runtime-env->json cold))
  (val cold-spec (task-spec {"id" "t9" "env" JOB-ENV "revision" "" "versions" (current-versions) "blob" ""
                             "runtimeEnv" cold-declared}
                            tasks))
  (val cold-first (plan 2 #(cold-spec) (WorldView (.observe rig.envs) #()) {} policy :warm #(warm)))
  (assert (= cold-first #((PrepareEnv (code-key cold-spec) cold-spec.runtime-env))) cold-first))


(deftest test-careful-scenario-9-the-sweep-keeps-pinned-latest-and-foreign-dirs [tmp-path monkeypatch]
  (.setenv monkeypatch "PYTHONDONTWRITEBYTECODE" "1")
  (<- rig Rig (make-rig tmp-path))
  ;; 空きの下限を空きより上に置く(下限を切った状態を作る)
  (setv rig.envs.sweep-floor-bytes (** 2 62))
  (<- l1 str (push-commit rig.lib {"native/core/lib.rs" "fn a() {}\n"} "lib 1"))
  (var views [])
  (for [n [1 2 3]]
    (<- sha str (push-commit rig.app (! (app-files n LOCK)) (.format "app {}" n)))
    (<- view (prepare rig (! (declare rig sha l1 LOCK))))
    (assert (= view.state CodeState.READY) view)
    (.append views view))
  (val #(a b c) (lfor v views (Path v.path)))
  ;; 使った時刻: a(固定)が最も古く・b・c(最後に作った = project の最新)の順
  (for [#(root at) [#(a 1000) #(b 2000) #(c 3000)]]
    (.write-text (/ root ".last-used") "")
    (os.utime (/ root ".last-used") #(at at)))
  ;; worker が作っていない dir(キーの形の名で完成マーカーの無い dir と、別の名の dir)
  (val foreign (/ rig.state "roots" "0123456789abcdef01234567"))
  (.mkdir foreign)
  (.write-text (/ foreign "keep.txt") "not ours\n")
  (val notes (/ rig.state "roots" "notes"))
  (.mkdir notes)
  (.sweep rig.envs (frozenset #((+ "env-" a.name))))
  (assert (.exists a) "固定された root は残る")
  (assert (not (.exists b)) "固定されていない古い root は消える")
  (assert (.exists c) "project ごとの最新の root(bytecode の引き継ぎ元)は残る")
  (assert (and (.exists foreign) (.exists notes)) "worker が作っていない dir は消さない"))
