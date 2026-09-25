;;; 「手番を始める」の判断 — 純関数 1 つ(設計 7 節の表)。死んだ(降りた)process の起こし直しを決めるのはここだけ。
;;;
;;; 入力 = 登録簿の観測(SessionView)・会話の始まり方(origin)・transcript が在るか(持ち込みの後)。
;;; 答え = Refuse(型で返す失敗)か Launch(起こす — 降りる途中の process を待つか・冷えた続きの前の命令を走らせるか)。
;;; 上の層に見えるのは TurnStarted か失敗の型だけで、起こし直したことは見えない。
(import dataclasses [dataclass])
(import doeff_claude_code.values [ClaudeTurn FreshSession ResumeSession ForkSession])
(import doeff_claude_code.effects [SessionIdInUse SessionNotFound TurnInFlight])


(defclass [(dataclass :frozen True)] SessionView []
  "登録簿の観測: running-turn = 走っている手番(無ければ None)/ retiring = 前の手番の process が降りる途中 /
   known = この handler がこの会話を知っている(閉じた会話を含む)。"
  (setv #^ bool known False)
  (setv #^ (| ClaudeTurn None) running-turn None)
  (setv #^ bool retiring False))

(defclass [(dataclass :frozen True)] Refuse []
  (#^ object outcome))

(defclass [(dataclass :frozen True)] Launch []
  "起こす。wait-retire = 同じ会話の降りる途中の process を待ってから(1 つの会話に生きた process は 1 つ)/
   cold-resume = 起こす前に冷えた続きの前の命令を走らせる。"
  (setv #^ bool wait-retire False)
  (setv #^ bool cold-resume False))

(setv StartDecision (| Refuse Launch))


(defn start-decision [origin #^ SessionView view #^ bool transcript-present #^ bool has-cold-resume-prompt]
  "7 節の表。ForkSession は親の transcript だけを見る(親の手番が走っていても枝は別の会話)。"
  (cond
    (isinstance origin FreshSession)
      (if (or view.known transcript-present)
          (Refuse (SessionIdInUse origin.session-id))
          (Launch))
    (isinstance origin ResumeSession)
      (cond
        (is-not view.running-turn None) (Refuse (TurnInFlight view.running-turn))
        (not transcript-present) (Refuse (SessionNotFound origin.session-id))
        True (Launch :wait-retire view.retiring :cold-resume has-cold-resume-prompt))
    (isinstance origin ForkSession)
      (if transcript-present
          (Launch)
          (Refuse (SessionNotFound origin.parent-session-id)))
    True (raise (TypeError (.format "会話の始まり方が閉語彙の外: {!r}" origin)))))
