;;; 「手番を始める」の判断 — 純関数 1 つ(設計 7 節の表)。process を起こすか・生きて待つ process を使い回すかを決めるのはここだけ。
;;;
;;; 入力 = 登録簿の観測(SessionView)・会話の始まり方(origin)・transcript が在るか(持ち込みの後)・冷えた続きの前の命令が在るか・
;;; この手番の起こした時の条件の鍵(argv.hy の launch-key)。
;;; 答え = Refuse(型で返す失敗)か Reuse(生きて手番を待つ process に入力を書く)か Launch(起こす — 降りる途中の process を待つか・
;;; 生きて待つ process を先に降ろすか・冷えた続きの前の命令を走らせるか)。
;;; 上の層に見えるのは TurnStarted か失敗の型だけで、起こし直したか使い回したかは見えない。
;;; 入力の前の事前起動(ClaudeWarmSession)も同じ判断を使う — Reuse は「同じ起動条件の process が既に待っているので起動しない」と読む。
(import dataclasses [dataclass])
(import doeff_claude_code.values [ClaudeTurn FreshSession ResumeSession ForkSession])
(import doeff_claude_code.effects [SessionIdInUse SessionNotFound TurnInFlight])


(defclass [(dataclass :frozen True)] SessionView []
  "登録簿の観測: running-turn = 走っている手番(無ければ None)/ retiring = 前の process が降りる途中 /
   idle-key = 生きて手番を待つ process の起こした時の条件の鍵(無ければ None)/
   known = この handler がこの会話を知っている(閉じた会話を含む)/
   warmed-fresh = 新しい会話(FreshSession)として入力なしで事前起動し(ClaudeWarmSession)、まだターンを 1 度も始めていない — CLI は
   入力の前に会話の記録を作らないので、この会話の最初のターンは同じ id の FreshSession のまま(使用済みの id として拒否しない)。"
  (setv #^ bool known False)
  (setv #^ (| ClaudeTurn None) running-turn None)
  (setv #^ bool retiring False)
  (setv #^ (| str None) idle-key None)
  (setv #^ bool warmed-fresh False))

(defclass [(dataclass :frozen True)] Refuse []
  (#^ object outcome))

(defclass [(dataclass :frozen True)] Reuse []
  "生きて手番を待つ process に、この手番の入力を書く(起こさない・init を待たない — #3672)。")

(defclass [(dataclass :frozen True)] Launch []
  "起こす。wait-retire = 同じ会話の降りる途中の process を待ってから(1 つの会話に生きた process は 1 つ)/
   retire-idle = 生きて待つ process を、起こした時の条件が違うので先に降ろす(wait-retire と組む)/
   cold-resume = 起こす前に冷えた続きの前の命令を走らせる。"
  (setv #^ bool wait-retire False)
  (setv #^ bool retire-idle False)
  (setv #^ bool cold-resume False))

(setv StartDecision (| Refuse Reuse Launch))


(defn #^ (| float None) retire-time [#^ (| float None) credential-expires-at #^ float floor-seconds]
  "生きた process を止める刻(資格の期限 − 床・epoch 秒)を、起こした・使い回した手番の spec から決めるため(#3672 の D2)。期限を
   知らなければ None(床で止めない)。"
  (if (is credential-expires-at None) None (- credential-expires-at floor-seconds)))

(defn #^ bool credential-due [#^ (| float None) retire-after #^ float now]
  "その process を資格の床で止める時か(止める刻を過ぎたか)を判じるため — 手番の境と、手番を走らせていない process の見回りの
   どちらもこの 1 点で判じる(D2)。"
  (and (is-not retire-after None) (>= now retire-after)))


(defn start-decision [origin #^ SessionView view #^ bool transcript-present #^ bool has-cold-resume-prompt #^ str wanted-key]
  "7 節の表。ForkSession は親の transcript だけを見る(親の手番が走っていても枝は別の会話・親の生きた process は使わない)。
   続き(ResumeSession)は、生きて待っている process のキーが wanted-key と同じ時だけ使い回す。違えば停止してから起動する。
   新しい会話(FreshSession)も、入力なしで事前起動してまだターンの無い会話(view.warmed-fresh)なら同じ規則で使い回す・再起動する。
   ターンを始める時(ClaudeStartTurn)と入力の前に事前起動する時(ClaudeWarmSession)の両方がこの 1 か所で決める(事前起動の Reuse =
   起動しない)。"
  (cond
    (isinstance origin FreshSession)
      (cond
        view.warmed-fresh (if (= view.idle-key wanted-key)
                              (Reuse)
                              (Launch :wait-retire (or view.retiring (is-not view.idle-key None))
                                      :retire-idle (is-not view.idle-key None)))
        (or view.known transcript-present) (Refuse (SessionIdInUse origin.session-id))
        True (Launch))
    (isinstance origin ResumeSession)
      (cond
        (is-not view.running-turn None) (Refuse (TurnInFlight view.running-turn))
        (not transcript-present) (Refuse (SessionNotFound origin.session-id))
        (= view.idle-key wanted-key) (Reuse)
        True (Launch :wait-retire (or view.retiring (is-not view.idle-key None))
                     :retire-idle (is-not view.idle-key None)
                     :cold-resume has-cold-resume-prompt))
    (isinstance origin ForkSession)
      (if transcript-present
          (Launch)
          (Refuse (SessionNotFound origin.parent-session-id)))
    True (raise (TypeError (.format "会話の始まり方が閉語彙の外: {!r}" origin)))))
