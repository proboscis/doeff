;; 実行環境の宣言(runtime_env_model)と準備の Program(env_prepare の prepare-env)の検 — 速い模擬(env_world の模擬の世界・仮想の時計)。
;;
;; 準備の段で確かめられる筋書き(設計 worker-runtime-env.md 節 5):
;;   2 送り手の repo の commit だけ変える → 新しい root・sync は増えるが download は 0
;;   3 lock を変える → 新しいキー・download が増える
;;   4 同じ lock・別の project の commit → 新しい root・download 0・native の build 0
;;   5 native の source を変える → build が 1 回だけ増え、次の root は wheel を使い回す
;;   7 repo を 3 つ → 3 つのツリーが兄弟に並び、import の根の順が宣言どおり・bytecode は 3 つの木を焼きの子 process 1 回で焼き、
;;     その前と後に進みの印を触る
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
(import doeff_cluster.worker.protocol.env_translation [editable-dirs hy-dist-version])
(import doeff_core_effects.file_effects [ListDirectory])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.scheduler [Spawn Task Gather])
(import doeff_time [SimClock sim-time-handler GetMonotonic])
(import doeff_cluster.shared.intent.runtime_env_model [RepoCheckout NativeWheel PythonProject ToolRequirement EnvVar RuntimeEnv
                                                       RuntimeEnvInvalid InvalidKind EnvFailure EnvFailureKind])
(import doeff_cluster.shared.core.runtime_env_rules [env-key key-material runtime-env->json runtime-env-of-json])
(import doeff_cluster.worker.core.env_prepare [prepare-env carry-source] doeff_cluster.worker.intent.env_prepare_model [PrepareRequest KnownRoot EnvReady ROOTS-PTH StageStarted CarryFrom] doeff_cluster.shared.intent.env_marker_model [ENV-MARKER])
(import doeff_cluster.sim.env_world [env-world EnvWorld EnvWorldLog WorldRemote WorldCommit WorldFile read-world-log world-files
                                set-uv-failure set-unreachable UvFailure UvFault BAKE-CARRY-SECONDS BAKE-COMPILE-SECONDS
                                BAKE-CLOSURE-SECONDS BAKE-SCAN-SECONDS])

(setv PLATFORM "linux-x86_64")
(import tests.env_fixtures [LOCK HY-2-LOCK APP-URL LIB-URL sha-of lock-sha app-commit lib-commit env-of base-world])


(defk prepare [env known]
  {:pre [(: env RuntimeEnv) (: known tuple)] :post [(: % (| EnvReady EnvFailure))]}
  "worker と同じ置き場(state/roots/<キー>)に root を準備する。"
  (<- key str (env-key env PLATFORM))
  (<- result (| EnvReady EnvFailure)
      (prepare-env (PrepareRequest :env env :key key :platform PLATFORM :root (.format "/state/roots/{}" key)
                                   :known known :min-free-bytes 1024)))
  result)


(defk marker-hy-version [ready]
  {:pre [(: ready EnvReady)] :post [(: % (| str None))]}
  "完成した root の印に書いた Hy の compiler の版を読むため(worker の known-roots と同じく、次の準備の known へ印の値を渡す — #3706)。"
  (<- files tuple (world-files ready.root))
  (val marker (json.loads (next (gfor f files :if (= f.path (.format "{}/{}" ready.root ENV-MARKER)) f.text))))
  (get marker "hyVersion"))


