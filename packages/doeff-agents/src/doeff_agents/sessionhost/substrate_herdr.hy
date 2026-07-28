;;; herdr substrate handler(第二 substrate トライアル)— Tmux* 6 effect の
;;; herdr socket API への束縛。
;;;
;;; real-substrate(substrate.hy)が生 IO の唯一の家であることは変えない:
;;; この handler は多重化(mux)effect だけを解釈し、非 mux substrate
;;; (Clock / Proc / Fs / Env / SessionStore)は未処理のまま外側の
;;; real-substrate へ素通しする。設置は host.hy run-hosted の 1 箇所で、
;;; real-substrate の内側に挿す。
;;;
;;; effect 語彙は Tmux* のまま(改名は成功後の語彙中立化 ADR — 別チェンジ)。
;;; 物理の出典は Phase 0 プローブ実測(herdr 0.7.1 / protocol 14、2026-07-07、
;;; conformance/herdr-physics.md に記録。agent.start の改形と名前登録経路は
;;; herdr 0.7.5 / protocol 17 で 2026-07-29 再実測 — 同文書の追補):
;;;   - transport: newline-JSON over unix socket(~/.config/herdr/herdr.sock)。
;;;     request line 全体に ~1MiB 上限(実測境界 1,048,336B OK / 1,049,344B 拒否、
;;;     超過は server 側 "api request line is too large" + BrokenPipe)。
;;;   - 名前の一意性は herdr がネイティブ強制(agent_name_taken)= tmux
;;;     new-session の duplicate session 拒否と同 parity。protocol 17
;;;     (herdr 0.7.5、再実測 2026-07-29 — herdr-physics.md 追補)で
;;;     agent.start は {name kind pane_id} の「既存 pane への管理対象 agent
;;;     起動 + 検出待ち」に改形されたため、shell pane の名前登録は
;;;     pane.report_agent → agent.rename 経由(herdr-new-session-io 参照)。
;;;   - pane.read の本文は result.read.text。source 名は underscore
;;;     (recent_unwrapped — hyphen は socket で拒否)。
;;;   - recent / recent_unwrapped = スクロールバック + 現在画面の tail-N。
;;;     ただしスクロールバックが空の間は空文字(0.7.1 quirk)→ visible へ
;;;     fallback(visible は grid 折返しのまま = fallback 期のみの caveat)。
;;;   - pane.read format=text は「行末スペースをトリム」する(実測 2026-07-07、
;;;     全 source 共通)。tmux capture-pane -J は trailing space を保持するため
;;;     text 形式は capture parity を満たせない — codex idle prompt "\n› "
;;;     (trailing space 込み部分文字列一致)が構造的に検出不能になる
;;;     (= S18-herdr launch ハングの根本原因)。format=ansi は grid 再構成の
;;;     SGR 込みで trailing space を保持し、wrap join も有効、空 quirk も同一
;;;     (実測)— capture は ansi 読み + strip で parity を復元する。
;;;   - pane.send_keys のキー名: Enter/Up/Down/Left/Right/Escape/Tab/Space は
;;;     大文字小文字不問、BSpace は backspace のみ、Home/End は非対応。

(require doeff-hy.macros [deff defhandler])

(import json)
(import os)
(import re)
(import socket)
(import time)

(import doeff_agents.sessionhost.effects [
  TmuxNewSession
  TmuxHasSession
  TmuxPaneCurrentCommand
  TmuxCapture
  TmuxSendKeys
  TmuxKillSession])
;; TUI 物理(paste-settle / confirm ループ / 禁止 env)は Claude・Codex の
;; REPL 物理であって tmux の物理ではない — substrate.hy から import して
;; 再利用する(コピーしない)。
(import doeff_agents.sessionhost.substrate [
  CONFIRM-INITIAL-SECONDS
  CONFIRM-MAX-RETRIES
  CONFIRM-RETRY-SECONDS
  PASTE-SETTLE-SECONDS
  SHELL-PROMPT-SUPPRESSING-ENV
  ensure-no-forbidden-agent-env
  unsubmitted-paste-input?])


;; ---------------------------------------------------------------------------
;; 定数(Phase 0 実測物理)
;; ---------------------------------------------------------------------------

