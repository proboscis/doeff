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
;;; ここは型と定数だけ。キー・JSON の往復・失敗の値の組み立て・子の環境変数の組の検め(env-key・runtime-env->json・
;;; runtime-env-of-json・env-failure・child-environ-refusal ほか)は doeff_cluster.shared.core.runtime_env_rules、鍵の長さ・platform の名・
;;; native の wheel の鍵と置き場(ENV_KEY_LENGTH・current_platform・native_key ほか)は doeff_cluster.shared.core.native_wheel。
(require doeff-hy.macros [deff val])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(require doeff-hy.record [defenum defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import enum [StrEnum])
(import re)

(val RUNTIME-ENV-FORMAT 1)
;; 子 process の入口(job_entry)と worker の約束の版。worker は準備の確かめ(処理ステージ 10)で root の中の値を読み、範囲の外なら
;; env-incompatible で断る。
(val CHILD-PROTOCOL 1)
(val SUPPORTED-CHILD-PROTOCOLS (frozenset #(1)))

(val NAME-PATTERN (re.compile r"[a-z0-9][a-z0-9._-]*"))
(val COMMIT-PATTERN (re.compile r"[0-9a-f]{40}"))
(val SHA256-PATTERN (re.compile r"[0-9a-f]{64}"))
(val ENV-VAR-PATTERN (re.compile r"[A-Z][A-Z0-9_]*"))
;; 子の環境変数のうち、宣言で置けない物(worker が組む・interpreter と uv の挙動を変える)。
(val RESERVED-ENV-PREFIXES #("PYTHON" "HY_" "UV_" "LD_" "DOEFF_"))
(val RESERVED-ENV-NAMES (frozenset #("PATH" "HOME" "VIRTUAL_ENV")))
;; 子の環境変数のうち、値が秘密の中身になる名の末尾(宣言の行・task の行・詰めた Program は coordinator の状態に残るので置けない —
;; 秘密は置き場を名指す名(末尾 PATH-ENV-SUFFIXES — 値は file や dir の path)で運ぶ)。service の :environ・実行環境の env-vars・
;; task の :environ が同じ規則 1 つ(EnvVar)で断る(2026-09-28)。
(val SECRET-ENV-SUFFIXES #("_TOKEN" "_KEY" "_SECRET" "_PASSWORD"))
(val PATH-ENV-SUFFIXES #("_FILE" "_DIR" "_PATH"))


;; --- 宣言の誤り ---------------------------------------------------------------------------

;; 宣言の誤りの種類。DIRTY-TREE から後ろは送り手の手元の checkout を読んで断る物(runtime_env.hy)。NOT-IN-CHECKOUT と
;; REVISION-DIFFERS は系の宣言(declare)が断る物: 系の関数の source が git の checkout の中に無い・checkout の HEAD が宣言の版と違う
;; (2026-09-28 — 詰める Program の参照する code と、実行先が宣言の版で展開する code を一致させる)。
(defenum InvalidKind
  INVALID-NAME DUPLICATE-REPO BAD-COMMIT BAD-URL BAD-SHA256 UNKNOWN-REPO BAD-PATH RESERVED-ENV-VAR SECRET-ENV-VAR EMPTY BAD-JSON
  DIRTY-TREE COMMIT-NOT-ON-REMOTE SENDER-SOURCE-DIFFERS NOT-IN-CHECKOUT REVISION-DIFFERS)


(defclass RuntimeEnvInvalid [ValueError]
  "宣言が誤っている(呼び手の誤り・送れない)。coordinator は同じ誤りを HTTP 400 で断る。
   check-name・check-relative・check-tuple = 宣言の型(下の record)が作る時に欄を検め、誤りならこの例外(tuple の形の誤りは TypeError)を
   投げる口(この型だけの層では module の関数を置けないので、投げる例外の class に置く)。"
  (defn #^ None __init__ [self #^ InvalidKind kind #^ str detail]  ; defk にできない: 例外の class の初期化
    (.__init__ (super) (.format "{}: {}" kind.value detail))
    (setv self.kind kind self.detail detail))

  (defn [staticmethod] #^ None check-name [#^ str what #^ str name]  ; defk にできない: dataclass の __post_init__ から呼ぶ純粋な検査
    (when (not (and (isinstance name str) (.fullmatch NAME-PATTERN name)))
      (raise (RuntimeEnvInvalid InvalidKind.INVALID-NAME (.format "{} は英小文字・数字・. _ - の名: {!r}" what name)))))

  (defn [staticmethod] #^ None check-relative [#^ str what #^ str path]  ; defk にできない: dataclass の __post_init__ から呼ぶ純粋な検査
    "repo の中の相対の dir(`.` 可)。絶対 path・`..`・空の部分・`:` と `,` を断る。"
    (setv parts (.split path "/"))
    (when (or (not (isinstance path str)) (not path) (.startswith path "/") (in ":" path) (in "," path)
              (any (gfor p parts (or (= p "") (= p "..")))))
      (raise (RuntimeEnvInvalid InvalidKind.BAD-PATH (.format "{} は repo の中の相対の dir: {!r}" what path)))))

  (defn [staticmethod] #^ None check-tuple [#^ str what #^ object value #^ type item-type]  ; defk にできない: dataclass の __post_init__ から呼ぶ純粋な検査
    (when (not (and (isinstance value tuple) (all (gfor v value (isinstance v item-type)))))
      (raise (TypeError (.format "{} は {} の tuple: {!r}" what item-type.__name__ value))))))


;; --- 宣言の型 -----------------------------------------------------------------------------

(defrecord RepoCheckout
  "root の下に並べる repo 1 つ。name = root の下の dir 名(宣言の中で一意)・url = clone 元・commit = 40 桁の sha(branch 名は断る)。"
  (#^ str name)
  (#^ str url)
  (#^ str commit)
  (defn #^ None __post-init__ [self]  ; defk にできない: dataclass の検査の口
    (RuntimeEnvInvalid.check-name "repo の名" self.name)
    (when (not (and (isinstance self.url str) self.url (not (any (gfor c self.url (.isspace c))))))
      (raise (RuntimeEnvInvalid InvalidKind.BAD-URL (.format "repo {} の url: {!r}" self.name self.url))))
    (when (not (and (isinstance self.commit str) (.fullmatch COMMIT-PATTERN self.commit)))
      (raise (RuntimeEnvInvalid InvalidKind.BAD-COMMIT (.format "repo {} の commit は 40 桁の sha: {!r}" self.name self.commit))))))


(defrecord NativeWheel
  "wheel で入れる native の package。package = uv の package 名・repo = source を持つ repo の名・
   paths = wheel の中身を決める repo の中の dir(キー = 各 dir の git の tree hash)。"
  (#^ str package)
  (#^ str repo)
  (#^ tuple paths)
  (defn #^ None __post-init__ [self]  ; defk にできない: dataclass の検査の口
    (RuntimeEnvInvalid.check-name "native の package" self.package)
    (RuntimeEnvInvalid.check-name "native の repo" self.repo)
    (RuntimeEnvInvalid.check-tuple "NativeWheel.paths" self.paths str)
    (when (not self.paths) (raise (RuntimeEnvInvalid InvalidKind.EMPTY (.format "native {} の paths が空" self.package))))
    (for [p self.paths] (RuntimeEnvInvalid.check-relative (.format "native {} の path" self.package) p))))


(defrecord PythonProject
  "pyproject.toml と uv.lock を持つ uv の project。lock-sha256 = uv.lock の sha256(送り手が計算し、worker が展開の後に照合する)。
   python = uv の Python の指定・groups = 入れる dependency group・native = wheel で入れる native の package。"
  (#^ str repo)
  (#^ str path)
  (#^ str lock-sha256)
  (#^ str python)
  (setv #^ tuple groups #())
  (setv #^ tuple native #())
  (defn #^ None __post-init__ [self]  ; defk にできない: dataclass の検査の口
    (RuntimeEnvInvalid.check-name "project の repo" self.repo)
    (RuntimeEnvInvalid.check-relative "project の path" self.path)
    (when (not (and (isinstance self.lock-sha256 str) (.fullmatch SHA256-PATTERN self.lock-sha256)))
      (raise (RuntimeEnvInvalid InvalidKind.BAD-SHA256 (.format "lock の sha256 は 16 進 64 桁: {!r}" self.lock-sha256))))
    (when (not (and (isinstance self.python str) self.python))
      (raise (RuntimeEnvInvalid InvalidKind.EMPTY "project の python が空")))
    (RuntimeEnvInvalid.check-tuple "PythonProject.groups" self.groups str)
    (for [g self.groups] (RuntimeEnvInvalid.check-name "dependency group" g))
    (RuntimeEnvInvalid.check-tuple "PythonProject.native" self.native NativeWheel)))


(defrecord ToolRequirement
  "worker が名乗る道具(例: 外部の CLI)。version = 版の範囲(空 = 何でもよい)。キーに入らない(置き先の選択で見る)。"
  (#^ str name)
  (setv #^ str version "")
  (defn #^ None __post-init__ [self]  ; defk にできない: dataclass の検査の口
    (RuntimeEnvInvalid.check-name "道具の名" self.name)))


(defrecord EnvVar
  "子 process に足す環境変数。秘密は置かない(coordinator の状態に残る)。キーに入らない。"
  (#^ str name)
  (#^ str value)
  (defn #^ None __post-init__ [self]  ; defk にできない: dataclass の検査の口
    (when (not (and (isinstance self.name str) (.fullmatch ENV-VAR-PATTERN self.name)))
      (raise (RuntimeEnvInvalid InvalidKind.INVALID-NAME (.format "環境変数の名は英大文字・数字・_: {!r}" self.name))))
    (when (or (in self.name RESERVED-ENV-NAMES) (.startswith self.name RESERVED-ENV-PREFIXES))
      (raise (RuntimeEnvInvalid InvalidKind.RESERVED-ENV-VAR (.format "worker が組む環境変数は宣言で置けない: {}" self.name))))
    (when (and (.endswith self.name SECRET-ENV-SUFFIXES) (not (.endswith self.name PATH-ENV-SUFFIXES)))
      (raise (RuntimeEnvInvalid InvalidKind.SECRET-ENV-VAR
                (.format "秘密の中身を名指す環境変数は置けない(宣言と task は coordinator の状態に残る — 秘密は file の path の名 {} で運ぶ): {}"
                         (.join "・" PATH-ENV-SUFFIXES) self.name))))
    (when (not (isinstance self.value str))
      (raise (TypeError (.format "環境変数 {} の値は文字列: {!r}" self.name self.value)))))
  (defn [staticmethod] #^ (| str None) environ-refusal [#^ object environ]  ; defk にできない: effect の構成子(__post_init__)が呼ぶ純粋な検査
    "子の環境変数の組(service の :environ・task の :environ — 名 → 文字列の dict)が受けられない理由(受けられれば None)。
     名と値の規則は EnvVar 1 つ — 型の構成子(RemoteJob)と core の判断(runtime_env_rules.child-environ-refusal)が同じ規則を読むため、
     型の側に置く(intent が core を読まない)。"
    (when (not (isinstance environ dict))
      (return (.format "environ は環境変数の名 → 文字列の dict: {!r}" environ)))
    (for [#(k v) (.items environ)]
      (when (not (isinstance v str))
        (return (.format "environ の {} の値は文字列: {!r}" k v)))
      (try
        (EnvVar :name k :value v)
        (except [error RuntimeEnvInvalid]
          (return (.format "environ の {}: {}" k error)))))
    None))


(defrecord RuntimeEnv
  "実行環境の宣言。repos = 1 つ以上・project = 依存の正本の uv の project・
   import-roots = \"<repo の名>/<repo の中の相対の dir>\"(前が先。project の venv の .pth に並ぶ)。"
  (#^ tuple repos)
  (#^ PythonProject project)
  (#^ tuple import-roots)
  (setv #^ tuple env-vars #())
  (setv #^ tuple tools #())
  (setv #^ int format RUNTIME-ENV-FORMAT)
  ;; 焼く範囲の入口の module の名(空 = import の根の下を全部焼く)。準備はこの module たちの import の閉包だけを焼き、閉包の外の
  ;; module は子が import した時に作られる。root の file の中身を変えないのでキーに入れない(2026-09-26・#664 の実測: 焼く 1,063 file の
  ;; うち task が読むのは約 2 割)。
  (setv #^ tuple bytecode-entries #())
  (defn #^ None __post-init__ [self]  ; defk にできない: dataclass の検査の口
    (RuntimeEnvInvalid.check-tuple "RuntimeEnv.repos" self.repos RepoCheckout)
    (RuntimeEnvInvalid.check-tuple "RuntimeEnv.bytecode-entries" self.bytecode-entries str)
    (for [m self.bytecode-entries]
      (when (not (all (gfor part (.split m ".") (.isidentifier part))))
        (raise (RuntimeEnvInvalid InvalidKind.INVALID-NAME (.format "焼く範囲の入口は module の名(点で区切る): {!r}" m)))))
    (RuntimeEnvInvalid.check-tuple "RuntimeEnv.import-roots" self.import-roots str)
    (RuntimeEnvInvalid.check-tuple "RuntimeEnv.env-vars" self.env-vars EnvVar)
    (RuntimeEnvInvalid.check-tuple "RuntimeEnv.tools" self.tools ToolRequirement)
    (when (not self.repos) (raise (RuntimeEnvInvalid InvalidKind.EMPTY "repos が空")))
    (when (not self.import-roots) (raise (RuntimeEnvInvalid InvalidKind.EMPTY "import の根が空")))
    (when (!= self.format RUNTIME-ENV-FORMAT)
      (raise (RuntimeEnvInvalid InvalidKind.BAD-JSON (.format "宣言の形の版 {} は扱えない(扱える版 = {})" self.format RUNTIME-ENV-FORMAT))))
    (setv names (lfor r self.repos r.name))
    (when (!= (len names) (len (set names)))
      (raise (RuntimeEnvInvalid InvalidKind.DUPLICATE-REPO (.format "repo の名が重なる: {}" names))))
    (setv known (frozenset names))
    (for [#(what repo) (+ [#("project" self.project.repo)]
                          (lfor w self.project.native #((.format "native {}" w.package) w.repo)))]
      (when (not-in repo known)
        (raise (RuntimeEnvInvalid InvalidKind.UNKNOWN-REPO (.format "{} の repo {} が宣言に無い" what repo)))))
    (for [root self.import-roots]
      (setv #(repo _ rel) (.partition root "/"))
      (when (not-in repo known)
        (raise (RuntimeEnvInvalid InvalidKind.UNKNOWN-REPO (.format "import の根 {!r} の repo が宣言に無い" root))))
      (RuntimeEnvInvalid.check-relative (.format "import の根 {!r} の dir" root) rel))
    (setv env-names (lfor v self.env-vars v.name))
    (when (!= (len env-names) (len (set env-names)))
      (raise (RuntimeEnvInvalid InvalidKind.INVALID-NAME (.format "環境変数の名が重なる: {}" env-names))))))


;; --- 準備の失敗(worker の側・値で返す) ----------------------------------------------------

;; 準備の失敗の種類(答えの 12 種。宣言の誤りの env-invalid は答えではなく送り手の例外 RuntimeEnvInvalid)。どれも子 process を
;; 起こす前に起きるので、置き直しても同じ task を 2 度実行しない。worker は URL を断らない — 鍵の表に無い URL は鍵なしで clone し、
;; 読めない非公開の repo は clone の失敗(repo-unreachable)で返る(2026-10-05 に断る種類 repo-denied を外した)。
;;   repo-unreachable    clone / fetch の失敗(一時 — network・読む資格の無い非公開の repo)
;;   commit-missing      fetch の後も commit が無い(送り手が push していない)
;;   lock-mismatch       展開した uv.lock の sha256 が宣言と違う
;;   lock-stale          uv sync が「lock が古い」で断る(worker の準備は --frozen なので今は出ない — #2730。翻訳の読み分けと共に残す)
;;   sync-failed         uv sync のその他の失敗(一時 / 恒久は uv の出力で分ける)
;;   env-incompatible    名前の影・子の約束の版の外
;;   memory-killed       組みの子(uv の build・sync)が cgroup の memory の上限で殺された: signal 9 で終わり、cgroup の memory.events の
;;                       oom_kill が子を起こす前より増えた(恒久 — 同じ上限の下で組み直しても同じく殺される。増えていない signal 9 は
;;                       今までどおり native-build-failed / sync-failed の一時の失敗・#3668・cisco-c8 の可 2026-10-06 00:3x)
(defenum EnvFailureKind
  REPO-UNREACHABLE COMMIT-MISSING LOCK-MISMATCH LOCK-STALE SYNC-FAILED NATIVE-BUILD-FAILED
  PYTHON-UNAVAILABLE TOOL-MISSING DISK-FULL ENV-INCOMPATIBLE PREPARE-TIMEOUT MEMORY-KILLED)


;; kind の既定の「一時か」。sync-failed と native-build-failed は起きた所の handler が値で決める。
(val RETRYABLE-KINDS (frozenset #(EnvFailureKind.REPO-UNREACHABLE EnvFailureKind.PYTHON-UNAVAILABLE
                                   EnvFailureKind.DISK-FULL EnvFailureKind.PREPARE-TIMEOUT)))


(defrecord EnvFailure
  "準備の失敗。retryable = 一時の失敗(coordinator が起動前の task を別の worker へ置き直せる)。"
  (#^ EnvFailureKind kind)
  (#^ str detail)
  (#^ bool retryable)
  (defn #^ None __post-init__ [self]  ; defk にできない: dataclass の検査の口
    (when (not (isinstance self.kind EnvFailureKind))
      (raise (TypeError (.format "EnvFailure.kind は EnvFailureKind: {!r}" self.kind))))))
