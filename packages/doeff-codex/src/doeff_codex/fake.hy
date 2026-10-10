;;; fake の handler — 公開 effect(effects.hy)に、筋書きの答え(FakeReply)で memory の上で答える。
;;;
;;; 本番の handler と同じ不変条件を守る(tests/test_scenarios.hy が両方に当てる): 1 つの会話に走っているターンは多くとも 1 つ・
;;; CodexStartTurn 1 回に終わりはちょうど 1 つ・出来事の seq は会話の中で単調に増える・同じ会話・同じ宣言の続きは process を起こし
;;; 直さない(数は CodexLaunchCount)。出来事は本番と同じ記録の型(lines.hy)で出す。待ちは doeff-time の時計(仮想の時計の下では一瞬)。
(require doeff-hy.macros [defhandler defk <- val var])
(val MODULE-TAGS {:context "codex" :role "process"})
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import doeff_time [Delay])
(import doeff_codex.values [CodexSessionSpec CodexTurn CodexEvent FreshThread ResumeThread])
(import doeff_codex.lines [TurnStarted :as TurnStartedLine TextDelta AgentMessageDone ItemStarted TurnEnded TurnStatus])
(import doeff_codex.effects [CodexStartTurn CodexInterruptTurn CodexReadTurnEvents CodexAnswerRequest CodexCloseSession
                             CodexLaunchCount BackendLost TurnStarted InterruptRequested TurnEventPage SessionClosed
                             ThreadUnknown TurnInFlight NoTurnInFlight UnknownTurn NoSuchRequest])


