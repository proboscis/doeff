;;; doeff-claude-code の公開 effect 7 つと、その戻り値の型(設計 4.3)。
;;;
;;; 単位は「claude の会話(session)と、その上の手番」。process の単位の操作(起こす・stdin に書く・信号・降ろす・pid の生存)は
;;; 公開しない — handler の内側の語彙。失敗は例外ではなく戻り値の型で返す(成功の型と失敗の型の判別可能な union)。
;;; handler の実装の誤り(I/O の予期しない例外)だけが例外として上がる。
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import doeff_claude_code.values [ClaudeSessionSpec ClaudeHome ClaudeTurn TurnInput Allow Deny
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


;; --- 失敗の戻り値 ---------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] SessionNotFound []
  "続きを頼んだ会話の transcript が手元に無く、持ち込み(carry)も無い。"
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
