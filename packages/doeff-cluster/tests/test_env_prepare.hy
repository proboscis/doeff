;; 実行環境の宣言(runtime_env_model)と準備の Program(env_prepare の prepare-env)の検 — 速い模擬(env_world の模擬の世界・仮想の時計)。
;;
;; 準備の段で確かめられる筋書き(設計 worker-runtime-env.md 節 5):
;;   2 送り手の repo の commit だけ変える → 新しい root・sync は増えるが download は 0
;;   3 lock を変える → 新しいキー・download が増える
;;   4 同じ lock・別の project の commit → 新しい root・download 0・native の build 0
;;   5 native の source を変える → build が 1 回だけ増え、次の root は wheel を使い回す
;;   7 repo を 3 つ → 3 つのツリーが兄弟に並び、import の根の順が宣言どおり・bytecode の処理ステージは repo の木ごとに進みの印を触る
;; 反例: キーから import の根を外すと根だけ違う宣言が同じ root になる・根と同じ最上位の名の第三者の package・失敗の組(節 3.6)・
;; bytecode の引き継ぎ元を dir の名の順で選ぶと、同じ commit の root が在っても古い commit の root から引き継ぐ(#3515 の B)。
;; 筋書き 1・2 の実行と 6(同時の準備)は worker と子の起動の検(E10 の便 2)で確かめる。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import hashlib)
(import json)
(import pytest)
(import pathlib [Path])
(import doeff [Program with-handlers])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_cluster.worker.protocol.env_translation [editable-dirs repo-identity])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.scheduler [Spawn Task Gather])
(import doeff_time [SimClock sim-time-handler GetMonotonic])
(import doeff_cluster.shared.intent.runtime_env_model [RepoCheckout NativeWheel PythonProject ToolRequirement EnvVar RuntimeEnv
                                                       RuntimeEnvInvalid InvalidKind EnvFailure EnvFailureKind])
(import doeff_cluster.shared.core.runtime_env_rules [env-key key-material runtime-env->json runtime-env-of-json])
(import doeff_cluster.worker.core.env_prepare [prepare-env carry-source] doeff_cluster.worker.intent.env_prepare_model [PrepareRequest KnownRoot EnvReady ROOTS-PTH StageStarted] doeff_cluster.shared.intent.env_marker_model [ENV-MARKER])
(import doeff_cluster.sim.env_world [env-world EnvWorld EnvWorldLog WorldRemote WorldCommit WorldFile read-world-log world-files
                                set-uv-failure set-unreachable UvFailure UvFault])

(setv PLATFORM "linux-x86_64")
(import tests.env_fixtures [LOCK APP-URL LIB-URL sha-of lock-sha app-commit lib-commit env-of base-world])


(defk prepare [env known]
  {:pre [(: env RuntimeEnv) (: known tuple)] :post [(: % (| EnvReady EnvFailure))]}
  "worker と同じ置き場(state/roots/<キー>)に root を準備する。"
  (<- key str (env-key env PLATFORM))
  (<- result (| EnvReady EnvFailure)
      (prepare-env (PrepareRequest :env env :key key :platform PLATFORM :root (.format "/state/roots/{}" key)
                                   :known known :min-free-bytes 1024)))
  result)