(defrecord FakeReply
  "筋書きの答え 1 つ: pieces = 答えの文字の途中の欠片(順に TextDelta になり、連ねると答えの全文)/ hold = 真なら終わりを出さずに
   止め(CodexInterruptTurn)を待つ(途中で止める筋書きのため)。"
  {:tags {:context "codex" :role "type"}}
  (#^ (get tuple #(str ...)) pieces)
  (setv #^ bool hold False))


(defclass FakeTurn []
  "fake の 1 つのターン: events = 出した出来事 / end = 終わり(まだなら None)。"
  (defn __init__ [self]
    (setv #^ (get tuple #(CodexEvent ...)) self.events #())
    (setv self.end None)))


(defclass FakeThread []
  "fake の 1 つの会話: spec = 今の process を起こした宣言 / launches = 起こした process の数 / alive = process が生きているか /
   current = 今のターンの id / next-seq = 出来事の番号。"
  (defn __init__ [self #^ CodexSessionSpec spec]
    (setv self.spec spec
          self.launches 1
          self.alive True
          self.current None
          self.next-seq 0)
    (setv #^ (get dict #(str FakeTurn)) self.turns {}))

  (defn add [self #^ FakeTurn turn record]
    "記録を seq を振ってターンの出来事に積むため(終わりの記録なら終わりにもする)。"
    (setv self.next-seq (+ self.next-seq 1))
    (setv turn.events (+ turn.events #((CodexEvent :seq self.next-seq :record record))))
    (when (isinstance record TurnEnded) (setv turn.end record)))

  (defn running [self]
    "走っているターン(無ければ None)。"
    (setv turn (if (is self.current None) None (.get self.turns self.current)))
    (if (and (is-not turn None) (is turn.end None)) turn None)))


(defclass FakeCodexWorld []
  "fake の世界: respond = 入力の文字 → FakeReply(筋書き)。thread とターンの id は世界の数えで振る(fake-thread-N・fake-turn-N)。"
  (defn __init__ [self #^ Callable respond]
    (setv self.respond respond
          self.made 0)
    (setv #^ (get dict #(str FakeThread)) self.threads {}))

  (defn #^ str fresh-id [self #^ str kind]
    "thread・ターン・item の id を振るため。"
    (setv self.made (+ self.made 1))
    (.format "fake-{}-{}" kind self.made)))


(defk fake-start [#^ FakeCodexWorld world #^ CodexStartTurn asked]
  {:pre [(: world FakeCodexWorld) (: asked CodexStartTurn)] :post [(: % (| TurnStarted ThreadUnknown TurnInFlight))]
   :tags {:context "codex" :role "process"}}
  "ターンを始めるため: 筋書きの答えの欠片を TextDelta に並べ、hold でなければ全文と終わり(COMPLETED)まで出す。続きは同じ宣言で生きた
   process が在れば使い回した数え(起こし直しは launches を増やす)。"
  (val origin asked.origin)
  (val thread (match origin
                (ResumeThread) (.get world.threads origin.thread-id)
                _ (do (setv made (FakeThread asked.spec))
                      (setv (get world.threads (.fresh-id world "thread")) made)
                      made)))
  (when (is thread None)
    (return (ThreadUnknown :thread-id origin.thread-id :detail "fake の世界にこの thread は無い")))
  (val thread-id (next (gfor #(key value) (.items world.threads) :if (is value thread) key)))
  (when (is-not (.running thread) None)
    (return (TurnInFlight :turn (CodexTurn :thread-id thread-id :turn-id thread.current))))
  (when (and (isinstance origin ResumeThread) (or (not thread.alive) (!= thread.spec asked.spec)))
    (setv thread.launches (+ thread.launches 1)
          thread.alive True
          thread.spec asked.spec))
  (val turn-id (.fresh-id world "turn"))
  (val item-id (.fresh-id world "item"))
  (val turn (FakeTurn))
  (setv (get thread.turns turn-id) turn
        thread.current turn-id)
  (val reply (world.respond asked.text))
  (.add thread turn (TurnStartedLine :thread-id thread-id :turn-id turn-id))
  (.add thread turn (ItemStarted :thread-id thread-id :turn-id turn-id :item-id item-id :item-type "agentMessage"))
  (for [piece reply.pieces]
    (.add thread turn (TextDelta :thread-id thread-id :turn-id turn-id :item-id item-id :text piece)))
  (when (not reply.hold)
    (.add thread turn (AgentMessageDone :thread-id thread-id :turn-id turn-id :item-id item-id :text (.join "" reply.pieces)))
    (.add thread turn (TurnEnded :thread-id thread-id :turn-id turn-id :status TurnStatus.COMPLETED)))
  (TurnStarted :turn (CodexTurn :thread-id thread-id :turn-id turn-id)))


(defk fake-read [#^ FakeCodexWorld world #^ CodexReadTurnEvents asked]
  {:pre [(: world FakeCodexWorld) (: asked CodexReadTurnEvents)] :post [(: % (| TurnEventPage UnknownTurn))]
   :tags {:context "codex" :role "process"}}
  "ターンの出来事を読むため。新しい出来事も終わりも無ければ、上限の秒を時計で待ってから空の頁を返す(fake では待つ間に何も起きない)。"
  (val thread (.get world.threads asked.turn.thread-id))
  (val turn (if (is thread None) None (.get thread.turns asked.turn.turn-id)))
  (when (is turn None) (return (UnknownTurn :turn asked.turn)))
  (val events (tuple (gfor event turn.events :if (> event.seq asked.after-seq) event)))
  (when (and (not events) (is turn.end None))
    (<- (Delay asked.wait-up-to)))
  (TurnEventPage :events events :next-seq (if events (. (get events -1) seq) asked.after-seq) :end turn.end))


(defk fake-interrupt [#^ FakeCodexWorld world #^ CodexTurn turn]
  {:pre [(: world FakeCodexWorld) (: turn CodexTurn)] :post [(: % (| InterruptRequested NoTurnInFlight))]
   :tags {:context "codex" :role "process"}}
  "走っているターンを INTERRUPTED で終えるため(本番の turn/interrupt の答えと同じ終わり)。"
  (val thread (.get world.threads turn.thread-id))
  (val running (if (is thread None) None (.running thread)))
  (when (or (is running None) (!= thread.current turn.turn-id))
    (return (NoTurnInFlight :turn turn)))
  (.add thread running (TurnEnded :thread-id turn.thread-id :turn-id turn.turn-id :status TurnStatus.INTERRUPTED))
  (InterruptRequested))


(defk fake-close [#^ FakeCodexWorld world #^ CodexCloseSession asked]
  {:pre [(: world FakeCodexWorld) (: asked CodexCloseSession)] :post [(: % SessionClosed)] :tags {:context "codex" :role "process"}}
  "会話を閉じるため: 走っているターンを BackendLost で終え、process を降ろした数えにする。"
  (val thread (.get world.threads asked.thread-id))
  (when (is thread None) (return (SessionClosed :was-running False)))
  (val running (.running thread))
  (when (is-not running None)
    (setv running.end (BackendLost :detail (.format "会話を閉じた: {}" asked.reason))))
  (setv thread.alive False)
  (SessionClosed :was-running (is-not running None)))


(defk fake-launch-count [#^ FakeCodexWorld world #^ str thread-id]
  {:pre [(: world FakeCodexWorld) (: thread-id str)] :post [(: % int)] :tags {:context "codex" :role "process"}}
  "会話のために起こした process の数を答えるため(本番と同じ検の口)。"
  (val thread (.get world.threads thread-id))
  (if (is thread None) 0 thread.launches))


;; 引数に残す理由: world は筋書きと fake の状態の持ち主で、検の composition root が 1 つ作って渡す(本番の codex-handler の host と同じ)。
(defhandler fake-codex-handler [world]
  (CodexStartTurn [origin spec text]
    (<- outcome (fake-start world effect))
    (resume outcome))
  (CodexInterruptTurn [turn]
    (<- outcome (fake-interrupt world turn))
    (resume outcome))
  (CodexReadTurnEvents [turn after-seq wait-up-to]
    (<- page (fake-read world effect))
    (resume page))
  (CodexAnswerRequest [turn request-id result]
    (resume (NoSuchRequest :request-id request-id)))
  (CodexCloseSession [thread-id reason]
    (<- closed (fake-close world effect))
    (resume closed))
  (CodexLaunchCount [thread-id]
    (<- count (fake-launch-count world thread-id))
    (resume count)))
