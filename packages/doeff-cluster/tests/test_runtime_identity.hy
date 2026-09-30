;; 入口の検め(doeff_cluster/runtime_identity.hy)の検。
;;
;;   * 宣言・印・キー・module の置き場が揃えば一致を答え、宣言の repo と commit と pid を返す。
;;   * env-vars だけ違う宣言が同じ root(同じキー)を使い回しても一致(キーの材料は root の中身を決める欄だけ)。
;;   * 反例: 宣言が無い(image の venv で起きた)・印の無い root・読めない形式の印・渡されたキーか印のキーの違い・宣言の root の外
;;     から import した module・root の venv(第三者の package の置き場)から import した module・import できない module —
;;     それぞれ失敗の kind で名乗る。
;;   * この process を読む handler(process-runtime-facts)と材料を渡す handler(given-runtime-facts)が同じ場面に同じ答えを返すことは
;;     契約テスト test_runtime_facts_contract.hy。
;; 印は本物の書き手(env_prepare の EnvMarker と env-marker->json)で作り、キーは env-key で計算する(形式を手書きしない — 書き手の形が
;; 変われば検が赤になる)。
(require doeff-hy.macros [<- val])
(import json)
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_cluster.runtime_env_model [RuntimeEnv RepoCheckout PythonProject EnvVar runtime-env->json env-key])
(import doeff_cluster.env_prepare [EnvMarker env-marker->json])
(import doeff_cluster.runtime_identity [IdentityFailureKind ModuleOrigin ProcessFacts RuntimeIdentity RuntimeIdentityMismatch
                                          RepoCommit check-runtime-identity given-runtime-facts])

