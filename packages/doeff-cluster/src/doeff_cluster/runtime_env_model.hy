;;; 実行環境の宣言(runtime env)の型・キー・JSON の往復・準備の失敗の型(2026-09-26)。
;;;
;;; task の送り手は「どの repo のどの commit を並べ、どの uv の project の lock で依存を入れ、どの dir を import の根にするか」を
;;; 宣言し、worker はその宣言から実行環境(root)を準備して、root の中の子 process で task を走らせる。worker の process は
;;; 再起動しない — 新しい commit・新しい lock・新しい native の wheel は、どれも新しい root を作るだけ。
;;;
;;;   (RuntimeEnv :repos #((RepoCheckout "app" "ssh://git@host/app.git" <40 桁>)
;;;                        (RepoCheckout "lib" "ssh://git@host/lib.git" <40 桁>))
;;;               :project (PythonProject "app" "." <uv.lock の sha256> "3.14"
;;;                                       :native #((NativeWheel "lib-native" "lib" #("native/core"))))
;;;               :import-roots #("app/." "app/vendor"))
;;;
;;; 宣言の誤り(名の重複・branch 名の commit・宣言に無い repo・`..` を含む path・予約した環境変数)は、送る前に例外
;;; RuntimeEnvInvalid で断る(呼び手の誤り)。準備の失敗(worker の側で起きる)は値 EnvFailure で返す。
;;;
;;; キー env-key は root の中身を決める物(repos・project・import-roots・format)と platform だけから作る。env-vars と tools は
;;; file を変えないのでキーに入れない。準備の手順の版もキーに入れない(coordinator と worker の版が違っても同じ宣言が同じキーになる)。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defenum defrecord])
(import dataclasses [dataclass])
(import enum [StrEnum])
(import hashlib)
(import json)
(import re)

