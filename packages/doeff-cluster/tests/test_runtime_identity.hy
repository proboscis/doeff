;; 入口の検め(doeff_cluster/shared/core/runtime_identity.hy)の検。
;;
;;   * 宣言・印・キー・module の置き場が揃えば一致を答え、宣言の repo と commit と pid を返す。
;;   * env-vars だけ違う宣言が同じ root(同じキー)を使い回しても一致(キーの材料は root の中身を決める欄だけ)。
;;   * 反例: 宣言が無い(image の venv で起きた)・印の無い root・読めない形式の印・渡されたキーか印のキーの違い・宣言の root の外
;;     から import した module・root の venv(第三者の package の置き場)から import した module・import できない module —
;;     それぞれ失敗の kind で名乗る。
;;   * この process を読む handler(process-runtime-facts)と材料を渡す handler(given-runtime-facts)が同じ場面に同じ答えを返すことは
;;     契約テスト test_runtime_facts_contract.hy。
;; 印は本物の書き手(env_prepare の EnvMarker と env-marker->json)で作り、キーは env-key で計算する(形式を手書きしない — 書き手の形が
;; 変われば検が赤になる)。宣言・キー・root は検ごとに場面 declared-scene(defk)で作る(deftest が scheduler つきで回す — #2914)。
(require doeff-hy.macros [deftest defk <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import json)
(import doeff [with_handlers])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv RepoCheckout PythonProject EnvVar])
(import doeff_cluster.shared.core.runtime_env_rules [runtime-env->json env-key])
(import doeff_cluster.worker.core.env_prepare [env-marker->json] doeff_cluster.worker.intent.env_prepare_model [EnvMarker])
(import doeff_cluster.shared.intent.runtime_identity_model [IdentityFailureKind ModuleOrigin ProcessFacts RootMarker RuntimeIdentity RuntimeIdentityMismatch RepoCommit])
(import doeff_cluster.shared.intent.env_marker_model [BytecodeCounts TreeCounts])
(import doeff_cluster.shared.core.runtime_identity [check-runtime-identity decode-marker marker-bytecode])
(import doeff_hy.wire [Malformed])
(import doeff_cluster.shared.protocol.runtime_facts [given-runtime-facts])