(setv DEFAULT-HERDR-SOCKET
      (os.path.join (os.path.expanduser "~") ".config" "herdr" "herdr.sock"))

;; request line の実測上限 1,048,336B に対する保守値。チャンク判定は生バイト長
;; でなく「JSON エスケープ後の request line 実バイト長」で行う — 改行・引用符・
;; 制御文字の escape 膨張(\n → 2B、制御文字 → 6B)が生バイト基準を破るため。
(setv REQUEST-LINE-BYTE-LIMIT 1000000)

;; tmux キー名 → herdr キー名。ALLOWED-UNBLOCK-KEY-NAMES(policy.hy)と
;; dialog dismiss キー(markers.hy)が送出しうる語彙を被覆する。Home/End は
;; herdr 非対応 → readline / TUI 入力箱で同義の ctrl+a / ctrl+e へ写像
;; (意図 = 行頭・行末移動の保存)。その他は herdr が大文字小文字不問で
;; 受理するため素通し。
(setv HERDR-KEY-NAME-MAP
      {"BSpace" "backspace"
       "Home" "ctrl+a"
       "End" "ctrl+e"})

;; protocol 17 の名前登録(pane.report_agent → agent.rename)で使う authority
;; source と placeholder kind(実測 2026-07-29)。source は報告主体の識別子で、
;; pane.clear_agent_authority が同じ source を要求する。agent は schema 上
;; 自由文字列(PaneReportAgentParams.agent は type: string — AgentStartParams
;; の kind 語彙とは別 field で、任意文字列受理を実測)。rename 直後に authority
;; を clear すると kind 表示ごと消え、実 agent 起動後は herdr の画面検出が
;; 正しい kind を付け直すため、shell pane 生成時点の実体に正直な値を使う。
(setv HERDR-AGENT-AUTHORITY-SOURCE "doeff-sessionhost")
(setv HERDR-AGENT-PLACEHOLDER-KIND "doeff-shell")

;; pane.read format=ansi の応答から剥がすエスケープ列: CSI(SGR 含む)/
;; OSC(BEL・ST 終端)/その他の ESC シーケンス(ECMA-48: ESC + intermediates
;; [ -/]* + final [0-~] — DECSC `\x1b7` のような private 形も含む)。
;; alternation の順序で CSI・OSC が先にマッチする。
(setv ANSI-ESCAPE-RE
      (re.compile (+ r"\x1b\[[0-9;:?]*[ -/]*[@-~]"
                     r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
                     r"|\x1b[ -/]*[0-~]")))

(deff normalize-ansi-read [text]
  {:pre [(: text str)]
   :post [(: % str)]}
  "pane.read format=ansi の本文 → tmux capture-pane -p -J 相当の plain text。
   エスケープ列を strip し、grid 行区切り \\r\\n を \\n に正規化する。
   trailing space は SGR の内側に座る(実測: `\\x1b[1m› \\x1b[0m`)ため strip
   後も保持される — text 形式(行末スペースをトリム)との決定的な差で、
   codex idle prompt \"\\n› \" の検出可能性はこの保持に依存する。"
  (.replace (.sub ANSI-ESCAPE-RE "" text) "\r\n" "\n"))


;; ---------------------------------------------------------------------------
;; socket client(newline-JSON RPC、1 call = 1 接続)
;; ---------------------------------------------------------------------------

(defclass HerdrApiError [RuntimeError]
  "herdr の error 封筒({\"error\":{\"code\",\"message\"}})。code を保持し、
   呼び手が不在系(agent_not_found)を値へ落とせるようにする。"
  (defn __init__ [self code message]
    (.__init__ RuntimeError self f"herdr api error {code}: {message}")
    (setv self.code code)))


