;;; 本番の handler — 公開 effect 7 つ(と検の口 ClaudeDropProcess)に、claude の print mode の子 process で答える。
;;;
;;; 方針 = 手番ごとに process を起こす(手番の終わりの result の行で stdin に EOF を出して降ろす — #517)。次の手番は
;;; `--resume <id>` の新しい process。起こし直しの判断は ClaudeStartTurn の中の 1 か所(decision.start-decision)だけで、
;;; 上の層は会話の id と手番の参照しか持たない。
;;;
;;; 不変条件(fake と共通 — tests/test_scenarios.hy が両方に当てる):
;;;   1 つの会話に走っている手番は多くとも 1 つ・生きた process は多くとも 1 つ(降りる途中の process は待ってから起こす)。
;;;   ClaudeStartTurn 1 回に終わりはちょうど 1 つ。行の seq は会話の中で単調増加。
;;;
;;; 状態は ClaudeCodeHost(composition root が 1 つ作って handler に渡す)が持つ。読み手の thread と handler の節は
;;; 会話ごとの lock で状態機械(dialogue.hy)の値を差し替える。待つ所(init・出来事・降りるの待ち)は doeff-time の
;;; GetMonotonic / Delay で刻む(VM の thread を眠らせない)。
(require doeff-hy.macros [defhandler defk <-])
(import collections.abc [Callable])
(import dataclasses [replace])
(import os)
(import os.path)
(import threading)
(import uuid)
(import doeff_time [Delay GetMonotonic])
(import doeff_claude_code.values [ClaudeTurn ClaudeHome ClaudeSessionSpec TurnInput FreshSession ResumeSession ForkSession
                                  LinkFromHome Rebuilt IMAGE-MIMES])
(import doeff_claude_code.lines [ClaudeStreamLine PermissionRequested Interrupted parse-record classify-record])
(import doeff_claude_code.effects [ClaudeStartTurn ClaudeInjectInput ClaudeInterruptTurn ClaudeReadTurnEvents
                                   ClaudeAnswerPermission ClaudeCloseSession ClaudeSessionStatus
                                   TurnStarted InputQueued InterruptRequested TurnEventPage Answered SessionClosed
                                   SessionStatus Idle TurnRunning Closed TranscriptPresent TranscriptAbsent
                                   CarryRefused LaunchFailed AttachmentRefused NoTurnInFlight UnknownTurn NoSuchRequest
                                   ProcessStillAlive])
(import doeff_claude_code.faults [ClaudeDropProcess])
(import doeff_claude_code.dialogue :as dialogue)
(import doeff_claude_code.dialogue [DialogueState])
(import doeff_claude_code.decision [SessionView Refuse start-decision])
(import doeff_claude_code.argv [transcript-dir transcript-path launch-argv cold-resume-argv process-env])
(import doeff_claude_code.process [ClaudeProcess EOF-GRACE-SECONDS TERM-GRACE-SECONDS])

(setv POLL-SECONDS 0.05)
(setv KEPT-TURNS 16)
(setv RETIRE-WAIT-SECONDS (+ EOF-GRACE-SECONDS (* 2 TERM-GRACE-SECONDS) 2.0))
(setv COLD-RESUME-TIMEOUT-SECONDS 600.0)


;; --- 状態 ---------------------------------------------------------------------------------------

(defclass TurnLog []
  "1 つの手番の行と終わり。"
  (defn __init__ [self]
    (setv self.lines [] self.end None)))

(defclass Binding []
  "1 つの process と、その process が今走らせている手番の番号(生き残った入力の手番へ進む)。"
  (defn __init__ [self #^ int turn-seq]
    (setv self.process None self.turn-seq turn-seq)))

(defclass SessionRuntime []
  "1 つの会話の状態(handler の中だけ)。session-id は ForkSession の init を読むまで空。"
  (defn __init__ [self #^ str session-id #^ ClaudeHome home #^ str canonical-cwd]
    (setv self.session-id session-id
          self.home home
          self.canonical-cwd canonical-cwd
          self.lock (threading.Lock)
          self.state (DialogueState :session-id session-id)
          self.binding None
          self.turns {}
          self.current-seq 0
          self.next-line-seq 0
          self.init-seen False
          self.closed False))

  (defn [property] process [self]
    (if (is self.binding None) None self.binding.process))

  (defn current-log [self]
    (.get self.turns self.current-seq))

  (defn running-turn [self]
    "走っている手番(無ければ None)。"
    (setv log (.current-log self))
    (if (and (is-not log None) (is log.end None) (> self.current-seq 0))
        (ClaudeTurn (or self.session-id self.state.session-id) self.current-seq)
        None))

  (defn open-turn [self]
    "新しい手番の番号を開く(古い手番は KEPT-TURNS だけ残す)。"
    (+= self.current-seq 1)
    (setv (get self.turns self.current-seq) (TurnLog))
    (for [old (list self.turns)]
      (when (<= old (- self.current-seq KEPT-TURNS))
        (del (get self.turns old))))
    self.current-seq))


(defclass ClaudeCodeHost []
  "handler の状態: 会話の id → SessionRuntime。command = 実行ファイルと前置きの引数(例: #(\"claude\"))・clock = 行の時刻を
   刻む関数(clock.clock-of)・launch-timeout = init の行を待つ上限(秒)。"
  (defn __init__ [self #^ tuple command clock [launch-timeout 120.0]]
    (setv self.command command
          self.clock clock
          self.launch-timeout (float launch-timeout)
          self.runtimes {}
          self.lock (threading.Lock)))

  (defn runtime [self #^ str session-id]
    (with [self.lock] (.get self.runtimes session-id)))

  (defn register [self #^ SessionRuntime runtime]
    (with [self.lock] (setv (get self.runtimes runtime.session-id) runtime)))

  (defn forget [self #^ str session-id #^ SessionRuntime runtime]
    (with [self.lock]
      (when (is (.get self.runtimes session-id) runtime)
        (del (get self.runtimes session-id))))))


;; --- 読み手の thread からの呼び(lock の中で状態機械を進める) ------------------------------------------

(defn apply-transition [#^ SessionRuntime runtime #^ Binding binding transition]
  "遷移の答えを運ぶ: 状態を差し替え、stdin へ書き、SIGINT を送り、手番の終わりを記し、close なら process を降ろし始める。
   runtime.lock の中で呼ぶ。"
  (setv runtime.state transition.state)
  (setv process binding.process)
  (for [line transition.sends] (.send process line))
  (when transition.signal (.interrupt process))
  (when (is-not transition.session-id None)
    (setv runtime.init-seen True)
    (when (not runtime.session-id) (setv runtime.session-id transition.session-id)))
  (when (is-not transition.end None)
    (setv log (.get runtime.turns binding.turn-seq))
    (setv end transition.end)
    (when transition.continues
      (setv next-seq (.open-turn runtime))
      (setv binding.turn-seq next-seq)
      (setv end (replace end :continued-by (ClaudeTurn runtime.session-id next-seq))))
    (when (and (is-not log None) (is log.end None))
      (setv log.end end)))
  (when transition.close (.retire process)))

(defn on-line [#^ SessionRuntime runtime #^ Binding binding clock #^ str raw]
  (setv record (parse-record raw))
  (when (is record None) (return None))
  (setv kind (classify-record record))
  (setv at (clock))
  (with [runtime.lock]
    (setv log (.get runtime.turns binding.turn-seq))
    (when (is-not log None)
      (.append log.lines (ClaudeStreamLine :seq runtime.next-line-seq :at at :kind kind :raw (.rstrip raw "\n")))
      (+= runtime.next-line-seq 1))
    (when (is runtime.binding binding)
      (apply-transition runtime binding
                        (dialogue.on-record runtime.state record
                                            (if (isinstance kind PermissionRequested) kind None))))))

(defn on-exit [#^ SessionRuntime runtime #^ Binding binding exit-code #^ str stderr-tail]
  (with [runtime.lock]
    (when (is runtime.binding binding)
      (apply-transition runtime binding (dialogue.on-exit runtime.state exit-code stderr-tail)))))


;; --- I/O の小さな道具(handler の中だけ) --------------------------------------------------------------

(defn #^ bool file-present [#^ str path] (os.path.exists path))

(defn link-if-present [#^ str source #^ str target]
  "周辺の置き物の持ち込み(在れば張る・無ければ飛ばす)。"
  (when (and (os.path.exists source) (not (os.path.lexists target)))
    (os.makedirs (os.path.dirname target) :exist-ok True)
    (os.symlink source target)))

(defn apply-carry [#^ ClaudeHome home #^ str canonical-cwd #^ str session-id carry]
  "transcript の持ち込み。答え = None(持ち込めた・既に在る・持ち込む物が無い)か CarryRefused。既に在る transcript は上書きしない。"
  (setv target (transcript-path home.config-dir canonical-cwd session-id))
  (when (or (is carry None) (os.path.lexists target)) (return None))
  (try
    (cond
      (isinstance carry Rebuilt)
        (do
          (os.makedirs (os.path.dirname target) :exist-ok True)
          (setv temporary (+ target ".doeff-tmp"))
          (with [handle (open temporary "w" :encoding "utf-8")] (.write handle carry.jsonl-text))
          (os.replace temporary target))
      (isinstance carry LinkFromHome)
        (do
          (setv source-dir (transcript-dir carry.source-home.config-dir canonical-cwd))
          (setv source (transcript-path carry.source-home.config-dir canonical-cwd session-id))
          (when (not (os.path.exists source)) (return None))
          (os.makedirs (os.path.dirname target) :exist-ok True)
          (os.symlink source target)
          (link-if-present (+ source-dir "/sessions-index.json")
                           (+ (transcript-dir home.config-dir canonical-cwd) "/sessions-index.json"))
          (for [part ["session-env" "file-history"]]
            (link-if-present (.format "{}/{}/{}" carry.source-home.config-dir part session-id)
                             (.format "{}/{}/{}" home.config-dir part session-id)))))
    (except [error OSError]
      (return (CarryRefused (.format "{}: {}" target error)))))
  None)

(defn session-view [runtime]
  (if (is runtime None)
      (SessionView)
      (with [runtime.lock]
        (SessionView :known True
                     :running-turn (.running-turn runtime)
                     :retiring (and (is-not runtime.process None) (.alive runtime.process))))))

(defn refused-attachment [#^ TurnInput input]
  (setv refused (lfor item input.attachments :if (not-in item.mime IMAGE-MIMES) item.mime))
  (if refused (AttachmentRefused (get refused 0)) None))


;; --- 待ち(doeff-time の時計で刻む) --------------------------------------------------------------------

(defk wait-until [ready #^ float seconds]
  {:pre [(: ready Callable) (: seconds float)] :post [(: % bool)]}
  "ready() が真になるまで待つ(上限 seconds 秒)。答え = 真になったか。"
  (<- started (GetMonotonic))
  (while (not (ready))
    (<- now (GetMonotonic))
    (when (>= (- now started) seconds) (return False))
    (<- (Delay POLL-SECONDS)))
  True)

(defk run-cold-resume [#^ ClaudeCodeHost host #^ ClaudeSessionSpec spec #^ str session-id]
  {:pre [(: host ClaudeCodeHost) (: spec ClaudeSessionSpec) (: session-id str)] :post [(: % bool)]}
  "冷えた続きの前の 1 回きりの命令を走らせて終わりを待つ。答え = 走り終えたか(失敗しても手番は起こす — 最適化の命令)。"
  (setv done (threading.Event))
  (try
    (setv process (ClaudeProcess (cold-resume-argv host.command spec session-id) spec.cwd (process-env spec.home)
                                 (fn [raw] None) (fn [code tail] (.set done))))
    (except [OSError] (return False)))
  (.close-stdin process)
  (<- finished (wait-until (fn [] (.is-set done)) COLD-RESUME-TIMEOUT-SECONDS))
  (when (not finished) (.drop process))
  finished)


;; --- 節の中身 -----------------------------------------------------------------------------------

(defn spawn-turn [#^ ClaudeCodeHost host #^ SessionRuntime runtime #^ ClaudeSessionSpec spec origin #^ TurnInput input]
  "手番の process を起こして入力を書く。答え = 手番の番号か LaunchFailed(実行ファイルが無い等)。"
  (with [runtime.lock]
    (setv runtime.closed False
          runtime.init-seen False
          runtime.state (DialogueState :session-id runtime.session-id))
    (setv turn-seq (.open-turn runtime))
    (setv binding (Binding turn-seq))
    (setv runtime.binding binding)
    (setv transition (dialogue.begin-turn runtime.state input))
    (try
      (setv binding.process
            (ClaudeProcess (launch-argv host.command spec origin) spec.cwd (process-env spec.home)
                           (fn [raw] (on-line runtime binding host.clock raw))
                           (fn [code tail] (on-exit runtime binding code tail))))
      (except [error OSError]
        (setv runtime.binding None)
        (setv (. (.current-log runtime) end) (Interrupted))
        (return (LaunchFailed :stderr-tail (str error)))))
    (apply-transition runtime binding transition)
    turn-seq))

(defk await-init [#^ ClaudeCodeHost host #^ SessionRuntime runtime #^ int turn-seq #^ bool fresh-runtime]
  {:pre [(: host ClaudeCodeHost) (: runtime SessionRuntime) (: turn-seq int) (: fresh-runtime bool)]
   :post [(: % (| TurnStarted LaunchFailed))]}
  "init の行(か process の終わり)まで待つ。init の前に降りた・期限を過ぎた = LaunchFailed(新しく作った会話の記録は忘れる)。"
  (setv process runtime.process)
  (<- seen (wait-until (fn [] (or runtime.init-seen (not (.alive process)))) host.launch-timeout))
  (with [runtime.lock]
    (setv started runtime.init-seen))
  (if started
      (do
        (when (not (.runtime host runtime.session-id)) (.register host runtime))
        (TurnStarted (ClaudeTurn runtime.session-id turn-seq) runtime.session-id))
      (do
        (when seen
          (<- (wait-until (fn [] (is-not (. (.get runtime.turns turn-seq) end) None)) 2.0)))
        (.drop process)
        (with [runtime.lock]
          (setv log (.get runtime.turns turn-seq))
          (when (is log.end None) (setv log.end (Interrupted))))
        (when fresh-runtime (.forget host runtime.session-id runtime))
        (LaunchFailed :exit-code (.exit-code process)
                      :stderr-tail (if seen (.stderr-tail process)
                                       (.format "no init line within {} seconds" host.launch-timeout))))))

(defk start-turn [#^ ClaudeCodeHost host #^ ClaudeStartTurn request]
  {:pre [(: host ClaudeCodeHost) (: request ClaudeStartTurn)] :post [(: % "StartTurnOutcome")]}
  (setv origin request.origin spec request.spec input request.input)
  (setv refused (refused-attachment input))
  (when (is-not refused None) (return refused))
  (setv canonical (os.path.realpath spec.cwd))
  (setv target-id (if (isinstance origin ForkSession) origin.parent-session-id origin.session-id))
  (setv carried (apply-carry spec.home canonical target-id (if (isinstance origin FreshSession) None origin.carry)))
  (when (is-not carried None) (return carried))
  (setv runtime (if (isinstance origin ForkSession) None (.runtime host target-id)))
  (setv decision (start-decision origin (session-view runtime)
                                 (file-present (transcript-path spec.home.config-dir canonical target-id))
                                 (is-not spec.cold-resume-prompt None)))
  (when (isinstance decision Refuse) (return decision.outcome))
  (when decision.wait-retire
    (setv old runtime.process)
    (<- down (wait-until (fn [] (not (.alive old))) RETIRE-WAIT-SECONDS))
    (when (not down)
      (return (LaunchFailed :stderr-tail "the previous process of this session did not go down"))))
  (when decision.cold-resume
    (<- (run-cold-resume host spec target-id)))
  (setv fresh-runtime (is runtime None))
  (when fresh-runtime
    (setv runtime (SessionRuntime (if (isinstance origin ForkSession) "" target-id) spec.home canonical))
    (when (not (isinstance origin ForkSession)) (.register host runtime)))
  (setv spawned (spawn-turn host runtime spec origin input))
  (when (isinstance spawned LaunchFailed)
    (when fresh-runtime (.forget host target-id runtime))
    (return spawned))
  (<- outcome (await-init host runtime spawned fresh-runtime))
  outcome)

(defn in-flight-log [runtime #^ ClaudeTurn turn]
  "名指した手番が走っていればその TurnLog(でなければ None)。runtime.lock の中で呼ぶ。"
  (setv log (.get runtime.turns turn.turn-seq))
  (if (and (= runtime.current-seq turn.turn-seq) (is-not log None) (is log.end None)) log None))

(defn inject-input [#^ ClaudeCodeHost host #^ ClaudeTurn turn #^ TurnInput input]
  (setv refused (refused-attachment input))
  (when (is-not refused None) (return refused))
  (setv runtime (.runtime host turn.session-id))
  (when (is runtime None) (return (NoTurnInFlight turn.session-id)))
  (with [runtime.lock]
    (setv transition (if (is (in-flight-log runtime turn) None) None (dialogue.inject runtime.state input)))
    (when (or (is transition None) (not (.alive runtime.process)))
      (return (NoTurnInFlight turn.session-id)))
    (apply-transition runtime runtime.binding transition))
  (InputQueued input.ref))

(defn interrupt-turn [#^ ClaudeCodeHost host #^ ClaudeTurn turn]
  (setv runtime (.runtime host turn.session-id))
  (when (is runtime None) (return (NoTurnInFlight turn.session-id)))
  (with [runtime.lock]
    (setv transition (if (is (in-flight-log runtime turn) None) None
                         (dialogue.interrupt runtime.state (str (uuid.uuid4)))))
    (when (is transition None) (return (NoTurnInFlight turn.session-id)))
    (apply-transition runtime runtime.binding transition))
  (InterruptRequested))

(defn answer-permission [#^ ClaudeCodeHost host #^ ClaudeAnswerPermission request]
  (setv runtime (.runtime host request.turn.session-id))
  (when (is runtime None) (return (NoSuchRequest request.request-id)))
  (with [runtime.lock]
    (setv transition (if (is (in-flight-log runtime request.turn) None) None
                         (dialogue.answer-permission runtime.state request.request-id request.answer)))
    (when (is transition None) (return (NoSuchRequest request.request-id)))
    (apply-transition runtime runtime.binding transition))
  (Answered))

(defn page-of [runtime #^ ClaudeTurn turn #^ int after-seq]
  "名指した手番の after-seq より後の行と終わり(知らない手番は None)。"
  (with [runtime.lock]
    (setv log (.get runtime.turns turn.turn-seq))
    (when (is log None) (return None))
    (setv lines (tuple (gfor line log.lines :if (> line.seq after-seq) line)))
    (TurnEventPage lines (if lines (. (get lines -1) seq) after-seq) log.end)))

(defk read-events [#^ ClaudeCodeHost host #^ ClaudeReadTurnEvents request]
  {:pre [(: host ClaudeCodeHost) (: request ClaudeReadTurnEvents)] :post [(: % (| TurnEventPage UnknownTurn))]}
  (setv turn request.turn)
  (setv runtime (.runtime host turn.session-id))
  (when (or (is runtime None) (is (page-of runtime turn request.after-seq) None))
    (return (UnknownTurn turn)))
  (<- (wait-until (fn [] (setv page (page-of runtime turn request.after-seq))
                         (or page.lines (is-not page.end None)))
                  (float request.wait-up-to)))
  (page-of runtime turn request.after-seq))

(defk close-session [#^ ClaudeCodeHost host #^ ClaudeCloseSession request]
  {:pre [(: host ClaudeCodeHost) (: request ClaudeCloseSession)] :post [(: % (| SessionClosed ProcessStillAlive))]}
  (setv runtime (.runtime host request.session-id))
  (when (is runtime None) (return (SessionClosed False)))
  (with [runtime.lock]
    (setv was-running (is-not (.running-turn runtime) None))
    (setv transition (dialogue.close-session runtime.state))
    (setv runtime.state transition.state)
    (when (is-not transition.end None)
      (setv (. (.current-log runtime) end) transition.end))
    (setv runtime.closed True)
    (setv process runtime.process))
  (when (and (is-not process None) (.alive process))
    (.retire process)
    (<- (wait-until (fn [] (or (not (.alive process)) (.retire-finished process))) RETIRE-WAIT-SECONDS))
    (when (.alive process)
      (return (ProcessStillAlive (.format "process pid {} of session {} did not go down after EOF, SIGTERM and SIGKILL ({})"
                                          process.pid request.session-id request.reason)))))
  (SessionClosed was-running))

(defn session-status [#^ ClaudeCodeHost host #^ ClaudeSessionStatus request]
  (setv path (transcript-path request.home.config-dir (os.path.realpath request.cwd) request.session-id))
  (setv transcript (if (os.path.exists path) (TranscriptPresent (os.path.getmtime path)) (TranscriptAbsent)))
  (setv runtime (.runtime host request.session-id))
  (setv state
        (if (is runtime None)
            (Idle)
            (with [runtime.lock]
              (setv running (.running-turn runtime))
              (cond
                (is-not running None) (TurnRunning running)
                runtime.closed (Closed)
                True (Idle)))))
  (SessionStatus state transcript))

(defn drop-process [#^ ClaudeCodeHost host #^ str session-id]
  (setv runtime (.runtime host session-id))
  (setv process (if (is runtime None) None runtime.process))
  (and (is-not process None) (.drop process)))


;; --- handler -----------------------------------------------------------------------------------

(defhandler claude-code-handler [host]
  (ClaudeStartTurn [origin spec input]
    (<- outcome (start-turn host effect))
    (resume outcome))
  (ClaudeInjectInput [turn input]
    (resume (inject-input host turn input)))
  (ClaudeInterruptTurn [turn]
    (resume (interrupt-turn host turn)))
  (ClaudeReadTurnEvents [turn after-seq wait-up-to]
    (<- page (read-events host effect))
    (resume page))
  (ClaudeAnswerPermission [turn request-id answer]
    (resume (answer-permission host effect)))
  (ClaudeCloseSession [session-id reason]
    (<- closed (close-session host effect))
    (resume closed))
  (ClaudeSessionStatus [home cwd session-id]
    (resume (session-status host effect)))
  (ClaudeDropProcess [session-id]
    (resume (drop-process host session-id))))