(val AC-COMMIT (* "a" 40))
(val DF-COMMIT (* "d" 40))
(val OTHER-COMMIT (* "b" 40))
(val PLATFORM "linux-x86_64")
(val PID 4242)
(val MODULES #("app_jobs" "doeff" "doeff_cluster"))


(defrecord Declared
  "検の場面: env = 宣言(app の commit は AC-COMMIT)・key = その env-key・root = 準備の置き場の root・in-root = root の中の宣言の
   repo から import した module の置き場(app_jobs・doeff・doeff_cluster)。"
  (#^ RuntimeEnv env)
  (#^ str key)
  (#^ str root)
  (#^ (get tuple #(ModuleOrigin ...)) in-root))


(defk env-of [ac-commit [env-vars #()]]
  {:pre [(: ac-commit str) (: env-vars tuple)] :post [(: % RuntimeEnv)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "app の commit と env-vars から、app と doeff の 2 つの repo を持つ宣言を組むため。"
  (RuntimeEnv :repos #((RepoCheckout :name "app" :url "https://example.com/app.git"
                                     :commit ac-commit)
                       (RepoCheckout :name "doeff" :url "https://github.com/proboscis/doeff.git" :commit DF-COMMIT))
              :project (PythonProject :repo "app" :path "." :lock-sha256 (* "0" 64) :python "3.14")
              :import-roots #("app/." "app/vendor")
              :env-vars env-vars))


(defk key-of [env]
  {:pre [(: env RuntimeEnv)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "宣言の root のキー(本物の env-key・platform は PLATFORM)。"
  (<- key str (env-key env PLATFORM))
  key)


(defk env-json [env]
  {:pre [(: env RuntimeEnv)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "宣言 → worker が DOEFF_RUNTIME_ENV に置くのと同じ JSON の文字列。"
  (<- body dict (runtime-env->json env))
  (json.dumps body))


(defk env-marker-json [env [format None]]
  {:pre [(: env RuntimeEnv) (: format (| int None))] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "doeff-cluster の準備が root に置くのと同じ完成の印の中身(format で印の形式の版を差し替える)。"
  (<- raw dict (env-marker->json (EnvMarker :env env :key (! (key-of env)) :platform PLATFORM :stages #() :downloaded 0
                                            :built 0 :interpreter "/usr/bin/python3" :child-protocol 1)))
  (when (is-not format None)
    (setv (get raw "format") format))
  (json.dumps raw))


(defk declared-scene []
  {:pre [] :post [(: % Declared)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "宣言 AC-COMMIT の場面(宣言・キー・root・root の中の module の置き場)を、検ごとに本物の env-key で作るため。"
  (val env (! (env-of AC-COMMIT)))
  (val key (! (key-of env)))
  (val root (+ "/work/envs/" key))
  (Declared :env env :key key :root root
            :in-root #((ModuleOrigin :module "app_jobs" :file (+ root "/app/app_jobs/__init__.py"))
                       (ModuleOrigin :module "doeff" :file (+ root "/doeff/packages/doeff/src/doeff/__init__.py"))
                       (ModuleOrigin :module "doeff_cluster" :file (+ root "/doeff/packages/doeff-cluster/src/doeff_cluster/__init__.py")))))


(defk read-of [scene [declared-json None] [key None] [root None] [marker-json None] [origins None]]
  {:pre [(: scene Declared) (: declared-json (| str None)) (: key (| str None)) (: root (| str None)) (: marker-json (| str None))
         (: origins (| (get tuple #(ModuleOrigin ...)) None))]
   :post [(: % ProcessFacts)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "場面の宣言どおりに起きた process が読む事実(欄を渡すとその欄だけ差し替えて反例を作る — 渡さない欄は場面のまま)。"
  (ProcessFacts :declared-json (if (is declared-json None) (! (env-json scene.env)) declared-json)
                :key (if (is key None) scene.key key)
                :root (if (is root None) scene.root root)
                :marker-json (if (is marker-json None) (! (env-marker-json scene.env)) marker-json)
                :origins (if (is origins None) scene.in-root origins)
                :pid PID))


(defk judged [read]
  {:pre [(: read ProcessFacts)] :post [(: % (| RuntimeIdentity RuntimeIdentityMismatch))] :tags {:context "doeff-cluster-test" :role "entry"}}
  "読んだ事実を材料の handler(given-runtime-facts)で渡し、本物の入口の検め check-runtime-identity に判じさせる。"
  (<- verdict (| RuntimeIdentity RuntimeIdentityMismatch) (with_handlers [(given-runtime-facts read)] (check-runtime-identity MODULES)))
  verdict)


(defk mismatch-of [read]
  {:pre [(: read ProcessFacts)] :post [(: % RuntimeIdentityMismatch)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "食い違いの筋書きの答え — 確かめが食い違い(RuntimeIdentityMismatch)を返したことを先に確かめてから、種類と説明を読むため
   (一致を返した時は、属性の誤りで落ちるのではなく、食い違いを見逃したと名指して赤にする)。"
  (val got (! (judged read)))
  (assert (isinstance got RuntimeIdentityMismatch) (+ "食い違いのはずが一致を返した: " (repr got)))
  got)


(defk kind-of [read]
  {:pre [(: read ProcessFacts)] :post [(: % IdentityFailureKind)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "食い違いの種類だけを読むため。"
  (. (! (mismatch-of read)) kind))


(defk origins-with [scene module file]
  {:pre [(: scene Declared) (: module str) (: file str)] :post [(: % (get tuple #(ModuleOrigin ...)))] :tags {:context "doeff-cluster-test" :role "entry"}}
  "場面の module の置き場のうち、module 1 つだけを file から import した事にする(反例の置き場を作るため)。"
  (tuple (gfor o scene.in-root (if (= o.module module) (ModuleOrigin :module module :file file) o))))


(deftest test-declared-root-and-modules-agree
  (<- scene Declared (declared-scene))
  (assert (= (! (judged (! (read-of scene))))
             (RuntimeIdentity :key scene.key :root scene.root :pid PID
                              :commits #((RepoCommit :name "app" :commit AC-COMMIT)
                                         (RepoCommit :name "doeff" :commit DF-COMMIT))))))


(deftest test-env-vars-alone-do-not-change-the-root
  ;; 同じキーの root を、env-vars だけ違う宣言が使い回す — 一致(丸ごとの等しさで比べると恒久に不一致になる)。
  (<- scene Declared (declared-scene))
  (val declared (! (env-of AC-COMMIT :env-vars #((EnvVar :name "LOG_LEVEL" :value "debug")))))
  (assert (= (! (key-of declared)) scene.key))
  (assert (isinstance (! (judged (! (read-of scene :declared-json (! (env-json declared)))))) RuntimeIdentity)))


(deftest test-no-declaration-is-undeclared
  ;; 反例: image の venv で起きた process には宣言が無い。
  (<- scene Declared (declared-scene))
  (assert (= (! (kind-of (! (read-of scene :declared-json "")))) IdentityFailureKind.UNDECLARED)))


(deftest test-root-without-marker-is-unmarked
  (<- scene Declared (declared-scene))
  (assert (= (! (kind-of (! (read-of scene :root "" :marker-json "")))) IdentityFailureKind.ROOT-UNMARKED)))


(deftest test-unknown-marker-format
  (<- scene Declared (declared-scene))
  (val got (! (mismatch-of (! (read-of scene :marker-json (! (env-marker-json scene.env :format 99)))))))
  (assert (= got.kind IdentityFailureKind.MARKER-MISMATCH))
  (assert (in "99" got.detail)))


(deftest test-passed-key-differs-from-declaration
  (<- scene Declared (declared-scene))
  (val got (! (mismatch-of (! (read-of scene :key "k0")))))
  (assert (= got.kind IdentityFailureKind.MARKER-MISMATCH))
  (assert (in "k0" got.detail)))


(deftest test-root-of-another-commit
  ;; 反例: 前の commit の root(印の宣言とキーが古い)の上で、新しい宣言を渡されて起きた process。
  (<- scene Declared (declared-scene))
  (val other (! (env-of OTHER-COMMIT)))
  (val got (! (mismatch-of (! (read-of scene :marker-json (! (env-marker-json other)))))))
  (assert (= got.kind IdentityFailureKind.MARKER-MISMATCH))
  (assert (in (! (key-of other)) got.detail)))


(deftest test-module-from-image-venv-is-outside
  ;; 反例: 宣言の root で起きたのに、doeff_cluster だけ image の venv から import していた。
  (<- scene Declared (declared-scene))
  (val image-file "/opt/app/doeff/.venv/lib/python3.14t/site-packages/doeff_cluster/__init__.py")
  (val got (! (mismatch-of (! (read-of scene :origins (! (origins-with scene "doeff_cluster" image-file)))))))
  (assert (= got.kind IdentityFailureKind.MODULE-OUTSIDE-ROOT))
  (assert (in "doeff_cluster" got.detail))
  (assert (in image-file got.detail))
  (assert (not (in "app_jobs" got.detail))))


(deftest test-module-from-the-roots-own-venv-is-outside
  ;; 反例: root の中でも project の venv(第三者の package の置き場 — editable でない古い wheel)から来た module は数えない。
  (<- scene Declared (declared-scene))
  (val venv-file (+ scene.root "/app/.venv/lib/python3.14t/site-packages/doeff_cluster/__init__.py"))
  (val got (! (mismatch-of (! (read-of scene :origins (! (origins-with scene "doeff_cluster" venv-file)))))))
  (assert (= got.kind IdentityFailureKind.MODULE-OUTSIDE-ROOT))
  (assert (in venv-file got.detail)))


(deftest test-module-in-root-but-outside-declared-repos
  ;; 反例: root の中でも、宣言の repo の dir の外(例: 名が接頭辞で重なる別の dir)は一致と数えない。
  (<- scene Declared (declared-scene))
  (val got (! (mismatch-of (! (read-of scene :origins (! (origins-with scene "app_jobs"
                                                                       (+ scene.root "/app-old/app_jobs/__init__.py"))))))))
  (assert (= got.kind IdentityFailureKind.MODULE-OUTSIDE-ROOT)))


(deftest test-unimportable-module-is-outside
  (<- scene Declared (declared-scene))
  (val got (! (mismatch-of (! (read-of scene :origins (cut scene.in-root 2))))))
  (assert (= got.kind IdentityFailureKind.MODULE-OUTSIDE-ROOT))
  (assert (in "import できない" got.detail)))


;; --- 完成の印の bytecode の欄(#3607 の H2・#3675)-------------------------------------------------------------
;; 印の JSON に欄を足しただけ(形式の版は 1 のまま)なので、読み手は欄の無い前の形の印も、知らない欄を足した後の形の印も断らずに読む。
;; 数の欄は報告と log の値で、置き場の名指し(decode-marker と入口の検め)は読まない — 欄の名を替えても PVC の前の形の印の置き場が使える。

(deftest test-a-marker-written-before-the-bytecode-field-still-reads-as-a-prepared-root
  ;; 失敗ケース: bytecode の欄の無い印(足す前に書かれた印)でも root は準備済みとして読め、数の読み手は None(記録が無い — 0 で埋めない)。
  ;; 欄が在る物として読む形(get)に戻すと読めずに落ち、0 で埋める形に戻すと None でなくなって赤。
  (<- scene Declared (declared-scene))
  (val raw (json.loads (! (env-marker-json scene.env))))
  (del (get raw "bytecode"))
  (val old (json.dumps raw))
  (<- verdict (| RuntimeIdentity RuntimeIdentityMismatch) (judged (! (read-of scene :marker-json old))))
  (assert (isinstance verdict RuntimeIdentity) verdict)
  (<- marker (| RootMarker None) (decode-marker old))
  (assert (and (is-not marker None) (= marker.key scene.key)) marker)
  (<- counts (| BytecodeCounts Malformed None) (marker-bytecode raw))
  (assert (is counts None) counts))


(deftest test-a-marker-with-the-earlier-compiled-counts-still-names-a-prepared-root
  ;; 失敗ケース(#3675 の読み手の条件): #3675 より前の worker が書いた印(bytecode の欄が compiled・carried・failed の形)の置き場は、新しい
  ;; worker の入口の検めでも準備済みの root として一致を答える(PVC に残る置き場を作り直さない・入口の検めで落とさない)。名指し
  ;; (decode-marker)が数の欄を読む形に戻すと、欄の名を rebuilt・reused に替えた型で parse が断り、読めない印として落ちて赤。数の読み手は
  ;; 前の形を Malformed のまま返す(0 にも None にも畳まない)。
  (<- scene Declared (declared-scene))
  (val raw (json.loads (! (env-marker-json scene.env))))
  (setv (get raw "bytecode") {"compiled" 3 "carried" 1 "failed" 0 "scanSeconds" 0.5 "closureSeconds" 0.75 "carrySeconds" 0.25
                              "compileSeconds" 1.5 "trees" [{"name" "app" "compiled" 3 "carried" 1 "failed" 0}]})
  (val old (json.dumps raw))
  (<- verdict (| RuntimeIdentity RuntimeIdentityMismatch) (judged (! (read-of scene :marker-json old))))
  (assert (isinstance verdict RuntimeIdentity) verdict)
  (<- marker (| RootMarker None) (decode-marker old))
  (assert (and (is-not marker None) (= marker.env scene.env)) marker)
  (<- counts (| BytecodeCounts Malformed None) (marker-bytecode raw))
  (assert (isinstance counts Malformed) counts))


(deftest test-the-marker-bytecode-field-reads-back-and-unknown-fields-are-dropped
  ;; 書き手(env-marker->json)が綴った bytecode の欄は数の読み手(marker-bytecode)で同じ値に読み戻せ、印と欄に足された知らない欄は
  ;; 読み捨てる(断らない)。名指しの検めも同じ印で一致を答える。
  (<- scene Declared (declared-scene))
  (val counts (BytecodeCounts :carried 1 :rebuilt 2 :reused 1 :failed 0 :scan-seconds 0.5 :closure-seconds 0.75 :carry-seconds 0.25
                              :compile-seconds 1.5 :trees #((TreeCounts :name "app" :carried 1 :rebuilt 2 :reused 1 :failed 0))))
  (<- raw dict (env-marker->json (EnvMarker :env scene.env :key scene.key :platform PLATFORM :stages #() :downloaded 0 :built 0
                                            :interpreter "/usr/bin/python3" :child-protocol 1 :bytecode counts)))
  (setv (get raw "laterField") {"anything" 1})
  (setv (get (get raw "bytecode") "laterField") 2)
  (val text (json.dumps raw))
  (<- verdict (| RuntimeIdentity RuntimeIdentityMismatch) (judged (! (read-of scene :marker-json text))))
  (assert (isinstance verdict RuntimeIdentity) verdict)
  (<- read-back (| BytecodeCounts Malformed None) (marker-bytecode (json.loads text)))
  (assert (= read-back counts) read-back))
