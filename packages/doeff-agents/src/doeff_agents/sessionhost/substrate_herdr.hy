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
;;; conformance/herdr-physics.md に記録。agent.start の改形は herdr 0.7.5 /
;;; protocol 17 で 2026-07-29 再実測、session 同一性アンカーは 2026-08-01 /
;;; 08-09 再実測 — 同文書の追補):
;;;   - transport: newline-JSON over unix socket(~/.config/herdr/herdr.sock)。
;;;     request line 全体に ~1MiB 上限(実測境界 1,048,336B OK / 1,049,344B 拒否、
;;;     超過は server 側 "api request line is too large" + BrokenPipe)。
;;;   - session 同一性のアンカーは workspace label(doeff が workspace.create で
;;;     所有し、実 agent 起動後も残存 — 実測 2026-08-01/08-09)。herdr の
;;;     agent 名簿は同一性を担えない: pane 内で実 agent が起動すると ~2 秒で
;;;     herdr の実 agent 検出が名札を上書きし、agent.get {target: session 名}
;;;     が agent_not_found になる(実測 2026-08-01 n=3 決定的)。重複 session
;;;     名の拒否は herdr が label 重複を拒否しない(実測 2026-08-09)ため
;;;     doeff 側の create-then-verify が所有する(herdr-new-session-io 参照)。
;;;   - 外部命名席(`ai` 等が herdr の名簿に付けた agent 名で呼ばれる対話席 —
;;;     koine session.adopt の session_name)は doeff 所有の workspace を持たず、
;;;     workspace label は repo 名など(実測 2026-08-17: 自席 = agent 名
;;;     s-7bfc5d5028 / label "doeff")。has-session は「名簿に居る ∨ label
;;;     holder が在る」、session-pane-ids は label holder が無い名前に限り
;;;     名簿の pane を返す — 名簿は**実在確認**としてだけ読む
;;;     (herdr-external-agent-pane-id-io — 同一性・kill には使わない)。
;;;   - **workspace_id は創出順に単調でない**(実測 2026-08-14 — 詳細は
;;;     herdr-workspace-order-key の docstring と herdr-physics.md)。よって
;;;     label を持つ workspace 群は「どれが本物か」を選べない集合として扱う:
;;;     生死 = 非空、帰属観測 = 全 holder の pane 和、kill = 全 holder 閉鎖。
;;;     順序が要るのは同時作成どうしの合意(gate の事後照合)だけ。
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
  TmuxSessionPaneIds
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

