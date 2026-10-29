;;; driver 層の I/O 語彙(agora-redesign 段 7 lane 7c — 決定 1.3)。
;;;
;;; 既知の形: algebraic effects(effect = 要求の値・handler = 実行の家・
;;; composition root が handler を選ぶ)。この module は要求の形だけを持ち、
;;; 実世界には触らない。実行は `doeff_agents._io_handlers`(本番)と
;;; `doeff_agents._io_fake`(検)の 2 つの handler が担う。
;;;
;;; 置き場の理由: doeff-agents の driver 層(adapters / tmux / session の
;;; 台帳 / agentd の client)は、判断と I/O が 1 つの関数に同居していた
;;; (round 3 の棚卸しで 8 file)。判断はそのまま Python / Hy に残し、
;;; I/O の 1 行だけをこの語彙の要求へ置き換える。
;;;
;;; agentd(sessionhost)の substrate 語彙
;;; (`doeff_agents._sessionhost.effects` の Fs* / Proc* / Env*)とは別の
;;; 語彙。あちらは ADR-DOE-AGENTS-004 で Rust の oracle に対して凍結された
;;; 契約面で、driver 層の要求(実行ファイルの探索・home の複製・unix socket
;;; の 1 往復)を持たない。2 つの語彙を 1 つへ畳む便は未着手(lane 7c の
;;; 報告に間隙として登記)。
;;;
;;; 語彙の規則(agora-redesign master plan 0b): ここは「道具」の層の名前で、
;;; domain の言葉(会話・仕事・郵便)は 1 つも持たない。

(require doeff-hy.macros [defk <-])

