;;; 本番の handler — 公開 effect(effects.hy)に、codex の app-server の子 process(process.hy の CodexProcess)で答える。
;;;
;;; 方針 = 会話(thread)の process はターンをまたいで生かし、同じ会話・同じ宣言(CodexSessionSpec)の続きはその process に turn/start を
;;; 書く。宣言が違えば降ろしてから起こし直し、新しい process で thread/resume する。process が無い続き(降りた・別の handler が始めた
;;; 会話)も新しい process で thread/resume する。起こすか使い回すかの判断は start-turn の 1 か所だけで、上の層は会話の id とターンの
;;; 参照しか持たない。
;;;
;;; 不変条件(fake と共通 — tests/test_scenarios.hy が両方に当てる):
;;;   1 つの会話に走っているターンは多くとも 1 つ・生きた process は多くとも 1 つ。CodexStartTurn 1 回に終わりはちょうど 1 つ
;;;   (lines.TurnEnded か、終わりの行の前に process が消えた BackendLost か、道具を呼べない process だった ToolsUnavailable)。
;;;   出来事の seq は会話の中で単調に増える。
;;;
;;; 道具を呼べない process: codex が code mode を使えず道具の呼びを閉じたと名乗った(lines.CodeModeUnavailable)process の model は、
;;; 道具を 1 つも呼べないまま文だけを返し、ターンは completed になる。codex はこの警告を thread ごとに 1 度だけ、ターンを始める時
;;; (turn/start の答えより前 — 走っているターンがまだ無い)に出すので、handler は警告を process の事実(SessionRuntime.tools-lost)として
;;; 持ち、そのターン(警告の後に立つか、警告の時に走っている物)を ToolsUnavailable で終えて process を降ろす。次のターンは使い回さず
;;; 新しい process で起こす(codex が道具を呼べるかを確かめ直す — 降ろし中の process の行は次のターンに積まない)。
;;;
;;; 状態は CodexHost(composition root が 1 つ作って handler に渡す)が持つ。読み手の thread(CodexProcess の on-line・on-exit)と
;;; handler の節は会話ごとの lock で状態を差し替える。待つ所(要求の答え・出来事・降りるの待ち)は呼び鈴(Doorbell — CreateExternalPromise
;;; の約束)を掛けてから条件を読み直し、WaitWithin で上限の秒まで待つ。状態を変えた側が呼び鈴を鳴らす(時間で起きて確かめない)。
;;; 手本 = doeff-claude-code の handler.hy(同じ待ち方・同じ host の持ち方)。
;;;
;;; 会話の状態は寿命の違う 2 つに分ける: 続きの記録(Continuation — thread の id・今の宣言・出来事の seq・起こした process の数。小さく、
;;; 会話の寿命の間 CodexHost が持つ。閉じた後の ResumeThread が seq と起こした数を続けるのに要る)と、生きた状態(SessionRuntime — process・
;;; 走っているターン・ターンの記録 TurnLog。重い: 道具の出力の全文を含む)。ターンの記録は読み手が終わりまで読めば要らず、閉じた会話は
;;; 読み終えた記録を外し、記録が無くなって process も降りた時に生きた状態ごと外して続きの記録だけを残す(1 会話が閉じた後も重い記録を
;;; 持ち続けると、codex の会話の数だけ host の本体の memory が重くなる — zeus の turn-host の観測)。
(require doeff-hy.macros [defhandler defk deff <- val var])
(val MODULE-TAGS {:context "codex" :role "process"})
(import collections.abc [Callable])
(import sqlite3)
(import threading)
(import doeff [run])
(import doeff_core_effects.scheduler [CreateExternalPromise ExternalPromise])
(import doeff_time [GetMonotonic WaitWithin])
(import doeff_codex.values [CodexSessionSpec CodexTurn CodexEvent CodexInput FreshThread ResumeThread])
(import doeff_codex.lines :as lines)
(import doeff_codex.limits [merged-limits usage-limit-hit-of])
(import dataclasses [replace])
(import doeff_codex.rpc [app-server-argv initialize-request initialized-notification thread-start-request thread-resume-request
                         turn-start-request turn-steer-request turn-interrupt-request server-response-line])
(import doeff_codex.process [CodexProcess CutLine EOF-GRACE-SECONDS TERM-GRACE-SECONDS LINE-CAP-BYTES])
(import doeff_codex.credential_home [open-credential-home close-credential-home credential-env state-db-claim state-db-release
                                     repoint-state-db])
(import doeff_codex.effects [CodexStartTurn CodexSteerTurn CodexInterruptTurn CodexReadTurnEvents CodexAnswerRequest
                             CodexCloseSession CodexLaunchCount BackendLost ToolsUnavailable TurnStarted Steered InterruptRequested
                             TurnEventPage Answered SessionClosed ThreadUnknown TurnInFlight LaunchFailed RequestRefused NoTurnInFlight
                             UnknownTurn NoSuchRequest ProcessStillAlive])