;; 旧実装(PR #569 まで)が名前登録(pane.report_agent → agent.rename)で
;; 使った authority source / placeholder kind は撤去済み — agent 名簿への
;; 登録は行わない。実 agent 起動で herdr の実 agent 検出が ~2 秒で名札を
;; 上書きするため(実測 2026-08-01 n=3)、agent 名簿は session 同一性を
;; 担えず、登録は「実 agent 起動までしか持たない名札」という誤解を生む
;; 死荷重になる。同一性は workspace label(下記 herdr-label-workspace-ids-io)。

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

(deff herdr-workspace-order-key [workspace-id]
  {:pre [(: workspace-id str)]
   :post [(: % tuple)]}
  "workspace_id の決定的な全順序の鍵(shortlex: 桁数優先、同桁は ASCII)。

   ⚠ これは**創出順の主張ではない**。herdr の workspace_id は創出順に単調では
   ない — 実測 2026-08-14(稼働 herdr 0.7.5 / protocol 17): 連続 create が
   w3NZ → w3N0、独立の再現で w3MZ → w3M0(いずれも後発のほうが shortlex で
   先に来る)。初版の docstring はこれを「base62 風カウンタで創出順に単調増加」
   と書いていたが、根拠の実測(2026-08-09 の w1VS → w1VT → w1VV)は末尾桁の
   繰り上がりを跨いでおらず、一般には成立しない。

   用途はただ 1 つ = **同時作成どうしが同じ勝者に合意するための規約**
   (どの全順序でも合意は成立する)。素の文字列比較は桁境界で順序が変わる
   (\"w1VS\" < \"w3R\")ため、規約としては桁数優先で固定する。
   既存 session の検出にこの鍵を使ってはならない(→ herdr-new-session-verdict)。"
  #((len workspace-id) workspace-id))

(deff herdr-new-session-verdict [ws-id pre-holders post-holders]
  {:pre [(: ws-id str) (: pre-holders list) (: post-holders list)]
   :post [(: % str)]}
  "new-session の判定(純関数)。\"duplicate\" / \"vanished\" / \"ok\" のいずれか。

   段の分担 — workspace_id が創出順に単調でない(herdr-workspace-order-key の
   実測)ため、「id 最小 = 先行 session」で既存を検出してはならない:
     - **事前照会**(pre-holders = 作成前の名簿)が既存 session の検出を担う。
       1 つでも居れば重複 — id の順序に一切依存しない。
     - **事後照合**(post-holders = 作成後の名簿)は、事前照会と作成の間に
       割り込んだ**同時作成どうし**の決着だけに使う。競合者は同じ名簿から
       同じ全順序で同じ勝者を選ぶので、ちょうど 1 つが残る。
     - 作成直後に自分が名簿に居ない = 重複ではない別の失敗(\"vanished\")。
       旧実装は空の保持者一覧の先頭を取って IndexError にしており、呼び手の
       RuntimeError 捕捉を素通りしていた(保持者一覧の添字参照は semgrep
       doeff-agents-herdr-label-holders-must-not-be-indexed で恒久禁止)。"
  (cond
    pre-holders "duplicate"
    (not-in ws-id post-holders) "vanished"
    (!= (get (sorted post-holders :key herdr-workspace-order-key) 0) ws-id) "duplicate"
    True "ok"))

(deff herdr-label-holders [listing label]
  {:pre [(: listing dict) (: label str)]
   :post [(: % list)]}
  "workspace.list の応答 + label → その label を持つ workspace_id **集合**
   (herdr が並べた順のまま)。返り値は集合として扱う契約で、**要素の位置に
   意味は無い** — 「先頭が本物の session」という読み方をしてはならない
   (workspace_id は創出順に単調でない: herdr-workspace-order-key の実測)。
   実際の消費は生死 = 非空判定、帰属 = 全要素の pane 和、kill = 全要素の
   閉鎖で、いずれも順序に依存しない。"
  (lfor ws (get listing "workspaces")
        :if (= (.get ws "label") label)
        (get ws "workspace_id")))

(deff herdr-label-workspace-ids-io [socket-path label]
  {:pre [(: socket-path str) (: label str)]
   :post [(: % list)]}
  "label を持つ workspace_id 集合(不在は空 list)。session 同一性アンカーの
   解決面: label は doeff が workspace.create で所有し、実 agent 起動後も
   残存する(実測 2026-08-01/08-09 — herdr の agent 名簿と違い実 agent 検出に
   上書きされない)。順序づけはここでは行わない — 同時作成の合意に全順序が
   要る場面(herdr-new-session-verdict の事後照合)だけが、その場で
   herdr-workspace-order-key を適用する。"
  (herdr-label-holders (herdr-call socket-path "workspace.list" {}) label))

(deff herdr-session-pane-ids-io [socket-path session-name]
  {:pre [(: socket-path str) (: session-name str)]
   :post [(: % list)]}
  "session 名 → 所有 pane 集合(ADR-DOE-AGENTS-010 R4 の帰属観測)。
   label を持つ **全 workspace** の pane.list を連結する — tmux
   `list-panes -s -t NAME`(その名前の session が現に持つ全 pane)と同じ
   「名前に帰属する pane 全部」の意味。holder を 1 つ選ぶ実装は、holder が
   2 つ並んだ状態(同時作成の敗者が自分を閉じる前の過渡・敗者の close 失敗・
   doeff 外の同名 label・conformance の帯域外 create)で生きた pane を
   不可視にし、policy.hy の帰属検証(R4)がその席を pane 消失 = vanished と
   誤って終端する(実測反例 2026-08-14: holder 2 に対し返る pane は 1 つ)。
   不在は空 list(tmux 側の session 不在 parity)。観測中に holder が
   消えるのは正常な競合なので、その holder は pane 0 個として扱う
   (pane.list はその時 workspace_not_found を返す — 実測 2026-08-14)。"
  (setv holders (herdr-label-workspace-ids-io socket-path session-name))
  (setv pane-ids [])
  (for [ws-id holders]
    (try
      (setv listing (herdr-call socket-path "pane.list" {"workspace_id" ws-id}))
      (except [e HerdrApiError]
        (when (!= e.code "workspace_not_found")
          (raise))
        (continue)))
    (.extend pane-ids (lfor pane (get listing "panes") (get pane "pane_id"))))
  ;; 外部命名席(herdr-external-agent-pane-id-io 参照): doeff 所有の workspace が
  ;; 1 つも無い名前だけ、herdr の agent 名簿で 1 pane に解決する。label holder
  ;; が在る名前では名簿を見ない — doeff 所有 session の pane 集合に、同名の
  ;; 外部 agent の pane を混ぜない(R4 が他人の pane を帰属と誤認しない)。
  (when (not holders)
    (setv external (herdr-external-agent-pane-id-io socket-path session-name))
    (when (is-not external None)
      (.append pane-ids external)))
  pane-ids)


(deff herdr-registry-agent-pane-id [result name]
  {:pre [(: result dict) (: name str)]
   :post [(: % (| str None))]}
  "agent.get の応答 + 要求した名前 → その名前を持つ agent の pane_id(純関数)。
   herdr の AgentTarget は agent 名と pane_id の両方を target に受ける
   (実測 2026-08-17: agent.get {target: \"w4XN:p9\"} が解決する)ため、
   応答の agent.name が要求した名前と完全一致する時だけ採用する — pane_id を
   session 名として渡した呼び手に「その名前の席が居る」と答えない。"
  (setv agent (.get result "agent"))
  (when (not (isinstance agent dict))
    (return None))
  (when (!= (.get agent "name") name)
    (return None))
  (setv pane-id (.get agent "pane_id"))
  (if (isinstance pane-id str) pane-id None))

(deff herdr-external-agent-pane-id-io [socket-path name]
  {:pre [(: socket-path str) (: name str)]
   :post [(: % (| str None))]}
  "**外部命名席**の実在確認: doeff が作っていない pane に herdr 上で agent 名 =
   name が付いている席(`ai` 等が herdr の名簿に登録した対話席 — koine の
   session.adopt が session_name に運ぶのはこの名前で、その workspace label は
   repo 名などで session 名と一致しない。実測 2026-08-17: 自席 w4XN:p9 は
   agent 名 s-7bfc5d5028・workspace label \"doeff\")。不在は None。

   これは session **同一性**のアンカーではない — doeff 所有 session の同一性・
   帰属・kill は workspace label だけで決まる(herdr の実 agent 検出は doeff が
   付けた名札を ~2 秒で消す — 実測 n=4)。名簿は「その名前の席が今 herdr に
   居るか」を答えるためだけに読む(観測のみ・変異なし)— 消費者は
   TmuxHasSession(論理和の片腕)と TmuxSessionPaneIds(label holder が無い
   名前に限る)の 2 つだけ。この 1 点以外で agent.get を呼ぶことは semgrep
   doeff-agents-herdr-session-identity-not-agent-name が禁止する(waiver
   marker は下の 1 行のみ)。"
  (try
    ;; registry-existence-probe: 外部命名席の実在確認だけに許す(同一性・帰属・kill は label)
    (setv result (herdr-call socket-path "agent.get" {"target" name}))
    (except [e HerdrApiError]
      (when (= e.code "agent_not_found")
        (return None))
      (raise)))
  (herdr-registry-agent-pane-id result name))

(deff herdr-new-session-io [socket-path session-name work-dir env]
  {:pre [(: socket-path str) (: session-name str) (: work-dir str) (: env dict)]
   :post [(: % str)]}
  "TmuxNewSession の実体(herdr 0.7.5 / protocol 17): workspace.create が
   label / cwd / env を直接受けるため、専用 workspace の root pane が
   そのまま session pane になる(常に独立フル幅 grid = tmux new-session の
   幾何学 parity)。shell は herdr の既定 shell 起動に委ねる(tmux
   new-session の default-shell parity)。

   session 同一性のアンカー = workspace label(issue
   substrate-herdr-session-identity-anchor-r2-607f0c)。旧実装(PR #569)の
   agent 名簿登録(pane.report_agent → agent.rename)は行わない — pane 内で
   実 agent が起動すると ~2 秒で herdr の実 agent 検出が名札を上書きし、
   agent.get で解決不能になる(実測 2026-08-01 n=3 決定的)。label は
   実 agent 起動後も残存し(同実測)、agent 名の invalid_agent_name 制約
   (小文字開始・[a-z0-9_-]・32 文字以内)も受けない(実測 2026-08-09:
   60 文字・大文字・記号入り label を受理)ので session 名を無変換で持てる。

   禁止 env reject と prompt 抑制 env は tmux 側と同じ substrate 所有。
   workspace は最後の pane close で自動消滅する(protocol 17 でも実測)。"
  (ensure-no-forbidden-agent-env env)
  (setv effective-env (dict env))
  (for [[key value] SHELL-PROMPT-SUPPRESSING-ENV]
    (when (not-in key effective-env)
      (setv (get effective-env key) value)))
  ;; 重複 session 名の拒否(tmux duplicate 拒否 parity)— doeff 側で所有する。
  ;; herdr は label 重複をネイティブ拒否しない(実測 2026-08-09: 同 label の
  ;; workspace.create は 2 つ目も成功)。判定は 2 段(herdr-new-session-verdict):
  ;;   段 1 = 事前照会。既存 session の検出はこちらが担う。workspace を作る前に
  ;;     名簿を見て、1 つでも居れば作らずに拒否する — id の順序に依存しない。
  ;;     初版は create-then-verify 一本で「id 最小 = 先行 session」に依存して
  ;;     いたが、workspace_id は創出順に単調でない(実測 2026-08-14: w3NZ の
  ;;     次の create が w3N0)ため、後発が勝者に選ばれて重複が素通りしていた。
  ;;   段 2 = 事後照合。段 1 と作成の間に割り込んだ**同時作成**だけがここに残る。
  ;;     競合者は同じ名簿から同じ全順序で同じ勝者に合意するので、ちょうど 1 つが
  ;;     残る(合意に必要なのは全順序であって創出順ではない)。敗者は自分の
  ;;     workspace を閉じてから raise する。
  (setv pre-holders (herdr-label-workspace-ids-io socket-path session-name))
  ;; 段 1 = verdict の pre-holders 節の先出し(規則は同じで、workspace を作る前に
  ;; 適用することで無駄な生成と、close 失敗時の label 二重保持を避ける)。
  (when pre-holders
    ;; 診断は保持者を全部並べる(1 つ選んで名乗ると「その 1 つが session だ」と
    ;; 読める — 集合のどれが本物かは id からは決まらない)。
    (setv incumbents (.join ", " pre-holders))
    (raise (RuntimeError
             (+ f"herdr new-session failed: duplicate session: {session-name} "
                f"(label held by workspace {incumbents})"))))
  (setv ws-result (herdr-call socket-path "workspace.create"
                              {"label" session-name
                               "cwd" work-dir
                               "env" effective-env
                               "focus" False}))
  (setv ws-id (get (get ws-result "workspace") "workspace_id"))
  (setv pane-id (get (get ws-result "root_pane") "pane_id"))
  (setv post-holders (herdr-label-workspace-ids-io socket-path session-name))
  (setv verdict (herdr-new-session-verdict ws-id [] post-holders))
  (when (!= verdict "ok")
    (try
      (herdr-call socket-path "workspace.close" {"workspace_id" ws-id})
      (except [Exception]
        None))  ; 敗者の掃除は best-effort — 失敗の送出を優先する
    (when (= verdict "vanished")
      (raise (RuntimeError
               (+ f"herdr new-session failed: workspace {ws-id} for {session-name} "
                  f"disappeared from workspace.list right after create"))))
    (setv others (.join ", " (lfor h post-holders :if (!= h ws-id) h)))
    (raise (RuntimeError
             (+ f"herdr new-session failed: duplicate session: {session-name} "
                f"(label held by workspace {others})"))))
  pane-id)

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
  "TmuxKillSession の実体: label を持つ workspace を **全部** workspace.close
   する(1 つの workspace の全 pane ごと落とすのは tmux kill-session の
   全 window/pane 破棄 parity。旧実装の単一 pane.close と違い S19c 型の
   sibling pane が残る workspace も取り残さない。加えて holder が複数ある
   状態でも「その名前の session は消える」= tmux が同名 session を持てない
   ことの parity)。holder を 1 つ選んで閉じる実装は生きた workspace を残し、
   呼び手には「殺した」と返る(実測反例 2026-08-14: holder 2 → kill 後も
   1 つが生存)。不在は raise(tmux kill-session の非 0 exit と同 parity —
   cancel / cleanup program は has-session で guard してから呼ぶ)。
   閉鎖中の自然消滅は成功と同義(workspace_not_found のみ飲み込む — 他の
   error 封筒は呼び手へ上げる)。"
  (setv holders (herdr-label-workspace-ids-io socket-path session-name))
  (when (not holders)
    (raise (RuntimeError f"herdr kill-session failed: {session-name}")))
  (for [ws-id holders]
    (try
      (herdr-call socket-path "workspace.close" {"workspace_id" ws-id})
      (except [e HerdrApiError]
        (when (!= e.code "workspace_not_found")
          (raise)))))
  None)


;; ---------------------------------------------------------------------------
;; herdr substrate handler(mux effect のみ — 非 mux は外側へ素通し)
;; ---------------------------------------------------------------------------

(defhandler herdr-substrate [socket-path]
  (TmuxNewSession [session-name work-dir env]
    (resume (herdr-new-session-io socket-path session-name work-dir env)))

  (TmuxHasSession [session-name]
    ;; 生死 = 外部命名席(herdr の agent 名簿に name が居る — koine
    ;; session.adopt / 鏡原則の substrate_present が見る対話席)、または
    ;; doeff 所有 session(label holder の有無 — どれが「本物」かは選ばない)。
    ;; 論理和なので順序は答えを変えない — 名簿を先に引くのは費用: 対話席用
    ;; host の台帳は外部命名席が大半(adopted 行 134 / 全行 134 — 2026-08-17
    ;; 実測)で、session.list は行ごとに has-session を probe する(ADR-007
    ;; R4)。名簿 hit なら 1 RPC で決まり、workspace.list(全 workspace の
    ;; payload)を毎行引かずに済む。doeff 所有 session はもう片方で解決する。
    (resume (or (is-not (herdr-external-agent-pane-id-io socket-path session-name)
                        None)
                (bool (herdr-label-workspace-ids-io socket-path session-name)))))

  (TmuxSessionPaneIds [session-name]
    (resume (herdr-session-pane-ids-io socket-path session-name)))

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
