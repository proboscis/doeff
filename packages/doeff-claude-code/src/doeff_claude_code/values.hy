;;; 公開 effect の欄の値の型(設計 layer2-effects-design.md 4.1)。data だけで I/O を行わない。
;;;
;;; 上の層が持つ身元は会話の id(claude の session_id = transcript)と手番の参照(ClaudeTurn)だけ。
;;; pid・argv・process の生死はこの package の handler の内側に閉じ、ここの型に載らない。
(import dataclasses [dataclass field])
(import uuid)
(import doeff_hy.frozen [FrozenMap frozen-json-object frozen-map-of])


(defn #^ (get FrozenMap str) checked-env [#^ object value #^ str what]
  "process の env の検め: 文字列 → 文字列の写像を凍らせた FrozenMap(外れは TypeError)。"
  (setv env (frozen-map-of value what))
  (when (not (all (gfor item (.values env) (isinstance item str))))
    (raise (TypeError (.format "{} の値は文字列: {!r}" what value))))
  env)


(defn #^ str checked-session-id [#^ str value #^ str what]
  "会話の id の検め: CLI の --session-id / --resume が受ける UUID の綴りちょうど(外れは ValueError)。"
  (when (not (isinstance value str))
    (raise (TypeError (.format "{} は文字列: {!r}" what value))))
  (try
    (setv parsed (uuid.UUID value))
    (except [ValueError]
      (raise (ValueError (.format "{} は UUID の綴り: {!r}" what value)))))
  (when (!= (str parsed) (.lower value))
    (raise (ValueError (.format "{} は UUID の正規の綴り(小文字・区切りつき): {!r}" what value))))
  value)


;; --- 家(資格と記録の置き場) -------------------------------------------------------------------

(defclass [(dataclass :frozen True)] ClaudeHome []
  "claude の家: CLAUDE_CONFIG_DIR と、起こす process の env ちょうど(凍らせた写像 — 作る時に写し取る)。資格・PATH・HOME は
   上の層(composition root)が env に置く。handler は env を読むだけで os.environ を足さない。"
  (#^ str config-dir)
  (setv #^ (get FrozenMap str) env (field :default-factory FrozenMap))
  (defn __post_init__ [self]
    (when (not (and (isinstance self.config-dir str) self.config-dir))
      (raise (ValueError (.format "ClaudeHome.config_dir は空でない文字列: {!r}" self.config-dir))))
    (object.__setattr__ self "env" (checked-env self.env "ClaudeHome.env"))))


;; --- 許可の方策 --------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] BypassAll []
  "全部を許す(--dangerously-skip-permissions)。")

