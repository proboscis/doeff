;;; 本番の handler — 公開 effect(effects.hy)に、codex の app-server の子 process(process.hy の CodexProcess)で答える。
;;;
;;; 方針 = 会話(thread)の process はターンをまたいで生かし、同じ会話・同じ宣言(CodexSessionSpec)の続きはその process に turn/start を
;;; 書く。宣言が違えば降ろしてから起こし直し、新しい process で thread/resume する。process が無い続き(降りた・別の handler が始めた
;;; 会話)も新しい process で thread/resume する。起こすか使い回すかの判断は start-turn の 1 か所だけで、上の層は会話の id とターンの
;;; 参照しか持たない。
;;;
;;; 不変条件(fake と共通 — tests/test_scenarios.hy が両方に当てる):
;;;   1 つの会話に走っているターンは多くとも 1 つ・生きた process は多くとも 1 つ。CodexStartTurn 1 回に終わりはちょうど 1 つ
;;;   (lines.TurnEnded か、終わりの行の前に process が消えた BackendLost)。出来事の seq は会話の中で単調に増える。
;;;
;;; 状態は CodexHost(composition root が 1 つ作って handler に渡す)が持つ。読み手の thread(CodexProcess の on-line・on-exit)と
;;; handler の節は会話ごとの lock で状態を差し替える。待つ所(要求の答え・出来事・降りるの待ち)は呼び鈴(Doorbell — CreateExternalPromise
;;; の約束)を掛けてから条件を読み直し、WaitWithin で上限の秒まで待つ。状態を変えた側が呼び鈴を鳴らす(時間で起きて確かめない)。
;;; 手本 = doeff-claude-code の handler.hy(同じ待ち方・同じ host の持ち方)。
(require doeff-hy.macros [defhandler defk deff <- val var])
(val MODULE-TAGS {:context "codex" :role "process"})
(import collections.abc [Callable])
(import threading)
(import doeff [run])
(import doeff_core_effects.scheduler [CreateExternalPromise ExternalPromise])
(import doeff_time [GetMonotonic WaitWithin])
(import doeff_codex.values [CodexSessionSpec CodexTurn CodexEvent FreshThread ResumeThread])
(import doeff_codex.lines :as lines)
(import doeff_codex.rpc [app-server-argv initialize-request initialized-notification thread-start-request thread-resume-request
                         turn-start-request turn-interrupt-request server-response-line])
(import doeff_codex.process [CodexProcess EOF-GRACE-SECONDS TERM-GRACE-SECONDS])
(import doeff_codex.effects [CodexStartTurn CodexInterruptTurn CodexReadTurnEvents CodexAnswerRequest CodexCloseSession
                             CodexLaunchCount BackendLost TurnStarted InterruptRequested TurnEventPage Answered SessionClosed
                             ThreadUnknown TurnInFlight LaunchFailed RequestRefused NoTurnInFlight UnknownTurn NoSuchRequest
                             ProcessStillAlive])

(val RETIRE-WAIT-SECONDS (+ EOF-GRACE-SECONDS (* 2 TERM-GRACE-SECONDS) 2.0))
;; 会話ごとに残すターンの記録の数(古いターンの出来事は読めなくなる — UnknownTurn)。
(val KEPT-TURNS 16)
(val CLIENT-NAME "doeff-codex")
(val CLIENT-VERSION "0.1.0")


;; --- 状態 ---------------------------------------------------------------------------------------