(deff herdr-request-line [method params]
  {:pre [(: method str) (: params dict)]
   :post [(: % bytes)]}
  "1 リクエスト行の UTF-8 bytes(compact・非 ASCII 素通し)。チャンク判定は
   この実バイト長で行う(server の上限は request line 全体に掛かる)。"
  (.encode (+ (json.dumps {"id" "doeff-substrate" "method" method
                           "params" params}
                          :separators #("," ":") :ensure-ascii False)
              "\n")
           "utf-8"))

(deff herdr-call [socket-path method params]
  {:pre [(: socket-path str) (: method str) (: params dict)]
   :post [(: % "result 値(dict)— error 封筒は HerdrApiError")]}
  "herdr socket への 1 RPC。接続は都度張る(monitor cadence では十分安価で、
   daemon 再起動へも自然に追従する)。"
  (setv line (herdr-request-line method params))
  (setv sock (socket.socket socket.AF-UNIX socket.SOCK-STREAM))
  (try
    (.connect sock socket-path)
    (.sendall sock line)
    (setv chunks [])
    (while True
      (setv data (.recv sock 65536))
      (when (= data b"")
        (break))
      (.append chunks data)
      (when (in b"\n" data)
        (break)))
    (finally (.close sock)))
  (setv raw (.strip (.decode (.join b"" chunks) "utf-8")))
  (when (= raw "")
    (raise (RuntimeError f"herdr returned no response for {method}")))
  (setv response (json.loads raw))
  (when (in "error" response)
    (setv err (get response "error"))
    (raise (HerdrApiError (.get err "code" "unknown") (.get err "message" ""))))
  (get response "result"))


;; ---------------------------------------------------------------------------
;; herdr 生 IO(oracle tmux_* との対応は各 deff の docstring)
;; ---------------------------------------------------------------------------

(deff herdr-new-session-io [socket-path session-name work-dir env]
  {:pre [(: socket-path str) (: session-name str) (: work-dir str) (: env dict)]
   :post [(: % str)]}
  "TmuxNewSession の実体(herdr 0.7.5 / protocol 17、再実測 2026-07-29 —
   herdr-physics.md 追補): workspace.create が cwd / env を直接受けるため、
   専用 workspace の root pane がそのまま session pane になる(常に独立
   フル幅 grid = tmux new-session の幾何学 parity。旧 protocol 14 の
   root pane close ダンスは不要)。shell は herdr の既定 shell 起動に委ねる
   (tmux new-session の default-shell parity)。
   protocol 14 の agent.start {name cwd argv env workspace_id} は protocol 17
   で {name kind pane_id} —「既存 shell pane への管理対象 agent 起動 +
   検出待ち」— に改形され、shell pane の名前付き生成には使えない(旧 payload
   に kind を足しても missing field pane_id、実測)。名前(TmuxHasSession /
   TmuxKillSession が agent.get で解決)は pane.report_agent(外部 authority
   で agent エントリを作る)→ agent.rename(名簿登録 — 重複名は
   agent_name_taken をネイティブ拒否 = tmux duplicate 拒否 parity)→
   pane.clear_agent_authority(state authority を画面検出へ返す — 名前は
   terminal に残る、実測)で登録する。
   禁止 env reject と prompt 抑制 env は tmux 側と同じ substrate 所有。
   登録途中で失敗した場合は作った workspace を閉じてから元のエラーを
   再送出する。workspace は最後の pane close で自動消滅する(protocol 17
   でも実測)ので kill-session 側の追加 cleanup は不要。"
  (ensure-no-forbidden-agent-env env)
  (setv effective-env (dict env))
  (for [[key value] SHELL-PROMPT-SUPPRESSING-ENV]
    (when (not-in key effective-env)
      (setv (get effective-env key) value)))
  (setv ws-result (herdr-call socket-path "workspace.create"
                              {"label" session-name
                               "cwd" work-dir
                               "env" effective-env
                               "focus" False}))
  (setv ws-id (get (get ws-result "workspace") "workspace_id"))
  (setv pane-id (get (get ws-result "root_pane") "pane_id"))
  (try
    (herdr-call socket-path "pane.report_agent"
                {"pane_id" pane-id
                 "source" HERDR-AGENT-AUTHORITY-SOURCE
                 "agent" HERDR-AGENT-PLACEHOLDER-KIND
                 "state" "unknown"})
    (herdr-call socket-path "agent.rename"
                {"target" pane-id "name" session-name})
    (herdr-call socket-path "pane.clear_agent_authority"
                {"pane_id" pane-id
                 "source" HERDR-AGENT-AUTHORITY-SOURCE})
    (except [Exception]
      (try
        (herdr-call socket-path "workspace.close" {"workspace_id" ws-id})
        (except [Exception]
          None))  ; workspace 掃除は best-effort — 元のエラーを優先して再送出
      (raise)))
  pane-id)

