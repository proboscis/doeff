;;; doeff-claude-code の公開 effect 9 つと、その戻り値の型(設計 4.3)。
;;;
;;; ClaudeStartTurn / ClaudeInjectInput / ClaudeInterruptTurn / ClaudeReadTurnEvents / ClaudeAnswerPermission /
;;; ClaudeCloseSession / ClaudeSessionStatus / ClaudeExportSession / ClaudeWarmSession。
;;;
;;; 単位は「claude の会話(session)と、その上の手番」。process の単位の操作(起こす・stdin に書く・信号・降ろす・pid の生存)は
;;; 公開しない — handler の内側の語彙(ClaudeWarmSession も「会話を入力の前に事前起動する」操作で、process を指定しない)。失敗は
;;; 例外ではなく戻り値の型で返す(成功の型と失敗の型の判別可能な union)。handler の実装の誤り(I/O の予期しない例外)だけが例外として
;;; 上がる。
(require doeff-hy.macros [defeffect val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import doeff_claude_code.values [ClaudeSessionSpec ClaudeHome ClaudeTurn TurnInput Allow Deny checked-session-id
                                  FreshSession ResumeSession ForkSession])
(import doeff_claude_code.lines [ClaudeStreamLine Completed Failed Interrupted BackendLost])


;; --- effect ----------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] ClaudeStartTurn [EffectBase]
  "手番を始める。死んだ(降りた)process の起こし直しの判断はこの effect の handler の中の 1 か所だけ(設計 7 節)。
   答え = TurnStarted | SessionNotFound | SessionIdInUse | TurnInFlight | CarryRefused | LaunchFailed | AttachmentRefused。"
  (#^ (| FreshSession ResumeSession ForkSession) origin)
  (#^ ClaudeSessionSpec spec)
  (#^ TurnInput input)
  (defn __post_init__ [self]
    (when (not (isinstance self.origin #(FreshSession ResumeSession ForkSession)))
      (raise (TypeError (.format "ClaudeStartTurn.origin は FreshSession / ResumeSession / ForkSession: {!r}" self.origin))))))

(defclass [(dataclass :frozen True)] ClaudeInjectInput [EffectBase]
  "走っている手番に入力を足す。答え = InputQueued | NoTurnInFlight(運命は InputFate の行で届く)。"
  (#^ ClaudeTurn turn)
  (#^ TurnInput input))

(defclass [(dataclass :frozen True)] ClaudeInterruptTurn [EffectBase]
  "手番を止める。答え = InterruptRequested | NoTurnInFlight(終わりは ClaudeReadTurnEvents の Interrupted で届く)。"
  (#^ ClaudeTurn turn))

(defclass [(dataclass :frozen True)] ClaudeReadTurnEvents [EffectBase]
  "手番の出来事を読む: after-seq より後の行を、新しい行か終わりが来るか wait-up-to 秒が過ぎるまで待って返す。
   答え = TurnEventPage | UnknownTurn。"
  (#^ ClaudeTurn turn)
  (#^ int after-seq)
  (#^ float wait-up-to))

(defclass [(dataclass :frozen True)] ClaudeAnswerPermission [EffectBase]
  "許可の問いに答える。答え = Answered | NoSuchRequest。"
  (#^ ClaudeTurn turn)
  (#^ str request-id)
  (#^ (| Allow Deny) answer)
  (defn __post_init__ [self]
    (when (not (isinstance self.answer #(Allow Deny)))
      (raise (TypeError "ClaudeAnswerPermission.answer は Allow / Deny")))))

(defclass [(dataclass :frozen True)] ClaudeCloseSession [EffectBase]
  "会話を閉じる(冪等)。走っている手番は Interrupted で終わる。答え = SessionClosed | ProcessStillAlive。"
  (#^ str session-id)
  (#^ str reason))

(defclass [(dataclass :frozen True)] ClaudeSessionStatus [EffectBase]
  "会話の状態を読む(pid は返さない)。答え = SessionStatus。"
  (#^ ClaudeHome home)
  (#^ str cwd)
  (#^ str session-id))

(defclass [(dataclass :frozen True)] ClaudeExportSession [EffectBase]
  "会話の transcript の写しを家から取り出す(家の外に預けて、別の家へ ResumeSession の carry = Rebuilt で持ち込むため)。
   cwd は ClaudeSessionStatus と同じく実体の path(realpath)へ正規化して置き場を決める。写すのは transcript の jsonl 1 つだけ
   (subagent の記録・memory の dir は写さない)。session-id は会話の id の綴り(UUID — 置き場の外の path を名指せない)。
   答え = SessionExported | SessionNotFound(transcript が無い・空)。"
  (#^ ClaudeHome home)
  (#^ str cwd)
  (#^ str session-id)
  (defn __post_init__ [self]
    (checked-session-id self.session-id "ClaudeExportSession.session_id")))


;; --- 成功の戻り値 ---------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] TurnStarted []
  "手番が始まった。session-id = 手番の会話の id(ForkSession なら CLI が決めた新しい id)。"
  (#^ ClaudeTurn turn)
  (#^ str session-id))

(defclass [(dataclass :frozen True)] InputQueued []
  (#^ str ref))

(defclass [(dataclass :frozen True)] InterruptRequested [])

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
  "transcript の写し: jsonl の本文ちょうど(空でない)。Rebuilt(jsonl-text) に渡すと同じ transcript が持ち込める。"
  (#^ str jsonl-text)
  (defn __post_init__ [self]
    (when (not (and (isinstance self.jsonl-text str) (.strip self.jsonl-text)))
      (raise (ValueError "SessionExported.jsonl_text は空でない文字列")))))


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