(val AC-COMMIT (* "a" 40))
(val DF-COMMIT (* "d" 40))
(val OTHER-COMMIT (* "b" 40))
(val PLATFORM "linux-x86_64")
(val PID 4242)
(val MODULES #("app_jobs" "doeff" "doeff_cluster"))


(defn done [program]
  (run (scheduled program)))


(defn env-of [#^ str ac-commit #** extra]
  (RuntimeEnv :repos #((RepoCheckout :name "app" :url "https://example.com/app.git"
                                     :commit ac-commit)
                       (RepoCheckout :name "doeff" :url "https://github.com/proboscis/doeff.git" :commit DF-COMMIT))
              :project (PythonProject :repo "app" :path "." :lock-sha256 (* "0" 64) :python "3.14")
              :import-roots #("app/." "app/vendor")
              #** extra))


(defn key-of [#^ RuntimeEnv env]
  (done (env-key env PLATFORM)))


(defn env-json [#^ RuntimeEnv env]
  "宣言 → worker が DOEFF_RUNTIME_ENV に置くのと同じ JSON の文字列。"
  (json.dumps (done (runtime-env->json env))))


(defn marker-json [#^ RuntimeEnv env #** over]
  "doeff-cluster の準備が root に置くのと同じ完成の印の中身(over で欄を差し替える)。"
  (setv raw (done (env-marker->json (EnvMarker :env env :key (key-of env) :platform PLATFORM :stages #() :downloaded 0
                                               :built 0 :interpreter "/usr/bin/python3" :child-protocol 1))))
  (.update raw over)
  (json.dumps raw))


(val DECLARED (env-of AC-COMMIT))
(val KEY (key-of DECLARED))
(val ROOT (+ "/work/envs/" KEY))
(val IN-ROOT #((ModuleOrigin :module "app_jobs" :file (+ ROOT "/app/app_jobs/__init__.py"))
               (ModuleOrigin :module "doeff" :file (+ ROOT "/doeff/packages/doeff/src/doeff/__init__.py"))
               (ModuleOrigin :module "doeff_cluster" :file (+ ROOT "/doeff/packages/doeff-cluster/src/doeff_cluster/__init__.py"))))


(defn read-of [#** over]
  (setv base {"declared_json" (env-json DECLARED) "key" KEY "root" ROOT "marker_json" (marker-json DECLARED) "origins" IN-ROOT "pid" PID})
  (.update base over)
  (ProcessFacts #** base))


(defn judged [#^ ProcessFacts read]
  (done (with_handlers [(given-runtime-facts read)] (check-runtime-identity MODULES))))


(defn #^ RuntimeIdentityMismatch mismatch-of [#^ ProcessFacts read]
  "食い違いの筋書きの答え — 確かめが食い違い(RuntimeIdentityMismatch)を返したことを先に確かめてから、種類と説明を読むため
   (一致を返した時は、属性の誤りで落ちるのではなく、食い違いを見逃したと名指して赤にする)。"
  (setv got (judged read))
  (assert (isinstance got RuntimeIdentityMismatch) (+ "食い違いのはずが一致を返した: " (repr got)))
  got)


(defn kind-of [#^ ProcessFacts read]
  (. (mismatch-of read) kind))


(defn origins-with [#^ str module #^ str file]
  (tuple (gfor o IN-ROOT (if (= o.module module) (ModuleOrigin :module module :file file) o))))


(defn test-declared-root-and-modules-agree []
  (assert (= (judged (read-of))
             (RuntimeIdentity :key KEY :root ROOT :pid PID
                              :commits #((RepoCommit :name "app" :commit AC-COMMIT)
                                         (RepoCommit :name "doeff" :commit DF-COMMIT))))))


(defn test-env-vars-alone-do-not-change-the-root []
  ;; 同じキーの root を、env-vars だけ違う宣言が使い回す — 一致(丸ごとの等しさで比べると恒久に不一致になる)。
  (setv declared (env-of AC-COMMIT :env-vars #((EnvVar :name "LOG_LEVEL" :value "debug"))))
  (assert (= (key-of declared) KEY))
  (assert (isinstance (judged (read-of :declared-json (env-json declared))) RuntimeIdentity)))


(defn test-no-declaration-is-undeclared []
  ;; 反例: image の venv で起きた process には宣言が無い。
  (assert (= (kind-of (read-of :declared-json "")) IdentityFailureKind.UNDECLARED)))


(defn test-root-without-marker-is-unmarked []
  (assert (= (kind-of (read-of :root "" :marker-json "")) IdentityFailureKind.ROOT-UNMARKED)))


(defn test-unknown-marker-format []
  (setv got (mismatch-of (read-of :marker-json (marker-json DECLARED :format 99))))
  (assert (= got.kind IdentityFailureKind.MARKER-MISMATCH))
  (assert (in "99" got.detail)))


(defn test-passed-key-differs-from-declaration []
  (setv got (mismatch-of (read-of :key "k0")))
  (assert (= got.kind IdentityFailureKind.MARKER-MISMATCH))
  (assert (in "k0" got.detail)))


(defn test-root-of-another-commit []
  ;; 反例: 前の commit の root(印の宣言とキーが古い)の上で、新しい宣言を渡されて起きた process。
  (setv got (mismatch-of (read-of :marker-json (marker-json (env-of OTHER-COMMIT)))))
  (assert (= got.kind IdentityFailureKind.MARKER-MISMATCH))
  (assert (in (key-of (env-of OTHER-COMMIT)) got.detail)))


(defn test-module-from-image-venv-is-outside []
  ;; 反例: 宣言の root で起きたのに、doeff_cluster だけ image の venv から import していた。
  (setv image-file "/opt/app/doeff/.venv/lib/python3.14t/site-packages/doeff_cluster/__init__.py")
  (setv got (mismatch-of (read-of :origins (origins-with "doeff_cluster" image-file))))
  (assert (= got.kind IdentityFailureKind.MODULE-OUTSIDE-ROOT))
  (assert (in "doeff_cluster" got.detail))
  (assert (in image-file got.detail))
  (assert (not (in "app_jobs" got.detail))))


(defn test-module-from-the-roots-own-venv-is-outside []
  ;; 反例: root の中でも project の venv(第三者の package の置き場 — editable でない古い wheel)から来た module は数えない。
  (setv venv-file (+ ROOT "/app/.venv/lib/python3.14t/site-packages/doeff_cluster/__init__.py"))
  (setv got (mismatch-of (read-of :origins (origins-with "doeff_cluster" venv-file))))
  (assert (= got.kind IdentityFailureKind.MODULE-OUTSIDE-ROOT))
  (assert (in venv-file got.detail)))


(defn test-module-in-root-but-outside-declared-repos []
  ;; 反例: root の中でも、宣言の repo の dir の外(例: 名が接頭辞で重なる別の dir)は一致と数えない。
  (setv got (mismatch-of (read-of :origins (origins-with "app_jobs"
                                                    (+ ROOT "/app-old/app_jobs/__init__.py")))))
  (assert (= got.kind IdentityFailureKind.MODULE-OUTSIDE-ROOT)))


(defn test-unimportable-module-is-outside []
  (setv got (mismatch-of (read-of :origins (cut IN-ROOT 2))))
  (assert (= got.kind IdentityFailureKind.MODULE-OUTSIDE-ROOT))
  (assert (in "import できない" got.detail)))
