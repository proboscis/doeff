;;; doeff-claude-code の公開 effect 11 と、その戻り値の型(設計 4.3)。
;;;
;;; ClaudeStartTurn / ClaudeInjectInput / ClaudeInterruptTurn / ClaudeReadTurnEvents / ClaudeAnswerPermission /
;;; ClaudeCloseSession / ClaudeSessionStatus / ClaudeExportSession / ClaudeWarmSession / ClaudeReplaceSessionCredential /
;;; ClaudePrintOnce。
;;;
;;; 単位は「claude の会話(session)と、その上の手番」。process の単位の操作(起こす・stdin に書く・信号・降ろす・pid の生存)は
;;; 公開しない — handler の内側の語彙(ClaudeWarmSession も「会話を入力の前に事前起動する」操作で、process を指定しない)。失敗は
;;; 例外ではなく戻り値の型で返す(成功の型と失敗の型の判別可能な union)。handler の実装の誤り(I/O の予期しない例外)だけが例外として
;;; 上がる。ClaudePrintOnce だけは会話と手番の外の 1 回きりの問い(構造化の答えを 1 つ受けて降りる — 会話の id も記録も持たない)。
(require doeff-hy.macros [defeffect val])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import json)
(import doeff [EffectBase])
(import doeff_hy._frozen [FrozenJson freeze-json])
(import doeff_claude_code._values [ClaudeSessionSpec ClaudeHome ClaudeTurn TurnInput Allow Deny checked-session-id
                                  FreshSession ResumeSession ForkSession SessionOrigin LentCredential PrintContent])
(import doeff_claude_code._lines [ClaudeStreamLine Completed Failed Interrupted BackendLost Usage AccountLimitHit])


