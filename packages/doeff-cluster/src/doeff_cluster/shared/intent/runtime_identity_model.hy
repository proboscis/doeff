;;; 入口の検め(この process が宣言した実行環境の root の、宣言の commit の code を import しているか)の型と effect
;;; (runtime_identity から分けた・#2344)。判断の Program は doeff_cluster.shared.core.runtime_identity・
;;; 渡した材料で答える handler は doeff_cluster.shared.protocol.runtime_facts。一致の根拠と失敗の kind の表は core の頭の註。
(require doeff-hy.macros [val])
(require doeff-hy.record [defenum defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(import dataclasses [dataclass])
(import enum [StrEnum])
(import doeff [EffectBase])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])


;; 入口の検めの失敗の種類(上の表)。
(defenum IdentityFailureKind
  (UNDECLARED "undeclared")
  (ROOT-UNMARKED "root-unmarked")
  (MARKER-MISMATCH "marker-mismatch")
  (MODULE-OUTSIDE-ROOT "module-outside-root"))


(defrecord ModuleOrigin
  "確かめる module 1 つの置き場。file = import が解いた file の絶対 path(解けなければ空)。"
  (#^ str module)
  (#^ str file))


(defrecord RootMarker
  "root の完成の印から読んだ形式の版・キー・platform・宣言(形式の版が違えば env は None — 読まない)。"
  (#^ int format)
  (#^ str key)
  (#^ str platform)
  (#^ (| RuntimeEnv None) env))


(defrecord RuntimeFacts
  "検めの材料。declared = 渡された宣言(無ければ None)・key = 渡されたキー・root = 印を持つ root の絶対 path(無ければ空)・
   marker = その印(root が無ければ None)・origins = 確かめる module の置き場。"
  (#^ (| RuntimeEnv None) declared)
  (#^ str key)
  (#^ str root)
  (#^ (| RootMarker None) marker)
  (#^ (get tuple #(ModuleOrigin ...)) origins))


(defrecord RepoCommit
  "一致した repo 1 つ(報告と記録に載せる)。"
  (#^ str name)
  (#^ str commit))


(defrecord RuntimeIdentity
  "一致の答え。process はこの宣言の root の code で動いている。pid = この process の id(報告に載せる)。"
  (#^ str key)
  (#^ str root)
  (#^ (get tuple #(RepoCommit ...)) commits)
  (#^ int pid))


(defrecord RuntimeIdentityMismatch
  "不一致の答え。kind = IdentityFailureKind・detail = 人が読む理由(どの module がどこから来たか等)・pid = この process の id。"
  (#^ IdentityFailureKind kind)
  (#^ str detail)
  (#^ int pid))


(defrecord ProcessFacts
  "この process から読んだ材料(文字列のまま — 宣言の型への読み戻しは Program が行う)。declared-json = DOEFF_RUNTIME_ENV
   (無ければ空)・key = DOEFF_RUNTIME_ENV_KEY・root = 印を持つ root の絶対 path(無ければ空)・marker-json = その印の file の中身
   (root が無ければ空)・origins = 確かめる module の置き場・pid = この process の id。"
  (#^ str declared-json)
  (#^ str key)
  (#^ str root)
  (#^ str marker-json)
  (#^ (get tuple #(ModuleOrigin ...)) origins)
  (#^ int pid))


(defclass [(dataclass :frozen True)] ReadRuntimeFacts [EffectBase]
  "検めの材料を読む。modules = 確かめる module の名の tuple。答え = ProcessFacts。"
  (#^ (get tuple #(str ...)) modules))