(defk known-of [#* ready]
  {:pre [(: ready tuple)] :post [(: % tuple)]}
  "完成した root の列 → 次の準備の known(どれも完成した root(EnvReady)であることを確かめてから読む・完成の時刻は列の順・Hy の版は
   印から読む)。"
  (for [r ready]
    (assert (isinstance r EnvReady) r))
  (var known #())
  (for [#(made r) (enumerate ready)]
    (<- hy-version (| str None) (marker-hy-version r))
    (:= known (+ known #((KnownRoot :env r.env :root r.root :made-ms made :hy-version hy-version)))))
  known)


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


(defk listed-hy-version [site]
  {:pre [(: site str)] :post [(: % (| str None))]}
  "本物の file の一覧(ListDirectory)から翻訳の Hy の版の読み(hy-dist-version)の答えを返すため。"
  (<- entries tuple (ListDirectory site))
  (<- version (| str None) (hy-dist-version entries))
  version)


(deftest test-the-hy-version-is-read-from-the-venv-dist-info [#^ Path tmp-path]
  ;; 本物の handler の読み(#3706): site-packages の `hy-<版>.dist-info` の dir の名から版を読む。名が hy で始まる別の package
  ;; (hy_extra・hyperlink)と、同じ名の file は読まない。Hy の dist-info が無ければ None(引き継ぎ元を選ばない)。
  (val site (/ tmp-path "site-packages"))
  (for [d ["hy_extra-2.0.dist-info" "hyperlink-21.0.dist-info" "httpx-0.28.1.dist-info"]] (.mkdir (/ site d) :parents True))
  (.write-text (/ site "hy-9.9.dist-info") "")
  (<- missing (| str None) (with-handlers [os-file-handler] (listed-hy-version (str site))))
  (assert (is missing None) missing)
  (.mkdir (/ site "hy-1.1.0.dist-info"))
  (<- found (| str None) (with-handlers [os-file-handler] (listed-hy-version (str site))))
  (assert (= found "1.1.0") found))


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


;; 失敗ケース(#3607 の H2): 準備の後の完成マーカーに、bytecode の処理ステージの焼いた数と処理ごとの秒が、焼く道具の報告(模擬の道具の
;; 全体の行と木ごとの行)と同じ値で載る — 次に準備が長い時、どの処理で時間を使ったかを印 1 つで読むため。印に書く所(prepare-env の
;; EnvMarker の bytecode・env-marker->json の欄)を外すと、欄が null か無くなって赤。木は名と数だけ(path は載せない)。
(defk bytecode-marker-scenario []
  {:pre [] :post [(: % bool)]}
  "業務の木(app)と editable の木(lib)を焼く準備の完成マーカーの bytecode の欄が、模擬の焼く道具の報告と同じ値である事を確かめるため。"
  (<- env RuntimeEnv (env-of "app-editable" "lib-1" EDITABLE-LOCK))
  (<- ready (prepare env #()))
  (assert (isinstance ready EnvReady) ready)
  (<- files dict (files-under ready.root))
  (val marker (json.loads (get files (.format "{}/{}" ready.root ENV-MARKER))))
  (assert (= (get marker "bytecode")
             {"carried" 0 "rebuilt" 4 "reused" 0 "failed" 0
              "scanSeconds" BAKE-SCAN-SECONDS "closureSeconds" BAKE-CLOSURE-SECONDS
              "carrySeconds" BAKE-CARRY-SECONDS "compileSeconds" BAKE-COMPILE-SECONDS
              "trees" [{"name" "app" "carried" 0 "rebuilt" 2 "reused" 0 "failed" 0}
                       {"name" "lib" "carried" 0 "rebuilt" 2 "reused" 0 "failed" 0}]})
          (get marker "bytecode"))
  True)


(deftest test-the-ready-marker-carries-the-bytecode-counts-and-seconds-the-baker-reported
  (<- world EnvWorld (base-world))
  (<- app WorldCommit (app-commit "app-editable" EDITABLE-LOCK "V = 5\n"))
  (<- ok bool (run-in-world (replace world :remotes (tuple (gfor r world.remotes
                                                                 (if (= r.url APP-URL) (replace r :commits (+ r.commits #(app))) r))))
                            (bytecode-marker-scenario)))
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


;; 失敗ケース(#3675): 前の root から bytecode を引き継ぐ準備は、焼く道具に「引き継ぎ元の commit から変わった path の一覧」(git diff)を
;; 渡す。渡さないと道具は変わった file の .pyc も持ち越し、Python の source は焼く計画から外れ(.pyc が在る)・Hy の source は全部を pool で
;; 照らし直す(本番の agent-worker-2 の印で compiled = 計画の数)。版ごとのコードの木の準備(worker/core/code_rules)は前から git diff を渡している。
;; 翻訳が --changed を渡さない形に戻すと、模擬の道具が受けた一覧が空になって赤。一覧の file は焼いた後に消える。
(defk changed-list-scenario []
  {:pre [] :post [(: % bool)]}
  "app の commit だけ変えた 2 つ目の準備で、焼く道具が app の木の変わった path(app/__init__.py)だけを一覧で受け、引き継ぎの無い 1 つ目の
   準備では空の一覧を受ける事を確かめるため。"
  (<- first-env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- first EnvReady (prepare first-env #()))
  (<- before EnvWorldLog (read-world-log))
  (assert (= (tuple (gfor #(_ paths) before.changed paths)) #(#())) before.changed)
  (<- second-env RuntimeEnv (env-of "app-2" "lib-1" LOCK))
  (<- known tuple (known-of first))
  (<- second (prepare second-env known))
  (assert (isinstance second EnvReady) second)
  (<- after EnvWorldLog (read-world-log))
  (val given (cut after.changed (len before.changed) None))
  (assert (= given #(#((.format "{}/app" second.root) #("app/__init__.py")))) given)
  (<- left tuple (world-files "/state/changed/"))
  (assert (= left #()) left)
  True)


(deftest test-a-carried-bake-is-given-the-paths-changed-since-the-carry-source
  (<- world EnvWorld (base-world))
  (<- ok bool (run-in-world world (changed-list-scenario)))
  (assert ok))


(defk lock-change-scenario []
  {:pre [] :post [(: % bool)]}
  "筋書き 3: lock を変える(Hy の版は同じ)→ 新しいキー・増えた package だけ download・bytecode は前の root から引き継ぎ、変わった
   source だけを焼く(#3706 — 失敗ケース: 引き継ぎ元の候補の条件に lock の一致を残すと、依存を 1 本足しただけで木の全部を焼き直し、
   carried が 0 で赤)。"
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
  (assert (> (- after.carried before.carried) 0) "lock だけ違う前の root から bytecode を引き継ぐ")
  ;; app の木で焼くのは、app-1 から変わった source(app/__init__.py)の 1 つだけ — 変わらない vendor/tool.py は引き継ぐ。
  (<- hy-version (| str None) (marker-hy-version changed))
  (assert (= hy-version HY-VERSION) hy-version)
  (<- files tuple (world-files changed.root))
  (val marker (json.loads (next (gfor f files :if (= f.path (.format "{}/{}" changed.root ENV-MARKER)) f.text))))
  (val app-tree (next (gfor t (get marker "bytecode" "trees") :if (= (get t "name") "app") t)))
  (assert (= #((get app-tree "rebuilt") (get app-tree "carried")) #(1 1)) app-tree)
  True)


(deftest test-changing-the-lock-downloads-only-the-new-packages
  (<- world EnvWorld (base-world))
  (<- ok bool (run-in-world world (lock-change-scenario)))
  (assert ok))


(defk hy-change-scenario []
  {:pre [] :post [(: % bool)]}
  "lock の hy の行の版を変える → venv の Hy の compiler の版が違うので、前の root から bytecode を引き継がない(#3706 — 反例: Hy の版を
   比べずに候補にすると、別の compiler で焼いた .pyc を持ち越して carried が 0 でなく赤)。"
  (<- env-1 RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- first EnvReady (prepare env-1 #()))
  (<- known tuple (known-of first))
  (<- before EnvWorldLog (read-world-log))
  (<- env-hy RuntimeEnv (env-of "app-hy" "lib-1" HY-2-LOCK))
  (<- changed (prepare env-hy known))
  (<- after EnvWorldLog (read-world-log))
  (assert (isinstance changed EnvReady) changed)
  (<- hy-version (| str None) (marker-hy-version changed))
  (assert (= hy-version "1.2.0") hy-version)
  (assert (= (- after.carried before.carried) 0))
  True)


(deftest test-changing-the-hy-version-does-not-carry-bytecode
  (<- world EnvWorld (base-world))
  (<- ok bool (run-in-world world (hy-change-scenario)))
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


(defk one-bake-scenario []
  {:pre [] :post [(: % bool)]}
  "筋書き 7 の root(焼く根を持つ repo の木 3 つ)の準備で、bytecode の焼きの子 process が 1 回だけ起き、3 つの木を全部焼く事を確かめるため。"
  (<- before EnvWorldLog (read-world-log))
  (<- ok bool (three-repos-scenario))
  (<- after EnvWorldLog (read-world-log))
  (assert (= (- after.compiles before.compiles) 1)
          (.format "焼きの子 process は 1 回のはずが {} 回(木ごとに起こしている)" (- after.compiles before.compiles)))
  (assert (= (sorted (gfor #(tree _) after.compiled-trees (get (.rsplit tree "/" 1) 1))) ["app" "lib" "tools"]) after.compiled-trees)
  ok)


(deftest test-the-bytecode-stage-bakes-every-tree-in-one-call-and-marks-progress-around-it
  ;; 失敗ケース(c): 焼く根を持つ repo の木 3 つを、bytecode の焼きの子 process 1 回で焼く — 木ごとに起こすと、木 1 つの終わりを待つ間
  ;; ほかの core が遊ぶ。木ごとの呼びに戻すと、模擬の数え(焼きの呼びの回数)が 3 になって赤。
  ;; 進みの印: 1 回の焼きの前と後に触り直す(#3515 — 頭の印だけでは、bytecode を 267.9 秒焼いている準備も worker から停滞に見えた)。
  ;; = 頭の印 1 つ + 前と後の 2 つ。ほかの処理ステージは頭の印だけ。
  (<- world EnvWorld (base-world))
  (<- tools WorldCommit (lib-commit "tools-1" ""))
  (val tools-commit (replace tools :files #((WorldFile :path "src/tool/__init__.py" :text "Z = 3\n"))))
  (val marks (StageMarks))
  (<- handlers list (env-world (replace world :remotes (+ world.remotes #((WorldRemote :url "file:///remotes/tools.git"
                                                                                       :commits #(tools-commit)))))))
  (<- ok bool ((state) ((sim-time-handler :clock (SimClock)) (with-handlers (+ handlers [(seen-stages marks)]) (one-bake-scenario)))))
  (assert ok)
  (assert (= marks.names #("disk" "mirror" "tree" "lock" "native" "sync" "wheels" "roots" "bytecode" "bytecode" "bytecode" "probe"))
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


;; 新しい root の venv の Hy の compiler の版(LOCK の hy の行の版 — 引き継ぎ元の候補は同じ版の root に限る・#3706)。
(val HY-VERSION "1.1.0")


(defk picked-tree [known env name]
  {:pre [(: known tuple) (: env RuntimeEnv) (: name str)] :post [(: % (| str None))]}
  "引き継ぎ元の選び(carry-source — 新しい root の Hy の版は HY-VERSION)が選んだ木の path を読むため(選ばなければ None)— 選んだ木の
   commit は、その木を展開した commit で
   ある事も確かめる(変わった file を決める git diff の片側 — #3675)。"
  (<- picked (| CarryFrom None) (carry-source known env name HY-VERSION))
  (match picked
    None None
    (CarryFrom :tree tree :commit commit)
      (do (val root (next (gfor k known :if (.startswith tree (+ k.root "/")) k)))
          (val repo (next (gfor r root.env.repos :if (= r.name name) r)))
          (assert (= commit repo.commit) #(picked repo))
          tree)))


(defk carry-root [dir app doeff made-ms]
  {:pre [(: dir str) (: app str) (: doeff str) (: made-ms int)] :post [(: % KnownRoot)]}
  "完成済みの root 1 つ(/state/roots/<dir>・app と doeff の commit・完成の時刻・Hy の版は HY-VERSION)を引き継ぎ元の候補として
   作るため。"
  (<- env RuntimeEnv (carry-env app doeff))
  (KnownRoot :env env :root (.format "/state/roots/{}" dir) :made-ms made-ms :hy-version HY-VERSION))


(deftest test-the-bytecode-carry-prefers-a-root-at-the-same-commits
  ;; 失敗ケース(直す前は赤): dir の名で先に来る古い commit の root(aaaa)ではなく、新しい宣言と同じ app と doeff の commit で組んだ
  ;; root(bbbb)を選ぶ(2026-10-05 の本番の形)。
  (<- env RuntimeEnv (carry-env "app-new" "doeff-new"))
  (<- older KnownRoot (carry-root "aaaa" "app-old" "doeff-old" 1000))
  (<- same KnownRoot (carry-root "bbbb" "app-new" "doeff-new" 2000))
  (<- picked (| str None) (picked-tree #(older same) env "app"))
  (assert (= picked "/state/roots/bbbb/app") picked)
  ;; 両方の commit が同じ root は、doeff の commit だけ・app の commit だけが同じで後から完成した root より先(完成の時刻より
  ;; commit の一致が先)。
  (<- newer KnownRoot (carry-root "cccc" "app-other" "doeff-new" 3000))
  (<- same-app KnownRoot (carry-root "dddd" "app-new" "doeff-old" 4000))
  (<- still (| str None) (picked-tree #(older same newer same-app) env "app"))
  (assert (= still "/state/roots/bbbb/app") still)
  ;; doeff のツリーは、同じ doeff の commit の root が 2 つ在るので、後から完成した方(cccc)。
  (<- macros-tree (| str None) (picked-tree #(older same newer) env "doeff"))
  (assert (= macros-tree "/state/roots/cccc/doeff") macros-tree)
  ;; Python か venv の Hy の compiler の版が違う root と、Hy の版が分からない root(印に欄の無い前の root)は、同じ commit で後から
  ;; 完成していても候補にしない(.pyc の magic と abi は Python で、Hy の展開は Hy の compiler で決まる — #3706)。
  (val other-python (replace same :root "/state/roots/0001" :made-ms 4000
                                  :env (replace same.env :project (replace same.env.project :python "3.13"))))
  (val other-hy (replace same :root "/state/roots/0002" :made-ms 4000 :hy-version "1.2.0"))
  (val unknown-hy (replace same :root "/state/roots/0003" :made-ms 4000 :hy-version None))
  (<- kept (| str None) (picked-tree #(other-python other-hy unknown-hy older same) env "app"))
  (assert (= kept "/state/roots/bbbb/app") kept)
  ;; 新しい root の Hy の版が分からない時(venv に Hy が無い)は、どの root からも引き継がない。
  (<- unknown-new (| CarryFrom None) (carry-source #(older same) env "app" None))
  (assert (is unknown-new None) unknown-new))


(deftest test-the-bytecode-carry-ignores-a-lock-that-differs
  ;; 失敗ケース(直す前は赤 — #3706・記録の表の service の worker で 228.6 秒・compiled 1102・carried 0): 依存を 1 本上げただけで
  ;; lock の sha256 だけが違う root(Python・Hy の版・repo の正体・commit は同じ)は引き継ぎ元に選ばれる。lock の一致を条件に残すと、
  ;; 後から完成した lock 違いの root(0000)でなく bbbb を選び、bbbb も lock 違いなら候補が無くなって赤。
  (<- env RuntimeEnv (carry-env "app-new" "doeff-new"))
  (<- same KnownRoot (carry-root "bbbb" "app-new" "doeff-new" 2000))
  (val other-lock (replace same :root "/state/roots/0000" :made-ms 4000
                                :env (replace same.env :project (replace same.env.project :lock-sha256 (* "c" 64)))))
  (<- picked (| str None) (picked-tree #(same other-lock) env "app"))
  (assert (= picked "/state/roots/0000/app") picked)
  (<- only (| str None) (picked-tree #(other-lock) env "app"))
  (assert (= only "/state/roots/0000/app") only))


(deftest test-the-bytecode-carry-falls-back-to-a-root-with-the-same-macros
  ;; 失敗ケース(直す前は赤): 同じ app の commit の root が無い時は、doeff(Hy の macro の repo)の commit が同じ root(bbbb)を、
  ;; dir の名で先に来る root(aaaa — 後から完成したが doeff の commit が違う)より先に選ぶ。
  (<- env RuntimeEnv (carry-env "app-new" "doeff-new"))
  (<- older KnownRoot (carry-root "aaaa" "app-old" "doeff-old" 3000))
  (<- macros KnownRoot (carry-root "bbbb" "app-other" "doeff-new" 1000))
  (<- picked (| str None) (picked-tree #(older macros) env "app"))
  (assert (= picked "/state/roots/bbbb/app") picked))


(deftest test-the-same-macros-beat-the-same-repo-commit
  ;; 失敗ケース(直す前は赤): app の commit が同じで doeff の commit が違う root(aaaa — dir の名で先・後から完成)は、app の commit が
  ;; 違い doeff の commit が同じ root(bbbb)に負ける。Hy の .pyc は使った macro の file が変わると全部無効になるので、aaaa から引き継ぐと
  ;; 全部を組み直し、bbbb からなら変わった app の file の分だけを組む。
  (<- env RuntimeEnv (carry-env "app-new" "doeff-new"))
  (<- same-app KnownRoot (carry-root "aaaa" "app-new" "doeff-old" 3000))
  (<- same-macros KnownRoot (carry-root "bbbb" "app-other" "doeff-new" 1000))
  (<- picked (| str None) (picked-tree #(same-app same-macros) env "app"))
  (assert (= picked "/state/roots/bbbb/app") picked))


(deftest test-the-bytecode-carry-takes-the-newest-root-when-no-version-matches
  ;; 失敗ケース(直す前は赤): app の commit も doeff の commit も合う root が無い時は、最も新しく完成した root(bbbb)を選ぶ
  ;; (dir の名で先に来る aaaa ではない)。
  (<- env RuntimeEnv (carry-env "app-new" "doeff-new"))
  (<- oldest KnownRoot (carry-root "aaaa" "app-old" "doeff-old" 1000))
  (<- newest KnownRoot (carry-root "bbbb" "app-other" "doeff-other" 3000))
  (<- middle KnownRoot (carry-root "cccc" "app-third" "doeff-third" 2000))
  (<- picked (| str None) (picked-tree #(oldest newest middle) env "app"))
  (assert (= picked "/state/roots/bbbb/app") picked)
  ;; 完成の時刻が同じなら root の path の順(known の並びに依らない)。
  (<- twin KnownRoot (carry-root "0000" "app-twin" "doeff-twin" 3000))
  (<- tied (| str None) (picked-tree #(oldest newest twin middle) env "app"))
  (assert (= tied "/state/roots/0000/app") tied)
  ;; 宣言に doeff の repo が無ければ doeff の条件は使わず、app の commit が同じ root(bbbb — dir の名で aaaa より後・aaaa より前に完成)
  ;; → 最も新しく完成した root の順。
  (<- plain RuntimeEnv (env-of "app-new" "lib-1" LOCK))
  (<- plain-old RuntimeEnv (env-of "app-old" "lib-1" LOCK))
  (<- plain-other RuntimeEnv (env-of "app-other" "lib-1" LOCK))
  (val plain-older (KnownRoot :env plain-old :root "/state/roots/aaaa" :made-ms 2000 :hy-version HY-VERSION))
  (val plain-same (KnownRoot :env plain :root "/state/roots/bbbb" :made-ms 1000 :hy-version HY-VERSION))
  (val plain-newest (KnownRoot :env plain-other :root "/state/roots/cccc" :made-ms 3000 :hy-version HY-VERSION))
  (<- plain-picked (| str None) (picked-tree #(plain-older plain-same plain-newest) plain "app"))
  (assert (= plain-picked "/state/roots/bbbb/app") plain-picked)
  (<- plain-newer (| str None) (picked-tree #(plain-older plain-newest) plain "app"))
  (assert (= plain-newer "/state/roots/cccc/app") plain-newer)
  ;; 候補が無ければ引き継がない。
  (<- nothing (| str None) (picked-tree #() env "app"))
  (assert (is nothing None) nothing))


;; 反例(2026-10-06 の cluster・#3693): 同じ repo を https(https://github.com/o/r.git)と scp の形
;; (git@github.com:o/r.git)で宣言した root どうしが、url の綴りの比べで候補にならず、全部を焼き直した。
;; 候補の比べは url の正体(url-location — host・owner・name)の一致で、手元の path どうしだけが綴りの一致。

(defk spelled-env [app app-url doeff doeff-url]
  {:pre [(: app str) (: app-url str) (: doeff str) (: doeff-url str)] :post [(: % RuntimeEnv)]}
  "carry-env の app と doeff の url を、渡した綴りに置き換えた宣言を作るため(lock と Python は carry-env と同じ)。"
  (<- base RuntimeEnv (carry-env app doeff))
  (replace base :repos #((replace (get base.repos 0) :url app-url) (replace (get base.repos 1) :url doeff-url))))


(deftest test-the-bytecode-carry-matches-a-repo-spelled-another-way
  ;; 失敗ケース(直す前は赤): 引き継ぎ元の root は scp の形(git@github.com:o/app)、新しい宣言は https(…/o/app.git)で同じ repo を
  ;; 名指す — 引き継ぎ元に選ばれる。
  (<- env RuntimeEnv (spelled-env "app-new" "https://github.com/o/app.git" "doeff-new" "https://github.com/o/doeff.git"))
  (<- env-scp RuntimeEnv (spelled-env "app-new" "git@github.com:o/app" "doeff-new" "git@github.com:o/doeff"))
  (val scp-root (KnownRoot :env env-scp :root "/state/roots/aaaa" :made-ms 1000 :hy-version HY-VERSION))
  (<- picked (| str None) (picked-tree #(scp-root) env "app"))
  (assert (= picked "/state/roots/aaaa/app") picked)
  ;; macro の repo(doeff)も綴りに依らず同じ repo と読む: doeff の commit だけが同じ scp の形の root(bbbb)は、後から完成した
  ;; どの commit も違う root(cccc)より先。
  (<- env-macros RuntimeEnv (spelled-env "app-other" "ssh://git@github.com/o/app.git" "doeff-new" "git@github.com:o/doeff.git"))
  (<- env-none RuntimeEnv (spelled-env "app-third" "https://github.com/o/app" "doeff-old" "https://github.com/o/doeff"))
  (val macros-root (KnownRoot :env env-macros :root "/state/roots/bbbb" :made-ms 1000 :hy-version HY-VERSION))
  (val none-root (KnownRoot :env env-none :root "/state/roots/cccc" :made-ms 2000 :hy-version HY-VERSION))
  (<- by-macros (| str None) (picked-tree #(none-root macros-root) env "app"))
  (assert (= by-macros "/state/roots/bbbb/app") by-macros)
  ;; 反例: owner か name か host が違えば別の repo — 候補にしない。
  (for [other ["git@github.com:p/app" "git@github.com:o/app2" "git@gitlab.com:o/app"]]
    (<- other-env RuntimeEnv (spelled-env "app-new" other "doeff-new" "git@github.com:o/doeff"))
    (<- other-picked (| str None) (picked-tree #((KnownRoot :env other-env :root "/state/roots/dddd" :made-ms 1000 :hy-version HY-VERSION))
                                                 env "app"))
    (assert (is other-picked None) #(other other-picked))))


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
  ;; 届かない(鍵の表に無い url は断らない — test-a-url-missing-from-the-key-table-is-cloned-without-a-key)
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
                                 ;; signal 9 で殺されたが cgroup の oom_kill は増えない = 今までどおり一時の native-build-failed
                                 #(UvFault.BUILD-KILLED EnvFailureKind.NATIVE-BUILD-FAILED True)
                                 ;; cgroup の memory の上限で殺された(signal 9・oom_kill が増えた)= 恒久の memory-killed(#3668)
                                 #(UvFault.BUILD-MEMORY-KILLED EnvFailureKind.MEMORY-KILLED False)
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


;; --- 宣言の url の綴りと worker の鍵の表(2026-09-28 の事故 — daily-verify が 10 分落ちた)------------------------------
;; 送り手の checkout の remote が ssh(git@github.com:o/r.git)でも、鍵の表が同じ repo を https で持っていれば、同じ repo として
;; clone は表の綴りで行う(表は鍵の表 1 つ — 宣言の側に綴りの表を持たない)。鍵の表は url を断らない(2026-10-05 に断る分岐と
;; repo-denied を外した): 表に無い url は宣言の綴りのまま鍵なしで clone へ進む。

(val LIB-HTTPS "https://github.com/o/lib.git")


(defk https-lib-world []
  {:pre [] :post [(: % EnvWorld)]}
  "鍵の表の綴りを https に揃えた世界を作るため(lib の remote を LIB-HTTPS に置き換える — 鍵の表は世界の remote の url から作られる)。"
  (<- world EnvWorld (base-world))
  (replace world :remotes (tuple (gfor r world.remotes (if (= r.url LIB-URL) (replace r :url LIB-HTTPS) r)))))


(defk lib-spelled-env [url]
  {:pre [(: url str)] :post [(: % RuntimeEnv)]}
  "lib を url の綴りで名指す宣言を作るため(送り手の checkout の remote の綴りがそのまま入った宣言の再現)。"
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (replace env :repos (tuple (gfor r env.repos (if (= r.name "lib") (replace r :url url) r)))))


(defk cloned-as-scenario [env expected]
  {:pre [(: env RuntimeEnv) (: expected tuple)] :post [(: % bool)]}
  "宣言が準備でき(断られず)、mirror が expected の綴りの url で clone されたことを確かめるため — worker が取りに行った綴りを外から読む。"
  (<- ready (prepare env #()))
  (assert (isinstance ready EnvReady) ready)
  (<- mirrors dict (files-under "/state/mirrors"))
  (val urls (sorted (gfor [path text] (.items mirrors) :if (.endswith path "/remote-url") text)))
  (assert (= urls (sorted expected)) urls)
  True)


(deftest test-an-ssh-spelling-of-a-listed-https-repo-is-prepared-with-the-listed-spelling
  (<- world EnvWorld (https-lib-world))
  (<- env RuntimeEnv (lib-spelled-env "git@github.com:o/lib.git"))
  (<- ok bool (run-in-world world (cloned-as-scenario env #(APP-URL LIB-HTTPS))))
  (assert ok)
  ;; 反例: 鍵の表から外した repo を別の綴りで名指すと、表の綴りには引き当てず、宣言の綴りのまま clone する(断らない)。
  (<- unlisted bool (run-in-world (replace world :unlisted (frozenset #(LIB-HTTPS)))
                                  (cloned-as-scenario env #(APP-URL "git@github.com:o/lib.git"))))
  (assert unlisted)
  ;; 反例: 別の owner の同じ名の repo は同じ repo ではない — 表の綴り(o/lib)に引き当てず、宣言の綴り(p/lib)のまま clone する。
  (<- other RuntimeEnv (lib-spelled-env "git@github.com:p/lib.git"))
  (<- other-ok bool (run-in-world world (cloned-as-scenario other #(APP-URL "git@github.com:p/lib.git"))))
  (assert other-ok))


(deftest test-a-url-missing-from-the-key-table-is-cloned-without-a-key
  ;; 鍵の表は url に deploy key を結ぶだけで、url を断らない。表に無い url は鍵なしで clone へ進む。
  (<- world EnvWorld (base-world))
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  ;; 表に無い非公開の repo(鍵なしでは読めない — 模擬では届かない url)は、断りではなく clone の失敗(一時の repo-unreachable)で返る。
  (<- (expect-failure (replace world :unlisted (frozenset #(LIB-URL)) :unreachable (frozenset #(LIB-URL)))
                      env EnvFailureKind.REPO-UNREACHABLE True))
  ;; lib だけ表に無い: 断らずに宣言の綴りで clone し、root を完成させる。
  (<- one bool (run-in-world (replace world :unlisted (frozenset #(LIB-URL))) (cloned-as-scenario env #(APP-URL LIB-URL))))
  (assert one)
  ;; 表が空(--repo-keys が空の worker): どの url も断らない。
  (<- empty bool (run-in-world (replace world :unlisted (frozenset (gfor r world.remotes r.url)))
                               (cloned-as-scenario env #(APP-URL LIB-URL))))
  (assert empty))


(deftest test-a-third-party-package-shadowing-a-root-is-refused
  ;; 反例: 第三者の package が根と同じ最上位の名(app)を持つと、根の module が隠れるので env-incompatible で断る。
  (<- world EnvWorld (base-world))
  (<- shadow RuntimeEnv (env-of "app-shadow" "lib-1" (+ LOCK "vendor-shadow==1.0 top=app\n")))
  (<- (expect-failure world shadow EnvFailureKind.ENV-INCOMPATIBLE False)))