;; --- 成功の戻り値 ---------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] TurnStarted []
  "手番が始まった。session-id = 手番の会話の id(ForkSession なら CLI が決めた新しい id)。"
  (#^ ClaudeTurn turn)
  (#^ str session-id))

(defclass [(dataclass :frozen True)] InputQueued []
  (#^ str ref))

(defclass [(dataclass :frozen True)] InterruptRequested [])

(defclass [(dataclass :frozen True)] InputAlreadyRead []
  "入力を名指した止め(ClaudeInterruptTurn の for-input)で、名指した入力 ref を CLI がもう読んでいた(運命が queued でない)ので止めなかった
   — 走っている手番はそのまま続く。"
  (#^ str ref))

(defclass [(dataclass :frozen True)] TurnEventPage []
  "lines = after-seq より後の行(重複も欠落もなく)・next-seq = 次に渡す after-seq・end = 手番の終わり(まだなら None)。"
  (#^ (get tuple #(ClaudeStreamLine ...)) lines)
  (#^ int next-seq)
  (#^ (| Completed Failed Interrupted BackendLost None) end))

(defclass [(dataclass :frozen True)] Answered [])

(defclass [(dataclass :frozen True)] SessionClosed []
  (#^ bool was-running))

(defclass [(dataclass :frozen True)] Idle [])
(defclass [(dataclass :frozen True)] TurnRunning [] (#^ ClaudeTurn turn))
(defclass [(dataclass :frozen True)] Closed [])
(setv SessionState (| Idle TurnRunning Closed))

(defclass [(dataclass :frozen True)] TranscriptPresent []
  "last-activity = transcript の最終更新(epoch 秒)。"
  (#^ float last-activity))
(defclass [(dataclass :frozen True)] TranscriptAbsent [])
(setv TranscriptState (| TranscriptPresent TranscriptAbsent))

(defclass [(dataclass :frozen True)] SessionStatus []
  (#^ (| Idle TurnRunning Closed) state)
  (#^ (| TranscriptPresent TranscriptAbsent) transcript))

(defclass [(dataclass :frozen True)] SessionExported []
  "transcript の写し: jsonl の本文ちょうど(空白だけでない)。Rebuilt(jsonl-text) に渡すと同じ transcript が持ち込める。末尾だけを
   頼んだ取り出し(ClaudeExportSession の tail-bytes)の答えは、末尾の行の列(行の頭から始まる)で、持ち込みには使えない。
   空白だけかの判定は先頭から空白でない字を探すだけにする(100 MB 級の写しを strip で写し直さない — card ki-a264a73efcce)。"
  (#^ str jsonl-text)
  (defn __post_init__ [self]
    (when (not (and (isinstance self.jsonl-text str) self.jsonl-text (not (.isspace self.jsonl-text))))
      (raise (ValueError "SessionExported.jsonl_text は空白だけでない文字列")))))


;; --- 失敗の戻り値 ---------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] SessionNotFound []
  "続きを頼んだ会話の transcript が手元に無く、持ち込み(carry)も無い。ClaudeExportSession では transcript が無い・空。"
  (#^ str session-id))

(defclass [(dataclass :frozen True)] SessionIdInUse []
  "FreshSession の id が既に在る(transcript が在るか、この handler が知っている)。"
  (#^ str session-id))

(defclass [(dataclass :frozen True)] TurnInFlight []
  "同じ会話に走っている手番がある。"
  (#^ ClaudeTurn turn))

(defclass [(dataclass :frozen True)] CarryRefused []
  (#^ str detail))

(defclass [(dataclass :frozen True)] LaunchFailed []
  "process が init の行を出す前に降りた(か、起動の期限を過ぎた)。"
  (setv #^ (| int None) exit-code None)
  (setv #^ str stderr-tail ""))

(defclass [(dataclass :frozen True)] AttachmentRefused []
  (#^ str mime))

(defclass [(dataclass :frozen True)] NoTurnInFlight []
  (#^ str session-id))

(defclass [(dataclass :frozen True)] UnknownTurn []
  (#^ ClaudeTurn turn))

(defclass [(dataclass :frozen True)] NoSuchRequest []
  (#^ str request-id))

(defclass [(dataclass :frozen True)] ProcessStillAlive []
  "降ろす手順(EOF → SIGTERM → SIGKILL)を全部踏んでも降りない。所有は保ち、次の呼びが降ろし直す。"
  (#^ str detail))

(setv StartTurnOutcome (| TurnStarted SessionNotFound SessionIdInUse TurnInFlight CarryRefused LaunchFailed
                          AttachmentRefused))
(val ExportSessionOutcome (| SessionExported SessionNotFound))


;; --- effect ----------------------------------------------------------------------------------
;; 基底の EffectBase の型引数が答えの型(使い手の `<-` が答えを型つきで読む・#4257)。答えの型を実行時に引くので、effect は戻り値の型の後に置く。

(defclass [(dataclass :frozen True)] ClaudeStartTurn [(get EffectBase StartTurnOutcome)]
  "手番を始める。死んだ(降りた)process の起こし直しの判断はこの effect の handler の中の 1 か所だけ(設計 7 節)。
   答え = TurnStarted | SessionNotFound | SessionIdInUse | TurnInFlight | CarryRefused | LaunchFailed | AttachmentRefused。
   tap = 行の写し手(None = 写さない — card ki-31b41eac5071): この手番(と同じ process で続く生き残った入力の手番)の記録に
   足した行(ClaudeStreamLine)を 1 つずつ、行を足した直後に呼ぶ関数。本番の handler は stdout の読み手の thread から会話の lock の外で
   呼び、fake は行を出した所で呼ぶ — 上の層が ClaudeReadTurnEvents で頁を受ける遅れ(秒〜分)に縛られずに、行を読んだ時点で自分の
   記録を書くため。写し手は塞がず、例外を出さない(出した例外は handler が捨てる — 読み手の thread を止めない)。"
  (#^ SessionOrigin origin)
  (#^ ClaudeSessionSpec spec)
  (#^ TurnInput input)
  (setv #^ (| (get Callable #([ClaudeStreamLine] None)) None) tap None)
  (defn __post_init__ [self]
    (when (not (isinstance self.origin #(FreshSession ResumeSession ForkSession)))
      (raise (TypeError (.format "ClaudeStartTurn.origin は FreshSession / ResumeSession / ForkSession: {!r}" self.origin))))
    (when (not (or (is self.tap None) (callable self.tap)))
      (raise (TypeError (.format "ClaudeStartTurn.tap は呼べる物か None: {!r}" self.tap))))))

(defclass [(dataclass :frozen True)] ClaudeInjectInput [(get EffectBase (| InputQueued NoTurnInFlight AttachmentRefused))]
  "走っている手番に入力を足す。答え = InputQueued | NoTurnInFlight | AttachmentRefused(添付の種類の断り — 手番の始まりと同じ検め)。
   運命は InputFate の行で届く。"
  (#^ ClaudeTurn turn)
  (#^ TurnInput input))

(defclass [(dataclass :frozen True)] ClaudeInterruptTurn [(get EffectBase (| InterruptRequested InputAlreadyRead NoTurnInFlight))]
  "手番を止める。答え = InterruptRequested | InputAlreadyRead | NoTurnInFlight(終わりは ClaudeReadTurnEvents の Interrupted で届く)。
   for-input = この止めが早く読ませたい足した入力の ref(None = 名指さない止め)。名指した入力を CLI がもう読んでいれば止めずに
   InputAlreadyRead で答える(読んだかを知るのは CLI と同じ process のこの層だけ — CLI が入力の運命を名乗らない版では名指さない止めと同じ)。"
  (#^ ClaudeTurn turn)
  (setv #^ (| str None) for-input None))

(defclass [(dataclass :frozen True)] ClaudeReadTurnEvents [(get EffectBase (| TurnEventPage UnknownTurn))]
  "手番の出来事を読む: after-seq より後の行を、新しい行か終わりが来るか wait-up-to 秒が過ぎるまで待って返す。
   答え = TurnEventPage | UnknownTurn。"
  (#^ ClaudeTurn turn)
  (#^ int after-seq)
  (#^ float wait-up-to))

(defclass [(dataclass :frozen True)] ClaudeAnswerPermission [(get EffectBase (| Answered NoSuchRequest))]
  "許可の問いに答える。答え = Answered | NoSuchRequest。"
  (#^ ClaudeTurn turn)
  (#^ str request-id)
  (#^ (| Allow Deny) answer)
  (defn __post_init__ [self]
    (when (not (isinstance self.answer #(Allow Deny)))
      (raise (TypeError "ClaudeAnswerPermission.answer は Allow / Deny")))))

(defclass [(dataclass :frozen True)] ClaudeCloseSession [(get EffectBase (| SessionClosed ProcessStillAlive))]
  "会話を閉じる(冪等)。走っている手番は Interrupted で終わる。答え = SessionClosed | ProcessStillAlive。"
  (#^ str session-id)
  (#^ str reason))

(defclass [(dataclass :frozen True)] ClaudeSessionStatus [(get EffectBase SessionStatus)]
  "会話の状態を読む(pid は返さない)。答え = SessionStatus。"
  (#^ ClaudeHome home)
  (#^ str cwd)
  (#^ str session-id))

(defclass [(dataclass :frozen True)] ClaudeExportSession [(get EffectBase ExportSessionOutcome)]
  "会話の transcript の写しを家から取り出す(家の外に預けて、別の家へ ResumeSession の carry = Rebuilt で持ち込むため)。
   cwd は ClaudeSessionStatus と同じく実体の path(realpath)へ正規化して置き場を決める。写すのは transcript の jsonl 1 つだけ
   (subagent の記録・memory の dir は写さない)。session-id は会話の id の綴り(UUID — 置き場の外の path を名指せない)。
   tail-bytes = 末尾の何 byte までを読むか(None = 全部)。末尾だけの答えは、切り口の後の最初の行の頭から始まる行の列で、持ち込み
   には使えない — 写しの中の最近の行だけを読む使い手(道具の呼びで名指した作業場を新しい方から拾う等)が、100 MB 級の transcript を
   手番ごとに丸ごと読まないため(card ki-a264a73efcce)。
   from-byte = 頭から何 byte 目より後ろだけを読むか(None = 全部)。答えは transcript の from-byte byte 目から末尾まで(from-byte は字の頭 —
   前に取り出した写しの byte の長さの内の字の切れ目)で、持ち込みには使えない — 前に取り出した写しを持つ使い手が、伸びた分だけを
   読むため(手番の終わりの写しの預け・card ki-3b521394a063)。transcript が from-byte より短い・from-byte が字の途中なら SessionNotFound。
   tail-bytes と from-byte は一緒に名指さない。
   答え = SessionExported | SessionNotFound(transcript が無い・空)。"
  (#^ ClaudeHome home)
  (#^ str cwd)
  (#^ str session-id)
  (setv #^ (| int None) tail-bytes None)
  (setv #^ (| int None) from-byte None)
  (defn __post_init__ [self]
    (checked-session-id self.session-id "ClaudeExportSession.session_id")
    (when (and (is-not self.tail-bytes None)
               (or (isinstance self.tail-bytes bool) (not (isinstance self.tail-bytes int)) (< self.tail-bytes 1)))
      (raise (ValueError (.format "ClaudeExportSession.tail_bytes は 1 以上の整数か None: {!r}" self.tail-bytes))))
    (when (and (is-not self.from-byte None)
               (or (isinstance self.from-byte bool) (not (isinstance self.from-byte int)) (< self.from-byte 0)))
      (raise (ValueError (.format "ClaudeExportSession.from_byte は 0 以上の整数か None: {!r}" self.from-byte))))
    (when (and (is-not self.tail-bytes None) (is-not self.from-byte None))
      (raise (ValueError "ClaudeExportSession の tail_bytes と from_byte は一緒に名指さない")))))


;; --- 入力の前に会話の process を事前起動して待たせる ------------------------------------------------------------

(defrecord SessionWarmed
  "会話の process が入力を書かれずに待っている(今起動したか、同じ起動条件の process が既に待っていた — どちらかは見せない)。
   session-id = 事前起動した会話の id(origin の id そのまま)。"
  (#^ str session-id))

(val WarmSessionOutcome (| SessionWarmed SessionNotFound SessionIdInUse TurnInFlight CarryRefused LaunchFailed))

(defeffect ClaudeWarmSession
  "会話の process を最初の入力の前に起動し、入力を書かずに待たせる — 起動してから入力を受けられるまでの秒を、入力が来る前に済ませる
   ため。origin・spec は ClaudeStartTurn と同じ意味。後に来た同じ会話の ClaudeStartTurn は、起動条件のキー(argv.hy の launch-key)が
   同じならこの process に入力を書き(起動しない)、違えば停止して(理由 LAUNCH-CHANGED)再起動する。新しい会話(FreshSession)を事前
   起動した時は、最初のターンも同じ id の FreshSession で頼む(CLI は入力の前に会話の記録を作らないので、続き ResumeSession には
   ならない)。最初の入力の前に CLI が出してよい行は SessionStart の hook の開始と応答だけで、ほかの行を出した process はターンの外の
   出力として停止する(dialogue.hy)。事前起動した process は ClaudeCloseSession でターンなしに停止できる。同じ起動条件の process が
   既に待っていれば起動しない。枝分かれ(ForkSession)は受けない — 枝の id は入力の後の init で CLI が決めるので、次のターンがその
   process を指定できない。
   結果 = SessionWarmed | SessionNotFound | SessionIdInUse | TurnInFlight | CarryRefused | LaunchFailed(拒否は ClaudeStartTurn と同じ)。"
  {:fields [(: origin (| FreshSession ResumeSession)) (: spec ClaudeSessionSpec)]
   :pre [(: origin (| FreshSession ResumeSession)) (: spec ClaudeSessionSpec)]
   :answer WarmSessionOutcome
   :tags {:context "claude-code" :role "intent"}})


;; --- 生かす本数の上限を越える起動の知らせ(handler が出し、上の層のホストが答える)-------------------------------------------

(defeffect ClaudeLiveLimitExceeded
  "handler が会話の process を起動する時に、生きた process の本数がホストの上限(ClaudeCodeHost の live-limit・fake の
   FakeClaudeWorld の live-limit)を越えるので、ホストへ知らせる(#4072 の E1b)。handler は上限のために process を
   止めず、起動を待たせず、失敗にもしない — 本数を数えて止める CLI を選ぶのは上の層のホストの 1 か所で、この知らせはホストの数えと
   handler の数えが食い違った事を見せる。前は handler が手番を走らせていない一番古い process を黙って止めた(本番 2026-10-08 09:45 —
   ホストの知らない CLI が止まった)。session-id = 起動する会話・live = 起動の後の生きた process の本数(降りる途中の process は
   数えない)・limit = 上限・warm = 入力の前の事前起動(ClaudeWarmSession)か。答え = None。"
  {:fields [(: session-id str) (: live int) (: limit int) (: warm bool)]
   :pre [(: session-id str) (: live int) (: limit int) (: warm bool)]
   :answer None
   :tags {:context "claude-code" :role "intent"}})


;; --- 走っている会話の process の資格を替える(card ki-4ffdafa021e5)-------------------------------------------

(defrecord CredentialReplaced
  "会話の生きた process の資格の家の .credentials.json を新しい資格へ替えた(次の API の呼びから新しい token を使う — 手番の途中でも)。
   生きた process を止める刻も新しい資格の期限から決め直した。session-id = 名指した会話。"
  (#^ str session-id))

(defrecord NoSuchSession
  "名指した会話をこの handler が知らない(起こしていない・起動に失敗して忘れた)。替える process が無い。"
  (#^ str session-id))

(defrecord SessionProcessDown
  "名指した会話を知っているが、生きた process が無い(降りた・降りる途中・まだ起こしていない)。次の手番は宣言(ClaudeSessionSpec の
   credential)の資格で起こすので、替える物は無い。"
  (#^ str session-id))

(defrecord HomeCredentialSession
  "名指した会話の生きた process は家の設定 dir の資格で起こした(宣言に credential が無かった)。借りた資格の家が無いので替えない —
   借りた資格で走らせたいなら、credential を持つ宣言の手番で起こし直す(起動条件のキーが違うので handler が起こし直す)。"
  (#^ str session-id))

(val ReplaceCredentialOutcome (| CredentialReplaced NoSuchSession SessionProcessDown HomeCredentialSession))

(defeffect ClaudeReplaceSessionCredential
  "会話(session-id)の生きた process の資格を、別の借りた資格(credential — 新しい access token と期限)へ一息に替える。預かり所が口座の
   資格を更新すると前の access token が上流で死ぬので、更新した token を走っている CLI へ配るため(card ki-4ffdafa021e5)。
   handler は process だけの設定 dir の .credentials.json を、同じ dir の別名に書いて rename で替える(CLI は手番の間でも途中でも次の
   API の呼びから新しい token を使い、401 を受けると file を読み直す — CLI 2.1.292 の実測)。生きた process を止める刻(期限 − 床)も
   新しい期限から決め直す。手番を走らせているかは問わない。次の手番の宣言の資格は呼び手が持つ(この effect は宣言を書き換えない —
   次の手番で使い回す process には、その手番の宣言の資格を同じ形で書く)。
   答え = CredentialReplaced | NoSuchSession | SessionProcessDown | HomeCredentialSession(失敗も値で返す — 例外にしない)。"
  {:fields [(: session-id str) (: credential LentCredential)]
   :pre [(: session-id str) (: credential LentCredential)]
   :answer ReplaceCredentialOutcome
   :tags {:context "claude-code" :role "intent"}})


;; --- 1 回きりの print(構造化の答えを 1 つ受けて降りる)--------------------------------------------------------

(defclass [(dataclass :frozen True :kw-only True)] PrintAnswered []
  "1 回きりの print の CLI が構造化の答えを返した。structured-output = CLI の structured_output(--json-schema に合う答え — 深く凍らせた
   JSON の値・CLI の値のまま)/ usage = 消費の token(CLI が名乗った数だけ — lines.Usage)/ cost-usd = total_cost_usd(CLI が API の
   価格で換算した額・名乗らなければ None)/ models = 答えた model の名(modelUsage の鍵の順)/ elapsed-ms = handler が測った、CLI の
   process を起こしてから降りるまでのミリ秒 / api-ms = duration_api_ms(API の呼びの合計・名乗らなければ None)。"
  (#^ FrozenJson structured-output)
  (#^ Usage usage)
  (#^ (| float None) cost-usd)
  (#^ (get tuple #(str ...)) models)
  (#^ int elapsed-ms)
  (#^ (| int None) api-ms)
  (defn __post_init__ [self]
    (object.__setattr__ self "structured_output" (freeze-json self.structured-output))))

(defrecord PrintFailed
  "1 回きりの print の CLI が構造化の答えを返さずに降りた・起こせなかった。exit-code = process の終了 code(起こせなかった時は None)/
   api-status = CLI が名乗った API の誤りの HTTP status(api_error_status — 401 = 資格が無効・429 = 利用枠など。API の誤りでなければ
   None)/ detail = 何が起きたかの 1 行(CLI の result の文・起こせなかった訳・答えの行が無かった事)/ stderr-tail = stderr の末尾
   (lines.STDERR-TAIL-CHARS 字まで)。"
  (#^ (| int None) exit-code)
  (#^ (| int None) api-status)
  (#^ str detail)
  (#^ str stderr-tail))

(defrecord PrintTimedOut
  "1 回きりの print の CLI が待ちの上限(seconds 秒)の内に降りなかったので、handler が止めた(process の組に SIGKILL)。
   stderr-tail = 止めるまでの stderr の末尾。"
  (#^ float seconds)
  (#^ str stderr-tail))

(defrecord PrintLimited
  "1 回きりの print の口座の利用枠が尽きて、CLI が答えずに降りた(card ki-eb4f8e1db64d — 誤りの PrintFailed と分ける: 上の層が「枠が
   いつ戻るか」を言い、資格や CLI の不具合と取り違えないため)。limit = 尽きた事実(lines.AccountLimitHit — どの枠・戻る刻・CLI の限度の
   文。会話の手番の終わりと同じ型)/ exit-code = process の終了 code / stderr-tail = stderr の末尾。どの口座かは CLI の行に無い — 資格を
   渡した呼び手が知っている。"
  (#^ AccountLimitHit limit)
  (#^ (| int None) exit-code)
  (#^ str stderr-tail))

(val PrintOutcome (| PrintAnswered PrintFailed PrintLimited PrintTimedOut))

(defn #^ bool is-json-object-text [#^ object text]
  "--json-schema に渡せる文か(JSON の object の文ちょうど — CLI は読めない Schema で答えを出さずに降りる)。"
  (and (isinstance text str)
       (try
         (isinstance (json.loads text) dict)
         (except [ValueError] False))))

(defeffect ClaudePrintOnce
  "CLI の print mode を 1 回だけ走らせ、答えの JSON Schema に合う構造化の答えを 1 つ受ける(会話の id も記録も残さない)。
   home = 家(設定 dir と子の env ちょうど)/ model = --model / system-prompt = --system-prompt(CLI の既定の system の文を替える)/
   json-schema = --json-schema(答えの Schema の JSON の文 — object)/ input = 標準入力に渡す本文(argv に載せない — 文字列 = 素の文字の
   まま書く・PrintContent = --input-format stream-json の user の 1 行で書く。画像を含む本文だけが後者)/ timeout-seconds =
   待ちの上限(CLI の起動と終わりの秒を含む — 越えたら止めて PrintTimedOut)/ credential = 借りた資格(LentCredential — その process
   だけの設定 dir の .credentials.json に置き、降りたら dir ごと消す。None = 家の設定 dir の資格のまま)/ thinking-tokens = 子の env の
   MAX_THINKING_TOKENS(0 = 考えない。None = CLI の既定)/ effort = --effort(None = CLI の既定)。
   CLI は道具なし・設定の源なし・家の MCP なし・記録なしで起こす(argv.hy の PRINT-ONCE-FLAGS)。
   答え = PrintAnswered | PrintFailed | PrintLimited | PrintTimedOut(失敗も値で返す — 例外にしない)。"
  {:fields [(: home ClaudeHome) (: model str) (: system-prompt str) (: json-schema str) (: input (| str PrintContent))
            (: timeout-seconds float) (: credential (| LentCredential None) None) (: thinking-tokens (| int None) None) (: effort (| str None) None)]
   :pre [(: home ClaudeHome) (: model str) (bool model) (: system-prompt str) (is-json-object-text json-schema)
         (: input (| str PrintContent)) (: timeout-seconds float) (> timeout-seconds 0)
         (: credential (| LentCredential None))
         (or (is thinking-tokens None) (and (isinstance thinking-tokens int) (not (isinstance thinking-tokens bool))
                                            (>= thinking-tokens 0)))
         (or (is effort None) (and (isinstance effort str) (bool effort)))]
   :answer PrintOutcome
   :tags {:context "claude-code" :role "intent"}})
