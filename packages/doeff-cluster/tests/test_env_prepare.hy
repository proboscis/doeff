;; 実行環境の宣言(runtime_env_model)と準備の Program(env_prepare の prepare-env)の検 — 速い模擬(env_fake の fake-env・仮想の時計)。
;;
;; 準備の段で確かめられる筋書き(設計 worker-runtime-env.md 節 5):
;;   2 送り手の repo の commit だけ変える → 新しい root・sync は増えるが download は 0
;;   3 lock を変える → 新しいキー・download が増える
;;   4 同じ lock・別の project の commit → 新しい root・download 0・native の build 0
;;   5 native の source を変える → build が 1 回だけ増え、次の root は wheel を使い回す
;;   7 repo を 3 つ → 3 つのツリーが兄弟に並び、import の根の順が宣言どおり
;; 反例: キーから import の根を外すと根だけ違う宣言が同じ root になる・根と同じ最上位の名の第三者の package・失敗の組(節 3.6)。
;; 筋書き 1・2 の実行と 6(同時の準備)は worker と子の起動の検(E10 の便 2)で確かめる。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(import dataclasses [replace])
(import hashlib)
(import json)
(import pytest)
(import doeff [Program])
(import doeff_core_effects.handlers [state])
(import doeff_time [SimClock sim-time-handler GetMonotonic])
(import doeff_cluster.runtime_env_model [RepoCheckout NativeWheel PythonProject ToolRequirement EnvVar RuntimeEnv
                                         RuntimeEnvInvalid InvalidKind EnvFailure EnvFailureKind env-key key-material
                                         runtime-env->json runtime-env-of-json])
(import doeff_cluster.env_prepare [PrepareRequest KnownRoot EnvReady prepare-env ENV-MARKER ROOTS-PTH])
(import doeff_cluster.env_fake [fake-env FakeEnvWorld FakeEnvLog FakeRemote FakeCommit FakeFile ReadFakeEnvLog ListFakeFiles
                                SetFakeUvFailure])

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
  "完成した root の列 → 次の準備の known。"
  (tuple (gfor r ready (KnownRoot :env r.env :root r.root))))


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
;; 筋書きは 1 つの handler の組(仮想の時計 + fake-env)の下で走る defk に書き、確かめも中に置く(fake の状態はその組の間だけ)。

(defk run-in-world [world scenario]
  {:pre [(: world FakeEnvWorld) (: scenario Program)] :post [(: % bool)]}
  "筋書き(引数なしの defk の呼び出し = Program)を仮想の時計と fake-env の下で走らせる。"
  (<- ok bool ((state) ((sim-time-handler :clock (SimClock)) ((fake-env world) scenario))))
  ok)


(defk files-under [root]
  {:pre [(: root str)] :post [(: % dict)]}
  "root の下の模擬の file の path → 中身。"
  (<- files tuple (ListFakeFiles root))
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
  (assert (= (get files (.format "{}/app/.venv/{}" ready.root ROOTS-PTH)) (.format "{0}/app\n{0}/app/vendor" ready.root)))
  (val marker (json.loads (get files (.format "{}/{}" ready.root ENV-MARKER))))
  (assert (= (get marker "key") ready.key))
  (assert (= (get marker "interpreter") ready.interpreter))
  (assert (= (lfor s (get marker "stages") (get s "name"))
             ["disk" "mirror" "tree" "lock" "native" "sync" "wheels" "roots" "bytecode" "probe"]))
  True)


(deftest test-a-cold-root-is-prepared-and-marked
  (<- world FakeEnvWorld (base-world))
  (<- ok bool (run-in-world world (cold-scenario)))
  (assert ok))