(val RETIRE-WAIT-SECONDS (+ EOF-GRACE-SECONDS (* 2 TERM-GRACE-SECONDS) 2.0))
;; 開いた会話ごとに残すターンの記録の数(古いターンの出来事は読めなくなる — UnknownTurn)。読み手(doeff-agents の headless_codex の pull)は
;; 走っているターンだけを名指して読み、次のターンは前のターンの終わりを読んでから始めるので、今のターンと続きの数本があればよい
;; (16 本では、道具の出力の全文を含む記録を、開いた会話ごとに 16 ターン分 host の本体が持った)。
(val KEPT-TURNS 4)
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
  "1 つのターンの出来事と終わり(まだなら end は None)。pending = 答えを待つ codex からの要求の id(ServerRequest)。limits = そのターンで
   届いた口座の枠の知らせを重ねた物(limits.hy の merged-limits の答え — 枠の名ごとに 1 つ・終わりの利用枠切れの事実の材料)。
   end-read = 読み手に終わりを頁で渡した(頁は after-seq より後の出来事を全部載せるので、終わりを渡した時点で出来事も全部渡している —
   読み手は終わりの後にそのターンを読まない)。"
  (defn __init__ [self]
    (setv #^ (get tuple #(CodexEvent ...)) self.events #())
    (setv self.end None)
    (setv #^ bool self.end-read False)
    (setv #^ tuple self.pending #())
    (setv #^ tuple self.limits #())))


(defclass Continuation []
  "会話の続きを起こすのに要る小さな記録(会話の寿命の間 CodexHost が持つ — 閉じた後も残す): thread-id = codex の thread の id
   (新しい会話の thread/start の答えまで None)/ spec = 今の宣言(同じ宣言の続きは生きた process を使い回し、違えば起こし直す)/
   next-seq = 最後に振った出来事の seq(出来事の seq は会話の中で単調に増え、process を起こし直しても・生きた状態が外れても続く)/
   launches = 会話のために起こした process の数。重いターンの記録(SessionRuntime.turns)を持たない — 寿命が違うので別の記録にしてある。"
  (defn __init__ [self #^ CodexSessionSpec spec]
    (setv self.thread-id None
          self.spec spec
          self.next-seq 0
          self.launches 0)))


(defclass SessionRuntime []
  "1 つの会話(thread)の生きた状態(handler の中だけ)。continuation = この会話の続きの記録(CodexHost が会話の寿命の間持つ物と
   同じ物 — thread の id・宣言・seq・起こした数はここにだけ在る)。generation = 今の process を
   起こした回の番号(古い process の行と終わりを、今の process の物と取り違えないため)。credential-home = 今の process の資格の家
   (借りた資格で起こした時だけ — 降ろしたら消す)。answers = 要求の id → 答えの記録(要求ごとに
   handler が待って読む — 読んだら消す)。outside = 走っているターンの外で読んだ記録(数えて見せるため・ターンの頁には載せない)。
   late = 走っているターンの無い間に届いた、終わったターンを名乗る遅れた記録(後ろで走った命令の終わりなど — 次のターンの頭に積む)。
   tools-lost = 今の process が道具を呼べないと codex が名乗った事実(lines.CodeModeUnavailable — 無ければ None。process を起こし
   直すと消す)。"
  (defn __init__ [self #^ Continuation continuation]
    (setv self.continuation continuation
          self.process None
          self.generation 0
          self.lock (threading.Lock)
          self.doorbell (Doorbell)
          self.next-request-id 0
          self.current-turn None
          self.closed False
          self.exit-code None
          self.stderr-tail None
          self.credential-home None
          self.tools-lost None)
    (setv #^ (get dict #((| int str) object)) self.answers {})
    (setv #^ (get dict #(str TurnLog)) self.turns {})
    (setv #^ tuple self.outside #())
    (setv #^ tuple self.late #()))

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

  (defn mark-running [self #^ str turn-id]
    "codex が始めたと名乗ったターンを走っているターンにするため(その記録も作る)。ターンの無い間に届いた、終わったターンを名乗る遅れた
     記録(late)は、このターンの頭に積む。読みの thread(turn/start の答えと turn/started を読んだ時)と、答えを受けた handler の側の
     両方から呼ばれ、2 回目は何も足さない。lock の中で呼ぶ。"
    (setv self.current-turn turn-id)
    (setv started (.log-of self turn-id))
    (for [late self.late]
      (.add-event self started late))
    (setv self.late #())
    None)

  (defn running-log [self]
    "走っているターンの記録(無ければ None)。lock の中で呼ぶ。"
    (setv log (if (is self.current-turn None) None (.get self.turns self.current-turn)))
    (if (and (is-not log None) (is log.end None)) log None))

  (defn #^ bool runs [self #^ str turn-id]
    "名指したターンが今走っているターンか(終わりの通知を読んだ・走っているターンが別のターンに替わったなら偽)。lock の中で呼ぶ。"
    (and (is-not (.running-log self) None) (= self.current-turn turn-id)))

  (defn add-event [self #^ TurnLog log record]
    "記録を seq を振ってターンの出来事に積むため(seq は続きの記録が数える)。lock の中で呼ぶ。"
    (setv self.continuation.next-seq (+ self.continuation.next-seq 1))
    (setv log.events (+ log.events #((CodexEvent :seq self.continuation.next-seq :record record)))))

  (defn note-read [self #^ str turn-id #^ TurnEventPage page]
    "読み手に渡した頁を、そのターンの記録に記すため(終わりを渡したら、読み手はそのターンをもう読まない)。lock の中で呼ぶ。"
    (setv log (.get self.turns turn-id))
    (when (and (is-not log None) (is-not page.end None))
      (setv log.end-read True))
    None)

  (defn drop-read [self]
    "読み手が終わりまで読んだターンの記録を外すため(読み終えていない出来事と終わりを持つ記録は残す)。lock の中で呼ぶ。"
    (for [turn-id (list self.turns)]
      (when (. (get self.turns turn-id) end-read)
        (del (get self.turns turn-id))))
    None)

  (defn #^ bool drained [self]
    "閉じて、ターンの記録がもう無く、process も降りているか(生きた状態を CodexHost から外してよいか)。lock の中で呼ぶ。"
    (and self.closed
         (not self.turns)
         (or (is self.process None) (not (.alive self.process)))))

  (defn end-toolless-turn [self]
    "道具を呼べない process(tools-lost が在る)で走っているターンを、model が文だけを返して completed になる前に ToolsUnavailable で
     終え、process を降ろし始めるため(降りるのは待たない — 読みの thread から呼ぶ。続きは start-turn が新しい process で起こす)。
     走っているターンが無い・事実が無ければ何もしない。lock の中で呼ぶ。"
    (setv log (.running-log self))
    (when (and (is-not self.tools-lost None) (is-not log None))
      (setv log.end (ToolsUnavailable :detail self.tools-lost.message))
      (when (is-not self.process None)
        (.retire self.process)))
    None))


(defclass CodexHost []
  "handler の状態: thread の id → 続きの記録 Continuation(会話の寿命の間)と thread の id → 生きた状態 SessionRuntime(閉じた会話は
   ターンの記録を読み手が読み終え、process も降りたら外れる)。command = 実行ファイルと前置きの引数(例 #(\"codex\"))・launch-timeout =
   起動と要求の答えを待つ上限(秒)・line-cap = app-server の stdout の 1 行の読む上限(bytes — 超えた行は頭だけを分ける。process.hy)。
   lock の順は CodexHost → 会話(release だけが両方を取る)。"
  (defn __init__ [self #^ tuple command [launch-timeout 120.0] [line-cap LINE-CAP-BYTES]]
    (when (not command)
      (raise (ValueError "CodexHost.command は空でない tuple")))
    (setv self.command command
          self.launch-timeout (float launch-timeout)
          self.line-cap (int line-cap)
          self.lock (threading.Lock))
    (setv #^ (get dict #(str Continuation)) self.continuations {})
    (setv #^ (get dict #(str SessionRuntime)) self.runtimes {}))

  (defn runtime [self #^ str thread-id]
    "会話の生きた状態を引くため(知らなければ、または閉じて外れていれば None)。"
    (with [self.lock] (.get self.runtimes thread-id)))

  (defn continuation [self #^ str thread-id]
    "会話の続きの記録を引くため(知らなければ None — 閉じて生きた状態が外れた会話も持つ)。"
    (with [self.lock] (.get self.continuations thread-id)))

  (defn register [self #^ SessionRuntime runtime]
    "thread の id が決まった会話の続きの記録と生きた状態を覚えるため。"
    (with [self.lock]
      (setv thread-id runtime.continuation.thread-id)
      (setv (get self.continuations thread-id) runtime.continuation)
      (setv (get self.runtimes thread-id) runtime)))

  (defn #^ bool release [self #^ SessionRuntime runtime]
    "閉じて、ターンの記録がもう無く、process も降りた会話の生きた状態を外すため(続きの記録は残す)。閉じた時に世代を進めてあるので
     (close-session)、外した状態へ古い process の行が届いても積まれず、続きの記録の seq も進まない。答え = 外したか。"
    (with [self.lock]
      (with [runtime.lock]
        (setv thread-id runtime.continuation.thread-id)
        (setv released (and (is (.get self.runtimes thread-id) runtime) (.drained runtime)))
        (when released
          (del (get self.runtimes thread-id)))))
    released))


;; --- 読み手の thread からの呼び(lock の中で状態を進める) --------------------------------------------

(defk another-thread [#^ SessionRuntime runtime record]
  {:pre [(: runtime SessionRuntime) (: record lines.CodexLine)] :post [(: % bool)] :tags {:context "codex" :role "process"}}
  "記録が会話の thread でない thread(子の agent)を名乗るかを答えるため(ターンを名乗る記録だけが thread を名乗る。会話の thread は
   turn/start を書く前に決まる — handler の start-turn)。"
  (val thread-id runtime.continuation.thread-id)
  (and (isinstance record lines.TurnRecord) (is-not thread-id None) (!= record.thread-id thread-id)))

(defk absorb [#^ SessionRuntime runtime record]
  {:pre [(: runtime SessionRuntime) (: record lines.CodexLine)] :post [(: % None)] :tags {:context "codex" :role "process"}}
  "分けた記録を会話の状態へ積むため: 要求の答えは answers へ・ターンを名乗る記録(lines.TurnRecord — どの記録が名乗るかは lines が
   決める)は名乗ったターンの出来事へ(終わりは end へ — 終わりには、そのターンで重ねた口座の枠の知らせから作った利用枠切れの事実
   UsageLimitHit を載せる)・codex からの要求は走っているターンへ(答えを待つ id として)・口座の枠の窓を運ぶ記録(lines.LimitRecord —
   どの記録が運ぶかは lines が決める・ターンを名乗らない)は走っているターンの枠に重ねて積む(ターンをまたいで持ち越さない)・ほかは
   走っているターンへ(無ければ outside)。turn/start の答え(ターンの id を名乗る)と
   turn/started は、読んだ時点で走っているターンを立てる(mark-running)— 答えを待つ側が起きる前に読んだ、ターンを名乗らない行も
   そのターンへ積むため(設計検証 lt-4FZFGSA5AJ7DGD7BZ4656WY6HS で再現した競合)。終わったターンを名乗る遅れた記録(後ろで走った命令の
   終わり — 前のターンの終わりの後に、次のターンの間かターンの無い間に届く)は、上の層が終わったターンをもう読まないので、走っている
   ターンへ積み、無ければ late に取っておく(次のターンを立てた時にその頭に積む)。会話の thread でない thread を名乗る記録(子の
   agent — collabAgentToolCall の spawnAgent が同じ process の別の thread で走らせる。録った行 other-tools)は会話のターンに積まず
   outside へ置く(子のターンの始まりで走っているターンを替えない・子の答えを会話の答えにしない)。code mode が使えない警告
   (lines.CodeModeUnavailable — ターンを名乗らない)は process の事実 tools-lost にし、その時に走っているターンか、後で立つターンを
   ToolsUnavailable で終えて process を降ろし始める(end-toolless-turn — 頭の註の「道具を呼べない process」。警告は thread を名乗るが、
   code mode の使える・使えないは process の置き方で決まるので、子の agent の thread の警告も同じ事実にする)。降ろし中の process の、
   終えたターンを名乗る行は late に取っておかない(次の process のターンに積まない)。lock の中で呼ぶ。"
  (<- foreign (another-thread runtime record))
  (match record
    _ :if foreign (setv runtime.outside (+ runtime.outside #(record)))
    (lines.Response) (do (setv (get runtime.answers record.id) record)
                         (when (is-not record.turn-id None)
                           (.mark-running runtime record.turn-id)
                           (.end-toolless-turn runtime)))
    (lines.ErrorResponse) (setv (get runtime.answers record.id) record)
    (lines.TurnStarted) (do (.mark-running runtime record.turn-id)
                            (.add-event runtime (.log-of runtime record.turn-id) record)
                            (.end-toolless-turn runtime))
    (lines.CodeModeUnavailable)
    (do (setv runtime.tools-lost record)
        (val warned-in (.running-log runtime))
        (if (is warned-in None)
            (setv runtime.outside (+ runtime.outside #(record)))
            (.add-event runtime warned-in record))
        (.end-toolless-turn runtime))
    (lines.ServerRequest)
    (do (setv log (.running-log runtime))
        (if (is log None)
            (setv runtime.outside (+ runtime.outside #(record)))
            (do (setv log.pending (+ log.pending #(record.id)))
                (.add-event runtime log record))))
    (lines.TurnEnded)
    (do (setv log (.log-of runtime record.turn-id))
        (<- hit (usage-limit-hit-of record log.limits))
        (setv ended (replace record :usage-limit hit))
        (.add-event runtime log ended)
        (when (is log.end None) (setv log.end ended)))
    _ (cond
        (isinstance record lines.LimitRecord)
        (do (setv log (.running-log runtime))
            (if (is log None)
                (setv runtime.outside (+ runtime.outside #(record)))
                (do (<- limits (merged-limits log.limits record))
                    (setv log.limits limits)
                    (.add-event runtime log record))))
        (isinstance record lines.TurnRecord)
        (do (val named (.log-of runtime record.turn-id))
            (val running (.running-log runtime))
            (cond
              (is named.end None) (.add-event runtime named record)
              (is-not running None) (.add-event runtime running record)
              (is-not runtime.tools-lost None) (setv runtime.outside (+ runtime.outside #(record)))
              True (setv runtime.late (+ runtime.late #(record)))))
        True
        (do (val current (.running-log runtime))
            (if (is current None)
                (setv runtime.outside (+ runtime.outside #(record)))
                (.add-event runtime current record))))))


(deff on-line [#^ SessionRuntime runtime #^ int generation raw]  ; defk にできない: CodexProcess の読み手の thread が呼ぶ callback
  {:pre [(: runtime SessionRuntime) (: generation int) (: raw (| str CutLine))] :post [(: % None)]}
  "stdout の 1 行を分けて会話の状態へ積み、呼び鈴を鳴らすため(今の process の行だけ — 降ろし中の古い process の行は捨てる)。読む上限を
   超えた行(CutLine)は頭だけを分ける(thread/resume の答えの履歴を本体に載せない — card ki-83f24000d86c)。"
  (setv record (run (match raw
                      (CutLine) (lines.classify-cut-line raw.head raw.size)
                      _ (lines.classify-line raw))))
  (with [runtime.lock]
    (when (= runtime.generation generation)
      (run (absorb runtime record))))
  (.ring runtime.doorbell))


(deff on-exit [#^ SessionRuntime runtime #^ int generation home exit-code #^ str stderr-tail]  ; defk にできない: 読み手の thread が呼ぶ callback
  {:pre [(: runtime SessionRuntime) (: generation int) (: home (| str None)) (: exit-code (| int None)) (: stderr-tail str)]
   :post [(: % None)]}
  "process の終わりを記し、終わりの行の無い走っているターンを BackendLost で閉じるため(今の process の終わりだけ)。その process の
   資格の家(home — 借りた資格で起こした時だけ)は、世代によらず消す(家はその process だけの物)。"
  (run (close-credential-home home))
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

(val EXIT-RECORD-SECONDS 5.0)  ; 読み手の thread が stdout の EOF の後に stderr の thread を待つ上限(process.hy の 2.0 秒)より長く

(defk exit-recorded [#^ SessionRuntime runtime]
  {:pre [(: runtime SessionRuntime)] :post [(: % bool)] :tags {:context "codex" :role "process"}}
  "降りた process の終わりの code と stderr の末尾(on-exit)が記されたかを読むため(code は信号でも None にならない)。"
  (with [runtime.lock]
    (val recorded (is-not runtime.exit-code None)))
  recorded)


(defk launch-failure [#^ SessionRuntime runtime #^ str stage]
  {:pre [(: runtime SessionRuntime) (: stage str)] :post [(: % LaunchFailed)] :tags {:context "codex" :role "process"}}
  "要求の答えを得られなかった時の失敗を、降りた process の終了の code と stderr の末尾つきで作るため。process が降りた直後は
   on-exit(読み手の thread が stdout の EOF と stderr の読み終わりの後に呼ぶ)がまだ code と stderr を記していないので、
   記されるまで(上限つき)待ってから読む。"
  (with [runtime.lock]
    (val alive (and (is-not runtime.process None) (.alive runtime.process))))
  (when (not alive)
    (<- (wait-until runtime.doorbell (fn [] (exit-recorded runtime)) EXIT-RECORD-SECONDS)))
  (with [runtime.lock]
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


;; codex 0.162.1 が turn/steer を「名指したターンは走っていない」と断る時の誤りの code(JSON-RPC の invalid request)と文の頭。codex の
;; binary の文字列と、偽の上流でターンの終わりと steer を競わせた実測で確かめた: 「走っているターンが無い」= no active turn to steer・
;; 「別のターンが走っている」= expected active turn id `…` but found `…`。足せない種類のターンへの断り(cannot steer a review turn・
;; cannot steer a compact turn)と空の入力の断りは、ターンが走っているので含めない。
(val NOT-RUNNING-CODE -32600)
(val NOT-RUNNING-WORDS #("no active turn to steer" "expected active turn id"))


(defk steer-refused-as-not-running [#^ SessionRuntime runtime #^ CodexTurn turn #^ lines.ErrorResponse answer]
  {:pre [(: runtime SessionRuntime) (: turn CodexTurn) (: answer lines.ErrorResponse)] :post [(: % bool)]
   :tags {:context "codex" :role "process"}}
  "turn/steer への codex の断りが、名指したターンがもう走っていない事を言うかを読むため。ターンの終わりと steer の行き違いで codex が
   断る時、断りの答えは終わりの通知(turn/completed)より先にも後にも届く(codex 0.162.1 の実測 — 終わりの通知が 5〜200 ms 遅れる
   順も在る)ので、2 つで読む: 断りの文が「走っているターンが無い・別のターンが走っている」を名乗る(終わりの通知より先に届いた断りも
   これで読める)か、断りを読んだ後の状態でそのターンの終わりを読んだ・走っているターンが別のターンに替わった。それ以外の断り(足せない
   種類のターン・空の入力など)は、走っていない事にしない。"
  (when (and (= answer.code NOT-RUNNING-CODE)
             (any (gfor words NOT-RUNNING-WORDS (.startswith answer.message words))))
    (return True))
  (with [runtime.lock]
    (val running (.runs runtime turn.turn-id)))
  (not running))


;; --- 起こす・降ろす -----------------------------------------------------------------------------

(defk retire [#^ SessionRuntime runtime]
  {:pre [(: runtime SessionRuntime)] :post [(: % bool)] :tags {:context "codex" :role "process"}}
  "今の process を降ろし、降りるまで(上限まで)待つため。降りたらその process の資格の家を消す(終わりの callback より先に消して、
   降ろした後に家が残らない)。答え = 降りたか(process が無ければ真)。"
  (val process runtime.process)
  (when (or (is process None) (not (.alive process)))
    (<- (close-credential-home runtime.credential-home))
    (return True))
  (.retire process)
  (<- (wait-until runtime.doorbell (fn [] (process-down process)) RETIRE-WAIT-SECONDS))
  (val down (not (.alive process)))
  (when down
    (<- (close-credential-home runtime.credential-home)))
  down)


(defclass StateDbHandoff []
  "DB を作る起動の lock を、lock を待つ thread から起動の手へ渡す受け渡し。手が上限で去った後に取れた lock は、thread が返す(誰も
   持たない lock を残さない)。thread と手の両方から触るので自分の lock を持つ。"
  (defn __init__ [self]
    (setv self.lock (threading.Lock)
          self.mut-taken None
          self.mut-left False))

  (defn offer [self claim]
    "待つ thread が取れた答え(True か lock)を置くため。答え = 手が受け取るか(去った後なら偽 — thread が lock を返す)。"
    (with [self.lock]
      (when self.mut-left (return False))
      (setv self.mut-taken claim)
      True))

  (defn leave [self]
    "手が待ちを終えるため。答え = 置かれた答え(まだ無ければ None — 以後の答えは thread が返す)。"
    (with [self.lock]
      (setv self.mut-left True)
      self.mut-taken)))


(deff take-state-db-lock [#^ str base #^ StateDbHandoff handoff bell]  ; defk にできない: 待つ thread の入口
  {:pre [(: base str) (: handoff StateDbHandoff) (: bell ExternalPromise)] :post [(: % None)]}
  "DB を作る起動の lock を取れるまで待ち、受け渡しに置いて呼び鈴を鳴らすため(手が去っていれば lock を返す)。"
  (setv claim (run (state-db-claim base True)))
  (when (not (.offer handoff claim))
    (run (state-db-release claim)))
  (.complete bell True))


(defk claim-state-db-within [#^ str base #^ float seconds]
  {:pre [(: base str) (: seconds float)] :post [(: % (| int bool))] :tags {:context "codex" :role "process"}}
  "状態の DB を作る起動の lock を、上限 seconds 秒まで待って取るため(credential_home.hy の頭の註 — DB が無い間は DB を作る起動を
   1 本ずつにする)。ほかの起動が持っていれば、待つ thread が lock を取って呼び鈴を鳴らすまで待つ。答え = state-db-claim の答え
   (DB が在れば True・取れた lock・上限まで取れなければ False)。"
  (<- claim (state-db-claim base False))
  (when (is-not claim False) (return claim))
  (val handoff (StateDbHandoff))
  (<- bell (CreateExternalPromise))
  (.start (threading.Thread :target (fn [] (take-state-db-lock base handoff bell)) :daemon True))
  (<- (WaitWithin bell.future seconds :park True))
  (val taken (.leave handoff))
  (if (is taken None) False taken))


(deff repoint-in-background [#^ str base bell]  ; defk にできない: 書き直しの thread の入口
  {:pre [(: base str) (: bell ExternalPromise)] :post [(: % None)]}
  "状態の DB の家を通る rollout の path を書き直し、終わったら(書き直せなかった時も)呼び鈴を鳴らすため。書き直せなかった時は、
   続きの要求に codex が答える(行が消えた家を指せば codex が断る — 断りが会話の失敗になる)。"
  (try
    (run (repoint-state-db base))
    (except [[sqlite3.Error OSError]]
      None)
    (finally
      (.complete bell True)))
  None)


(defk start-repoint [#^ str base]
  {:pre [(: base str)] :post [(: % ExternalPromise)] :tags {:context "codex" :role "process"}}
  "状態の DB の家を通る rollout の path の書き直し(credential_home.hy の repoint-state-db)を、手の外の thread で始めるため。
   書き直しは sqlite への書き込みで、disk が混む間は数十秒かかり、手で書くとその間 turn-host の全部の会話が止まる(配備の後の
   最初の起動で 59 秒)。答え = 書き直しが終わると鳴る呼び鈴(待つのは続きの要求の前の、その会話の手だけ — launch)。"
  (<- bell (CreateExternalPromise))
  (.start (threading.Thread :target (fn [] (repoint-in-background base bell)) :daemon True))
  bell)


(defk launch [#^ CodexHost host #^ SessionRuntime runtime #^ CodexSessionSpec spec origin]
  {:pre [(: host CodexHost) (: runtime SessionRuntime) (: spec CodexSessionSpec) (: origin (| FreshThread ResumeThread))]
   :post [(: % (| str ThreadUnknown LaunchFailed RequestRefused))]
   :tags {:context "codex" :role "process"}}
  "新しい process を起こし、初期化して thread を開くため(新しい会話は thread/start・続きは thread/resume)。答え = thread の id か失敗
   (失敗した process は降ろす)。宣言の家が借りた資格(auth-json)を持てば、その process だけの資格の家を env の CODEX_HOME の下に作って
   CODEX_HOME をそこへ向け、process が降りたら消す(credential_home.hy)。その process の状態の DB は家の外の元の CODEX_HOME に置き
   (CODEX_SQLITE_HOME)、DB がまだ無い間は、DB を作る起動(initialize の答えまで)を lock で 1 本ずつにする。DB の家を通る rollout の
   path の書き直しは手の外の thread で走らせ(start-repoint)、続き(thread/resume)の要求の前だけ、上限まで終わりを待つ。"
  (val base (.get spec.home.env "CODEX_HOME"))
  (when (and (is-not spec.home.auth-json None) (not base))
    (return (LaunchFailed :detail "借りた資格を置く元の CODEX_HOME が宣言の env に無い")))
  (<- argv (app-server-argv host.command spec.disabled-features))
  (with [runtime.lock]
    (setv runtime.generation (+ runtime.generation 1)
          runtime.continuation.launches (+ runtime.continuation.launches 1)
          runtime.continuation.spec spec
          runtime.exit-code None
          runtime.stderr-tail None
          runtime.tools-lost None)
    (val generation runtime.generation))
  (var home None)
  (var repointing None)
  (when (is-not spec.home.auth-json None)
    (<- opened (open-credential-home base spec.home.auth-json))
    (:= home opened)
    (<- bell (start-repoint base))
    (:= repointing bell))
  (with [runtime.lock]
    (setv runtime.credential-home home))
  (var claim None)
  (when (is-not home None)
    (<- claimed (claim-state-db-within base host.launch-timeout))
    (when (is claimed False)
      (<- (close-credential-home home))
      (return (LaunchFailed :detail "状態の DB を作るほかの起動が、起動の待ちの上限まで終わらない")))
    (:= claim claimed))
  (var env (dict spec.home.env))
  (when (is-not home None)
    (<- lent-env (credential-env spec.home.env base home))
    (:= env lent-env))
  (var process None)
  (try
    (:= process (CodexProcess argv spec.cwd env
                              (fn [raw] (on-line runtime generation raw))
                              (fn [code tail] (on-exit runtime generation home code tail))
                              (fn [] (.ring runtime.doorbell))
                              :line-cap host.line-cap))
    (except [OSError]
      ;; 起こせなかった(実行ファイルが無いなど)— on-exit は呼ばれないので、lock を返し、作った家をここで消してから上げる。
      (<- (state-db-release claim))
      (<- (close-credential-home home))
      (raise)))
  (with [runtime.lock]
    (setv runtime.process process))
  (<- greeted (request runtime "initialize" (fn [request-id] (initialize-request request-id CLIENT-NAME CLIENT-VERSION))
                       host.launch-timeout))
  ;; initialize に答えた codex は状態の DB を作り終えている(答えなければ降ろす)— どちらでも DB を作る起動の lock を返す。
  (<- (state-db-release claim))
  (when (not (isinstance greeted lines.Response))
    (<- (retire runtime))
    (return (if (isinstance greeted lines.ErrorResponse) (refused "initialize" greeted) greeted)))
  (<- ready-line (initialized-notification))
  (.send process ready-line)
  ;; 続きの thread の行が消えた家の path を指したままだと codex が thread/resume を断るので、書き直しの終わりを待つ(park — 待つ間も
  ;; ほかの会話の手は進む)。上限を過ぎたら待たずに要求を書き、codex の答えに任せる。新しい会話は書き直しを待たない。
  (when (and (isinstance origin ResumeThread) (is-not repointing None))
    (<- (WaitWithin repointing.future host.launch-timeout :park True)))
  (<- opened (match origin
               (FreshThread)
               (request runtime "thread/start"
                        (fn [request-id] (thread-start-request request-id spec.cwd :approval-policy spec.approval-policy
                                                               :sandbox spec.sandbox :model spec.model
                                                               :auto-compact-token-limit spec.auto-compact-token-limit))
                        host.launch-timeout)
               (ResumeThread)
               (request runtime "thread/resume"
                        (fn [request-id] (thread-resume-request request-id origin.thread-id :cwd spec.cwd
                                                                :approval-policy spec.approval-policy :sandbox spec.sandbox
                                                                :model spec.model
                                                                :auto-compact-token-limit spec.auto-compact-token-limit))
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

(defk image-urls-of [#^ CodexInput input]
  {:pre [(: input CodexInput)] :post [(: % tuple)] :tags {:context "codex" :role "foundation"}}
  "入力に添えた画像を、ターンの入力の image の url(data URL)へ写すため。"
  (tuple (gfor image input.images (.format "data:{};base64,{}" image.mime image.data-base64))))


(defk start-turn [#^ CodexHost host #^ CodexStartTurn asked]
  {:pre [(: host CodexHost) (: asked CodexStartTurn)] :post [(: % (| TurnStarted ThreadUnknown TurnInFlight LaunchFailed RequestRefused))]
   :tags {:context "codex" :role "process"}}
  "ターンを始めるため: 同じ会話・同じ宣言の生きた process が在れば使い回し(道具を呼べないと名乗った process は使い回さない)、無ければ
   起こして thread を開いてから turn/start を書く。"
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
  ;; 生きた状態が外れた閉じた会話の続きは、続きの記録(seq と起こした数)の上に新しい状態を作る。
  (val continued (match origin
                   (ResumeThread) (.continuation host origin.thread-id)
                   _ None))
  (val runtime (if (is known None) (SessionRuntime (if (is continued None) (Continuation asked.spec) continued)) known))
  (val reusable (and (is-not known None) (not known.closed) (is-not known.process None) (.alive known.process)
                     (= known.continuation.spec asked.spec) (is known.tools-lost None)))
  (when (not reusable)
    (<- (retire runtime))
    (<- opened (launch host runtime asked.spec origin))
    (when (not (isinstance opened str)) (return opened))
    (with [runtime.lock]
      (setv runtime.continuation.thread-id opened
            runtime.closed False))
    (.register host runtime))
  (<- urls (image-urls-of asked.input))
  (<- answer (request runtime "turn/start"
                      (fn [request-id] (turn-start-request request-id runtime.continuation.thread-id asked.input.text urls
                                                                       :effort asked.spec.effort))
                      host.launch-timeout))
  (match answer
    (lines.Response)
    (if (is answer.turn-id None)
        (LaunchFailed :detail "turn/start の答えがターンの id を名乗らない")
        ;; 走っているターンは、読みの thread がこの答えを読んだ時に立てた(absorb の mark-running — 遅れた記録もその時に頭に積んだ)。
        (TurnStarted :turn (CodexTurn :thread-id runtime.continuation.thread-id :turn-id answer.turn-id)))
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


(defk steer-turn [#^ CodexHost host #^ CodexSteerTurn asked]
  {:pre [(: host CodexHost) (: asked CodexSteerTurn)] :post [(: % (| Steered NoTurnInFlight RequestRefused LaunchFailed))]
   :tags {:context "codex" :role "process"}}
  "走っているターンに turn/steer を書き、codex が受けたかの答えを待つため(受けた入力はターンの次の区切りで読まれる)。codex が、名指した
   ターンはもう走っていないと断れば(ターンの終わりと steer の行き違い — steer-refused-as-not-running)、断りでなく NoTurnInFlight で
   答える(上の層は入力を次のターンへ回す)。"
  (val turn asked.turn)
  (val runtime (.runtime host turn.thread-id))
  (when (is runtime None) (return (NoTurnInFlight :turn turn)))
  (with [runtime.lock]
    (val log (.running-log runtime))
    (val running (and (is-not log None) (= runtime.current-turn turn.turn-id) (is-not runtime.process None)
                      (.alive runtime.process))))
  (when (not running) (return (NoTurnInFlight :turn turn)))
  (<- urls (image-urls-of asked.input))
  (<- answer (request runtime "turn/steer"
                      (fn [request-id] (turn-steer-request request-id turn.thread-id turn.turn-id asked.input.text urls))
                      host.launch-timeout))
  (match answer
    (lines.Response) (Steered)
    (lines.ErrorResponse) (do (<- gone (steer-refused-as-not-running runtime turn answer))
                              (if gone
                                  (NoTurnInFlight :turn turn)
                                  (do (<- refusal (refused "turn/steer" answer)) refusal)))
    _ answer))


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


(defk shed [#^ CodexHost host #^ SessionRuntime runtime]
  {:pre [(: host CodexHost) (: runtime SessionRuntime)] :post [(: % None)] :tags {:context "codex" :role "process"}}
  "閉じた会話の、読み手が終わりまで読んだターンの記録を外し、記録が無くなって process も降りていれば生きた状態ごと CodexHost から
   外すため(続きの記録 Continuation は残る)。読み終えていない出来事と終わりを持つ記録は、読み手が読むまで残す。"
  (with [runtime.lock]
    (.drop-read runtime))
  (.release host runtime)
  None)


(defk settle-read [#^ CodexHost host #^ SessionRuntime runtime #^ CodexTurn turn #^ TurnEventPage page]
  {:pre [(: host CodexHost) (: runtime SessionRuntime) (: turn CodexTurn) (: page TurnEventPage)] :post [(: % None)]
   :tags {:context "codex" :role "process"}}
  "読み手に渡した頁を記し、閉じた会話なら読み終えたターンの記録を外すため(開いた会話は続きのターンのために記録を持つ)。"
  (with [runtime.lock]
    (.note-read runtime turn.turn-id page)
    (val closed runtime.closed))
  (when closed
    (<- (shed host runtime)))
  None)


(defk read-events [#^ CodexHost host #^ CodexReadTurnEvents asked]
  {:pre [(: host CodexHost) (: asked CodexReadTurnEvents)] :post [(: % (| TurnEventPage UnknownTurn))]
   :tags {:context "codex" :role "process"}}
  "ターンの出来事を、新しい出来事か終わりが来るか上限の秒まで待って読むため。閉じた会話の終わりを読み手に渡したら、その
   ターンの記録は外れる(読み手は終わりの後にそのターンを読まない)。"
  (val runtime (.runtime host asked.turn.thread-id))
  (when (is runtime None) (return (UnknownTurn :turn asked.turn)))
  (<- (wait-until runtime.doorbell (fn [] (page-ready runtime asked.turn asked.after-seq)) (float asked.wait-up-to)))
  (<- page (page-of runtime asked.turn asked.after-seq))
  (when (is page None) (return (UnknownTurn :turn asked.turn)))
  (<- (settle-read host runtime asked.turn page))
  page)


(defk close-session [#^ CodexHost host #^ CodexCloseSession asked]
  {:pre [(: host CodexHost) (: asked CodexCloseSession)] :post [(: % (| SessionClosed ProcessStillAlive))]
   :tags {:context "codex" :role "process"}}
  "会話を閉じるため(冪等): 走っているターンを BackendLost で閉じ、process を降ろす。読み手が終わりまで読んだターンの記録は外し、
   読み手がまだ読んでいない終わりを持つ記録だけ残す(読み手が読んだ時に外れる)。"
  (val runtime (.runtime host asked.thread-id))
  (when (is runtime None) (return (SessionClosed :was-running False)))
  (with [runtime.lock]
    (val log (.running-log runtime))
    (when (is-not log None)
      (setv log.end (BackendLost :detail (.format "会話を閉じた: {}" asked.reason))))
    ;; 世代を進めて、降ろし中の process の行をここから捨てる: 読み手が終わりを読んで外したターンの記録を、降りる前の最後の行が
    ;; 作り直さない(作り直されると閉じた会話が外れず、続きの記録の seq も古い状態から進む)。
    (setv runtime.generation (+ runtime.generation 1)
          runtime.closed True))
  (.ring runtime.doorbell)
  (<- down (retire runtime))
  (when (not down)
    (return (ProcessStillAlive :detail (.format "thread {} の process が EOF・SIGTERM・SIGKILL の後も降りない({})"
                                                asked.thread-id asked.reason))))
  ;; 閉じた会話が持つのは続きの記録だけにする — 続き(ResumeThread)は続きの記録の上で新しい process を起こし、seq と起こした数を続ける。
  (<- (shed host runtime))
  (SessionClosed :was-running (is-not log None)))


(defk launch-count [#^ CodexHost host #^ str thread-id]
  {:pre [(: host CodexHost) (: thread-id str)] :post [(: % int)] :tags {:context "codex" :role "process"}}
  "会話のために起こした process の数を答えるため(検の口 — 使い回しを確かめる。閉じて生きた状態が外れた会話も続きの記録が数える)。"
  (val continuation (.continuation host thread-id))
  (if (is continuation None) 0 continuation.launches))


;; --- handler -----------------------------------------------------------------------------------

;; 引数に残す理由: host は会話の process と状態の持ち主で、composition root が 1 つ作って渡す(Ask で読む設定ではなく、生きた資源の束 —
;; doeff-claude-code の claude-code-handler と同じ)。
(defhandler codex-handler [host]
  (CodexStartTurn [origin spec input]
    (<- outcome (start-turn host effect))
    (resume outcome))
  (CodexSteerTurn [turn input]
    (<- outcome (steer-turn host effect))
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