(val RUNTIME-ENV-FORMAT 1)
;; 子 process の入口(job_entry)と worker の約束の版。worker は準備の確かめ(処理ステージ 10)で root の中の値を読み、範囲の外なら
;; env-incompatible で断る。
(val CHILD-PROTOCOL 1)
(val SUPPORTED-CHILD-PROTOCOLS (frozenset #(1)))
(val ENV-KEY-LENGTH 24)

(val NAME-PATTERN (re.compile r"[a-z0-9][a-z0-9._-]*"))
(val COMMIT-PATTERN (re.compile r"[0-9a-f]{40}"))
(val SHA256-PATTERN (re.compile r"[0-9a-f]{64}"))
(val ENV-VAR-PATTERN (re.compile r"[A-Z][A-Z0-9_]*"))
;; 子の環境変数のうち、宣言で置けない物(worker が組む・interpreter と uv の挙動を変える)。
(val RESERVED-ENV-PREFIXES #("PYTHON" "HY_" "UV_" "LD_" "DOEFF_"))
(val RESERVED-ENV-NAMES (frozenset #("PATH" "HOME" "VIRTUAL_ENV")))


;; --- 宣言の誤り ---------------------------------------------------------------------------

;; 宣言の誤りの種類。後ろの 3 つは送り手の組み立て(runtime_env.hy)が断る物。
(defenum InvalidKind
  INVALID-NAME DUPLICATE-REPO BAD-COMMIT BAD-URL BAD-SHA256 UNKNOWN-REPO BAD-PATH RESERVED-ENV-VAR EMPTY BAD-JSON
  DIRTY-TREE COMMIT-NOT-ON-REMOTE SENDER-SOURCE-DIFFERS)


(defclass RuntimeEnvInvalid [ValueError]
  "宣言が誤っている(呼び手の誤り・送れない)。coordinator は同じ誤りを HTTP 400 で断る。"
  (defn __init__ [self #^ InvalidKind kind #^ str detail]  ; defk にできない: 例外の class の初期化
    (.__init__ (super) (.format "{}: {}" kind.value detail))
    (setv self.kind kind self.detail detail)))


(defn _invalid [#^ InvalidKind kind #^ str detail]  ; defk にできない: dataclass の __post_init__ から呼ぶ純粋な検査
  (raise (RuntimeEnvInvalid kind detail)))


(defn _check-name [#^ str what #^ str name]  ; defk にできない: dataclass の __post_init__ から呼ぶ純粋な検査
  (when (not (and (isinstance name str) (.fullmatch NAME-PATTERN name)))
    (_invalid InvalidKind.INVALID-NAME (.format "{} は英小文字・数字・. _ - の名: {!r}" what name))))


(defn _check-relative [#^ str what #^ str path]  ; defk にできない: dataclass の __post_init__ から呼ぶ純粋な検査
  "repo の中の相対の dir(`.` 可)。絶対 path・`..`・空の部分・`:` と `,` を断る。"
  (setv parts (.split path "/"))
  (when (or (not (isinstance path str)) (not path) (.startswith path "/") (in ":" path) (in "," path)
            (any (gfor p parts (or (= p "") (= p "..")))))
    (_invalid InvalidKind.BAD-PATH (.format "{} は repo の中の相対の dir: {!r}" what path))))


(defn _check-tuple [#^ str what value item-type]  ; defk にできない: dataclass の __post_init__ から呼ぶ純粋な検査
  (when (not (and (isinstance value tuple) (all (gfor v value (isinstance v item-type)))))
    (raise (TypeError (.format "{} は {} の tuple: {!r}" what item-type.__name__ value)))))


;; --- 宣言の型 -----------------------------------------------------------------------------

(defrecord RepoCheckout
  "root の下に並べる repo 1 つ。name = root の下の dir 名(宣言の中で一意)・url = clone 元・commit = 40 桁の sha(branch 名は断る)。"
  (#^ str name)
  (#^ str url)
  (#^ str commit)
  (defn __post-init__ [self]  ; defk にできない: dataclass の検査の口
    (_check-name "repo の名" self.name)
    (when (not (and (isinstance self.url str) self.url (not (any (gfor c self.url (.isspace c))))))
      (_invalid InvalidKind.BAD-URL (.format "repo {} の url: {!r}" self.name self.url)))
    (when (not (and (isinstance self.commit str) (.fullmatch COMMIT-PATTERN self.commit)))
      (_invalid InvalidKind.BAD-COMMIT (.format "repo {} の commit は 40 桁の sha: {!r}" self.name self.commit)))))


(defrecord NativeWheel
  "wheel で入れる native の package。package = uv の package 名・repo = source を持つ repo の名・
   paths = wheel の中身を決める repo の中の dir(キー = 各 dir の git の tree hash)。"
  (#^ str package)
  (#^ str repo)
  (#^ tuple paths)
  (defn __post-init__ [self]  ; defk にできない: dataclass の検査の口
    (_check-name "native の package" self.package)
    (_check-name "native の repo" self.repo)
    (_check-tuple "NativeWheel.paths" self.paths str)
    (when (not self.paths) (_invalid InvalidKind.EMPTY (.format "native {} の paths が空" self.package)))
    (for [p self.paths] (_check-relative (.format "native {} の path" self.package) p))))


(defrecord PythonProject
  "pyproject.toml と uv.lock を持つ uv の project。lock-sha256 = uv.lock の sha256(送り手が計算し、worker が展開の後に照合する)。
   python = uv の Python の指定・groups = 入れる dependency group・native = wheel で入れる native の package。"
  (#^ str repo)
  (#^ str path)
  (#^ str lock-sha256)
  (#^ str python)
  (setv #^ tuple groups #())
  (setv #^ tuple native #())
  (defn __post-init__ [self]  ; defk にできない: dataclass の検査の口
    (_check-name "project の repo" self.repo)
    (_check-relative "project の path" self.path)
    (when (not (and (isinstance self.lock-sha256 str) (.fullmatch SHA256-PATTERN self.lock-sha256)))
      (_invalid InvalidKind.BAD-SHA256 (.format "lock の sha256 は 16 進 64 桁: {!r}" self.lock-sha256)))
    (when (not (and (isinstance self.python str) self.python))
      (_invalid InvalidKind.EMPTY "project の python が空"))
    (_check-tuple "PythonProject.groups" self.groups str)
    (for [g self.groups] (_check-name "dependency group" g))
    (_check-tuple "PythonProject.native" self.native NativeWheel)))


(defrecord ToolRequirement
  "worker が名乗る道具(例: 外部の CLI)。version = 版の範囲(空 = 何でもよい)。キーに入らない(置き先の選択で見る)。"
  (#^ str name)
  (setv #^ str version "")
  (defn __post-init__ [self]  ; defk にできない: dataclass の検査の口
    (_check-name "道具の名" self.name)))


(defrecord EnvVar
  "子 process に足す環境変数。秘密は置かない(coordinator の状態に残る)。キーに入らない。"
  (#^ str name)
  (#^ str value)
  (defn __post-init__ [self]  ; defk にできない: dataclass の検査の口
    (when (not (and (isinstance self.name str) (.fullmatch ENV-VAR-PATTERN self.name)))
      (_invalid InvalidKind.INVALID-NAME (.format "環境変数の名は英大文字・数字・_: {!r}" self.name)))
    (when (or (in self.name RESERVED-ENV-NAMES) (.startswith self.name RESERVED-ENV-PREFIXES))
      (_invalid InvalidKind.RESERVED-ENV-VAR (.format "worker が組む環境変数は宣言で置けない: {}" self.name)))
    (when (not (isinstance self.value str))
      (raise (TypeError (.format "環境変数 {} の値は文字列: {!r}" self.name self.value))))))


(defrecord RuntimeEnv
  "実行環境の宣言。repos = 1 つ以上・project = 依存の正本の uv の project・
   import-roots = \"<repo の名>/<repo の中の相対の dir>\"(前が先。project の venv の .pth に並ぶ)。"
  (#^ tuple repos)
  (#^ PythonProject project)
  (#^ tuple import-roots)
  (setv #^ tuple env-vars #())
  (setv #^ tuple tools #())
  (setv #^ int format RUNTIME-ENV-FORMAT)
  (defn __post-init__ [self]  ; defk にできない: dataclass の検査の口
    (_check-tuple "RuntimeEnv.repos" self.repos RepoCheckout)
    (_check-tuple "RuntimeEnv.import-roots" self.import-roots str)
    (_check-tuple "RuntimeEnv.env-vars" self.env-vars EnvVar)
    (_check-tuple "RuntimeEnv.tools" self.tools ToolRequirement)
    (when (not self.repos) (_invalid InvalidKind.EMPTY "repos が空"))
    (when (not self.import-roots) (_invalid InvalidKind.EMPTY "import の根が空"))
    (when (!= self.format RUNTIME-ENV-FORMAT)
      (_invalid InvalidKind.BAD-JSON (.format "宣言の形の版 {} は扱えない(扱える版 = {})" self.format RUNTIME-ENV-FORMAT)))
    (setv names (lfor r self.repos r.name))
    (when (!= (len names) (len (set names)))
      (_invalid InvalidKind.DUPLICATE-REPO (.format "repo の名が重なる: {}" names)))
    (setv known (frozenset names))
    (for [#(what repo) (+ [#("project" self.project.repo)]
                          (lfor w self.project.native #((.format "native {}" w.package) w.repo)))]
      (when (not-in repo known)
        (_invalid InvalidKind.UNKNOWN-REPO (.format "{} の repo {} が宣言に無い" what repo))))
    (for [root self.import-roots]
      (setv #(repo _ rel) (.partition root "/"))
      (when (not-in repo known)
        (_invalid InvalidKind.UNKNOWN-REPO (.format "import の根 {!r} の repo が宣言に無い" root)))
      (_check-relative (.format "import の根 {!r} の dir" root) rel))
    (setv env-names (lfor v self.env-vars v.name))
    (when (!= (len env-names) (len (set env-names)))
      (_invalid InvalidKind.INVALID-NAME (.format "環境変数の名が重なる: {}" env-names)))))


;; --- 準備の失敗(worker の側・値で返す) ----------------------------------------------------

;; 準備の失敗の種類(答えの 12 種。宣言の誤りの env-invalid は答えではなく送り手の例外 RuntimeEnvInvalid)。どれも子 process を
;; 起こす前に起きるので、置き直しても同じ task を 2 度実行しない。
;;   repo-denied         worker の許可表に URL が無い・鍵が無い(その worker では恒久)
;;   repo-unreachable    clone / fetch の network の失敗(一時)
;;   commit-missing      fetch の後も commit が無い(送り手が push していない)
;;   lock-mismatch       展開した uv.lock の sha256 が宣言と違う
;;   lock-stale          uv sync --locked が「lock が古い」で断る
;;   sync-failed         uv sync のその他の失敗(一時 / 恒久は uv の出力で分ける)
;;   env-incompatible    名前の影・子の約束の版の外
(defenum EnvFailureKind
  REPO-DENIED REPO-UNREACHABLE COMMIT-MISSING LOCK-MISMATCH LOCK-STALE SYNC-FAILED NATIVE-BUILD-FAILED
  PYTHON-UNAVAILABLE TOOL-MISSING DISK-FULL ENV-INCOMPATIBLE PREPARE-TIMEOUT)


;; kind の既定の「一時か」。sync-failed と native-build-failed は起きた所の handler が値で決める。
(val RETRYABLE-KINDS (frozenset #(EnvFailureKind.REPO-UNREACHABLE EnvFailureKind.PYTHON-UNAVAILABLE
                                   EnvFailureKind.DISK-FULL EnvFailureKind.PREPARE-TIMEOUT)))


(defrecord EnvFailure
  "準備の失敗。retryable = 一時の失敗(coordinator が起動前の task を別の worker へ置き直せる)。"
  (#^ EnvFailureKind kind)
  (#^ str detail)
  (#^ bool retryable)
  (defn __post-init__ [self]  ; defk にできない: dataclass の検査の口
    (when (not (isinstance self.kind EnvFailureKind))
      (raise (TypeError (.format "EnvFailure.kind は EnvFailureKind: {!r}" self.kind))))))


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
      "tools" (lfor t env.tools {"name" t.name "version" t.version})}))


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
      :format (.get value "format" RUNTIME-ENV-FORMAT)))
    (except [error [KeyError TypeError AttributeError]]
      (raise (RuntimeEnvInvalid InvalidKind.BAD-JSON (.format "宣言の JSON の形が違う: {}: {}" (. (type error) __name__) error)))))
  env)