(setv ASK-HOST-MODES #("default" "acceptEdits" "plan"))

(defclass [(dataclass :frozen True)] AskHost []
  "道具の前に許可の問いを出来事(PermissionRequested)で上へ渡し、ClaudeAnswerPermission の答えで進める
   (--permission-prompt-tool stdio)。mode は閉語彙 default / acceptEdits / plan。"
  (setv #^ str mode "default")
  (defn __post_init__ [self]
    (when (not-in self.mode ASK-HOST-MODES)
      (raise (ValueError (.format "AskHost.mode は {} のどれか: {!r}" ASK-HOST-MODES self.mode))))))

(defclass [(dataclass :frozen True)] DenyUnlisted []
  "名簿の道具だけを許し、ほかは問わずに断る(--permission-prompts none --allowedTools …)。"
  (setv #^ (get tuple #(str ...)) allowed-tools #())
  (defn __post_init__ [self]
    (when (not (all (gfor tool self.allowed-tools (and (isinstance tool str) tool))))
      (raise (ValueError (.format "DenyUnlisted.allowed_tools は空でない文字列の tuple: {!r}" self.allowed-tools))))))

(setv PermissionPolicy (| BypassAll AskHost DenyUnlisted))


;; --- MCP の server ----------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] McpSse []
  (#^ str url))

(defclass [(dataclass :frozen True)] McpStdio []
  "stdio の MCP の server: command・args と、server の process の env(凍らせた写像)。"
  (#^ str command)
  (setv #^ (get tuple #(str ...)) args #())
  (setv #^ (get FrozenMap str) env (field :default-factory FrozenMap))
  (defn __post_init__ [self]
    (object.__setattr__ self "env" (checked-env self.env "McpStdio.env"))))

(setv McpServer (| McpSse McpStdio))


;; --- 圧縮の閾値 ---------------------------------------------------------------------------------

(setv AUTOCOMPACT-MIN-TOKENS 100000)
(setv AUTOCOMPACT-MAX-TOKENS 1000000)

(defclass [(dataclass :frozen True)] AutocompactAuto []
  "CLI 自身の窓に合わせた閾値(--autocompact auto)。")

(defclass [(dataclass :frozen True)] AutocompactTokens []
  "閾値の token 数(--autocompact <n>)。CLI が受ける幅(100k〜1M・実測 2.1.274)の外は作れない
   — 幅の外の値を argv に載せると CLI が起動の段で死ぬ。"
  (#^ int tokens)
  (defn __post_init__ [self]
    (when (or (isinstance self.tokens bool) (not (isinstance self.tokens int))
              (not (<= AUTOCOMPACT-MIN-TOKENS self.tokens AUTOCOMPACT-MAX-TOKENS)))
      (raise (ValueError (.format "AutocompactTokens.tokens は {} 以上 {} 以下の整数: {!r}"
                                  AUTOCOMPACT-MIN-TOKENS AUTOCOMPACT-MAX-TOKENS self.tokens))))))

(setv AutocompactWindow (| AutocompactAuto AutocompactTokens))


;; --- 会話の宣言 ---------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] ClaudeSessionSpec []
  "会話を起こす時の宣言。settings は 1 つの --settings へ合流する(handler が置く鍵と合流する — argv.hy)。
   settings = CLI の settings の JSON(鍵の集合は CLI が決めるので深く凍らせた写像)/ mcp-servers = 名 → McpSse / McpStdio。
   cold-resume-prompt = 降りた会話を --resume で起こす前に 1 回だけ走らせる print mode の prompt(例: 圧縮の plugin の命令)。
   None なら走らせない。"
  (#^ ClaudeHome home)
  (#^ str cwd)
  (setv #^ (| str None) model None)
  (setv #^ (| str None) effort None)
  (setv #^ FrozenMap settings (field :default-factory FrozenMap))
  (setv #^ (get FrozenMap (| McpSse McpStdio)) mcp-servers (field :default-factory FrozenMap))
  (setv #^ (| BypassAll AskHost DenyUnlisted) permission (BypassAll))
  (setv #^ (| AutocompactAuto AutocompactTokens None) autocompact None)
  (setv #^ (| str None) system-prompt-append None)
  (setv #^ (| str None) cold-resume-prompt None)
  (defn __post_init__ [self]
    (when (not (isinstance self.home ClaudeHome))
      (raise (TypeError "ClaudeSessionSpec.home は ClaudeHome")))
    (when (not (and (isinstance self.cwd str) self.cwd))
      (raise (ValueError "ClaudeSessionSpec.cwd は空でない文字列")))
    (when (not (isinstance self.permission #(BypassAll AskHost DenyUnlisted)))
      (raise (TypeError (.format "ClaudeSessionSpec.permission は BypassAll / AskHost / DenyUnlisted: {!r}" self.permission))))
    (when (not (or (is self.autocompact None) (isinstance self.autocompact #(AutocompactAuto AutocompactTokens))))
      (raise (TypeError (.format "ClaudeSessionSpec.autocompact は AutocompactAuto / AutocompactTokens / None: {!r}" self.autocompact))))
    (object.__setattr__ self "settings" (frozen-json-object self.settings "ClaudeSessionSpec.settings"))
    (object.__setattr__ self "mcp_servers" (frozen-map-of self.mcp-servers "ClaudeSessionSpec.mcp_servers"))
    (when (not (all (gfor #(name server) (.items self.mcp-servers)
                          (and (isinstance name str) name (isinstance server #(McpSse McpStdio))))))
      (raise (TypeError "ClaudeSessionSpec.mcp_servers は 名 → McpSse / McpStdio")))))


;; --- 会話の始まり方と transcript の持ち込み -------------------------------------------------------

(defclass [(dataclass :frozen True)] LinkFromHome []
  "別の家の transcript を symlink で持ち込む(同じ cwd の置き場)。"
  (#^ ClaudeHome source-home))

(defclass [(dataclass :frozen True)] Rebuilt []
  "組み直した transcript(jsonl の本文)を書く。既に在れば上書きしない。"
  (#^ str jsonl-text)
  (defn __post_init__ [self]
    (when (not (and (isinstance self.jsonl-text str) (.strip self.jsonl-text)))
      (raise (ValueError "Rebuilt.jsonl_text は空でない文字列")))))

(setv TranscriptCarry (| LinkFromHome Rebuilt))

(defclass [(dataclass :frozen True)] FreshSession []
  "新しい会話(--session-id。id は呼び手が決める)。"
  (#^ str session-id)
  (defn __post_init__ [self] (checked-session-id self.session-id "FreshSession.session_id")))

(defclass [(dataclass :frozen True)] ResumeSession []
  "既にある会話の続き(--resume)。carry が在れば起こす前に持ち込む。"
  (#^ str session-id)
  (setv #^ (| LinkFromHome Rebuilt None) carry None)
  (defn __post_init__ [self]
    (checked-session-id self.session-id "ResumeSession.session_id")
    (when (not (or (is self.carry None) (isinstance self.carry #(LinkFromHome Rebuilt))))
      (raise (TypeError "ResumeSession.carry は LinkFromHome / Rebuilt / None")))))

(defclass [(dataclass :frozen True)] ForkSession []
  "既にある会話からの枝分かれ(--resume <親> --fork-session)。新しい id は TurnStarted で知る。"
  (#^ str parent-session-id)
  (setv #^ (| LinkFromHome Rebuilt None) carry None)
  (defn __post_init__ [self]
    (checked-session-id self.parent-session-id "ForkSession.parent_session_id")
    (when (not (or (is self.carry None) (isinstance self.carry #(LinkFromHome Rebuilt))))
      (raise (TypeError "ForkSession.carry は LinkFromHome / Rebuilt / None")))))

(setv SessionOrigin (| FreshSession ResumeSession ForkSession))


;; --- 手番の入力と参照 ---------------------------------------------------------------------------

(setv IMAGE-MIMES #("image/png" "image/jpeg" "image/gif" "image/webp"))

(defclass [(dataclass :frozen True)] ImageAttachment []
  "添付の画像 1 つ(base64)。mime の受理は handler が閉語彙 IMAGE-MIMES で検める(外れは AttachmentRefused)。"
  (#^ str mime)
  (#^ str data-base64))

(defclass [(dataclass :frozen True)] TurnInput []
  "手番の入力 1 つ。ref = 入力の行の名(stdin の行の uuid)— CLI が InputFate でこの綴りの運命を名乗る。"
  (#^ str text)
  (#^ str ref)
  (setv #^ (get tuple #(ImageAttachment ...)) attachments #())
  (defn __post_init__ [self]
    (when (not (isinstance self.text str))
      (raise (TypeError "TurnInput.text は文字列")))
    (when (not (and (isinstance self.ref str) self.ref))
      (raise (ValueError "TurnInput.ref は空でない文字列")))
    (when (not (all (gfor item self.attachments (isinstance item ImageAttachment))))
      (raise (TypeError "TurnInput.attachments は ImageAttachment の tuple")))))

(defclass [(dataclass :frozen True)] ClaudeTurn []
  "手番の参照: 会話の id と、その会話の中の手番の番号。pid・argv・出来事の置き場は持たない。"
  (#^ str session-id)
  (#^ int turn-seq))


;; --- 許可の答え ---------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] Allow []
  "許す。updated-input = 道具の入力の差し替え(凍らせた写像)— None なら問いの入力のまま。"
  (setv #^ (| FrozenMap None) updated-input None)
  (defn __post_init__ [self]
    (when (is-not self.updated-input None)
      (object.__setattr__ self "updated_input" (frozen-json-object self.updated-input "Allow.updated_input")))))

(defclass [(dataclass :frozen True)] Deny []
  (#^ str message))

(setv PermissionAnswer (| Allow Deny))
