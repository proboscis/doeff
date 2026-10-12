;;; 実行環境の宣言(runtime env)の純粋な判断: キー(env-key)・元の鍵(source-key — commit が替わっても変わらない環境の同一性)・
;;; JSON の往復(runtime-env->json・runtime-env-of-json)・準備の失敗の値(env-failure)・子の環境変数の組の検め(child-environ-refusal)・
;;; repo の url の正体(url-location)。
;;; 型と定数は doeff_cluster.shared.intent.runtime_env_model。
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import hashlib)
(import json)
(import doeff_traverse.helpers [sequence])
(import doeff_cluster.shared.core.native_wheel [ENV-KEY-LENGTH])
(import doeff_cluster.shared.intent.runtime_env_model [RUNTIME-ENV-FORMAT InvalidKind RuntimeEnvInvalid RepoCheckout
                                                       LocalPath RemoteRepo RepoLocation
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


(defk url-location [url]
  {:pre [(: url str)] :post [(: % RepoLocation)]}
  "repo の url を 1 度だけ読み、綴りに依らない正体にするため(同じ repo かを見る所はこの答えで比べる — #3693)。手元の path かの読み分けは
   git の url_is_local_not_ssh と同じ(file:// で始まる・`:` を含まない・最初の `/` が最初の `:` より前 → LocalPath・綴りのまま)。
   残りは網の url: `scheme://[user@]host[:port]/owner/name` と scp の形 `[user@]host:owner/name` を、小文字の host・owner・
   末尾の `.git` と前後の `/` を外した name の RemoteRepo にする(https・ssh://・git@host: は同じ正体)。"
  (val colon (.find url ":"))
  (val slash (.find url "/"))
  (if (or (.startswith url "file://") (< colon 0) (and (>= slash 0) (< slash colon)))
      (LocalPath :path url)
      (do (val url-form (= (cut url colon (+ colon 3)) "://"))
          (val after (if url-form (cut url (+ colon 3) None) (cut url (+ colon 1) None)))
          (val authority (if url-form (get (.partition after "/") 0) (cut url 0 colon)))
          (val path (if url-form (get (.partition after "/") 2) after))
          (val host (get (.partition (get (.rpartition authority "@") 2) ":") 0))
          (val parts (.rpartition (.removesuffix (.strip path "/") ".git") "/"))
          (RemoteRepo :host (.lower host) :owner (.strip (get parts 0) "/") :name (get parts 2)))))


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


(defk material-digest [material]
  {:pre [(: material dict)] :post [(: % str) (= (len %) ENV-KEY-LENGTH)]}
  "鍵の材料を正規化した JSON の sha256 の頭 24 桁にするため(env-key と source-key が同じ綴りで digest を作る)。"
  (val text (json.dumps material :sort-keys True :separators #("," ":") :ensure-ascii False))
  (cut (.hexdigest (hashlib.sha256 (.encode text "utf-8"))) 0 ENV-KEY-LENGTH))


(defk env-key [env platform]
  {:pre [(: env RuntimeEnv) (: platform str)] :post [(: % str) (= (len %) ENV-KEY-LENGTH)]}
  "env のキー(定義点はここ 1 つ)= 正規化した JSON の sha256 の頭 24 桁。"
  (<- material dict (key-material env platform))
  (<- key str (material-digest material))
  key)


(defk location-material [location]
  {:pre [(: location RepoLocation)] :post [(: % dict)]}
  "repo の正体(url-location の答え)を鍵の材料の JSON の値にするため。"
  (match location
    (LocalPath :path path) {"local" path}
    (RemoteRepo :host host :owner owner :name name) {"host" host "owner" owner "name" name}))


(defk repo-source-material [repo]
  {:pre [(: repo RepoCheckout)] :post [(: % dict)]}
  "宣言の repo 1 つの元の鍵の材料(名と url の正体 — commit は入れない)を作るため。"
  (<- location RepoLocation (url-location repo.url))
  (<- located dict (location-material location))
  {"name" repo.name "location" located})


(defk source-key-material [env platform]
  {:pre [(: env RuntimeEnv) (: platform str)] :post [(: % dict)]}
  "元の鍵の材料 = env-key の材料から commit を外した物(repo の名と url の正体・platform・project の lock の sha256・Python・組・native の
   宣言・import の根)。repo の url は綴りでなく正体(url-location)で入れるので、同じ repo を https と git@ で宣言した root が同じ元の鍵になる
   (#3693)。Rust の指紋(自前の native-key)は入れない — wheel は build の口が source の中身で引くようになり、worker は持たない(#3860)。"
  (<- material dict (key-material env platform))
  (<- repos tuple (sequence (gfor r env.repos (repo-source-material r))))
  (| material {"repos" (list repos)}))


(defk source-key [env platform]
  {:pre [(: env RuntimeEnv) (: platform str)] :post [(: % str) (= (len %) ENV-KEY-LENGTH)]}
  "元の鍵(定義点はここ 1 つ)= 版(commit)が替わっても変わらない環境の同一性。同じ元の鍵の完成済みの root は、木を hardlink で写す元に
   なる(置き場の鍵 env-key は commit を含み、置き先の選び・入口の検査・先の組みが版の名として読むので意味を変えない・#3677)。"
  (<- material dict (source-key-material env platform))
  (<- key str (material-digest material))
  key)


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
