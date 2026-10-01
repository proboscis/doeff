;;; 実行環境の宣言(runtime env)の純粋な判断: キー(env-key・native-key)・JSON の往復(runtime-env->json・runtime-env-of-json)・
;;; 準備の失敗の値(env-failure)・子の環境変数の組の検め(child-environ-refusal)。型と定数は doeff_cluster.shared.intent.runtime_env_model。
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import hashlib)
(import json)
(import platform)
(import doeff_cluster.shared.intent.runtime_env_model [RUNTIME-ENV-FORMAT ENV-KEY-LENGTH InvalidKind RuntimeEnvInvalid RepoCheckout
                                                       NativeWheel PythonProject ToolRequirement EnvVar RuntimeEnv
                                                       EnvFailureKind RETRYABLE-KINDS EnvFailure])


(defn #^ (| str None) child-environ-refusal [#^ object environ]  ; defk にできない: coordinator の本文の読み(Program の外)が呼ぶ純粋な判断
  "子の環境変数の組が受けられない理由(受けられれば None)。規則は型の側 EnvVar.environ-refusal の 1 つ(型の構成子と同じ規則)。"
  (EnvVar.environ-refusal environ))


;; --- 準備の失敗(worker の側・値で返す) ----------------------------------------------------

(defk env-failure [kind detail]
  {:pre [(: kind EnvFailureKind) (: detail str)] :post [(: % EnvFailure)]}
  "kind の既定の「一時か」で失敗を作る。"
  (EnvFailure :kind kind :detail detail :retryable (in kind RETRYABLE-KINDS)))


;; --- 純粋な換算 ---------------------------------------------------------------------------

(defk root-split [root]
  {:pre [(: root str)] :post [(: % tuple)]}
  "import の根 \"<repo>/<相対の dir>\" → #(repo の名 相対の dir)。"
  (val parts (.partition root "/"))
  #((get parts 0) (get parts 2)))


;; 読むのはこの process の機体の固定の事実(process の間は変わらない)。本来は foundation の読みだが、読み手に protocol の層
;; (worker の declared・coordinator_link)が在り、protocol は foundation を import できないので、キーの材料の隣に置く。
(defn #^ str current-platform []  ; defk にできない: worker と送り手の composition(Program の外)が読む
  "この機体の platform の名(env のキーの材料 — 例 linux-x86_64)。root の中の native の wheel と venv は platform ごとに違う。"
  (.format "{}-{}" (.lower (platform.system)) (.lower (platform.machine))))


(defk key-material [env platform]
  {:pre [(: env RuntimeEnv) (: platform str)] :post [(: % dict)]}
  "キーの材料(root の中身を決める物だけ)。env-vars・tools・準備の手順の版は入れない。"
  {"format" env.format
   "platform" platform
   "repos" (lfor r env.repos {"name" r.name "url" r.url "commit" r.commit})
   "project" {"repo" env.project.repo "path" env.project.path "lockSha256" env.project.lock-sha256
              "python" env.project.python "groups" (list env.project.groups)
              "native" (lfor w env.project.native {"package" w.package "repo" w.repo "paths" (list w.paths)})}
   "importRoots" (list env.import-roots)})


(defk env-key [env platform]
  {:pre [(: env RuntimeEnv) (: platform str)] :post [(: % str) (= (len %) ENV-KEY-LENGTH)]}
  "env のキー(定義点はここ 1 つ)= 正規化した JSON の sha256 の頭 24 桁。"
  (<- material dict (key-material env platform))
  (val text (json.dumps material :sort-keys True :separators #("," ":") :ensure-ascii False))
  (cut (.hexdigest (hashlib.sha256 (.encode text "utf-8"))) 0 ENV-KEY-LENGTH))


(defk native-key [wheel tree-hashes python platform]
  {:pre [(: wheel NativeWheel) (: tree-hashes tuple) (: python str) (: platform str)
         (= (len tree-hashes) (len wheel.paths))]
   :post [(: % str) (= (len %) ENV-KEY-LENGTH)]}
  "native の wheel のキー(定義点はここ 1 つ)= package・wheel の中身を決める dir ごとの git の tree hash・Python・platform の
   正規化した JSON の sha256 の頭 24 桁。tree-hashes は wheel.paths と同じ順。"
  (val material {"package" wheel.package
                 "trees" (lfor #(path tree) (zip wheel.paths tree-hashes) [path tree])
                 "python" python "platform" platform})
  (val text (json.dumps material :sort-keys True :separators #("," ":") :ensure-ascii False))
  (cut (.hexdigest (hashlib.sha256 (.encode text "utf-8"))) 0 ENV-KEY-LENGTH))


(defk runtime-env->json [env]
  {:pre [(: env RuntimeEnv)] :post [(: % dict)]}
  "宣言 → 通信の本文と子の環境変数(DOEFF_RUNTIME_ENV)に載せる JSON の値。"
  (<- material dict (key-material env ""))
  (del (get material "platform"))
  (| material
     {"envVars" (lfor v env.env-vars {"name" v.name "value" v.value})
      "tools" (lfor t env.tools {"name" t.name "version" t.version})}
     (if env.bytecode-entries {"bytecodeEntries" (list env.bytecode-entries)} {})))


(defk runtime-env-of-json [value]
  {:pre [(: value dict)] :post [(: % RuntimeEnv)]}
  "JSON の値 → 宣言。形が違えば RuntimeEnvInvalid(bad-json)、中身の誤りは型の検査の RuntimeEnvInvalid。"
  (try
    (val project (get value "project"))
    (val env (RuntimeEnv
      :repos (tuple (gfor r (get value "repos")
                          (RepoCheckout :name (get r "name") :url (get r "url") :commit (get r "commit"))))
      :project (PythonProject :repo (get project "repo") :path (get project "path")
                              :lock-sha256 (get project "lockSha256") :python (get project "python")
                              :groups (tuple (.get project "groups" []))
                              :native (tuple (gfor w (.get project "native" [])
                                                   (NativeWheel :package (get w "package") :repo (get w "repo")
                                                                :paths (tuple (get w "paths"))))))
      :import-roots (tuple (get value "importRoots"))
      :env-vars (tuple (gfor v (.get value "envVars" []) (EnvVar :name (get v "name") :value (get v "value"))))
      :tools (tuple (gfor t (.get value "tools" []) (ToolRequirement :name (get t "name") :version (.get t "version" ""))))
      :format (.get value "format" RUNTIME-ENV-FORMAT)
      :bytecode-entries (tuple (.get value "bytecodeEntries" []))))
    (except [error [KeyError TypeError AttributeError]]
      (raise (RuntimeEnvInvalid InvalidKind.BAD-JSON (.format "宣言の JSON の形が違う: {}: {}" (. (type error) __name__) error)))))
  env)