(import sys)
(import dataclasses [dataclass])
(import doeff [EffectBase])
;; 汎用の子 process の effect と答えの型は doeff-core-effects に 1 つ(agora-redesign #802 便 1)— この module は同じ型を re-export する
;; (driver 層の使い手と検は今までどおり doeff_agents._io_effects から読める)。
(import doeff_core_effects.process_effects [ProcessOutcome RunProcess ExecutableAt])


;; ---------------------------------------------------------------------------
;; 値(要求の答え)
;; ---------------------------------------------------------------------------


;; ---------------------------------------------------------------------------
;; 環境の読み
;; ---------------------------------------------------------------------------

(defclass [(dataclass :frozen True :kw-only True)] WhichExecutable [EffectBase]
  "PATH 上の実行ファイルを探す。見つからなければ None。"
  #^ str name)

(defclass [(dataclass :frozen True :kw-only True)] HomePath [EffectBase]
  "呼び手 process の home。")

(defclass [(dataclass :frozen True :kw-only True)] TempRoot [EffectBase]
  "一時 file の置き場の根。")

(defclass [(dataclass :frozen True :kw-only True)] ProcessId [EffectBase]
  "呼び手 process の pid(名前の衝突を避ける材料)。")

(defclass [(dataclass :frozen True :kw-only True)] EnvValue [EffectBase]
  "環境変数 1 つの値。未設定は None。"
  #^ str name)


;; ---------------------------------------------------------------------------
;; file の読み書き
;; ---------------------------------------------------------------------------

(defclass [(dataclass :frozen True :kw-only True)] PathExists [EffectBase]
  "path が実在するか(file・dir・symlink の解決後)。"
  #^ str path)

(defclass [(dataclass :frozen True :kw-only True)] ReadText [EffectBase]
  "text を読む。不在は None(不在は例外ではなく値)。"
  #^ str path)

(defclass [(dataclass :frozen True :kw-only True)] WriteText [EffectBase]
  "text を書く。親 dir は呼び手が MakeDirs で先に作る。"
  #^ str path
  #^ str text
  #^ (| int None) mode
  (setv mode None))

(defclass [(dataclass :frozen True :kw-only True)] AppendText [EffectBase]
  "text を末尾に足す。追記の 1 回は不可分(JSONL の 1 行)。"
  #^ str path
  #^ str text)

(defclass [(dataclass :frozen True :kw-only True)] MakeDirs [EffectBase]
  "dir を(親ごと)作る。既存は成功。"
  #^ str path
  #^ (| int None) mode
  (setv mode None))

(defclass [(dataclass :frozen True :kw-only True)] TouchFile [EffectBase]
  "空の file を用意し、mode を与える。既存の中身は変えない。"
  #^ str path
  #^ (| int None) mode
  (setv mode None))

(defclass [(dataclass :frozen True :kw-only True)] CopyFile [EffectBase]
  "file を複製する。source 不在は False(判断は呼び手)。"
  #^ str source
  #^ str target)

(defclass [(dataclass :frozen True :kw-only True)] ListDir [EffectBase]
  "dir の中身を glob で並べる。不在・権限不足は空(観測できない = 空)。"
  #^ str path
  #^ str pattern
  (setv pattern "*"))


;; ---------------------------------------------------------------------------
;; 子 process と socket
;; ---------------------------------------------------------------------------

(defclass [(dataclass :frozen True :kw-only True)] SpawnDetached [EffectBase]
  "子 process を起こして待たない(常駐の起動)。stdout / stderr は log へ。"
  #^ tuple argv
  #^ str log-path
  #^ (| str None) cwd
  (setv cwd None))

(defclass [(dataclass :frozen True :kw-only True)] StartSessionHost [EffectBase]
  "session host を常駐として起こして待たない。答えは pid。stdout / stderr は log へ。
   起こす argv は handler が `_session-host-argv` で組む: 既定は今の python で入口の module を
   起こす `[sys.executable -m doeff_agents._sessionhost.host --db … --socket … --max-running …]`
   (PATH を見ない・サブコマンドを持たない)。host-bin は起こす実行 file の差し替えの口
   ($DOEFF_AGENTD_BIN か呼び手の daemon_bin — 検・別の実装の host)で、同じ旗を受け取る。"
  #^ str db-path
  #^ str socket-path
  #^ int max-running
  #^ str log-path
  #^ (| str None) host-bin
  (setv host-bin None))

;; session host の入口の module(python -m の的)。結果の MCP server の入口は
;; sessionhost/impls/channel.hy が持つ — 役ごとに 1 つの module で、サブコマンドで振り分けない。
(setv _SESSION-HOST-MODULE "doeff_agents._sessionhost.host")

(defk _session-host-argv [host-bin db-path socket-path max-running]
  {:pre [(: host-bin (| str None)) (: db-path str) (: socket-path str) (: max-running int)]
   :post [(: % list)]
   :tags {:context "driver-io" :role "foundation"}}
  "StartSessionHost が起こす argv(本番と検の handler と、client の案内の文が読む 1 点)。"
  (+ (if (is host-bin None) [sys.executable "-m" _SESSION-HOST-MODULE] [host-bin])
     ["--db" db-path "--socket" socket-path "--max-running" (str max-running)]))

(defclass [(dataclass :frozen True :kw-only True)] UnixLineRequest [EffectBase]
  "unix socket へ 1 行送って 1 行読む。framing の外(JSON の組み立てと
   解釈)は呼び手の純粋な判断。"
  #^ str socket-path
  #^ str payload
  #^ (| float None) timeout
  (setv timeout None))

(defclass [(dataclass :frozen True :kw-only True)] UnixConnectProbe [EffectBase]
  "unix socket が接続を受けるかだけを見る(送らない)。答えは 3 つの値:
   \"accepting\"(繋がった)/ \"refused\"(不在が証明された — 相手不在か
   path 無し)/ \"unreachable\"(観測できなかった)。不在の証明と観測の失敗を
   1 つの bool へ畳むと、呼び手が「死んだ」と「見えない」を区別できない。"
  #^ str socket-path
  #^ float timeout)

;; 待ちの要求はこの語彙に無い — driver 層の program は doeff-time の Delay を yield し、1 秒が何であるかは
;; composition root が置く時間の handler が決める(本番 = io_handlers.hy の run-driver-io の sync-time-handler・
;; 検 = io_fake.hy の世界の時計。card ki-3c3d85253c2c・ADR-DOE-TIME-001 R1)。
(defclass [(dataclass :frozen True :kw-only True)] MonotonicTime [EffectBase]
  "単調時計の現在値(秒)。締切の計算に使う。")


;; ---------------------------------------------------------------------------
;; 要求の構築子(呼び手はこれを bind する — ADR-DOE-HY-004: defk のみ)
;; ---------------------------------------------------------------------------

(defk which-executable [name]
  {:pre [(: name str)]
   :post [(: % (| str None))]}
  "PATH 上の実行ファイルの絶対 path。無ければ None。"
  (<- found (WhichExecutable :name name))
  found)

(defk home-path []
  {:pre [] :post [(: % str)]}
  "呼び手 process の home。"
  (<- value (HomePath))
  value)

(defk temp-root []
  {:pre [] :post [(: % str)]}
  "一時 file の置き場の根。"
  (<- value (TempRoot))
  value)

(defk process-id []
  {:pre [] :post [(: % int)]}
  "呼び手 process の pid。"
  (<- value (ProcessId))
  value)

(defk env-value [name]
  {:pre [(: name str)]
   :post [(: % (| str None))]}
  "環境変数 1 つ。未設定は None。"
  (<- value (EnvValue :name name))
  value)

(defk path-exists [path]
  {:pre [(: path str)]
   :post [(: % bool)]}
  "path の実在。"
  (<- found (PathExists :path path))
  found)

(defk read-text [path]
  {:pre [(: path str)]
   :post [(: % (| str None))]}
  "text を読む。不在は None。"
  (<- content (ReadText :path path))
  content)

(defk write-text [path text [mode None]]
  {:pre [(: path str) (: text str) (: mode (| int None))]
   :post [(: % (| bool None))]}
  "text を書く。"
  (<- _ (WriteText :path path :text text :mode mode))
  None)

(defk append-text [path text]
  {:pre [(: path str) (: text str)]
   :post [(: % (| bool None))]}
  "text を末尾に足す。"
  (<- _ (AppendText :path path :text text))
  None)

(defk make-dirs [path [mode None]]
  {:pre [(: path str) (: mode (| int None))]
   :post [(: % (| bool None))]}
  "dir を親ごと作る。"
  (<- _ (MakeDirs :path path :mode mode))
  None)

(defk touch-file [path [mode None]]
  {:pre [(: path str) (: mode (| int None))]
   :post [(: % (| bool None))]}
  "空の file を用意する。"
  (<- _ (TouchFile :path path :mode mode))
  None)

(defk copy-file [source target]
  {:pre [(: source str) (: target str)]
   :post [(: % bool)]}
  "file を複製する。source 不在は False。"
  (<- copied (CopyFile :source source :target target))
  copied)

(defk list-dir [path [pattern "*"]]
  {:pre [(: path str) (: pattern str)]
   :post [(: % tuple)]}
  "dir の中身を並べる。"
  (<- entries (ListDir :path path :pattern pattern))
  entries)

(defk run-process [argv [stdin None] [timeout None] [cwd None]]
  {:pre [(: argv tuple) (: stdin (| str None)) (: timeout (| float None)) (: cwd (| str None))]
   :post [(: % ProcessOutcome)]}
  "子 process を 1 回走らせる。"
  (<- outcome (RunProcess :argv argv :stdin stdin :timeout timeout :cwd cwd))
  outcome)

(defk spawn-detached [argv log-path [cwd None]]
  {:pre [(: argv tuple) (: log-path str) (: cwd (| str None))]
   :post [(: % int)]}
  "常駐を起こして pid を返す。"
  (<- pid (SpawnDetached :argv argv :log-path log-path :cwd cwd))
  pid)

(defk unix-line-request [socket-path payload [timeout None]]
  {:pre [(: socket-path str) (: payload str) (: timeout (| float None))]
   :post [(: % str)]}
  "unix socket へ 1 行送って 1 行読む。"
  (<- line (UnixLineRequest :socket-path socket-path :payload payload :timeout timeout))
  line)

(defk unix-connect-probe [socket-path timeout]
  {:pre [(: socket-path str) (: timeout float)]
   :post [(: % str)]}
  "unix socket の観測: accepting / refused / unreachable。"
  (<- verdict (UnixConnectProbe :socket-path socket-path :timeout timeout))
  verdict)

(defk executable-at [path]
  {:pre [(: path str)]
   :post [(: % bool)]}
  "その path に実行できる file が在るか。"
  (<- found (ExecutableAt :path path))
  found)

(defk monotonic-time []
  {:pre [] :post [(: % float)]}
  "単調時計の現在値(秒)。"
  (<- value (MonotonicTime))
  value)