(defk known-of [#* ready]
  {:pre [(: ready tuple)] :post [(: % tuple)]}
  "完成した root の列 → 次の準備の known(どれも完成した root(EnvReady)であることを確かめてから読む・完成の時刻は列の順)。"
  (for [r ready]
    (assert (isinstance r EnvReady) r))
  (tuple (gfor #(made r) (enumerate ready) (KnownRoot :env r.env :root r.root :made-ms made))))


;; --- 宣言の型・キー ------------------------------------------------------------------------

(deftest test-the-declaration-refuses-mistakes-before-sending
  ;; 宣言の誤りは送る前に RuntimeEnvInvalid(種類つき)で断る。
  (val sha (* "a" 40))
  (val lock (* "b" 64))
  (val project (PythonProject :repo "app" :path "." :lock-sha256 lock :python "3.14"))
  (val repo (RepoCheckout :name "app" :url APP-URL :commit sha))
  (for [#(kind make)
        [#(InvalidKind.BAD-COMMIT (fn [] (RepoCheckout :name "app" :url APP-URL :commit "main")))
         #(InvalidKind.INVALID-NAME (fn [] (RepoCheckout :name "App" :url APP-URL :commit sha)))
         #(InvalidKind.DUPLICATE-REPO (fn [] (RuntimeEnv :repos #(repo repo) :project project :import-roots #("app/."))))
         #(InvalidKind.UNKNOWN-REPO (fn [] (RuntimeEnv :repos #(repo) :project (replace project :repo "lib")
                                                       :import-roots #("app/."))))
         #(InvalidKind.UNKNOWN-REPO (fn [] (RuntimeEnv :repos #(repo) :project project :import-roots #("lib/."))))
         #(InvalidKind.BAD-PATH (fn [] (RuntimeEnv :repos #(repo) :project project :import-roots #("app/../x"))))
         #(InvalidKind.BAD-PATH (fn [] (PythonProject :repo "app" :path "/abs" :lock-sha256 lock :python "3.14")))
         #(InvalidKind.BAD-SHA256 (fn [] (PythonProject :repo "app" :path "." :lock-sha256 "x" :python "3.14")))
         #(InvalidKind.RESERVED-ENV-VAR (fn [] (EnvVar :name "PYTHONPATH" :value "/x")))
         #(InvalidKind.RESERVED-ENV-VAR (fn [] (EnvVar :name "DOEFF_RUNTIME_ENV" :value "{}")))
         #(InvalidKind.EMPTY (fn [] (RuntimeEnv :repos #() :project project :import-roots #("app/."))))]]
    (with [caught (pytest.raises RuntimeEnvInvalid)] (make))
    (assert (= caught.value.kind kind) (.format "{} のはずが {}" kind caught.value.kind))))


(deftest test-the-key-covers-what-changes-the-root-and-nothing-else
  ;; キーは root の中身を決める物(repo・project・import の根・形の版)と platform だけで決まる。
  (<- base RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- k-base str (env-key base PLATFORM))
  (<- k-roots str (env-key (replace base :import-roots #("app/vendor" "app/.")) PLATFORM))
  (<- k-vars str (env-key (replace base :env-vars #((EnvVar :name "MODE" :value "x"))
                                        :tools #((ToolRequirement :name "cli"))) PLATFORM))
  (<- k-platform str (env-key base "linux-aarch64"))
  (assert (!= k-base k-roots) "import の根の順だけ違う宣言は別の root")
  (assert (= k-base k-vars) "環境変数と道具は file を変えないのでキーに入らない")
  (assert (!= k-base k-platform))
  ;; 反例: キーの材料から import の根を外した作り方では、根だけ違う 2 つの宣言が同じキー(= 古い root で走る)になる。
  (<- m-base dict (key-material base PLATFORM))
  (<- m-roots dict (key-material (replace base :import-roots #("app/vendor" "app/.")) PLATFORM))
  (del (get m-base "importRoots") (get m-roots "importRoots"))
  (assert (= (json.dumps m-base :sort-keys True) (json.dumps m-roots :sort-keys True))))


(deftest test-the-declaration-round-trips-through-json
  ;; 通信の本文と子の環境変数に載せる JSON は宣言へ戻る(環境変数と道具も運ぶ)。
  (<- base RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (val env (replace base :env-vars #((EnvVar :name "MODE" :value "x")) :tools #((ToolRequirement :name "cli" :version ">=2"))))
  (<- encoded dict (runtime-env->json env))
  (<- decoded RuntimeEnv (runtime-env-of-json (json.loads (json.dumps encoded))))
  (assert (= decoded env))
  (with [caught (pytest.raises RuntimeEnvInvalid)]
    (<- _ (runtime-env-of-json {"repos" []})))
  (assert (= caught.value.kind InvalidKind.BAD-JSON)))


;; --- 準備の筋書き --------------------------------------------------------------------------
;; 筋書きは 1 つの handler の組(仮想の時計 + env-world)の下で走る defk に書き、確かめも中に置く(fake の状態はその組の間だけ)。

(defk run-in-world [world scenario]
  {:pre [(: world EnvWorld) (: scenario Program)] :post [(: % bool)]}
  "筋書き(引数なしの defk の呼び出し = Program)を仮想の時計と env-world の下で走らせる。"
  (<- handlers list (env-world world))
  (<- ok bool ((state) ((sim-time-handler :clock (SimClock)) (with-handlers handlers scenario))))
  ok)


(defk files-under [root]
  {:pre [(: root str)] :post [(: % dict)]}
  "root の下の模擬の file の path → 中身。"
  (<- files tuple (world-files root))
  (dfor f files f.path f.text))


(defk cold-scenario []
  {:pre [] :post [(: % bool)]}
  "冷たい準備: repo が兄弟に並び、依存を取りに行き、native を build し、根の .pth と完成マーカーを置く。"
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- started float (GetMonotonic))
  (<- ready (prepare env #()))
  (<- ended float (GetMonotonic))
  (assert (isinstance ready EnvReady) ready)
  (assert (= #(ready.downloaded ready.built) #(3 1)))
  (assert (>= (- ended started) 120.0) "冷たい sync と build でそれぞれ 60 秒(仮想の時間)")
  ;; bytecode を作った interpreter は root の venv の物(マーカーで読める)
  (assert (= ready.interpreter (.format "{}/app/.venv/bin/python" ready.root)))
  (<- files dict (files-under ready.root))
  (assert (in (.format "{}/app/uv.lock" ready.root) files))
  (assert (in (.format "{}/lib/native/core/lib.rs" ready.root) files))
  (assert (= (get files (.format "{}/app/.venv/lib/python3.14/site-packages/{}" ready.root ROOTS-PTH)) (.format "{0}/app\n{0}/app/vendor\n" ready.root)))
  (val marker (json.loads (get files (.format "{}/{}" ready.root ENV-MARKER))))
  (assert (= (get marker "key") ready.key))
  (assert (= (get marker "interpreter") ready.interpreter))
  (assert (= (lfor s (get marker "stages") (get s "name"))
             ["disk" "mirror" "tree" "lock" "native" "sync" "wheels" "roots" "bytecode" "probe"]))
  True)


(deftest test-a-cold-root-is-prepared-and-marked
  (<- world EnvWorld (base-world))
  (<- ok bool (run-in-world world (cold-scenario)))
  (assert ok))


;; 反例(2026-10-02 の本番 — #2730): project の lock の dev の組に、worker が読めない private の repo の git の依存
;; (custody-scripted・ssh)が在った。worker は dev の組を入れない(--no-default-groups)のに、準備の sync が lock を解き直す形
;; (--locked)だったので、入れない組の git の依存まで取りに行き、Host key verification failed で準備が全部止まった。lock の中身は宣言の
;; lock-sha256 で縛ってあるので、準備は lock をそのまま使う(--frozen)— 入れる組の行だけを取りに行く。
(val PRIVATE-URL "ssh://git@github.com/example/private.git")
(val DEV-GIT-LOCK (+ LOCK (.format "private-scripted==0.1 group=dev git={}\n" PRIVATE-URL)))


(defk dev-git-scenario []
  {:pre [] :post [(: % bool)]}
  "入れない dev の組に届かない git の依存が在る lock でも、準備が通り、その依存を取りに行かない事を確かめるため。"
  (<- env RuntimeEnv (env-of "app-dev-git" "lib-1" DEV-GIT-LOCK))
  (<- ready (prepare env #()))
  (assert (isinstance ready EnvReady) ready)
  (assert (= ready.downloaded 3) "取りに行くのは本体の依存 3 つだけ(dev の組の行は入れない)")
  True)


(deftest test-a-private-git-dependency-in-an-uninstalled-group-does-not-stop-the-prepare
  (<- base EnvWorld (base-world))
  (<- dev WorldCommit (app-commit "app-dev-git" DEV-GIT-LOCK "V = 5\n"))
  (val world (replace base
                      :remotes (tuple (gfor r base.remotes (if (= r.url APP-URL) (replace r :commits (+ r.commits #(dev))) r)))
                      :unreachable #(PRIVATE-URL)))
  (<- ok bool (run-in-world world (dev-git-scenario)))
  (assert ok))


;; 反例(2026-09-27 の本番): 宣言の import の根は業務の repo だけで、venv に editable で入る依存の package(lib の木の中)は焼かれず、
;; 子と入口の検めが毎回 source から compile した(Hy の macro の展開で import が壁時計 240 秒)。editable の .pth が root の中の
;; repo を指すなら、その dir も焼く範囲に入る。
(val EDITABLE-LOCK (+ LOCK "lib==0.1.0 editable=../lib\nlib-extra==0.1.0 editable=../lib/extra/src\n"))


(defk editable-scenario []
  {:pre [] :post [(: % bool)]}
  "editable で入る依存の repo の木も、root の venv の interpreter で焼かれる(宣言の import の根に無くても)。"
  (<- env RuntimeEnv (env-of "app-editable" "lib-1" EDITABLE-LOCK))
  (<- ready (prepare env #()))
  (assert (isinstance ready EnvReady) ready)
  (<- log EnvWorldLog (read-world-log))
  (val trees (dfor #(tree roots) log.compiled-trees tree roots))
  (assert (= (get trees (.format "{}/app" ready.root)) #("." "vendor")) trees)
  (assert (= (.get trees (.format "{}/lib" ready.root)) #("." "extra/src"))
          (.format "editable の依存の木が焼かれていない: {}" trees))
  True)


(deftest test-the-editable-dirs-are-read-from-the-venv-pth-files [#^ Path tmp-path]
  ;; 本物の handler の読み: uv が editable の package ごとに置く .pth(dir の絶対 path 1 行)のうち root の中の dir だけ・import の根の
  ;; .pth と import の行と root の外の dir は除く。
  (val root (/ tmp-path "root"))
  (val site (/ root "app" ".venv" "lib" "python3.14" "site-packages"))
  (for [d ["lib" "lib/packages/core/src" "app"]] (.mkdir (/ root d) :parents True :exist-ok True))
  (.mkdir site :parents True)
  (.mkdir (/ tmp-path "outside"))
  (.write-text (/ site "_editable_impl_lib.pth") (str (/ root "lib")))
  (.write-text (/ site "_editable_impl_lib_core.pth") (+ (str (/ root "lib/packages/core/src")) "\n"))
  (.write-text (/ site "_other.pth") (+ "import sys\n# note\n" (str (/ tmp-path "outside")) "\n"))
  (.write-text (/ site ROOTS-PTH) (str (/ root "app")))
  (<- dirs tuple (with-handlers [os-file-handler] (editable-dirs (str site) (str root))))
  (assert (= dirs #("lib" "lib/packages/core/src"))))


;; 反例(構成レビュー 2026-09-27): editable で入るだけの依存の repo の bytecode は最適化で、焼けなくても env は作れる(子は import の時に
;; compile する)。source を持たない editable の根(native だけの package の dir)しか持たない repo で、焼く道具の「焼く物が無い」を
;; env の失敗にしない。宣言の根を持つ repo の失敗は今までどおり env の失敗(展開の失敗を捕まえるため)。
(val NATIVE-ONLY-LOCK (+ LOCK "lib-native-only==0.1.0 editable=../lib/native/core\n"))


(defk native-only-editable-scenario []
  {:pre [] :post [(: % bool)]}
  "source の無い editable の根だけの repo でも env は完成し、焼けなかったことは記録に残る。"
  (<- env RuntimeEnv (env-of "app-native-only" "lib-1" NATIVE-ONLY-LOCK))
  (<- ready (prepare env #()))
  (assert (isinstance ready EnvReady) (.format "editable だけの repo の焼きの失敗で env が失敗した: {}" ready))
  (<- log EnvWorldLog (read-world-log))
  (assert (any (gfor n log.notes (in "lib" n))) log.notes)
  True)


(deftest test-an-editable-only-repo-that-cannot-be-compiled-does-not-fail-the-env
  (<- world EnvWorld (base-world))
  (<- app WorldCommit (app-commit "app-native-only" NATIVE-ONLY-LOCK "V = 6\n"))
  (<- ok bool (run-in-world (replace world :remotes (tuple (gfor r world.remotes
                                                                 (if (= r.url APP-URL) (replace r :commits (+ r.commits #(app))) r))))
                            (native-only-editable-scenario)))
  (assert ok))


(deftest test-editable-dependencies-in-the-root-are-compiled-too
  (<- world EnvWorld (base-world))
  (<- app WorldCommit (app-commit "app-editable" EDITABLE-LOCK "V = 5\n"))
  (<- ok bool (run-in-world (replace world :remotes (tuple (gfor r world.remotes
                                                                 (if (= r.url APP-URL) (replace r :commits (+ r.commits #(app))) r))))
                            (editable-scenario)))
  (assert ok))


(defk commit-only-scenario []
  {:pre [] :post [(: % bool)]}
  "筋書き 2・4: project の repo の commit だけ変える(同じ lock)→ 新しい root・sync は増えるが download も build も 0・
   変わらない repo のツリーは複製・bytecode は前の root から引き継ぐ。"
  (<- first-env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- first EnvReady (prepare first-env #()))
  (<- before EnvWorldLog (read-world-log))
  (<- second-env RuntimeEnv (env-of "app-2" "lib-1" LOCK))
  (<- known tuple (known-of first))
  (<- started float (GetMonotonic))
  (<- second (prepare second-env known))
  (<- ended float (GetMonotonic))
  (<- after EnvWorldLog (read-world-log))
  (assert (isinstance second EnvReady) second)
  (assert (!= second.root first.root) "新しい commit は新しい root")
  (assert (= #(second.downloaded second.built) #(0 0)))
  (assert (= (- after.syncs before.syncs) 1))
  (assert (= (- after.copies before.copies) 1) "変わらない lib のツリーは前の root から複製")
  (assert (= (- after.archives before.archives) 1) "変わった app のツリーだけ mirror から展開")
  (assert (> (- after.carried before.carried) 0) "同じ lock と Python の root から bytecode を引き継ぐ")
  (assert (< (- ended started) 60.0) "温い準備は冷たい秒を払わない")
  (<- files dict (files-under second.root))
  (assert (= (get files (.format "{}/app/app/__init__.py" second.root)) "V = 2\n"))
  (assert (not (any (gfor p files (and (in "/.venv/wheels/" p) (in first.root p))))) "複製は元の root の venv を持ち越さない")
  True)


(deftest test-changing-only-the-project-commit-makes-a-warm-root
  (<- world EnvWorld (base-world))
  (<- ok bool (run-in-world world (commit-only-scenario)))
  (assert ok))


(defk lock-change-scenario []
  {:pre [] :post [(: % bool)]}
  "筋書き 3: lock を変える → 新しいキー・増えた package だけ download・bytecode は引き継がない(Hy と doeff-hy が変わり得る)。"
  (<- env-1 RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- first EnvReady (prepare env-1 #()))
  (<- known tuple (known-of first))
  (<- before EnvWorldLog (read-world-log))
  (<- env-3 RuntimeEnv (env-of "app-3" "lib-1" (+ LOCK "rich==13.9.4 top=rich\n")))
  (<- changed (prepare env-3 known))
  (<- after EnvWorldLog (read-world-log))
  (assert (isinstance changed EnvReady) changed)
  (assert (!= changed.key first.key))
  (assert (= changed.downloaded 1))
  (assert (= (- after.carried before.carried) 0))
  True)


(deftest test-changing-the-lock-downloads-only-the-new-packages
  (<- world EnvWorld (base-world))
  (<- ok bool (run-in-world world (lock-change-scenario)))
  (assert ok))


(defk native-change-scenario []
  {:pre [] :post [(: % bool)]}
  "筋書き 5: native の source を変える → build が 1 回だけ増え、同じ source の次の root は wheel を使い回す。"
  (<- env-1 RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- first EnvReady (prepare env-1 #()))
  (<- before EnvWorldLog (read-world-log))
  (<- env-lib-2 RuntimeEnv (env-of "app-1" "lib-2" LOCK))
  (<- known-1 tuple (known-of first))
  (<- changed EnvReady (prepare env-lib-2 known-1))
  (<- mid EnvWorldLog (read-world-log))
  (<- env-2 RuntimeEnv (env-of "app-2" "lib-2" LOCK))
  (<- known-2 tuple (known-of first changed))
  (<- again EnvReady (prepare env-2 known-2))
  (<- after EnvWorldLog (read-world-log))
  (assert (= (- mid.builds before.builds) 1))
  (assert (= changed.built 1))
  (assert (= (- after.builds mid.builds) 0))
  (assert (= again.built 0))
  True)


(deftest test-changing-the-native-source-builds-once
  (<- world EnvWorld (base-world))
  (<- ok bool (run-in-world world (native-change-scenario)))
  (assert ok))


(defk concurrent-wheel-scenario []
  {:pre [] :post [(: % bool)] :tags {:context "runtime-env" :role "program"}}
  "同じ native の source(同じ wheel のキー)の 2 つの準備を並行させる筋: 錠を待つので build は 1 回で、両方とも完成する(#835)。"
  (<- before EnvWorldLog (read-world-log))
  (<- env-1 RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- env-2 RuntimeEnv (env-of "app-2" "lib-1" LOCK))
  (<- one Task (Spawn (prepare env-1 #())))
  (<- two Task (Spawn (prepare env-2 #())))
  (<- results list (Gather one two))
  (<- after EnvWorldLog (read-world-log))
  (assert (all (gfor r results (isinstance r EnvReady))) results)
  (assert (= (- after.builds before.builds) 1) (.format "build は 1 回のはずが {} 回" (- after.builds before.builds)))
  True)


(deftest test-two-concurrent-preparations-of-one-wheel-build-it-once
  (<- world EnvWorld (base-world))
  (<- ok bool (run-in-world world (concurrent-wheel-scenario)))
  (assert ok))


(defk three-repos-scenario []
  {:pre [] :post [(: % bool)]}
  "筋書き 7: repo を 3 つ → 3 つのツリーが root の下に兄弟で並び、import の根が宣言の順で .pth に並ぶ。"
  (<- base RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- tools-sha str (sha-of "tools-1"))
  (val env (replace base
                    :repos (+ base.repos #((RepoCheckout :name "tools" :url "file:///remotes/tools.git" :commit tools-sha)))
                    :import-roots #("tools/src" "app/." "lib/.")))
  (<- ready (prepare env #()))
  (assert (isinstance ready EnvReady) ready)
  (<- files dict (files-under ready.root))
  (for [name ["app" "lib" "tools"]]
    (assert (any (gfor p files (.startswith p (.format "{}/{}/" ready.root name)))) name))
  (assert (= (get files (.format "{}/app/.venv/lib/python3.14/site-packages/{}" ready.root ROOTS-PTH))
             (.format "{0}/tools/src\n{0}/app\n{0}/lib\n" ready.root)))
  True)


(deftest test-three-repos-sit-side-by-side-with-roots-in-declared-order
  (<- world EnvWorld (base-world))
  (<- tools WorldCommit (lib-commit "tools-1" ""))
  (val tools-commit (replace tools :files #((WorldFile :path "src/tool/__init__.py" :text "Z = 3\n"))))
  (<- ok bool (run-in-world (replace world :remotes (+ world.remotes #((WorldRemote :url "file:///remotes/tools.git"
                                                                                    :commits #(tools-commit)))))
                            (three-repos-scenario)))
  (assert ok))


(defclass StageMarks []
  "seen-stages の記録: names = 準備の Program が出した進みの印(StageStarted)の名(出した順)。"
  (defn #^ None __init__ [self]
    (setv #^ (get tuple #(str ...)) self.names #())
    None))


(defhandler seen-stages [#^ StageMarks marks]
  ;; 引数に残す理由: 検ごとに別の記録を持つ。進みの印の名を数え、外側の翻訳(env-world の env-translation)へそのまま渡す。
  (StageStarted [name]
    (setv marks.names (+ marks.names #(name)))
    (<- effect)
    (resume None)))


(deftest test-the-bytecode-stage-marks-progress-after-each-repo-tree
  ;; bytecode の処理ステージは repo の木 1 つを焼き終えるごとに進みの印を触り直す(#3515 — 頭の印だけでは、bytecode を 267.9 秒
  ;; 焼いている準備も worker から停滞に見えた)。焼く根を持つ repo 3 つ = 頭の印 1 つ + 木ごとの印 3 つ。ほかの処理ステージは頭の印だけ。
  (<- world EnvWorld (base-world))
  (<- tools WorldCommit (lib-commit "tools-1" ""))
  (val tools-commit (replace tools :files #((WorldFile :path "src/tool/__init__.py" :text "Z = 3\n"))))
  (val marks (StageMarks))
  (<- handlers list (env-world (replace world :remotes (+ world.remotes #((WorldRemote :url "file:///remotes/tools.git"
                                                                                       :commits #(tools-commit)))))))
  (<- ok bool ((state) ((sim-time-handler :clock (SimClock)) (with-handlers (+ handlers [(seen-stages marks)]) (three-repos-scenario)))))
  (assert ok)
  (assert (= marks.names #("disk" "mirror" "tree" "lock" "native" "sync" "wheels" "roots" "bytecode" "bytecode" "bytecode" "bytecode"
                           "probe"))
          marks.names))


;; --- bytecode の引き継ぎ元の選び(#3515 の B)-----------------------------------------------------------
;; 反例(2026-10-05 の本番): 同じ lock と Python の完成済みの root が 2 つ在り、dir の名で先に来る root は古い doeff の commit で組んだ物、
;; もう 1 つは新しい宣言と同じ commit で組んだ物だった。選びが dir の名の順で最初の root だったので古い方から引き継ぎ、Hy の macro の
;; file が違うので約 2000 個の .pyc を組み直した(267.9 秒)。選びは 1 その repo の commit も doeff(Hy の macro の repo)の commit も
;; 同じ root → 2 doeff の commit が同じ root(Hy の .pyc は macro の file が変わると全部無効になるので、repo の commit の一致より先)→
;; 3 その repo の commit が同じ root → 4 最も新しく完成した root の順。宣言に doeff が無ければ 3 → 4。

(val DOEFF-URL "file:///remotes/doeff.git")


(defk carry-env [app doeff]
  {:pre [(: app str) (: doeff str)] :post [(: % RuntimeEnv)]}
  "app と doeff(Hy の macro を持つ repo — 送り手は doeff の checkout を doeff の名で並べる)を並べる宣言を作るため(lock と Python は
   どれも同じ = どの root も引き継ぎ元の候補になる)。"
  (<- base RuntimeEnv (env-of app "lib-1" LOCK #("app/." "app/vendor") False))
  (<- doeff-sha str (sha-of doeff))
  (replace base :repos #((get base.repos 0) (RepoCheckout :name "doeff" :url DOEFF-URL :commit doeff-sha))))


(defk carry-root [dir app doeff made-ms]
  {:pre [(: dir str) (: app str) (: doeff str) (: made-ms int)] :post [(: % KnownRoot)]}
  "完成済みの root 1 つ(/state/roots/<dir>・app と doeff の commit・完成の時刻)を引き継ぎ元の候補として作るため。"
  (<- env RuntimeEnv (carry-env app doeff))
  (KnownRoot :env env :root (.format "/state/roots/{}" dir) :made-ms made-ms))


(deftest test-the-bytecode-carry-prefers-a-root-at-the-same-commits
  ;; 失敗ケース(直す前は赤): dir の名で先に来る古い commit の root(aaaa)ではなく、新しい宣言と同じ app と doeff の commit で組んだ
  ;; root(bbbb)を選ぶ(2026-10-05 の本番の形)。
  (<- env RuntimeEnv (carry-env "app-new" "doeff-new"))
  (<- older KnownRoot (carry-root "aaaa" "app-old" "doeff-old" 1000))
  (<- same KnownRoot (carry-root "bbbb" "app-new" "doeff-new" 2000))
  (<- picked (| str None) (carry-source #(older same) env "app"))
  (assert (= picked "/state/roots/bbbb/app") picked)
  ;; 両方の commit が同じ root は、doeff の commit だけ・app の commit だけが同じで後から完成した root より先(完成の時刻より
  ;; commit の一致が先)。
  (<- newer KnownRoot (carry-root "cccc" "app-other" "doeff-new" 3000))
  (<- same-app KnownRoot (carry-root "dddd" "app-new" "doeff-old" 4000))
  (<- still (| str None) (carry-source #(older same newer same-app) env "app"))
  (assert (= still "/state/roots/bbbb/app") still)
  ;; doeff のツリーは、同じ doeff の commit の root が 2 つ在るので、後から完成した方(cccc)。
  (<- macros-tree (| str None) (carry-source #(older same newer) env "doeff"))
  (assert (= macros-tree "/state/roots/cccc/doeff") macros-tree)
  ;; lock か Python が違う root は、同じ commit で後から完成していても候補にしない(macro の展開が同じ Hy と doeff-hy で固定される組に
  ;; 限る — 前からの条件)。
  (val other-lock (replace same :root "/state/roots/0000" :made-ms 4000
                                :env (replace same.env :project (replace same.env.project :lock-sha256 (* "c" 64)))))
  (val other-python (replace same :root "/state/roots/0001" :made-ms 4000
                                  :env (replace same.env :project (replace same.env.project :python "3.13"))))
  (<- kept (| str None) (carry-source #(other-lock other-python older same) env "app"))
  (assert (= kept "/state/roots/bbbb/app") kept))


(deftest test-the-bytecode-carry-falls-back-to-a-root-with-the-same-macros
  ;; 失敗ケース(直す前は赤): 同じ app の commit の root が無い時は、doeff(Hy の macro の repo)の commit が同じ root(bbbb)を、
  ;; dir の名で先に来る root(aaaa — 後から完成したが doeff の commit が違う)より先に選ぶ。
  (<- env RuntimeEnv (carry-env "app-new" "doeff-new"))
  (<- older KnownRoot (carry-root "aaaa" "app-old" "doeff-old" 3000))
  (<- macros KnownRoot (carry-root "bbbb" "app-other" "doeff-new" 1000))
  (<- picked (| str None) (carry-source #(older macros) env "app"))
  (assert (= picked "/state/roots/bbbb/app") picked))


(deftest test-the-same-macros-beat-the-same-repo-commit
  ;; 失敗ケース(直す前は赤): app の commit が同じで doeff の commit が違う root(aaaa — dir の名で先・後から完成)は、app の commit が
  ;; 違い doeff の commit が同じ root(bbbb)に負ける。Hy の .pyc は使った macro の file が変わると全部無効になるので、aaaa から引き継ぐと
  ;; 全部を組み直し、bbbb からなら変わった app の file の分だけを組む。
  (<- env RuntimeEnv (carry-env "app-new" "doeff-new"))
  (<- same-app KnownRoot (carry-root "aaaa" "app-new" "doeff-old" 3000))
  (<- same-macros KnownRoot (carry-root "bbbb" "app-other" "doeff-new" 1000))
  (<- picked (| str None) (carry-source #(same-app same-macros) env "app"))
  (assert (= picked "/state/roots/bbbb/app") picked))


(deftest test-the-bytecode-carry-takes-the-newest-root-when-no-version-matches
  ;; 失敗ケース(直す前は赤): app の commit も doeff の commit も合う root が無い時は、最も新しく完成した root(bbbb)を選ぶ
  ;; (dir の名で先に来る aaaa ではない)。
  (<- env RuntimeEnv (carry-env "app-new" "doeff-new"))
  (<- oldest KnownRoot (carry-root "aaaa" "app-old" "doeff-old" 1000))
  (<- newest KnownRoot (carry-root "bbbb" "app-other" "doeff-other" 3000))
  (<- middle KnownRoot (carry-root "cccc" "app-third" "doeff-third" 2000))
  (<- picked (| str None) (carry-source #(oldest newest middle) env "app"))
  (assert (= picked "/state/roots/bbbb/app") picked)
  ;; 完成の時刻が同じなら root の path の順(known の並びに依らない)。
  (<- twin KnownRoot (carry-root "0000" "app-twin" "doeff-twin" 3000))
  (<- tied (| str None) (carry-source #(oldest newest twin middle) env "app"))
  (assert (= tied "/state/roots/0000/app") tied)
  ;; 宣言に doeff の repo が無ければ doeff の条件は使わず、app の commit が同じ root(bbbb — dir の名で aaaa より後・aaaa より前に完成)
  ;; → 最も新しく完成した root の順。
  (<- plain RuntimeEnv (env-of "app-new" "lib-1" LOCK))
  (<- plain-old RuntimeEnv (env-of "app-old" "lib-1" LOCK))
  (<- plain-other RuntimeEnv (env-of "app-other" "lib-1" LOCK))
  (val plain-older (KnownRoot :env plain-old :root "/state/roots/aaaa" :made-ms 2000))
  (val plain-same (KnownRoot :env plain :root "/state/roots/bbbb" :made-ms 1000))
  (val plain-newest (KnownRoot :env plain-other :root "/state/roots/cccc" :made-ms 3000))
  (<- plain-picked (| str None) (carry-source #(plain-older plain-same plain-newest) plain "app"))
  (assert (= plain-picked "/state/roots/bbbb/app") plain-picked)
  (<- plain-newer (| str None) (carry-source #(plain-older plain-newest) plain "app"))
  (assert (= plain-newer "/state/roots/cccc/app") plain-newer)
  ;; 候補が無ければ引き継がない。
  (<- nothing (| str None) (carry-source #() env "app"))
  (assert (is nothing None) nothing))


;; --- 失敗の組(節 3.6)と反例 ------------------------------------------------------------------

(defk failure-of [env]
  {:pre [(: env RuntimeEnv)] :post [(: % EnvFailure)]}
  "準備が失敗し、完成マーカーを置かなかったことを確かめて失敗を返す。"
  (<- result (prepare env #()))
  (assert (isinstance result EnvFailure) result)
  (<- key str (env-key env PLATFORM))
  (<- files dict (files-under (.format "/state/roots/{}" key)))
  (assert (not (any (gfor p files (.endswith p ENV-MARKER)))) "失敗した root に完成マーカーは無い")
  result)


(defk expect-failure [world env kind retryable]
  {:pre [(: world EnvWorld) (: env RuntimeEnv) (: kind EnvFailureKind) (: retryable bool)] :post [(: % bool)]}
  "world の下で env を準備すると kind の失敗(一時か恒久かも)になる。"
  (<- handlers list (env-world world))
  (<- failure EnvFailure ((state) ((sim-time-handler :clock (SimClock)) (with-handlers handlers (failure-of env)))))
  (assert (= failure.kind kind) (.format "{} のはずが {}: {}" kind failure.kind failure.detail))
  (assert (= failure.retryable retryable) failure)
  True)


(deftest test-each-failure-comes-back-as-its-kind
  (<- world EnvWorld (base-world))
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  ;; URL を許可表から外す / 届かない
  (<- (expect-failure (replace world :denied (frozenset #(LIB-URL))) env EnvFailureKind.REPO-DENIED False))
  (<- (expect-failure (replace world :unreachable (frozenset #(APP-URL))) env EnvFailureKind.REPO-UNREACHABLE True))
  ;; commit を push しない
  (<- unpushed RuntimeEnv (env-of "app-unpushed" "lib-1" LOCK))
  (<- (expect-failure world unpushed EnvFailureKind.COMMIT-MISSING False))
  ;; lock の hash を 1 文字変える
  (val h env.project.lock-sha256)
  (val wrong (+ (if (= (get h 0) "0") "1" "0") (cut h 1 None)))
  (<- (expect-failure world (replace env :project (replace env.project :lock-sha256 wrong)) EnvFailureKind.LOCK-MISMATCH False))
  ;; uv を失敗させる
  ;; 世界は uv の側の語で失敗を宣言し、業務の kind と一時かは翻訳が uv の出力と終わりから読み分ける。
  (for [#(fault kind retryable) [#(UvFault.LOCK-OUTDATED EnvFailureKind.LOCK-STALE False)
                                 #(UvFault.INDEX-UNREACHABLE EnvFailureKind.SYNC-FAILED True)
                                 #(UvFault.SDIST-BUILD-ERROR EnvFailureKind.SYNC-FAILED False)
                                 #(UvFault.NO-INTERPRETER EnvFailureKind.PYTHON-UNAVAILABLE True)
                                 #(UvFault.BUILD-KILLED EnvFailureKind.NATIVE-BUILD-FAILED True)
                                 #(UvFault.BUILD-ERROR EnvFailureKind.NATIVE-BUILD-FAILED False)]]
    (<- (expect-failure (replace world :uv-failure (UvFailure :fault fault :detail "uv の失敗")) env kind retryable)))
  ;; 空きを 0 にする
  (<- (expect-failure (replace world :disk-free 0) env EnvFailureKind.DISK-FULL True))
  ;; 子の約束の版が worker の扱える範囲の外
  (<- (expect-failure (replace world :child-protocol 99) env EnvFailureKind.ENV-INCOMPATIBLE False)))


(defk fetch-after-mirror-scenario [unreachable expected-kind expected-retryable]
  {:pre [(: unreachable frozenset) (: expected-kind EnvFailureKind) (: expected-retryable bool)] :post [(: % bool)]
   :tags {:context "runtime-env" :role "program"}}
  "mirror が在る状態で次の commit を取りに行く筋: 1 回目の準備で mirror を作り、届かない url を差し替えて、次の commit の準備の失敗を読む。"
  (<- env-1 RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- first (prepare env-1 #()))
  (assert (isinstance first EnvReady) first)
  (<- (set-unreachable unreachable))
  (<- unpushed RuntimeEnv (env-of "app-unpushed" "lib-1" LOCK))
  (<- failure EnvFailure (failure-of unpushed))
  (assert (= failure.kind expected-kind) (.format "{} のはずが {}: {}" expected-kind failure.kind failure.detail))
  (assert (= failure.retryable expected-retryable) failure)
  True)


(deftest test-a-fetch-that-cannot-reach-an-existing-mirror-is-unreachable-not-missing
  ;; mirror が在る状態で remote に届かなくなると、無い commit ではなく一時の届かない(repo-unreachable)で答える。
  (<- world EnvWorld (base-world))
  (<- ok bool (run-in-world world (fetch-after-mirror-scenario (frozenset #(APP-URL)) EnvFailureKind.REPO-UNREACHABLE True)))
  (assert ok)
  ;; 反例: 届く remote に commit が無い時は、恒久の commit-missing のまま。
  (<- missing-ok bool (run-in-world world (fetch-after-mirror-scenario (frozenset) EnvFailureKind.COMMIT-MISSING False)))
  (assert missing-ok))


;; --- 宣言の url の綴りと worker の許可表(2026-09-28 の事故 — daily-verify が 10 分落ちた)------------------------------
;; 送り手の checkout の remote が ssh(git@github.com:o/r.git)でも、許可表が同じ repo を https で持っていれば、同じ repo として受け、
;; clone は許可表の綴りで行う(表は許可表 1 つ — 宣言の側に綴りの表を持たない)。

(val LIB-HTTPS "https://github.com/o/lib.git")


(deftest test-a-git-url-names-its-repo-regardless-of-spelling
  (for [spelling ["https://github.com/o/lib.git" "https://github.com/o/lib" "https://github.com/o/lib/" "git@github.com:o/lib.git"
                  "ssh://git@github.com/o/lib.git" "ssh://git@github.com:22/o/lib.git" "https://GitHub.com/o/lib.git"]]
    (<- identity str (repo-identity spelling))
    (assert (= identity "github.com/o/lib") (.format "{} → {}" spelling identity)))
  ;; 反例: 別の owner・別の host・別の名は別の repo。
  (for [other ["git@github.com:p/lib.git" "https://gitlab.com/o/lib.git" "git@github.com:o/lib2.git"]]
    (<- other-identity str (repo-identity other))
    (assert (!= other-identity "github.com/o/lib") other)))


(defk https-lib-world []
  {:pre [] :post [(: % EnvWorld)]}
  "許可表の綴りを https に揃えた世界を作るため(lib の remote を LIB-HTTPS に置き換える — 許可表は世界の remote の url から作られる)。"
  (<- world EnvWorld (base-world))
  (replace world :remotes (tuple (gfor r world.remotes (if (= r.url LIB-URL) (replace r :url LIB-HTTPS) r)))))


(defk lib-spelled-env [url]
  {:pre [(: url str)] :post [(: % RuntimeEnv)]}
  "lib を url の綴りで名指す宣言を作るため(送り手の checkout の remote の綴りがそのまま入った宣言の再現)。"
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (replace env :repos (tuple (gfor r env.repos (if (= r.name "lib") (replace r :url url) r)))))


(defk ssh-spelling-scenario [env]
  {:pre [(: env RuntimeEnv)] :post [(: % bool)]}
  "ssh の綴りの宣言が準備でき、lib の mirror は許可表の綴り(https)で clone されていることを確かめるため。"
  (<- ready (prepare env #()))
  (assert (isinstance ready EnvReady) ready)
  (<- mirrors dict (files-under "/state/mirrors"))
  (val urls (sorted (gfor [path text] (.items mirrors) :if (.endswith path "/remote-url") text)))
  (assert (= urls (sorted [APP-URL LIB-HTTPS])) urls)
  True)


(deftest test-an-ssh-spelling-of-an-allowed-https-repo-is-prepared-with-the-allowed-spelling
  (<- world EnvWorld (https-lib-world))
  (<- env RuntimeEnv (lib-spelled-env "git@github.com:o/lib.git"))
  (<- ok bool (run-in-world world (ssh-spelling-scenario env)))
  (assert ok)
  ;; 反例: 許可表から外した repo は、別の綴りで名指しても断る(綴りを変えて許可表を抜けない)。
  (<- (expect-failure (replace world :denied (frozenset #(LIB-HTTPS))) env EnvFailureKind.REPO-DENIED False))
  ;; 反例: 別の owner の同じ名の repo は同じ repo ではない。
  (<- other RuntimeEnv (lib-spelled-env "git@github.com:p/lib.git"))
  (<- (expect-failure world other EnvFailureKind.REPO-DENIED False)))


(deftest test-a-third-party-package-shadowing-a-root-is-refused
  ;; 反例: 第三者の package が根と同じ最上位の名(app)を持つと、根の module が隠れるので env-incompatible で断る。
  (<- world EnvWorld (base-world))
  (<- shadow RuntimeEnv (env-of "app-shadow" "lib-1" (+ LOCK "vendor-shadow==1.0 top=app\n")))
  (<- (expect-failure world shadow EnvFailureKind.ENV-INCOMPATIBLE False)))