(defclass Doorbell []
  "状態が変わるのを待つ手(wait-until)が掛けた呼び鈴(CreateExternalPromise の約束)を集め、状態を変えた側が全部鳴らすため。読み手の
   thread と handler の節の両方から触るので自分の lock を持つ。鳴らすのは ExternalPromise.complete だけ(thread をまたいでよい唯一の口)。"
  (defn __init__ [self]
    (setv self.lock (threading.Lock))
    (setv #^ (get tuple #((get ExternalPromise bool) ...)) self.bells #()))

  (defn hang [self bell]
    "呼び鈴を掛けるため。"
    (with [self.lock]
      (setv self.bells (+ self.bells #(bell)))))

  (defn unhang [self bell]
    "鳴らずに起きた呼び鈴を外すため。"
    (with [self.lock]
      (setv self.bells (tuple (gfor other self.bells :if (is-not other bell) other)))))

  (defn ring [self]
    "掛かっている呼び鈴を全部鳴らして外すため(待つ手が状態を読み直す)。"
    (with [self.lock]
      (setv bells self.bells)
      (setv self.bells #()))
    (for [bell bells]
      (.complete bell True))))


(defclass TurnLog []
  "1 つのターンの出来事と終わり(まだなら end は None)。pending = 答えを待つ codex からの要求の id(ServerRequest)。"
  (defn __init__ [self]
    (setv #^ (get tuple #(CodexEvent ...)) self.events #())
    (setv self.end None)
    (setv #^ tuple self.pending #())))


(defclass SessionRuntime []
  "1 つの会話(thread)の状態(handler の中だけ)。thread-id は新しい会話の thread/start の答えまで None。generation = 今の process を
   起こした回の番号(古い process の行と終わりを、今の process の物と取り違えないため)。answers = 要求の id → 答えの記録(要求ごとに
   handler が待って読む — 読んだら消す)。outside = 走っているターンの外で読んだ記録(数えて見せるため・ターンの頁には載せない)。"
  (defn __init__ [self #^ CodexSessionSpec spec]
    (setv self.spec spec
          self.thread-id None
          self.process None
          self.generation 0
          self.launches 0
          self.lock (threading.Lock)
          self.doorbell (Doorbell)
          self.next-request-id 0
          self.next-seq 0
          self.current-turn None
          self.closed False
          self.exit-code None
          self.stderr-tail None)
    (setv #^ (get dict #((| int str) object)) self.answers {})
    (setv #^ (get dict #(str TurnLog)) self.turns {})
    (setv #^ tuple self.outside #()))

  (defn #^ int take-request-id [self]
    "次の要求の id を取るため(lock の中で呼ぶ — 1 つの process の中で重ならない)。"
    (setv self.next-request-id (+ self.next-request-id 1))
    self.next-request-id)

  (defn #^ TurnLog log-of [self #^ str turn-id]
    "ターンの記録を取るため(無ければ作る — turn/started が turn/start の答えより先に届いても積めるように)。新しく作る時は、走っている
     ターンを除いて古い記録を KEPT-TURNS 本まで刈る(長い会話で記録が積もり続けない)。lock の中で呼ぶ。"
    (when (not-in turn-id self.turns)
      (setv (get self.turns turn-id) (TurnLog))
      (setv stale (cut (list self.turns) 0 (max 0 (- (len self.turns) KEPT-TURNS))))
      (for [old stale]
        (when (!= old self.current-turn)
          (del (get self.turns old)))))
    (get self.turns turn-id))

  (defn running-log [self]
    "走っているターンの記録(無ければ None)。lock の中で呼ぶ。"
    (setv log (if (is self.current-turn None) None (.get self.turns self.current-turn)))
    (if (and (is-not log None) (is log.end None)) log None))

  (defn add-event [self #^ TurnLog log record]
    "記録を seq を振ってターンの出来事に積むため。lock の中で呼ぶ。"
    (setv self.next-seq (+ self.next-seq 1))
    (setv log.events (+ log.events #((CodexEvent :seq self.next-seq :record record))))))


(defclass CodexHost []
  "handler の状態: thread の id → SessionRuntime。command = 実行ファイルと前置きの引数(例 #(\"codex\"))・launch-timeout = 起動と
   要求の答えを待つ上限(秒)。"
  (defn __init__ [self #^ tuple command [launch-timeout 120.0]]
    (when (not command)
      (raise (ValueError "CodexHost.command は空でない tuple")))
    (setv self.command command
          self.launch-timeout (float launch-timeout)
          self.lock (threading.Lock))
    (setv #^ (get dict #(str SessionRuntime)) self.runtimes {}))

  (defn runtime [self #^ str thread-id]
    "会話の状態を引くため(知らなければ None)。"
    (with [self.lock] (.get self.runtimes thread-id)))

  (defn register [self #^ SessionRuntime runtime]
    "thread の id が決まった会話の状態を覚えるため。"
    (with [self.lock] (setv (get self.runtimes runtime.thread-id) runtime))))


;; --- 読み手の thread からの呼び(lock の中で状態を進める) --------------------------------------------

(defk absorb [#^ SessionRuntime runtime record]
  {:pre [(: runtime SessionRuntime) (: record lines.CodexLine)] :post [(: % None)] :tags {:context "codex" :role "process"}}
  "分けた記録を会話の状態へ積むため: 要求の答えは answers へ・ターンの記録はそのターンの出来事へ(終わりは end へ)・codex からの要求は
   走っているターンへ(答えを待つ id として)・ほかは走っているターンへ(無ければ outside)。lock の中で呼ぶ。"
  (match record
    (lines.Response) (setv (get runtime.answers record.id) record)
    (lines.ErrorResponse) (setv (get runtime.answers record.id) record)
    (lines.ServerRequest)
    (do (setv log (.running-log runtime))
        (if (is log None)
            (setv runtime.outside (+ runtime.outside #(record)))
            (do (setv log.pending (+ log.pending #(record.id)))
                (.add-event runtime log record))))
    (| (lines.TurnStarted) (lines.TextDelta) (lines.ReasoningDelta) (lines.AgentMessageDone) (lines.ItemStarted) (lines.ItemDone)
       (lines.TokenUsage) (lines.TurnError))
    (.add-event runtime (.log-of runtime record.turn-id) record)
    (lines.TurnEnded)
    (do (setv log (.log-of runtime record.turn-id))
        (.add-event runtime log record)
        (when (is log.end None) (setv log.end record)))
    _ (do (setv log (.running-log runtime))
          (if (is log None)
              (setv runtime.outside (+ runtime.outside #(record)))
              (.add-event runtime log record)))))


(deff on-line [#^ SessionRuntime runtime #^ int generation #^ str raw]  ; defk にできない: CodexProcess の読み手の thread が呼ぶ callback
  {:pre [(: runtime SessionRuntime) (: generation int) (: raw str)] :post [(: % None)]}
  "stdout の 1 行を分けて会話の状態へ積み、呼び鈴を鳴らすため(今の process の行だけ — 降ろし中の古い process の行は捨てる)。"
  (setv record (run (lines.classify-line raw)))
  (with [runtime.lock]
    (when (= runtime.generation generation)
      (run (absorb runtime record))))
  (.ring runtime.doorbell))


(deff on-exit [#^ SessionRuntime runtime #^ int generation exit-code #^ str stderr-tail]  ; defk にできない: 読み手の thread が呼ぶ callback
  {:pre [(: runtime SessionRuntime) (: generation int) (: exit-code (| int None)) (: stderr-tail str)] :post [(: % None)]}
  "process の終わりを記し、終わりの行の無い走っているターンを BackendLost で閉じるため(今の process の終わりだけ)。"
  (with [runtime.lock]
    (when (= runtime.generation generation)
      (setv runtime.exit-code exit-code
            runtime.stderr-tail stderr-tail)
      (setv log (.running-log runtime))
      (when (is-not log None)
        (setv log.end (BackendLost :detail "終わりの行(turn/completed)の前に codex の process が降りた"
                                   :exit-code exit-code :stderr-tail stderr-tail)))))
  (.ring runtime.doorbell))


;; --- 待ち(呼び鈴で起きる) --------------------------------------------------------------------

(defk wait-until [#^ Doorbell doorbell ready #^ float seconds]
  {:pre [(: doorbell Doorbell) (: ready Callable) (: seconds float)] :post [(: % bool)] :tags {:context "codex" :role "process"}}
  "ready の答えが真になるまで、状態を変えた側が鳴らす呼び鈴で起きて待つため(上限 seconds 秒 — 時間で起きて確かめない)。ready() =
   真偽を答える Program(状態を lock の中で読む defk)。取りこぼさない順: 呼び鈴を掛けてから ready を読み直す。鳴った・期限が来た・
   読み直しで真のどれでも、呼び鈴を外して約束を閉じる。答え = 真になったか。"
  (<- started (GetMonotonic))
  (<- ready-at-once (ready))
  (when ready-at-once (return True))
  (while True
    (<- bell (CreateExternalPromise))
    (.hang doorbell bell)
    (<- now-ready (ready))
    (<- now (GetMonotonic))
    (val left (- seconds (- now started)))
    (when (and (not now-ready) (> left 0))
      (<- (WaitWithin bell.future left :park True)))
    (.unhang doorbell bell)
    (.complete bell True)
    (when now-ready (return True))
    (when (<= left 0) (return False))))


;; --- 要求と答え --------------------------------------------------------------------------------

(defk launch-failure [#^ SessionRuntime runtime #^ str stage]
  {:pre [(: runtime SessionRuntime) (: stage str)] :post [(: % LaunchFailed)] :tags {:context "codex" :role "process"}}
  "要求の答えを得られなかった時の失敗を、降りた process の終了の code と stderr の末尾つきで作るため。"
  (with [runtime.lock]
    (val alive (and (is-not runtime.process None) (.alive runtime.process)))
    (val failure (LaunchFailed :detail (if alive
                                           (.format "{} の答えを待つ上限の秒を過ぎた" stage)
                                           (.format "{} の答えの前に codex の process が降りた" stage))
                               :exit-code runtime.exit-code
                               :stderr-tail (or runtime.stderr-tail ""))))
  failure)


(defk answered [#^ SessionRuntime runtime request-id process]
  {:pre [(: runtime SessionRuntime) (: request-id (| int str)) (: process CodexProcess)] :post [(: % bool)]
   :tags {:context "codex" :role "process"}}
  "要求の答えが届いたか、答えを書く process が降りたかを読むため(要求の待ちの条件)。"
  (with [runtime.lock]
    (val arrived (in request-id runtime.answers)))
  (or arrived (not (.alive process))))


(defk process-down [process]
  {:pre [(: process CodexProcess)] :post [(: % bool)] :tags {:context "codex" :role "process"}}
  "降ろし始めた process が降りたか、降ろす梯子を踏み終えたかを読むため(降りるの待ちの条件)。"
  (or (not (.alive process)) (.retire-finished process)))


(defk request [#^ SessionRuntime runtime #^ str method build #^ float seconds]
  {:pre [(: runtime SessionRuntime) (: method str) (: build Callable) (: seconds float)]
   :post [(: % (| lines.Response lines.ErrorResponse LaunchFailed))]
   :tags {:context "codex" :role "process"}}
  "要求を 1 つ書いて答えを待つため: build(id) = その id の要求の行の Program(rpc.hy)。答え = 答えの記録か、process が降りた・上限を
   過ぎた LaunchFailed(method を段の名にする)。"
  (with [runtime.lock]
    (val request-id (.take-request-id runtime))
    (val process runtime.process))
  (<- line (build request-id))
  (.send process line)
  (<- (wait-until runtime.doorbell (fn [] (answered runtime request-id process)) seconds))
  (with [runtime.lock]
    (val answer (.pop runtime.answers request-id None)))
  (when (is-not answer None) (return answer))
  (<- failure (launch-failure runtime method))
  failure)


(defk refused [#^ str method #^ lines.ErrorResponse answer]
  {:pre [(: method str) (: answer lines.ErrorResponse)] :post [(: % RequestRefused)] :tags {:context "codex" :role "process"}}
  "codex の誤りの答えを、要求の名つきの失敗にするため。"
  (RequestRefused :method method :code answer.code :message answer.message))


;; --- 起こす・降ろす -----------------------------------------------------------------------------

(defk retire [#^ SessionRuntime runtime]
  {:pre [(: runtime SessionRuntime)] :post [(: % bool)] :tags {:context "codex" :role "process"}}
  "今の process を降ろし、降りるまで(上限まで)待つため。答え = 降りたか(process が無ければ真)。"
  (val process runtime.process)
  (when (or (is process None) (not (.alive process))) (return True))
  (.retire process)
  (<- (wait-until runtime.doorbell (fn [] (process-down process)) RETIRE-WAIT-SECONDS))
  (not (.alive process)))


(defk launch [#^ CodexHost host #^ SessionRuntime runtime #^ CodexSessionSpec spec origin]
  {:pre [(: host CodexHost) (: runtime SessionRuntime) (: spec CodexSessionSpec) (: origin (| FreshThread ResumeThread))]
   :post [(: % (| str ThreadUnknown LaunchFailed RequestRefused))]
   :tags {:context "codex" :role "process"}}
  "新しい process を起こし、初期化して thread を開くため(新しい会話は thread/start・続きは thread/resume)。答え = thread の id か失敗
   (失敗した process は降ろす)。"
  (<- argv (app-server-argv host.command))
  (with [runtime.lock]
    (setv runtime.generation (+ runtime.generation 1)
          runtime.launches (+ runtime.launches 1)
          runtime.spec spec
          runtime.exit-code None
          runtime.stderr-tail None)
    (val generation runtime.generation))
  (val process (CodexProcess argv spec.cwd (dict spec.home.env)
                             (fn [raw] (on-line runtime generation raw))
                             (fn [code tail] (on-exit runtime generation code tail))
                             (fn [] (.ring runtime.doorbell))))
  (with [runtime.lock]
    (setv runtime.process process))
  (<- greeted (request runtime "initialize" (fn [request-id] (initialize-request request-id CLIENT-NAME CLIENT-VERSION))
                       host.launch-timeout))
  (when (not (isinstance greeted lines.Response))
    (<- (retire runtime))
    (return (if (isinstance greeted lines.ErrorResponse) (refused "initialize" greeted) greeted)))
  (<- ready-line (initialized-notification))
  (.send process ready-line)
  (<- opened (match origin
               (FreshThread)
               (request runtime "thread/start"
                        (fn [request-id] (thread-start-request request-id spec.cwd :approval-policy spec.approval-policy
                                                               :sandbox spec.sandbox :model spec.model))
                        host.launch-timeout)
               (ResumeThread)
               (request runtime "thread/resume"
                        (fn [request-id] (thread-resume-request request-id origin.thread-id :cwd spec.cwd
                                                                :approval-policy spec.approval-policy :sandbox spec.sandbox
                                                                :model spec.model))
                        host.launch-timeout)))
  (when (and (isinstance opened lines.Response) (is-not opened.thread-id None))
    (return opened.thread-id))
  (<- (retire runtime))
  (match opened
    (lines.ErrorResponse) (if (isinstance origin ResumeThread)
                              (ThreadUnknown :thread-id origin.thread-id :detail opened.message)
                              (do (<- refusal (refused "thread/start" opened)) refusal))
    (lines.Response) (LaunchFailed :detail "thread を開く答えが thread の id を名乗らない")
    _ opened))


;; --- effect の答え -----------------------------------------------------------------------------

(defk start-turn [#^ CodexHost host #^ CodexStartTurn asked]
  {:pre [(: host CodexHost) (: asked CodexStartTurn)] :post [(: % (| TurnStarted ThreadUnknown TurnInFlight LaunchFailed RequestRefused))]
   :tags {:context "codex" :role "process"}}
  "ターンを始めるため: 同じ会話・同じ宣言の生きた process が在れば使い回し、無ければ起こして thread を開いてから turn/start を書く。"
  (val origin asked.origin)
  (val known (match origin
               (ResumeThread) (.runtime host origin.thread-id)
               _ None))
  (when (is-not known None)
    (with [known.lock]
      (val running (.running-log known))
      (val running-id known.current-turn))
    (when (is-not running None)
      (return (TurnInFlight :turn (CodexTurn :thread-id origin.thread-id :turn-id running-id)))))
  (val runtime (if (is known None) (SessionRuntime asked.spec) known))
  (val reusable (and (is-not known None) (not known.closed) (is-not known.process None) (.alive known.process)
                     (= known.spec asked.spec)))
  (when (not reusable)
    (<- (retire runtime))
    (<- opened (launch host runtime asked.spec origin))
    (when (not (isinstance opened str)) (return opened))
    (with [runtime.lock]
      (setv runtime.thread-id opened
            runtime.closed False))
    (.register host runtime))
  (<- answer (request runtime "turn/start" (fn [request-id] (turn-start-request request-id runtime.thread-id asked.text))
                      host.launch-timeout))
  (match answer
    (lines.Response)
    (if (is answer.turn-id None)
        (LaunchFailed :detail "turn/start の答えがターンの id を名乗らない")
        (do (with [runtime.lock]
              (setv runtime.current-turn answer.turn-id)
              (.log-of runtime answer.turn-id))
            (TurnStarted :turn (CodexTurn :thread-id runtime.thread-id :turn-id answer.turn-id))))
    (lines.ErrorResponse) (do (<- refusal (refused "turn/start" answer)) refusal)
    _ answer))


(defk interrupt-turn [#^ CodexHost host #^ CodexTurn turn]
  {:pre [(: host CodexHost) (: turn CodexTurn)] :post [(: % (| InterruptRequested NoTurnInFlight))]
   :tags {:context "codex" :role "process"}}
  "走っているターンに turn/interrupt を書くため(答えは待たない — 終わりは出来事の TurnEnded で届く)。"
  (val runtime (.runtime host turn.thread-id))
  (when (is runtime None) (return (NoTurnInFlight :turn turn)))
  (with [runtime.lock]
    (val log (.running-log runtime))
    (val running (and (is-not log None) (= runtime.current-turn turn.turn-id) (is-not runtime.process None)
                      (.alive runtime.process)))
    (val request-id (if running (.take-request-id runtime) None))
    (val process runtime.process))
  (when (not running) (return (NoTurnInFlight :turn turn)))
  (<- line (turn-interrupt-request request-id turn.thread-id turn.turn-id))
  (.send process line)
  (InterruptRequested))


(defk answer-request [#^ CodexHost host #^ CodexAnswerRequest asked]
  {:pre [(: host CodexHost) (: asked CodexAnswerRequest)] :post [(: % (| Answered NoSuchRequest))]
   :tags {:context "codex" :role "process"}}
  "走っているターンが待つ codex からの要求に、同じ id で答えを書くため。"
  (val runtime (.runtime host asked.turn.thread-id))
  (when (is runtime None) (return (NoSuchRequest :request-id asked.request-id)))
  (with [runtime.lock]
    (val log (.running-log runtime))
    (val waiting (and (is-not log None) (= runtime.current-turn asked.turn.turn-id) (in asked.request-id log.pending)))
    (when waiting
      (setv log.pending (tuple (gfor pending log.pending :if (!= pending asked.request-id) pending))))
    (val process runtime.process))
  (when (not waiting) (return (NoSuchRequest :request-id asked.request-id)))
  (<- line (server-response-line asked.request-id asked.result))
  (.send process line)
  (Answered))


(defk page-of [#^ SessionRuntime runtime #^ CodexTurn turn #^ int after-seq]
  {:pre [(: runtime SessionRuntime) (: turn CodexTurn) (: after-seq int)] :post [(: % (| TurnEventPage None))]
   :tags {:context "codex" :role "process"}}
  "名指したターンの after-seq より後の出来事と終わりを読むため(知らないターンは None)。"
  (with [runtime.lock]
    (val log (.get runtime.turns turn.turn-id))
    (val events (if (is log None) #() (tuple (gfor event log.events :if (> event.seq after-seq) event)))))
  (if (is log None)
      None
      (TurnEventPage :events events :next-seq (if events (. (get events -1) seq) after-seq) :end log.end)))


(defk page-ready [#^ SessionRuntime runtime #^ CodexTurn turn #^ int after-seq]
  {:pre [(: runtime SessionRuntime) (: turn CodexTurn) (: after-seq int)] :post [(: % bool)] :tags {:context "codex" :role "process"}}
  "出来事の待ちの条件を読むため: 新しい出来事か終わりが在る(ターンが刈られて知らなくなったのも待ちを抜ける)。"
  (<- page (page-of runtime turn after-seq))
  (or (is page None) (bool page.events) (is-not page.end None)))


(defk read-events [#^ CodexHost host #^ CodexReadTurnEvents asked]
  {:pre [(: host CodexHost) (: asked CodexReadTurnEvents)] :post [(: % (| TurnEventPage UnknownTurn))]
   :tags {:context "codex" :role "process"}}
  "ターンの出来事を、新しい出来事か終わりが来るか上限の秒まで待って読むため。"
  (val runtime (.runtime host asked.turn.thread-id))
  (when (is runtime None) (return (UnknownTurn :turn asked.turn)))
  (<- (wait-until runtime.doorbell (fn [] (page-ready runtime asked.turn asked.after-seq)) (float asked.wait-up-to)))
  (<- page (page-of runtime asked.turn asked.after-seq))
  (if (is page None) (UnknownTurn :turn asked.turn) page))


(defk close-session [#^ CodexHost host #^ CodexCloseSession asked]
  {:pre [(: host CodexHost) (: asked CodexCloseSession)] :post [(: % (| SessionClosed ProcessStillAlive))]
   :tags {:context "codex" :role "process"}}
  "会話を閉じるため(冪等): 走っているターンを BackendLost で閉じ、process を降ろす。"
  (val runtime (.runtime host asked.thread-id))
  (when (is runtime None) (return (SessionClosed :was-running False)))
  (with [runtime.lock]
    (val log (.running-log runtime))
    (when (is-not log None)
      (setv log.end (BackendLost :detail (.format "会話を閉じた: {}" asked.reason))))
    (setv runtime.closed True))
  (.ring runtime.doorbell)
  (<- down (retire runtime))
  (when (not down)
    (return (ProcessStillAlive :detail (.format "thread {} の process が EOF・SIGTERM・SIGKILL の後も降りない({})"
                                                asked.thread-id asked.reason))))
  ;; 状態は閉じた印のまま残す — 続き(ResumeThread)は同じ状態の上で新しい process を起こし、seq と起こした数を続ける。
  (SessionClosed :was-running (is-not log None)))


(defk launch-count [#^ CodexHost host #^ str thread-id]
  {:pre [(: host CodexHost) (: thread-id str)] :post [(: % int)] :tags {:context "codex" :role "process"}}
  "会話のために起こした process の数を答えるため(検の口 — 使い回しを確かめる)。"
  (val runtime (.runtime host thread-id))
  (if (is runtime None) 0 runtime.launches))


;; --- handler -----------------------------------------------------------------------------------

;; 引数に残す理由: host は会話の process と状態の持ち主で、composition root が 1 つ作って渡す(Ask で読む設定ではなく、生きた資源の束 —
;; doeff-claude-code の claude-code-handler と同じ)。
(defhandler codex-handler [host]
  (CodexStartTurn [origin spec text]
    (<- outcome (start-turn host effect))
    (resume outcome))
  (CodexInterruptTurn [turn]
    (<- outcome (interrupt-turn host turn))
    (resume outcome))
  (CodexReadTurnEvents [turn after-seq wait-up-to]
    (<- page (read-events host effect))
    (resume page))
  (CodexAnswerRequest [turn request-id result]
    (<- outcome (answer-request host effect))
    (resume outcome))
  (CodexCloseSession [thread-id reason]
    (<- closed (close-session host effect))
    (resume closed))
  (CodexLaunchCount [thread-id]
    (<- count (launch-count host thread-id))
    (resume count)))
