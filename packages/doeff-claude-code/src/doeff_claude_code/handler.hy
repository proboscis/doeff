;;; 本番の handler — 公開 effect 9 つ(とテスト用の effect ClaudeDropProcess)に、claude の print mode の子 process で答える。
;;;
;;; 方針 = 会話の process は手番をまたいで生かし、次の手番(同じ会話の続き)の入力をその process へ書く(#3672 — 起こし直しと記録の
;;; 読み直しの 1.6〜2.6 秒を消す)。使い回すのは、起こした時の条件の鍵(argv.hy の launch-key — argv・cwd・env の指紋)が同じ時だけ。
;;; 違えば降ろしてから `--resume <id>` の新しい process を起こす。手番の外で出力した process は降ろす(dialogue.hy の on-record —
;;; #517 の事故の形の守り)。起動するか使い回すかの判断は decision.start-decision の 1 か所だけで、上の層は会話の id とターンの参照
;;; しか持たない。ClaudeWarmSession は同じ判断で、入力の前に process を起動して待たせる(ターンは開かない — 最初のターンがその process
;;; を使い回す。起動してから入力を受けられるまでの秒を入力の前に済ませる)。起動前の準備(待っている process の停止・停止の完了待ち・
;;; 冷えた続きの前の命令・本数の空き)はターンと事前起動で同じ prepare-launch を通る。
;;;
;;; 不変条件(fake と共通 — tests/test_scenarios.hy が両方に当てる):
;;;   1 つの会話に走っている手番は多くとも 1 つ・生きた process は多くとも 1 つ(降りる途中の process は待ってから起こす)。
;;;   ClaudeStartTurn 1 回に終わりはちょうど 1 つ。行の seq は会話の中で単調増加。
;;;
;;; 状態は ClaudeCodeHost(composition root が 1 つ作って handler に渡す)が持つ。読み手の thread と handler の節は
;;; 会話ごとの lock で状態機械(dialogue.hy)の値を差し替える。待つ所(init・出来事・降りるの待ち・本数の空き)は呼び鈴
;;; (Doorbell — CreateExternalPromise の約束)を掛けてから条件を読み直し、WaitWithin で上限の秒まで待つ。状態を変えた側(読み手の
;;; thread・process の終わり・handler の節)が呼び鈴を鳴らす(時間で起きて確かめない — 行が来てから待ちが抜けるまでに刻みの遅れが無い)。
;;;
;;; 計時の行(#3605): 送ってから CLI が答え始めるまでの秒を分けて測るため、手番の process を起こした所・init の行を受けた
;;; 所・init の後の最初の行を上の層へ初めて渡した所で、slog を 1 行ずつ出す。欄は名と壁の時刻(GetTime の epoch ミリ秒)と経過のミリ秒
;;; (GetMonotonic の差)だけで、本文・資格・env・argv は載せない。外側に時間の handler に加えて slog の答え手(本番 = doeff_core_effects の
;;; slog-handler・検 = slog-discard-handler)が要る。手番の終わりを上の層へ初めて渡した所でも 1 行出し(#3628)、その手番で CLI から
;;; 受けた本文の差分の行(text_delta)の数を載せる — 手番の文の途中が画面に出なかった時に、CLI が差分を出さなかったのか、出したが上で
;;; 運ばれなかったのかを分けるため(数だけで、差分の本文は載せない)。同じ行に考えている間の差分(thinking_delta)と道具の命令の差分
;;; (input_json_delta)の行の数と最初の刻も載せる(#3746 (a) — 差分の種類は lines.hy の DeltaKind)。本文の最初の差分を上の層へ初めて渡した所でも 1 行出し(#3696)、
;;; init の後の最初の stream の行(実物では message_start — init の次の行は hook の知らせなどで、それではない)の刻と、そこから最初の差分
;;; までの ms を載せる — 入力ごとの hook と API へ出すまで・モデルが考えた秒・最初の文字が画面に出るまでの起点を割るため。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass replace])
(import datetime [datetime])
(import os)
(import os.path)
(import pathlib [Path])
(import signal)
(import threading)
(import uuid)
(import doeff_core_effects.effects [slog])
(import doeff_core_effects.scheduler [CreateExternalPromise ExternalPromise])
(import doeff_time [GetMonotonic GetTime WaitWithin])
(import doeff_claude_code.values [ClaudeTurn ClaudeHome ClaudeSessionSpec TurnInput FreshSession ResumeSession ForkSession
                                  LinkFromHome Rebuilt IMAGE-MIMES])
(import doeff_claude_code.lines [ClaudeStreamLine Completed Failed Interrupted BackendLost Init PartialMessage DeltaKind parse-record
                                 classify-record recorded-cost])
(import doeff_claude_code.effects [ClaudeStartTurn ClaudeInjectInput ClaudeInterruptTurn ClaudeReadTurnEvents
                                   ClaudeAnswerPermission ClaudeCloseSession ClaudeSessionStatus ClaudeExportSession
                                   TurnStarted InputQueued InterruptRequested TurnEventPage Answered SessionClosed
                                   SessionStatus SessionExported SessionNotFound Idle TurnRunning Closed TranscriptPresent TranscriptAbsent
                                   CarryRefused LaunchFailed AttachmentRefused NoTurnInFlight UnknownTurn NoSuchRequest
                                   ProcessStillAlive ClaudeWarmSession SessionWarmed])
(import json)
(import doeff_claude_code.faults [ClaudeDropProcess ClaudeLiveProcess ClaudeEmitOutsideTurn LiveProcess NoLiveProcess StopReason])
(import doeff_claude_code.dialogue :as dialogue)
(import doeff_claude_code.dialogue [DialogueState])
(import doeff_claude_code.decision [SessionView Refuse Reuse Launch start-decision credential-due])
(import doeff_claude_code.argv [transcript-dir transcript-path launch-argv launch-key cold-resume-argv process-env])
(import doeff_claude_code.process [ClaudeProcess EOF-GRACE-SECONDS TERM-GRACE-SECONDS])

(setv KEPT-TURNS 16)
(setv RETIRE-WAIT-SECONDS (+ EOF-GRACE-SECONDS (* 2 TERM-GRACE-SECONDS) 2.0))
(setv COLD-RESUME-TIMEOUT-SECONDS 600.0)
;; 計時の行の名(頭の註 — #3605)。行を拾う時はこの名と欄 event で引く。
(val CLI-TIMING-LOG "claude CLI の起動の計時")


;; --- 状態 ---------------------------------------------------------------------------------------

(defclass Doorbell []
  "状態が変わるのを待つ手(wait-until)が掛けた呼び鈴(CreateExternalPromise の約束)を集め、状態を変えた側が全部鳴らすため。読み手の
   thread と handler の節の両方から触るので自分の lock を持つ。呼び鈴は object そのものを持つ(id で覚えない)。鳴らすのは
   ExternalPromise.complete だけ(thread をまたいでよい唯一の口・2 度目の完了は無視される)。"
  (defn __init__ [self]
    (setv self.lock (threading.Lock))
    (setv #^ (get tuple #((get ExternalPromise bool) ...)) self.bells #()))

  (defn #^ tuple hang [self bell]
    "呼び鈴を掛ける。答え = 掛けた先の組(wait-until が外す先)。"
    (with [self.lock]
      (setv self.bells (+ self.bells #(bell))))
    #(self))

  (defn unhang [self bell]
    "鳴らずに起きた呼び鈴を外す。"
    (with [self.lock]
      (setv self.bells (tuple (gfor other self.bells :if (is-not other bell) other)))))

  (defn ring [self]
    "掛かっている呼び鈴を全部鳴らして外す(答え = WaitWithin の期限の None と分ける True)。"
    (with [self.lock]
      (setv bells self.bells)
      (setv self.bells #()))
    (for [bell bells]
      (.complete bell True))))

(defclass TurnLog []
  "1 つの手番の行と終わり(まだ終わっていなければ end は None)。launched-at = この手番で process を起こし始めた刻(GetMonotonic の
   読み — process を起こさずに続いた手番は None)・launched-wall = 同じ刻の壁の時刻(GetTime の読み — 読み手の thread が行に刻む at と
   比べる)・reply-noted = init の後の最初の行の計時の行を出したか・text-noted = 本文の最初の差分の計時の行を出したか・end-noted = 手番の
   終わりの計時の行を出したか(頭の註の計時の行)。"
  (defn __init__ [self]
    (setv #^ (get list ClaudeStreamLine) self.lines [])
    (setv #^ (| Completed Failed Interrupted BackendLost None) self.end None)
    (setv #^ (| float None) self.launched-at None)
    (setv #^ (| datetime None) self.launched-wall None)
    (setv #^ bool self.reply-noted False)
    (setv #^ bool self.text-noted False)
    (setv #^ bool self.end-noted False)))

(defclass Binding []
  "1 つの process と、その process が今走らせている手番の番号(生き残った入力の手番へ進む)。
   process は起こすまで None。"
  (defn __init__ [self #^ int turn-seq]
    (setv #^ (| ClaudeProcess None) self.process None)
    (setv #^ int self.turn-seq turn-seq)))

(defn #^ ClaudeProcess process-of [#^ Binding binding]
  "binding の process。起こす前に読むのは handler の実装の誤り(RuntimeError)。"
  (setv process binding.process)
  (when (is process None)
    (raise (RuntimeError (.format "手番 {} の process をまだ起こしていない" binding.turn-seq))))
  process)

(defclass SessionRuntime []
  "1 つの会話の状態(handler の中だけ)。session-id は ForkSession の init を読むまで空。
   cost-mark = 最初の process の手番の額の起点(CLI の累積の額がどこから数えるか — 新しい会話は 0・知らない続きは None)。"
  (defn __init__ [self #^ str session-id #^ ClaudeHome home #^ str canonical-cwd #^ (| float None) [cost-mark None]]
    (setv self.session-id session-id
          self.home home
          self.canonical-cwd canonical-cwd
          self.lock (threading.Lock)
          self.state (DialogueState :session-id session-id :cost-mark cost-mark :start-mark cost-mark)
          self.current-seq 0
          self.next-line-seq 0
          self.init-seen False
          self.closed False
          ;; doorbell = この会話の状態(手番の行と終わり・init・process の生き死に)を待つ手の呼び鈴。
          self.doorbell (Doorbell))
    ;; launches = この会話で手番のために起こした process の数・stopped-because = 最後の process を降ろした訳(検の口 ClaudeLiveProcess が
    ;; 読む — 会話ごとに process を生かしたまま待たせる形の使い回しと守りを確かめるため・#3672)・launch-key = 今の process の起こした時の
    ;; 条件の鍵(argv.hy の launch-key — 次の手番で使い回してよいかを決める)。
    (setv #^ int self.launches 0)
    (setv #^ (| StopReason None) self.stopped-because None)
    (setv #^ (| str None) self.launch-key None)
    ;; last-used = 最後に手番を始めた刻(GetMonotonic の読み — 上限の本数で一番長く使われていない物を選ぶ・D2)・retire-after = 今の
    ;; process を止める刻(最後に起動した・再利用したターンの spec の credential-usable-until と同じ刻・epoch 秒 — 知らなければ None・
    ;; D2・#3753 (c))。
    (setv #^ (| float None) self.last-used None)
    (setv #^ (| float None) self.retire-after None)
    ;; warmed-fresh = 新しい会話として入力なしで事前起動し(ClaudeWarmSession)、まだターンを始めていない(decision.SessionView の同じ
    ;; 名前の欄 — 最初のターンは同じ id の FreshSession。ターンを始めると消える)。
    (setv #^ bool self.warmed-fresh False)
    (setv #^ (| Binding None) self.binding None)
    (setv #^ (get dict #(int TurnLog)) self.turns {}))

  (defn [property] #^ (| ClaudeProcess None) process [self]
    (if (is self.binding None) None self.binding.process))

  (defn #^ ClaudeProcess bound-process [self]
    "今の binding の process。binding も process も無いのは handler の実装の誤り(RuntimeError)。"
    (when (is self.binding None)
      (raise (RuntimeError (.format "会話 {} に process の binding が無い" self.session-id))))
    (process-of self.binding))

  (defn #^ (| TurnLog None) current-log [self]
    (.get self.turns self.current-seq))

  (defn #^ TurnLog open-log [self]
    "今の手番の TurnLog。手番を開く前に読むのは handler の実装の誤り(RuntimeError)。"
    (setv log (.current-log self))
    (when (is log None)
      (raise (RuntimeError (.format "会話 {} の手番 {} の記録が無い" self.session-id self.current-seq))))
    log)

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
   刻む関数(clock.clock-of)・live-limit = 同時に生かす process の本数の上限(走っている手番の process も数える — 機体の memory の
   予算 ÷ 1 本の memory。#3672 の D2)・launch-timeout = init の行を待つ上限と、上限の本数に空きを待つ上限(秒)。上限は呼び手の
   宣言から受ける(既定を持たない)。資格で process を止める刻は host が持たず、ターンの宣言(ClaudeSessionSpec の
   credential-usable-until — 借り手が余裕の秒を引いた後の刻)を何も足し引きせずに使う(#3753 (c))。"
  (defn __init__ [self #^ tuple command clock #^ int live-limit [launch-timeout 120.0]]
    ;; 数の型は注記が持つ。bool は int の子なので名指しで断り、値の範囲を断る。
    (when (or (isinstance live-limit bool) (< live-limit 1))
      (raise (ValueError (.format "ClaudeCodeHost.live_limit は 1 以上の整数: {!r}" live-limit))))
    (setv self.command command
          self.clock clock
          self.live-limit live-limit
          self.launch-timeout (float launch-timeout)
          self.lock (threading.Lock)
          ;; doorbell = 会話の組が変わる(足す・忘れる)のを待つ手の呼び鈴(本数の空きの待ち — hang-on-all)。
          self.doorbell (Doorbell))
    (setv #^ (get dict #(str SessionRuntime)) self.runtimes {}))

  (defn runtime [self #^ str session-id]
    (with [self.lock] (.get self.runtimes session-id)))

  (defn register [self #^ SessionRuntime runtime]
    (with [self.lock] (setv (get self.runtimes runtime.session-id) runtime))
    (.ring self.doorbell))

  (defn forget [self #^ str session-id #^ SessionRuntime runtime]
    (with [self.lock]
      (when (is (.get self.runtimes session-id) runtime)
        (del (get self.runtimes session-id))))
    (.ring self.doorbell))

  (defn #^ tuple hang-on-all [self bell]
    "本数の空きを待つ手の呼び鈴を、host と今の全部の会話に掛けるため(どの会話の手番の終わり・process の終わりでも、会話の組が
     変わっても鳴る)。host に先に掛けてから会話の組を読む — 読んだ後に足された会話は host の呼び鈴が知らせる。答え = 掛けた先の組。"
    (.hang self.doorbell bell)
    (with [self.lock]
      (setv runtimes (tuple (.values self.runtimes))))
    (for [runtime runtimes]
      (.hang runtime.doorbell bell))
    (+ #(self.doorbell) (tuple (gfor runtime runtimes runtime.doorbell)))))


;; --- 読み手の thread からの呼び(lock の中で状態機械を進める) ------------------------------------------

(defn apply-transition [#^ SessionRuntime runtime #^ Binding binding transition]
  "遷移の答えを運ぶ: 状態を差し替え、stdin へ書き、SIGINT を送り、手番の終わりを記し、降ろす訳が在れば記して process を降ろし始め、
   会話の呼び鈴を鳴らす(待つ手が読み直す)。runtime.lock の中で呼ぶ。"
  (setv runtime.state transition.state)
  (for [line transition.sends] (.send (process-of binding) line))
  (when transition.signal (.interrupt (process-of binding)))
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
  (when (is-not transition.retire None)
    (setv runtime.stopped-because transition.retire)
    (.retire (process-of binding)))
  (.ring runtime.doorbell))

(defn on-line [#^ SessionRuntime runtime #^ Binding binding #^ (get Callable #([] datetime)) clock #^ str raw]
  "stdout の 1 行を、今の process の行なら手番の記録に足して状態機械を進めるため。host の手番の外の行(前の process の行・手番の
   外の出力)は手番の記録に足さない — 終わった手番の頁に誰の物でもない行を混ぜない(手番の外の出力は状態機械が降ろす訳に変える)。"
  (setv record (parse-record raw))
  (when (is record None) (return None))
  (setv kind (classify-record record))
  (setv #^ datetime at (clock))
  (with [runtime.lock]
    (when (is-not runtime.binding binding) (return None))
    (setv log (.get runtime.turns binding.turn-seq))
    (when (and (is-not log None) runtime.state.in-flight)
      (.append log.lines (ClaudeStreamLine :seq runtime.next-line-seq :at at :kind kind :raw (.rstrip raw "\n")))
      (+= runtime.next-line-seq 1))
    (setv transition (dialogue.on-record runtime.state kind))
    (apply-transition runtime binding transition)
    ;; ターンの終わりで、行の時刻が資格の止める刻以上(ちょうども)なら止める(D2 — 残った入力のターンが続く時は終わりではない)。
    (when (and (is-not transition.end None) (not transition.continues) (is transition.retire None)
               (credential-due runtime.retire-after (.timestamp at)))
      (setv runtime.stopped-because StopReason.CREDENTIAL-FLOOR)
      (.retire (process-of binding)))))

(defn on-exit [#^ SessionRuntime runtime #^ Binding binding exit-code #^ str stderr-tail]
  (with [runtime.lock]
    (when (is runtime.binding binding)
      (apply-transition runtime binding (dialogue.on-exit runtime.state exit-code stderr-tail)))))


;; --- I/O の小さな道具(handler の中だけ) --------------------------------------------------------------

(defn #^ bool file-present [#^ str path] (os.path.exists path))

(defk recorded-cost-mark [#^ ClaudeHome home #^ str canonical-cwd #^ str session-id]
  {:pre [(: home ClaudeHome) (: canonical-cwd str) (: session-id str)] :post [(: % (| float None))]
   :tags {:context "claude-code" :role "foundation"}}
  "手番の額の起点を、この handler が前の process を見ていない会話(新しい host での続き・持ち込んだ transcript・枝の親)でも知るため、
   CLI が数え始める額 = その会話の transcript の最後の cost-state の額を読む(置き場は transcript-present と同じ transcript-path)。
   file が無い・読めない・行が無い・値が数でなければ None。"
  (val path (transcript-path home.config-dir canonical-cwd session-id))
  (val text (try
              (.read-text (Path path) :encoding "utf-8")
              (except [#(OSError UnicodeDecodeError)] None)))
  (when (is text None) (return None))
  (<- mark (recorded-cost text))
  mark)

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
  "起こすか使い回すかの判断(decision.start-decision)へ渡す、会話の今の観測。生きて降りる途中でなく手番を走らせていない process が
   在れば、その起こした時の条件の鍵(idle-key)。"
  (if (is runtime None)
      (SessionView)
      (with [runtime.lock]
        (setv process runtime.process)
        (setv running (.running-turn runtime))
        (if (or (is process None) (not (.alive process)))
            (SessionView :known True :running-turn running :warmed-fresh runtime.warmed-fresh)
            (SessionView :known True
                         :running-turn running
                         :retiring (is-not process.retiring None)
                         :idle-key (if (and (is process.retiring None) (is running None)) runtime.launch-key None)
                         :warmed-fresh runtime.warmed-fresh)))))

(defn refused-attachment [#^ TurnInput input]
  (setv refused (lfor item input.attachments :if (not-in item.mime IMAGE-MIMES) item.mime))
  (if refused (AttachmentRefused (get refused 0)) None))


;; --- 待ち(呼び鈴で起きる) --------------------------------------------------------------------

(defk wait-until [hang ready #^ float seconds]
  {:pre [(: hang Callable) (: ready Callable) (: seconds float)] :post [(: % bool)]
   :tags {:context "claude-code" :role "foundation"}}
  "ready() が真になるまで、状態を変えた側が鳴らす呼び鈴で起きて待つため(上限 seconds 秒 — 時間で起きて確かめない)。hang(bell) =
   呼び鈴を状態の持ち主の Doorbell に掛け、掛けた先の組を答える関数。取りこぼさない順: 呼び鈴を掛けてから ready を読み直す(読んだ
   直後・掛ける前に状態が変わっても、掛けた後の読み直しが見る。掛けた後の変化は呼び鈴が鳴る)。鳴った・期限が来た・読み直しで真の
   どれでも、呼び鈴を外して約束を閉じる(鳴らずに起きた呼び鈴を残さない)。答え = 真になったか。"
  (<- started (GetMonotonic))
  (when (ready) (return True))
  (while True
    (<- bell (CreateExternalPromise))
    (val hung (hang bell))
    (val now-ready (ready))
    (<- now (GetMonotonic))
    (val left (- seconds (- now started)))
    (when (and (not now-ready) (> left 0))
      (<- (WaitWithin bell.future left :park True)))
    (for [doorbell hung]
      (.unhang doorbell bell))
    (.complete bell True)
    (when now-ready (return True))
    (when (<= left 0) (return False))))

(defk run-cold-resume [#^ ClaudeCodeHost host #^ ClaudeSessionSpec spec #^ str session-id]
  {:pre [(: host ClaudeCodeHost) (: spec ClaudeSessionSpec) (: session-id str)] :post [(: % bool)]}
  "冷えた続きの前の 1 回きりの命令を走らせて終わりを待つ。答え = 走り終えたか(失敗しても手番は起こす — 最適化の命令)。"
  (val done (threading.Event))
  (val doorbell (Doorbell))
  (try
    (setv process (ClaudeProcess (cold-resume-argv host.command spec session-id) spec.cwd (process-env spec.home)
                                 (fn [raw] None) (fn [code tail] (.set done) (.ring doorbell)) (fn [] (.ring doorbell))))
    (except [OSError] (return False)))
  (.close-stdin process)
  (<- finished (wait-until (fn [bell] (.hang doorbell bell)) (fn [] (.is-set done)) COLD-RESUME-TIMEOUT-SECONDS))
  (when (not finished) (.drop process))
  finished)


;; --- 計時の行(頭の註 — #3605) --------------------------------------------------------------

(defk epoch-ms-of [at]
  {:pre [(: at (| datetime None))] :post [(: % (| int None))] :tags {:context "claude-code" :role "foundation"}}
  "計時の行の壁の時刻の欄を、他の process の行と並べられる epoch ミリ秒の整数で綴るため(時刻が無ければ None)。"
  (if (is at None) None (round (* (.timestamp at) 1000))))

(defk elapsed-ms [since until]
  {:pre [(: since (| float None)) (: until float)] :post [(: % (| int None))] :tags {:context "claude-code" :role "foundation"}}
  "GetMonotonic の 2 つの読みの差を、計時の行の経過の欄のミリ秒の整数で綴るため(起点が無ければ None)。"
  (if (is since None) None (round (* (- until since) 1000))))

(defk note-spawned [runtime turn-seq origin requested launching launching-wall]
  {:pre [(: runtime SessionRuntime) (: turn-seq int) (: origin (| FreshSession ResumeSession ForkSession)) (: requested float)
         (: launching float) (: launching-wall datetime)]
   :post [(: % None)] :tags {:context "claude-code" :role "foundation"}}
  "手番の process を起こした刻を手番の記録に置き(init・最初の行・最初の差分の行の経過の起点)、起こした所の計時の行を出すため。
   requested = 手番を頼まれた刻・launching = process を起こし始めた刻(どちらも GetMonotonic の読み)・launching-wall = launching と
   同じ刻の GetTime の読み。before-spawn-ms = 頼まれてから起こし始めるまで(降りるのの待ち・冷えた続きの前の命令・transcript の読みを
   含む)・spawn-ms = 起こすのに掛かった時間。"
  (<- launched (GetMonotonic))
  (<- at (GetTime))
  (with [runtime.lock]
    (val log (.get runtime.turns turn-seq))
    (when (is-not log None)
      (setv log.launched-at launching)
      (setv log.launched-wall launching-wall)))
  (<- wall-ms (epoch-ms-of at))
  (<- before-spawn-ms (elapsed-ms requested launching))
  (<- spawn-ms (elapsed-ms launching launched))
  (<- (slog CLI-TIMING-LOG :level "info" :event "spawned" :wall-ms wall-ms :before-spawn-ms before-spawn-ms :spawn-ms spawn-ms
            :session-id runtime.session-id :turn-seq turn-seq :origin (. (type origin) __name__)))
  None)

(defk note-reused [runtime turn-seq requested writing writing-wall]
  {:pre [(: runtime SessionRuntime) (: turn-seq int) (: requested float) (: writing float) (: writing-wall datetime)]
   :post [(: % None)] :tags {:context "claude-code" :role "foundation"}}
  "生きた process を使い回した手番の、入力を書いた所の計時の行を出すため(起こした所の行 spawned の代わり — #3672。その手番の
   最初の行・終わりの経過の起点は入力を書いた刻)。before-write-ms = 頼まれてから書き始めるまで。"
  (<- wall-ms (epoch-ms-of writing-wall))
  (<- before-write-ms (elapsed-ms requested writing))
  (<- (slog CLI-TIMING-LOG :level "info" :event "reused" :wall-ms wall-ms :before-write-ms before-write-ms
            :session-id runtime.session-id :turn-seq turn-seq))
  None)

(defk note-init [runtime turn-seq outcome]
  {:pre [(: runtime SessionRuntime) (: turn-seq int) (: outcome (| TurnStarted LaunchFailed))] :post [(: % None)]
   :tags {:context "claude-code" :role "foundation"}}
  "init の行を受けた(か受けずに起動を諦めた)所の計時の行を出すため。since-launch-ms = process を起こし始めてから待ちが init を見るまで
   (待ちは読み手の thread が init の行で鳴らす呼び鈴で起きるので、刻みの遅れは無い)・line-at-ms = 読み手の thread が init の行を読んだ壁の時刻(無ければ None)。"
  (<- now (GetMonotonic))
  (<- at (GetTime))
  (val log (with [runtime.lock] (.get runtime.turns turn-seq)))
  (val launched (if (is log None) None log.launched-at))
  (val init-at (if (is log None)
                   None
                   (with [runtime.lock] (next (gfor line log.lines :if (isinstance line.kind Init) line.at) None))))
  (<- wall-ms (epoch-ms-of at))
  (<- since-launch-ms (elapsed-ms launched now))
  (<- line-at-ms (epoch-ms-of init-at))
  (<- (slog CLI-TIMING-LOG :level "info" :event (if (isinstance outcome TurnStarted) "init" "launch-failed") :wall-ms wall-ms
            :since-launch-ms since-launch-ms :line-at-ms line-at-ms :session-id runtime.session-id :turn-seq turn-seq))
  None)

(defk claimed-first-reply [runtime log next-seq]
  {:pre [(: runtime SessionRuntime) (: log TurnLog) (: next-seq int)] :post [(: % (| ClaudeStreamLine None))]
   :tags {:context "claude-code" :role "foundation"}}
  "process を起こした手番の、init の行の次の行(CLI が答え始めた印)が上の層へ渡す頁の範囲(next-seq まで)に初めて入った時に、その行を
   1 度だけ取り出すため(取り出したら手番の記録に印を付け、2 度目からは None)。"
  (with [runtime.lock]
    (when (or (is log.launched-at None) log.reply-noted)
      (return None))
    (val lines (tuple log.lines))
    (val init-index (next (gfor #(index line) (enumerate lines) :if (isinstance line.kind Init) index) None))
    (when (or (is init-index None) (>= (+ init-index 1) (len lines)))
      (return None))
    (val reply (get lines (+ init-index 1)))
    (when (> reply.seq next-seq)
      (return None))
    (setv log.reply-noted True)
    reply))

(defk note-first-reply [runtime turn page]
  {:pre [(: runtime SessionRuntime) (: turn ClaudeTurn) (: page TurnEventPage)] :post [(: % None)]
   :tags {:context "claude-code" :role "foundation"}}
  "init の後の最初の行を上の層へ初めて渡した所の計時の行を出すため(process を起こした手番で 1 度だけ)。since-launch-ms = process を
   起こし始めてから渡すまで・line-at-ms = 読み手の thread がその行を読んだ壁の時刻・kind = その行の種類の名(中身は載せない)。"
  (val log (with [runtime.lock] (.get runtime.turns turn.turn-seq)))
  (when (is log None)
    (return None))
  (<- reply (claimed-first-reply runtime log page.next-seq))
  (when (is reply None)
    (return None))
  (<- now (GetMonotonic))
  (<- at (GetTime))
  (<- wall-ms (epoch-ms-of at))
  (<- since-launch-ms (elapsed-ms log.launched-at now))
  (<- line-at-ms (epoch-ms-of reply.at))
  (<- (slog CLI-TIMING-LOG :level "info" :event "first-reply" :wall-ms wall-ms :since-launch-ms since-launch-ms
            :line-at-ms line-at-ms :kind (. (type reply.kind) __name__) :session-id turn.session-id :turn-seq turn.turn-seq))
  None)

(defrecord FirstTextLines
  "本文の最初の差分の計時の行の材料: text = 本文の最初の差分の行・stream-start = init の後の最初の stream の行(PartialMessage — 実物では
   message_start = モデルが答え始めた刻・無ければ None)。2 つの行を読み手の thread が読んだ刻の差がモデルの考えた秒になる。init の
   次の行(first-reply の行)は hook の知らせなど API へ出す前の行なので起点にしない(#3696 の直し — cluster の task の log の実測で kind
   = Other・init の 7〜8 ms 後だった)。"
  (#^ ClaudeStreamLine text)
  (#^ (| ClaudeStreamLine None) stream-start))

(defk claimed-first-text [runtime log next-seq]
  {:pre [(: runtime SessionRuntime) (: log TurnLog) (: next-seq int)]
   :post [(: % (| FirstTextLines None))]
   :tags {:context "claude-code" :role "foundation"}}
  "手番の本文の最初の差分の行(text_delta の本文を持つ PartialMessage)が、上の層へ渡す頁の範囲(next-seq まで)に初めて入った時に、
   その行と、init の後の最初の stream の行の組を 1 度だけ取り出すため(取り出したら手番の記録に印を付け、2 度目からは None)。起点の
   刻の無い手番(launched-at が None)は取り出さない。"
  (with [runtime.lock]
    (when (or (is log.launched-at None) log.text-noted)
      (return None))
    (val lines (tuple log.lines))
    (val text (next (gfor line lines :if (and (isinstance line.kind PartialMessage) line.kind.text-delta) line) None))
    (when (or (is text None) (> text.seq next-seq))
      (return None))
    (val init-index (next (gfor #(index line) (enumerate lines) :if (isinstance line.kind Init) index) None))
    (val stream-start (if (is init-index None)
                          None
                          (next (gfor line (cut lines (+ init-index 1) None) :if (isinstance line.kind PartialMessage) line) None)))
    (setv log.text-noted True)
    (FirstTextLines :text text :stream-start stream-start)))

(defk note-first-text [runtime turn page]
  {:pre [(: runtime SessionRuntime) (: turn ClaudeTurn) (: page TurnEventPage)] :post [(: % None)]
   :tags {:context "claude-code" :role "foundation"}}
  "本文の最初の差分を上の層へ初めて渡した所の計時の行を出すため(手番ごとに 1 度だけ・起こした手番と使い回しの手番の両方 — #3696)。
   since-launch-ms = 手番の起点(process を起こし始めた刻か、使い回しなら入力を書いた刻)から渡すまで・line-at-ms = 読み手の thread が
   その差分の行を読んだ壁の時刻・stream-start-at-ms = init の後の最初の stream の行(実物では message_start)を読んだ壁の時刻(first-reply
   の line-at-ms との差 = 入力ごとの hook と API へ出して最初の 1 バイトまで)・after-stream-start-ms = その行からその差分の行を読むまで
   (モデルが考えた秒)。stream の行が無ければ後の 2 つは None。差分の本文は載せない。"
  (val log (with [runtime.lock] (.get runtime.turns turn.turn-seq)))
  (when (is log None)
    (return None))
  (<- claimed (claimed-first-text runtime log page.next-seq))
  (when (is claimed None)
    (return None))
  (<- now (GetMonotonic))
  (<- at (GetTime))
  (<- wall-ms (epoch-ms-of at))
  (<- since-launch-ms (elapsed-ms log.launched-at now))
  (<- line-at-ms (epoch-ms-of claimed.text.at))
  (val stream-start claimed.stream-start)
  (<- stream-start-at-ms (if (is stream-start None) None (epoch-ms-of stream-start.at)))
  (<- after-stream-start-ms (wall-elapsed-ms (if (is stream-start None) None stream-start.at) claimed.text.at))
  (<- (slog CLI-TIMING-LOG :level "info" :event "first-text" :wall-ms wall-ms :since-launch-ms since-launch-ms
            :line-at-ms line-at-ms :stream-start-at-ms stream-start-at-ms :after-stream-start-ms after-stream-start-ms
            :session-id turn.session-id :turn-seq turn.turn-seq))
  None)

(defk wall-elapsed-ms [since until]
  {:pre [(: since (| datetime None)) (: until (| datetime None))] :post [(: % (| int None))]
   :tags {:context "claude-code" :role "foundation"}}
  "GetTime の読み(手番の記録の起こし始めの壁の時刻)と、読み手の thread が行に刻んだ壁の時刻の差を、計時の行の経過の欄のミリ秒の
   整数で綴るため(どちらかが無ければ None)。epoch の秒で引くので、時差の付き方が違う 2 つの読みでも例外にしない。"
  (if (or (is since None) (is until None)) None (round (* (- (.timestamp until) (.timestamp since)) 1000))))

(defk claimed-turn-end [runtime log]
  {:pre [(: runtime SessionRuntime) (: log TurnLog)] :post [(: % (| (get tuple #(ClaudeStreamLine ...)) None))]
   :tags {:context "claude-code" :role "foundation"}}
  "手番の終わりを上の層へ初めて渡す時に、その手番で CLI から受けた差分の行(PartialMessage — 種類は DeltaKind)を受けた順に 1 度だけ
   取り出すため(取り出したら手番の記録に印を付け、2 度目からは None)。手番の記録は手番ごとなので、生きた process を使い回す手番でも
   前の手番の行は入らない。"
  (with [runtime.lock]
    (when log.end-noted
      (return None))
    (setv log.end-noted True)
    (tuple (gfor line log.lines :if (isinstance line.kind PartialMessage) line))))

(defk note-turn-end [runtime turn page]
  {:pre [(: runtime SessionRuntime) (: turn ClaudeTurn) (: page TurnEventPage)] :post [(: % None)]
   :tags {:context "claude-code" :role "foundation"}}
  "手番の終わりを上の層へ初めて渡した所の計時の行を出すため(どの手番でも 1 度だけ — #3628)。partial-lines = その手番で CLI から
   受けた本文の差分の行の数(手番の文の途中が画面に出なかった時に、CLI が差分を出さなかったのか、出したが上で運ばれなかったのかを
   分ける)・first-partial-since-launch-ms = process を起こし始めてから読み手の thread が最初の差分の行を読むまで(差分が無い・
   process を起こさずに続いた手番は None)・since-launch-ms = process を起こし始めてから終わりを渡すまで。差分の本文は載せない。
   thinking-deltas・tool-input-deltas = その手番で受けた考えている間の差分(thinking_delta)と道具の命令の差分(input_json_delta)の行の
   数・first-thinking-since-launch-ms・first-tool-input-since-launch-ms = それぞれの最初の行を読むまで(差分の無い手番は欄を出さない)—
   答えの前に考えていた間と、道具の命令を書いていた間を、本文の差分の無い間から分けるため(#3746 (a))。使い回した process の手番の
   起点は入力を書いた刻。"
  (when (is page.end None)
    (return None))
  (val log (with [runtime.lock] (.get runtime.turns turn.turn-seq)))
  (when (is log None)
    (return None))
  (<- deltas (claimed-turn-end runtime log))
  (when (is deltas None)
    (return None))
  (val partials (tuple (gfor line deltas :if (and (= line.kind.delta DeltaKind.TEXT) line.kind.text-delta) line)))
  (val thinking (tuple (gfor line deltas :if (= line.kind.delta DeltaKind.THINKING) line)))
  (val tool-input (tuple (gfor line deltas :if (= line.kind.delta DeltaKind.TOOL-INPUT) line)))
  (<- now (GetMonotonic))
  (<- at (GetTime))
  (<- wall-ms (epoch-ms-of at))
  (<- since-launch-ms (elapsed-ms log.launched-at now))
  (<- first-partial-since-launch-ms (wall-elapsed-ms log.launched-wall (if partials (. (get partials 0) at) None)))
  (<- first-thinking-since-launch-ms (wall-elapsed-ms log.launched-wall (if thinking (. (get thinking 0) at) None)))
  (<- first-tool-input-since-launch-ms (wall-elapsed-ms log.launched-wall (if tool-input (. (get tool-input 0) at) None)))
  ;; 最初の刻の欄は差分が在る時だけ出す(本文の差分の欄 first_partial_since_launch_ms は今までどおり None でも出す)。
  (val firsts (dfor #(name ms) [#("first_thinking_since_launch_ms" first-thinking-since-launch-ms)
                                #("first_tool_input_since_launch_ms" first-tool-input-since-launch-ms)]
                    :if (is-not ms None)
                    name ms))
  (<- (slog CLI-TIMING-LOG :level "info" :event "turn-end" :wall-ms wall-ms :since-launch-ms since-launch-ms
            :partial-lines (len partials) :first-partial-since-launch-ms first-partial-since-launch-ms
            :thinking-deltas (len thinking) :tool-input-deltas (len tool-input)
            :session-id turn.session-id :turn-seq turn.turn-seq #** firsts))
  None)


;; --- 節の中身 -----------------------------------------------------------------------------------

(defk launch-cost-mark [origin #^ (| float None) recorded #^ bool had-previous #^ (| int None) previous-exit
                        #^ (| float None) cost-mark #^ (| float None) start-mark]
  {:pre [(: origin (| FreshSession ResumeSession ForkSession)) (: recorded (| float None)) (: had-previous bool)
         (: previous-exit (| int None)) (: cost-mark (| float None)) (: start-mark (| float None))]
   :post [(: % (| float None))] :tags {:context "claude-code" :role "judgment"}}
  "新しく起動する process の CLI が数え始める額(ターンの費用の起点)を、ターンの process と入力なしで起動する process の両方で 1 か所
   で決めるため。CLI は終了する時に会話の累積の額を transcript に記録し、--resume・--fork-session の process は最後に記録した額から
   数え続ける(実測 2.1.283・#883 — ターンごとには記録せず、終了する時に 1 回)。新しい会話(FreshSession)は 0(何も使っていない —
   入力の前に停止した事前起動の process の後も 0)。ほかは recorded(transcript の最後の cost-state の額 — CLI が数え始める額そのもの)
   が在ればそれ。無ければ前の process の終わり方で決める: 前の process が無い・終了コード 0 = 今の起点(cost-mark)/ SIGKILL(額を
   記録できずに消えた — CLI は前の process が始まった時の額から数える)= 前の process の始まりの起点(start-mark)/ ほか(期限の
   SIGTERM・誤りの終了)= 分からないので None(そのターンの費用は None。次のターンからは読んだ行の累積で数え直す)。"
  (match origin
    (FreshSession) 0.0
    _ (cond
        (is-not recorded None) recorded
        (or (not had-previous) (= previous-exit 0)) cost-mark
        (= previous-exit (- signal.SIGKILL)) start-mark
        True None)))

(defk launch-mark-of [#^ SessionRuntime runtime origin #^ (| float None) recorded]
  {:pre [(: runtime SessionRuntime) (: origin (| FreshSession ResumeSession ForkSession)) (: recorded (| float None))]
   :post [(: % (| float None))] :tags {:context "claude-code" :role "foundation"}}
  "会話の前の process の終わり方と今の状態機械の起点を読み、新しく起動する process の費用の起点(launch-cost-mark)を出すため。"
  (val previous (with [runtime.lock] runtime.process))
  (val state (with [runtime.lock] runtime.state))
  (<- mark (launch-cost-mark origin recorded (is-not previous None) (if (is previous None) None (.exit-code previous))
                             state.cost-mark state.start-mark))
  mark)

(defk spawn-turn [#^ ClaudeCodeHost host #^ SessionRuntime runtime #^ ClaudeSessionSpec spec origin #^ TurnInput input
                  #^ (| float None) recorded #^ str key]
  {:pre [(: host ClaudeCodeHost) (: runtime SessionRuntime) (: spec ClaudeSessionSpec)
         (: origin (| FreshSession ResumeSession ForkSession)) (: input TurnInput) (: recorded (| float None)) (: key str)]
   :post [(: % (| int LaunchFailed))] :tags {:context "claude-code" :role "foundation"}}
  "ターンの process を起動して入力を書くため。key = この process の起動条件のキー(次のターンの使い回しの判断が読む)。
   結果 = ターンの番号か LaunchFailed(実行ファイルが無い等)。新しい process の状態機械へ引き継ぐのは会話の id と費用の起点
   (launch-mark-of)だけ。ターンを始めたので、入力なしで事前起動した新しい会話のマーク(warmed-fresh)は消す。"
  (<- mark (launch-mark-of runtime origin recorded))
  (with [runtime.lock]
    (setv runtime.closed False
          runtime.init-seen False
          runtime.warmed-fresh False
          runtime.state (DialogueState :session-id runtime.session-id :cost-mark mark :start-mark mark))
    (val turn-seq (.open-turn runtime))
    (val binding (Binding turn-seq))
    (setv runtime.binding binding)
    (val transition (dialogue.begin-turn runtime.state input))
    (try
      (setv binding.process
            (ClaudeProcess (launch-argv host.command spec origin) spec.cwd (process-env spec.home)
                           (fn [raw] (on-line runtime binding host.clock raw))
                           (fn [code tail] (on-exit runtime binding code tail))
                           (fn [] (.ring runtime.doorbell))))
      (setv runtime.launches (+ runtime.launches 1))
      (setv runtime.launch-key key)
      (setv runtime.retire-after spec.credential-usable-until)
      (except [error OSError]
        (setv runtime.binding None)
        (setv (. (.open-log runtime) end) (Interrupted :process-kept False))
        (.ring runtime.doorbell)
        (return (LaunchFailed :stderr-tail (str error)))))
    (apply-transition runtime binding transition)
    turn-seq))

(defk spawn-idle [#^ ClaudeCodeHost host #^ SessionRuntime runtime #^ ClaudeSessionSpec spec origin #^ (| float None) recorded
                  #^ str key]
  {:pre [(: host ClaudeCodeHost) (: runtime SessionRuntime) (: spec ClaudeSessionSpec) (: origin (| FreshSession ResumeSession))
         (: recorded (| float None)) (: key str)]
   :post [(: % (| LaunchFailed None))] :tags {:context "claude-code" :role "foundation"}}
  "会話の process を、入力を書かずに起動して待たせるため(ClaudeWarmSession — ターンは開かない)。状態機械は最初の入力を待つマーク
   (awaiting-first-input)つきで新しくする — 最初の入力までは SessionStart の hook の行だけをターンの外の出力と数えない(dialogue.hy)。
   stdout を読む thread が読んだ行はターンの記録に入らない(ターンが無い)。費用の起点・起動条件のキー・資格の止める刻はターンの process
   と同じ規則。新しい会話なら warmed-fresh のマークを付ける(最初のターンは同じ id の FreshSession)。結果 = None(起動した)か
   LaunchFailed(実行ファイルが無い等)。"
  (<- mark (launch-mark-of runtime origin recorded))
  (with [runtime.lock]
    (setv runtime.closed False
          runtime.init-seen False
          runtime.warmed-fresh (isinstance origin FreshSession)
          runtime.state (DialogueState :session-id runtime.session-id :cost-mark mark :start-mark mark
                                       :awaiting-first-input True))
    (val binding (Binding runtime.current-seq))
    (setv runtime.binding binding)
    (try
      (setv binding.process
            (ClaudeProcess (launch-argv host.command spec origin) spec.cwd (process-env spec.home)
                           (fn [raw] (on-line runtime binding host.clock raw))
                           (fn [code tail] (on-exit runtime binding code tail))
                           (fn [] (.ring runtime.doorbell))))
      (setv runtime.launches (+ runtime.launches 1))
      (setv runtime.launch-key key)
      (setv runtime.retire-after spec.credential-usable-until)
      (except [error OSError]
        (setv runtime.binding None)
        (return (LaunchFailed :stderr-tail (str error))))))
  None)

(defk await-init [#^ ClaudeCodeHost host #^ SessionRuntime runtime #^ int turn-seq #^ bool fresh-runtime]
  {:pre [(: host ClaudeCodeHost) (: runtime SessionRuntime) (: turn-seq int) (: fresh-runtime bool)]
   :post [(: % (| TurnStarted LaunchFailed))]}
  "init の行(か process の終わり)まで待つ。init の前に終了した・期限を過ぎた = LaunchFailed(fresh-runtime = ターンを 1 度も始めて
   いない会話 — 新しく作った・事前起動しただけの会話の状態は忘れる)。"
  (setv process (.bound-process runtime))
  (<- seen (wait-until (fn [bell] (.hang runtime.doorbell bell)) (fn [] (or runtime.init-seen (not (.alive process))))
                       host.launch-timeout))
  (with [runtime.lock]
    (setv started runtime.init-seen))
  (if started
      (do
        (when (not (.runtime host runtime.session-id)) (.register host runtime))
        (TurnStarted (ClaudeTurn runtime.session-id turn-seq) runtime.session-id))
      (do
        (when seen
          (<- (wait-until (fn [bell] (.hang runtime.doorbell bell))
                          (fn [] (is-not (. (get runtime.turns turn-seq) end) None))
                          2.0)))
        (.drop process)
        (with [runtime.lock]
          (setv log (get runtime.turns turn-seq))
          (when (is log.end None) (setv log.end (Interrupted :process-kept False)))
          (.ring runtime.doorbell))
        (when fresh-runtime (.forget host runtime.session-id runtime))
        (LaunchFailed :exit-code (.exit-code process)
                      :stderr-tail (if seen (.stderr-tail process)
                                       (.format "no init line within {} seconds" host.launch-timeout))))))

(defk reuse-turn [#^ SessionRuntime runtime #^ ClaudeSessionSpec spec #^ TurnInput input #^ float requested]
  {:pre [(: runtime SessionRuntime) (: spec ClaudeSessionSpec) (: input TurnInput) (: requested float)]
   :post [(: % (| TurnStarted None))]
   :tags {:context "claude-code" :role "foundation"}}
  "生きてターンを待つ process に、このターンの入力を書くため(#3672 — 起動せず init も待たない。CLI は入力ごとに init を出し直す
   が、書いた後なのでターンの中の行として受ける)。経過の起点は入力を書いた刻。止める刻はこのターンの spec の credential-usable-until
   に置き直す(同じ token の貸与が延びれば止める刻も延びる — D2)。判断の後に process が停止した・停止し始めた・ターンを走らせて
   いるなら None(呼び手が停止を待ってから起動する)。"
  (<- writing (GetMonotonic))
  (<- writing-wall (GetTime))
  (with [runtime.lock]
    (setv binding runtime.binding)
    (setv process runtime.process)
    (when (or (is binding None) (is process None) (not (.alive process)) (is-not process.retiring None)
              (is-not (.running-turn runtime) None))
      (return None))
    (setv turn-seq (.open-turn runtime))
    (setv binding.turn-seq turn-seq)
    (setv log (.open-log runtime))
    (setv log.launched-at writing log.launched-wall writing-wall)
    (setv runtime.last-used requested)
    ;; ターンを始めたので、入力なしで事前起動した新しい会話のマークは消す(事前起動した process を最初のターンが使い回した時もここ)。
    (setv runtime.warmed-fresh False)
    (setv runtime.retire-after spec.credential-usable-until)
    (apply-transition runtime binding (dialogue.begin-turn runtime.state input)))
  (<- (note-reused runtime turn-seq requested writing writing-wall))
  (TurnStarted (ClaudeTurn runtime.session-id turn-seq) runtime.session-id))

(defk retire-idle [#^ SessionRuntime runtime]
  {:pre [(: runtime SessionRuntime)] :post [(: % None)] :tags {:context "claude-code" :role "foundation"}}
  "生きて待つ process を、次の手番の起こした時の条件が違うので降ろし始めるため(訳 LAUNCH-CHANGED を記す — 降りるのは呼び手が待つ)。"
  (with [runtime.lock]
    (setv process runtime.process)
    (when (and (is-not process None) (.alive process) (is process.retiring None))
      (setv runtime.stopped-because StopReason.LAUNCH-CHANGED)
      (.retire process)))
  None)


;; --- 生かす本数の上限と資格の止める刻(#3672 の D2 — 止める判断は host のここ 1 か所)------------------------------------------------

(defrecord LiveView
  "上限の本数を数える時の 1 つの会話の観測: runtime = その会話・idle = 生きて手番を走らせていない(降ろしてよい)・
   last-used = 最後に手番を始めた刻。"
  (#^ SessionRuntime runtime)
  (#^ bool idle)
  (#^ (| float None) last-used))

(defn #^ (| LiveView None) live-view [#^ SessionRuntime runtime]
  "生かす本数に数える process(生きて降りる途中でない物)を持つ会話の観測を取るため(数えない会話は None)。"
  (with [runtime.lock]
    (setv process runtime.process)
    (if (or (is process None) (not (.alive process)) (is-not process.retiring None))
        None
        (LiveView :runtime runtime :idle (is (.running-turn runtime) None) :last-used runtime.last-used))))

(defn #^ bool retire-for-limit [#^ SessionRuntime runtime]
  "上限の本数のために、手番を走らせていない process を降ろし始めるため(訳 LIVE-LIMIT)。観測の後に手番を始めた・降りた物は降ろさ
   ない(答え = 降ろし始めたか)。"
  (with [runtime.lock]
    (setv process runtime.process)
    (if (and (is-not process None) (.alive process) (is process.retiring None) (is (.running-turn runtime) None))
        (do (setv runtime.stopped-because StopReason.LIVE-LIMIT)
            (.retire process)
            True)
        False)))

(defn #^ bool room-or-evict [#^ ClaudeCodeHost host]
  "起こす前に、生かす本数に空きを作るため: 上限に来ていれば、手番を走らせていない物のうち一番長く使われていない物(最後に手番を
   始めた刻が一番古い物)を降ろす。走っている手番の process は降ろさない。答え = 空きが在るか(全部が手番を走らせていれば偽 —
   呼び手は空くまで待つ)。"
  (with [host.lock]
    (setv runtimes (tuple (.values host.runtimes))))
  (setv views (tuple (gfor view (gfor runtime runtimes (live-view runtime)) :if (is-not view None) view)))
  (when (< (len views) host.live-limit)
    (return True))
  (setv idle (sorted (gfor view views :if view.idle view) :key (fn [view] (if (is view.last-used None) 0.0 view.last-used))))
  (and (bool idle) (retire-for-limit (. (get idle 0) runtime)) (< (- (len views) 1) host.live-limit)))

(defk make-room [#^ ClaudeCodeHost host]
  {:pre [(: host ClaudeCodeHost)] :post [(: % bool)] :tags {:context "claude-code" :role "foundation"}}
  "新しい process を起こす前に、生かす本数(host.live-limit)に空きができるまで待つため(空きを作るのは room-or-evict・待つ上限は
   launch-timeout)。答え = 空きができたか。"
  (<- room (wait-until (fn [bell] (.hang-on-all host bell)) (fn [] (room-or-evict host)) host.launch-timeout))
  room)

(defn retire-if-due [#^ SessionRuntime runtime #^ float now]
  "ターンを走らせていない生きた process を、今が資格の止める刻以上なら止めるため(理由 CREDENTIAL-FLOOR)。"
  (with [runtime.lock]
    (setv process runtime.process)
    (when (and (is-not process None) (.alive process) (is process.retiring None) (is (.running-turn runtime) None)
               (credential-due runtime.retire-after now))
      (setv runtime.stopped-because StopReason.CREDENTIAL-FLOOR)
      (.retire process))))

(defk retire-past-usable-until [#^ ClaudeCodeHost host]
  {:pre [(: host ClaudeCodeHost)] :post [(: % None)] :tags {:context "claude-code" :role "foundation"}}
  "host が呼ばれるたびに(ターンを始める前を含む)、ターンを走らせていない生きた process のうち、今が資格の止める刻(spec の
   credential-usable-until)以上の物を止めるため — 止める刻を過ぎた process へ次のターンを渡さない(D2・#3753 (c)。
   走っているターンの process はターンの終わりで on-line が止める。呼び手はこの理由を見て借りた資格を返す)。"
  (<- now datetime (GetTime))
  (with [host.lock]
    (setv runtimes (tuple (.values host.runtimes))))
  (for [runtime runtimes]
    (retire-if-due runtime (.timestamp now)))
  None)

(defrecord LaunchReady
  "新しい process を起動する前の準備が済んだ: recorded = 続き・枝の親の transcript の最後の cost-state の額(CLI が数え始める額 —
   無ければ None)。"
  (#^ (| float None) recorded))

(defk prepare-launch [#^ ClaudeCodeHost host runtime #^ ClaudeSessionSpec spec #^ str canonical #^ str target-id #^ Launch decision]
  {:pre [(: host ClaudeCodeHost) (: runtime (| SessionRuntime None)) (: spec ClaudeSessionSpec) (: canonical str) (: target-id str)
         (: decision Launch)]
   :post [(: % (| LaunchReady LaunchFailed))] :tags {:context "claude-code" :role "foundation"}}
  "新しい process を起動する前の準備を、ターン(start-turn)と入力の前の事前起動(warm-session)で同じ順に行うため: 生きて待っている
   process を停止する(理由 LAUNCH-CHANGED)→ 停止中の process の終了を待つ → transcript の最後の額を読む → 冷えた続きの前の命令 →
   生かす本数に空きを作る。結果 = LaunchReady か、終了しない・空かない時の LaunchFailed。"
  (when decision.retire-idle
    (when (is runtime None)
      (raise (RuntimeError (.format "生きた process を停止する会話 {} の状態が無い(start-decision の誤り)" target-id))))
    (<- (retire-idle runtime)))
  (when decision.wait-retire
    (when (is runtime None)
      (raise (RuntimeError (.format "process の終了を待つ会話 {} の状態が無い(start-decision の誤り)" target-id))))
    (val old (.bound-process runtime))
    (<- down (wait-until (fn [bell] (.hang runtime.doorbell bell)) (fn [] (not (.alive old))) RETIRE-WAIT-SECONDS))
    (when (not down)
      (return (LaunchFailed :stderr-tail "the previous process of this session did not go down"))))
  ;; CLI が数え始める額 = 続き・枝の親の transcript の最後の cost-state の額(前の process が終了して額を記録した後 = 終了を待った後に
  ;; 読む)。冷えた続きの前の命令より前に読む — その命令が使った額もこのターンの費用に数える。新しい会話は transcript が無いので None。
  (<- recorded (recorded-cost-mark spec.home canonical target-id))
  (when decision.cold-resume
    (<- (run-cold-resume host spec target-id)))
  ;; 生かす本数に空きを作る(D2 — 上限なら一番長く使われていない、ターン待ちの process を停止する・全部が動いていれば空くまで待つ)。
  (<- room (make-room host))
  (when (not room)
    (return (LaunchFailed :stderr-tail (.format "live-limit {} reached: every live process is running a turn (waited {} seconds)"
                                                host.live-limit host.launch-timeout))))
  (LaunchReady :recorded recorded))

(defk start-turn [#^ ClaudeCodeHost host #^ ClaudeStartTurn request]
  {:pre [(: host ClaudeCodeHost) (: request ClaudeStartTurn)] :post [(: % "StartTurnOutcome")]}
  ;; 計時の行の起点(頼まれた刻 — 頭の註)。
  (<- requested (GetMonotonic))
  (<- (retire-past-usable-until host))
  (setv origin request.origin spec request.spec input request.input)
  (setv refused (refused-attachment input))
  (when (is-not refused None) (return refused))
  (setv canonical (os.path.realpath spec.cwd))
  (setv target-id (if (isinstance origin ForkSession) origin.parent-session-id origin.session-id))
  (setv carried (apply-carry spec.home canonical target-id (if (isinstance origin FreshSession) None origin.carry)))
  (when (is-not carried None) (return carried))
  (setv runtime (if (isinstance origin ForkSession) None (.runtime host target-id)))
  (<- key (launch-key host.command spec))
  (var decision (start-decision origin (session-view runtime)
                                (file-present (transcript-path spec.home.config-dir canonical target-id))
                                (is-not spec.cold-resume-prompt None)
                                key))
  (when (isinstance decision Refuse) (return decision.outcome))
  (when (isinstance decision Reuse)
    (when (is runtime None)
      (raise (RuntimeError (.format "使い回す会話 {} の状態が無い(start-decision の誤り)" target-id))))
    (<- reused (reuse-turn runtime spec input requested))
    (when (is-not reused None) (return reused))
    ;; 判断の後に process が降りた・降り始めた(手番の外で出力した)— 降りるのを待ってから起こす。
    (:= decision (Launch :wait-retire True)))
  (<- ready (prepare-launch host runtime spec canonical target-id decision))
  (when (isinstance ready LaunchFailed) (return ready))
  ;; ターンを 1 度も始めていない会話(新しく作る・入力なしで事前起動しただけ)は、起動できなかった時に忘れる — 新しい会話の id は
  ;; 未使用の扱いに戻る。
  (val unused (or (is runtime None) runtime.warmed-fresh))
  (setv fresh-runtime (is runtime None))
  (when fresh-runtime
    ;; transcript に額の行が無い時の起点: 新しい会話の CLI は 0 から数える。この handler が前の process を見ていない続き・枝は
    ;; 分からない(None — 最初の手番の額は None)。
    (setv runtime (SessionRuntime (if (isinstance origin ForkSession) "" target-id) spec.home canonical
                                  :cost-mark (if (isinstance origin FreshSession) 0.0 None)))
    (when (not (isinstance origin ForkSession)) (.register host runtime)))
  (<- launching (GetMonotonic))
  (<- launching-wall (GetTime))
  (setv runtime.last-used requested)
  (<- spawned (spawn-turn host runtime spec origin input ready.recorded key))
  (when (isinstance spawned LaunchFailed)
    (when unused (.forget host target-id runtime))
    (return spawned))
  (<- (note-spawned runtime spawned origin requested launching launching-wall))
  (<- outcome (await-init host runtime spawned unused))
  (<- (note-init runtime spawned outcome))
  outcome)

(defk warm-session [#^ ClaudeCodeHost host #^ ClaudeWarmSession request]
  {:pre [(: host ClaudeCodeHost) (: request ClaudeWarmSession)] :post [(: % "WarmSessionOutcome")]
   :tags {:context "claude-code" :role "foundation"}}
  "会話の process を最初の入力の前に起動して待たせるため(ClaudeWarmSession)。起動するか・同じ起動条件の process が既に待っているので
   足りるかはターンと同じ start-decision の 1 か所で決め(Reuse = 起動しない)、拒否もターンと同じ型。起動前の準備はターンと同じ
   prepare-launch。起動できなかった新しい会話の状態は忘れる(id は未使用の扱い)。"
  (<- requested (GetMonotonic))
  (<- (retire-past-usable-until host))
  (val origin request.origin)
  (val spec request.spec)
  (val canonical (os.path.realpath spec.cwd))
  (val target-id origin.session-id)
  (val carried (apply-carry spec.home canonical target-id (match origin (ResumeSession :carry carry) carry _ None)))
  (when (is-not carried None) (return carried))
  (val known (.runtime host target-id))
  (<- key (launch-key host.command spec))
  (val decision (start-decision origin (session-view known)
                                (file-present (transcript-path spec.home.config-dir canonical target-id))
                                (is-not spec.cold-resume-prompt None)
                                key))
  (match decision
    (Refuse :outcome outcome) (return outcome)
    (Reuse) (return (SessionWarmed :session-id target-id))
    _ None)
  (<- ready (prepare-launch host known spec canonical target-id decision))
  (when (isinstance ready LaunchFailed) (return ready))
  (val runtime (if (is known None)
                   (SessionRuntime target-id spec.home canonical :cost-mark (if (isinstance origin FreshSession) 0.0 None))
                   known))
  (when (is known None) (.register host runtime))
  ;; ターンを 1 度も始めていない会話(新しく作る・事前起動しただけ)は、起動できなかった時に忘れる(start-turn と同じ)。
  (val unused (or (is known None) known.warmed-fresh))
  (setv runtime.last-used requested)
  (<- spawned (spawn-idle host runtime spec origin ready.recorded key))
  (when (isinstance spawned LaunchFailed)
    (when unused (.forget host target-id runtime))
    (return spawned))
  (SessionWarmed :session-id target-id))

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
    (setv binding runtime.binding)
    (setv process runtime.process)
    (when (or (is transition None) (is binding None) (is process None) (not (.alive process)))
      (return (NoTurnInFlight turn.session-id)))
    (apply-transition runtime binding transition))
  (InputQueued input.ref))

(defn interrupt-turn [#^ ClaudeCodeHost host #^ ClaudeTurn turn]
  (setv runtime (.runtime host turn.session-id))
  (when (is runtime None) (return (NoTurnInFlight turn.session-id)))
  (with [runtime.lock]
    (setv transition (if (is (in-flight-log runtime turn) None) None
                         (dialogue.interrupt runtime.state (str (uuid.uuid4)))))
    (setv binding runtime.binding)
    (when (or (is transition None) (is binding None)) (return (NoTurnInFlight turn.session-id)))
    (apply-transition runtime binding transition))
  (InterruptRequested))

(defn answer-permission [#^ ClaudeCodeHost host #^ ClaudeAnswerPermission request]
  (setv runtime (.runtime host request.turn.session-id))
  (when (is runtime None) (return (NoSuchRequest request.request-id)))
  (with [runtime.lock]
    (setv transition (if (is (in-flight-log runtime request.turn) None) None
                         (dialogue.answer-permission runtime.state request.request-id request.answer)))
    (setv binding runtime.binding)
    (when (or (is transition None) (is binding None)) (return (NoSuchRequest request.request-id)))
    (apply-transition runtime binding transition))
  (Answered))

(defn #^ (| TurnEventPage None) page-of [#^ SessionRuntime runtime #^ ClaudeTurn turn #^ int after-seq]
  "名指した手番の after-seq より後の行と終わり(知らない手番は None)。"
  (with [runtime.lock]
    (setv log (.get runtime.turns turn.turn-seq))
    (when (is log None) (return None))
    (setv lines (tuple (gfor line log.lines :if (> line.seq after-seq) line)))
    (TurnEventPage lines (if lines (. (get lines -1) seq) after-seq) log.end)))

(defk read-events [#^ ClaudeCodeHost host #^ ClaudeReadTurnEvents request]
  {:pre [(: host ClaudeCodeHost) (: request ClaudeReadTurnEvents)] :post [(: % (| TurnEventPage UnknownTurn))]}
  (<- (retire-past-usable-until host))
  (setv turn request.turn)
  (setv runtime (.runtime host turn.session-id))
  (when (or (is runtime None) (is (page-of runtime turn request.after-seq) None))
    (return (UnknownTurn turn)))
  ;; 待つ間に古い手番として刈られた手番は、知らない手番と同じに答える。
  (<- (wait-until (fn [bell] (.hang runtime.doorbell bell))
                  (fn [] (setv page (page-of runtime turn request.after-seq))
                         (or (is page None) (bool page.lines) (is-not page.end None)))
                  (float request.wait-up-to)))
  (setv page (page-of runtime turn request.after-seq))
  (when (is-not page None)
    (<- (note-first-reply runtime turn page))
    (<- (note-first-text runtime turn page))
    (<- (note-turn-end runtime turn page)))
  (if (is page None) (UnknownTurn turn) page))

(defk close-session [#^ ClaudeCodeHost host #^ ClaudeCloseSession request]
  {:pre [(: host ClaudeCodeHost) (: request ClaudeCloseSession)] :post [(: % (| SessionClosed ProcessStillAlive))]}
  (setv runtime (.runtime host request.session-id))
  (when (is runtime None) (return (SessionClosed False)))
  (with [runtime.lock]
    (setv was-running (is-not (.running-turn runtime) None))
    (setv transition (dialogue.close-session runtime.state))
    (setv runtime.state transition.state)
    (when (is-not transition.end None)
      (setv (. (.open-log runtime) end) transition.end))
    (setv runtime.closed True)
    (setv process runtime.process)
    (when (and (is-not process None) (.alive process))
      (setv runtime.stopped-because StopReason.SESSION-CLOSED))
    (.ring runtime.doorbell))
  (when (and (is-not process None) (.alive process))
    (.retire process)
    (<- (wait-until (fn [bell] (.hang runtime.doorbell bell)) (fn [] (or (not (.alive process)) (.retire-finished process)))
                    RETIRE-WAIT-SECONDS))
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

(defk export-session [#^ ClaudeExportSession request]
  {:pre [(: request ClaudeExportSession)] :post [(: % (| SessionExported SessionNotFound))]}
  "transcript の jsonl 1 つを utf-8 で読む(置き場は session-status と同じ規則)。無い・空 = SessionNotFound。"
  (val path (Path (transcript-path request.home.config-dir (os.path.realpath request.cwd) request.session-id)))
  (when (not (.is-file path)) (return (SessionNotFound request.session-id)))
  (val text (.read-text path :encoding "utf-8"))
  (if (.strip text) (SessionExported text) (SessionNotFound request.session-id)))

(defn drop-process [#^ ClaudeCodeHost host #^ str session-id]
  (setv runtime (.runtime host session-id))
  (setv process (if (is runtime None) None runtime.process))
  (and (is-not process None) (.drop process)))

(defk live-process [#^ ClaudeCodeHost host #^ str session-id]
  {:pre [(: host ClaudeCodeHost) (: session-id str)] :post [(: % (| LiveProcess NoLiveProcess))]
   :tags {:context "claude-code" :role "foundation"}}
  "会話の process の見え方を答えるため(fake と同じ筋書きで、使い回しと守りを確かめる口 — #3672)。生きた process =
   起こした process が生きていて、降りる途中でない物。"
  (val runtime (.runtime host session-id))
  (when (is runtime None) (return (NoLiveProcess :launches 0 :stopped-because None)))
  (with [runtime.lock]
    (val process runtime.process)
    (if (and (is-not process None) (.alive process) (is process.retiring None))
        (LiveProcess :launches runtime.launches)
        (NoLiveProcess :launches runtime.launches :stopped-because runtime.stopped-because))))

(defk emit-outside [#^ ClaudeCodeHost host #^ str session-id]
  {:pre [(: host ClaudeCodeHost) (: session-id str)] :post [(: % bool)] :tags {:context "claude-code" :role "foundation"}}
  "生きていて手番を走らせていない process に、手番の外の出力をさせるため(守りの筋書きの口 — 替え玉の CLI だけが読む行
   stub_emit_outside を stdin へ書く。本物の claude には撃たない)。答え = 出させる process が在ったか。"
  (<- view (live-process host session-id))
  (val runtime (.runtime host session-id))
  (when (or (is runtime None) (not (isinstance view LiveProcess))) (return False))
  (with [runtime.lock]
    (when (is-not (.running-turn runtime) None) (return False))
    (.send (.bound-process runtime) (+ (json.dumps {"type" "stub_emit_outside"}) "\n")))
  True)


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
    (<- (retire-past-usable-until host))
    (resume (session-status host effect)))
  (ClaudeExportSession [home cwd session-id]
    (<- exported (export-session effect))
    (resume exported))
  (ClaudeWarmSession [origin spec]
    (<- warmed (warm-session host effect))
    (resume warmed))
  (ClaudeDropProcess [session-id]
    (resume (drop-process host session-id)))
  (ClaudeLiveProcess [session-id]
    (<- view (live-process host session-id))
    (resume view))
  (ClaudeEmitOutsideTurn [session-id]
    (<- emitted (emit-outside host session-id))
    (resume emitted)))