(defk commit-only-scenario []
  {:pre [] :post [(: % bool)]}
  "筋書き 2・4: project の repo の commit だけ変える(同じ lock)→ 新しい root・sync は増えるが download も build も 0・
   変わらない repo のツリーは複製・bytecode は前の root から引き継ぐ。"
  (<- first-env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- first (prepare first-env #()))
  (<- before FakeEnvLog (ReadFakeEnvLog))
  (<- second-env RuntimeEnv (env-of "app-2" "lib-1" LOCK))
  (<- known tuple (known-of first))
  (<- started float (GetMonotonic))
  (<- second (prepare second-env known))
  (<- ended float (GetMonotonic))
  (<- after FakeEnvLog (ReadFakeEnvLog))
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
  (<- world FakeEnvWorld (base-world))
  (<- ok bool (run-in-world world (commit-only-scenario)))
  (assert ok))


(defk lock-change-scenario []
  {:pre [] :post [(: % bool)]}
  "筋書き 3: lock を変える → 新しいキー・増えた package だけ download・bytecode は引き継がない(Hy と doeff-hy が変わり得る)。"
  (<- first (prepare (! (env-of "app-1" "lib-1" LOCK)) #()))
  (<- known tuple (known-of first))
  (<- before FakeEnvLog (ReadFakeEnvLog))
  (<- changed (prepare (! (env-of "app-3" "lib-1" (+ LOCK "rich==13.9.4 top=rich\n"))) known))
  (<- after FakeEnvLog (ReadFakeEnvLog))
  (assert (isinstance changed EnvReady) changed)
  (assert (!= changed.key first.key))
  (assert (= changed.downloaded 1))
  (assert (= (- after.carried before.carried) 0))
  True)


(deftest test-changing-the-lock-downloads-only-the-new-packages
  (<- world FakeEnvWorld (base-world))
  (<- ok bool (run-in-world world (lock-change-scenario)))
  (assert ok))


(defk native-change-scenario []
  {:pre [] :post [(: % bool)]}
  "筋書き 5: native の source を変える → build が 1 回だけ増え、同じ source の次の root は wheel を使い回す。"
  (<- first (prepare (! (env-of "app-1" "lib-1" LOCK)) #()))
  (<- before FakeEnvLog (ReadFakeEnvLog))
  (<- changed (prepare (! (env-of "app-1" "lib-2" LOCK)) (! (known-of first))))
  (<- mid FakeEnvLog (ReadFakeEnvLog))
  (<- again (prepare (! (env-of "app-2" "lib-2" LOCK)) (! (known-of first changed))))
  (<- after FakeEnvLog (ReadFakeEnvLog))
  (assert (= (- mid.builds before.builds) 1))
  (assert (= changed.built 1))
  (assert (= (- after.builds mid.builds) 0))
  (assert (= again.built 0))
  True)


(deftest test-changing-the-native-source-builds-once
  (<- world FakeEnvWorld (base-world))
  (<- ok bool (run-in-world world (native-change-scenario)))
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
  (assert (= (get files (.format "{}/app/.venv/{}" ready.root ROOTS-PTH))
             (.format "{0}/tools/src\n{0}/app\n{0}/lib" ready.root)))
  True)


(deftest test-three-repos-sit-side-by-side-with-roots-in-declared-order
  (<- world FakeEnvWorld (base-world))
  (<- tools FakeCommit (lib-commit "tools-1" ""))
  (val tools-commit (replace tools :files #((FakeFile :path "src/tool/__init__.py" :text "Z = 3\n"))))
  (<- ok bool (run-in-world (replace world :remotes (+ world.remotes #((FakeRemote :url "file:///remotes/tools.git"
                                                                                    :commits #(tools-commit)))))
                            (three-repos-scenario)))
  (assert ok))


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
  {:pre [(: world FakeEnvWorld) (: env RuntimeEnv) (: kind EnvFailureKind) (: retryable bool)] :post [(: % bool)]}
  "world の下で env を準備すると kind の失敗(一時か恒久かも)になる。"
  (<- failure EnvFailure ((state) ((sim-time-handler :clock (SimClock)) ((fake-env world) (failure-of env)))))
  (assert (= failure.kind kind) (.format "{} のはずが {}: {}" kind failure.kind failure.detail))
  (assert (= failure.retryable retryable) failure)
  True)


(deftest test-each-failure-comes-back-as-its-kind
  (<- world FakeEnvWorld (base-world))
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  ;; URL を許可表から外す / 届かない
  (<- (expect-failure (replace world :denied (frozenset #(LIB-URL))) env EnvFailureKind.REPO-DENIED False))
  (<- (expect-failure (replace world :unreachable (frozenset #(APP-URL))) env EnvFailureKind.REPO-UNREACHABLE True))
  ;; commit を push しない
  (<- (expect-failure world (! (env-of "app-unpushed" "lib-1" LOCK)) EnvFailureKind.COMMIT-MISSING False))
  ;; lock の hash を 1 文字変える
  (val h env.project.lock-sha256)
  (val wrong (+ (if (= (get h 0) "0") "1" "0") (cut h 1 None)))
  (<- (expect-failure world (replace env :project (replace env.project :lock-sha256 wrong)) EnvFailureKind.LOCK-MISMATCH False))
  ;; uv を失敗させる
  (for [#(kind retryable) [#(EnvFailureKind.LOCK-STALE False) #(EnvFailureKind.SYNC-FAILED True)
                           #(EnvFailureKind.PYTHON-UNAVAILABLE True) #(EnvFailureKind.NATIVE-BUILD-FAILED False)]]
    (<- (expect-failure (replace world :uv-failure (EnvFailure :kind kind :detail "uv の失敗" :retryable retryable))
                        env kind retryable)))
  ;; 空きを 0 にする
  (<- (expect-failure (replace world :disk-free 0) env EnvFailureKind.DISK-FULL True))
  ;; 子の約束の版が worker の扱える範囲の外
  (<- (expect-failure (replace world :child-protocol 99) env EnvFailureKind.ENV-INCOMPATIBLE False)))


(deftest test-a-third-party-package-shadowing-a-root-is-refused
  ;; 反例: 第三者の package が根と同じ最上位の名(app)を持つと、根の module が隠れるので env-incompatible で断る。
  (<- world FakeEnvWorld (base-world))
  (<- (expect-failure world (! (env-of "app-shadow" "lib-1" (+ LOCK "vendor-shadow==1.0 top=app\n")))
                      EnvFailureKind.ENV-INCOMPATIBLE False)))