(deff herdr-agent-pane-id-io [socket-path session-name]
  {:pre [(: socket-path str) (: session-name str)]
   :post [(: % (| str None))]}
  "名前 → pane_id の解決(agent.get {target})。不在(agent_not_found)は
   None — TmuxHasSession の bool と TmuxKillSession の対象解決が共有する。"
  (try
    (setv result (herdr-call socket-path "agent.get" {"target" session-name}))
    (except [e HerdrApiError]
      (when (= e.code "agent_not_found")
        (return None))
      (raise)))
  (get (get result "agent") "pane_id"))

(deff herdr-capture-io [socket-path pane-id lines]
  {:pre [(: socket-path str) (: pane-id str) (: lines int)]
   :post [(: % str)]}
  "TmuxCapture の実体(tmux capture-pane -p -J -S -N parity):
   recent_unwrapped の tail N = スクロールバック末尾 + 現在画面、かつ折返し
   復元不要(tmux -J の非可逆 wrap-repair バグ類を構造的に回避)。
   format=ansi + normalize-ansi-read で読む — format=text は行末スペースを
   トリムし(実測)、tmux -J が保持する trailing space(codex idle prompt
   \"\\n› \" の検出が依存)を壊すため使えない(S18-herdr ハングの根本原因)。
   スクロールバックが空の間は空文字が返る(0.7.1 quirk)ので visible へ
   fallback する — 空判定は normalize 後(SGR だけの応答も空として扱う)。
   fallback 期のみ grid 折返しのままという caveat は herdr-physics.md に
   記録済み。"
  (setv result (herdr-call socket-path "pane.read"
                           {"pane_id" pane-id
                            "source" "recent_unwrapped"
                            "lines" (max 1 lines)
                            "format" "ansi"}))
  (setv text (normalize-ansi-read (get (get result "read") "text")))
  (when (!= text "")
    (return text))
  (setv fallback (herdr-call socket-path "pane.read"
                             {"pane_id" pane-id "source" "visible"
                              "format" "ansi"}))
  (normalize-ansi-read (get (get fallback "read") "text")))

(deff herdr-pane-current-command-io [socket-path pane-id]
  {:pre [(: socket-path str) (: pane-id str)]
   :post [(: % (| str None))]}
  "TmuxPaneCurrentCommand の実体: pane.process_info の
   foreground_processes[0].argv0(実測: name は version 文字列のことがある —
   claude で '2.1.201' — ため argv0 を使う)。pane 不在・foreground 不明は
   None(tmux display-message 失敗時の None と同 parity)。"
  (try
    (setv result (herdr-call socket-path "pane.process_info"
                             {"pane_id" pane-id}))
    (except [HerdrApiError]
      (return None)))
  (setv procs (get (get result "process_info") "foreground_processes"))
  (if procs
      (get (get procs 0) "argv0")
      None))

(deff chunked-send-texts [text]
  {:pre [(: text str)]
   :post [(: % list)]}
  "pane.send_text へ渡すチャンク列。判定は各チャンクの request line 実バイト長
   ≤ REQUEST-LINE-BYTE-LIMIT(escape 膨張込みの正確な物理)。超過時は
   文字数を半減して再判定 — 1 文字の escape は高々 12B なので必ず停止する。"
  (setv chunks [])
  (setv remaining text)
  (while remaining
    (setv take (len remaining))
    (while (> (len (herdr-request-line
                     "pane.send_text"
                     {"pane_id" "w000:p000" "text" (cut remaining 0 take)}))
              REQUEST-LINE-BYTE-LIMIT)
      (setv take (max 1 (// take 2))))
    (.append chunks (cut remaining 0 take))
    (setv remaining (cut remaining take None)))
  chunks)

(deff herdr-send-text-io [socket-path pane-id text]
  {:pre [(: socket-path str) (: pane-id str) (: text str)]
   :post [(: % "None")]}
  "literal paste の実体(oracle tmux_paste_literal parity)。~1MiB request-line
   上限のため分割送出する — tmux 16KB imsg 上限に対する load-buffer stdin 修正
   (33ab4bae)の herdr 版。5MB まで byte-exact を Phase 0 で実測済み。"
  (for [chunk (chunked-send-texts text)]
    (herdr-call socket-path "pane.send_text"
                {"pane_id" pane-id "text" chunk}))
  None)

(deff herdr-key-name [key]
  {:pre [(: key str)]
   :post [(: % str)]}
  "tmux キー名 → herdr キー名(BSpace → backspace、Home/End → ctrl+a/ctrl+e、
   他は素通し — herdr は標準キー名を大文字小文字不問で受理する)。"
  (.get HERDR-KEY-NAME-MAP key key))

(deff herdr-send-key-io [socket-path pane-id key]
  {:pre [(: socket-path str) (: pane-id str) (: key str)]
   :post [(: % "None")]}
  (herdr-call socket-path "pane.send_keys"
              {"pane_id" pane-id "keys" [(herdr-key-name key)]})
  None)

(deff herdr-send-keys-io [socket-path pane-id text literal submit]
  {:pre [(: socket-path str) (: pane-id str) (: text str)
         (: literal bool) (: submit bool)]
   :post [(: % "None")]}
  "TmuxSendKeys の実体(oracle tmux_send_keys と同じ骨格): literal はチャンク
   paste、literal=False はキー名送出。submit は settle 後の Enter + confirm
   ループ(paste 残留の Enter 再送 — ハザード 4 の盲窓物理はここが所有する)。
   settle / confirm の定数と unsubmitted-paste-input? は Claude / Codex の
   TUI 物理なので substrate.hy と共有する。"
  (if (and literal text)
      (herdr-send-text-io socket-path pane-id text)
      (when text
        (herdr-send-key-io socket-path pane-id text)))
  (when submit
    (time.sleep PASTE-SETTLE-SECONDS)
    (herdr-send-key-io socket-path pane-id "Enter")
    (when (and literal text)
      (time.sleep CONFIRM-INITIAL-SECONDS)
      (for [_ (range CONFIRM-MAX-RETRIES)]
        (setv output (herdr-capture-io socket-path pane-id 40))
        (when (not (unsubmitted-paste-input? output text))
          (break))
        (herdr-send-key-io socket-path pane-id "Enter")
        (time.sleep CONFIRM-RETRY-SECONDS))))
  None)

(deff herdr-kill-session-io [socket-path session-name]
  {:pre [(: socket-path str) (: session-name str)]
   :post [(: % "None")]}
  "TmuxKillSession の実体: 名前 → pane_id 解決の上 pane.close。不在は raise
   (tmux kill-session の非 0 exit と同 parity — cancel / cleanup program は
   has-session で guard してから呼ぶ)。"
  (setv pane-id (herdr-agent-pane-id-io socket-path session-name))
  (when (is pane-id None)
    (raise (RuntimeError f"herdr kill-session failed: {session-name}")))
  (herdr-call socket-path "pane.close" {"pane_id" pane-id})
  None)


;; ---------------------------------------------------------------------------
;; herdr substrate handler(mux effect のみ — 非 mux は外側へ素通し)
;; ---------------------------------------------------------------------------

(defhandler herdr-substrate [socket-path]
  (TmuxNewSession [session-name work-dir env]
    (resume (herdr-new-session-io socket-path session-name work-dir env)))

  (TmuxHasSession [session-name]
    (resume (is-not (herdr-agent-pane-id-io socket-path session-name) None)))

  (TmuxPaneCurrentCommand [pane-id]
    (resume (herdr-pane-current-command-io socket-path pane-id)))

  (TmuxCapture [pane-id lines]
    (resume (herdr-capture-io socket-path pane-id lines)))

  (TmuxSendKeys [pane-id text literal submit]
    (herdr-send-keys-io socket-path pane-id text literal submit)
    (resume None))

  (TmuxKillSession [session-name]
    (herdr-kill-session-io socket-path session-name)
    (resume None)))
