;;; 本番の handler — 公開 effect 10(とテスト用の effect ClaudeDropProcess)に、claude の print mode の子 process で答える。
;;;
;;; 方針 = 会話の process は手番をまたいで生かし、次の手番(同じ会話の続き)の入力をその process へ書く(#3672 — 起こし直しと記録の
;;; 読み直しの 1.6〜2.6 秒を消す)。使い回すのは、起こした時の条件の鍵(argv.hy の launch-key — argv・cwd・env の指紋)が同じ時だけ。
;;; 違えば降ろしてから `--resume <id>` の新しい process を起こす。手番の外で出力した process は降ろす(dialogue.hy の on-record —
;;; #517 の事故の形の守り)。起動するか使い回すかの判断は decision.start-decision の 1 か所だけで、上の層は会話の id とターンの参照
;;; しか持たない。ClaudeWarmSession は同じ判断で、入力の前に process を起動して待たせる(ターンは開かない — 最初のターンがその process
;;; を使い回す。起動してから入力を受けられるまでの秒を入力の前に済ませる)。起動前の準備(待っている process の停止・停止の完了待ち・
;;; 冷えた続きの前の命令・生かす本数の数え)はターンと事前起動で同じ prepare-launch を通る。
;;;
;;; 生かす本数の上限(ClaudeCodeHost の live-limit — #4072 の E1b): handler は上限のために process を止めず、起動を待たせず、失敗にも
;;; しない。止める CLI を選ぶのは上の層のホストの 1 か所で、handler は起動の後の本数が上限を越える時に、log の 1 行(LIVE-LIMIT-LOG)と
;;; 知らせ ClaudeLiveLimitExceeded(ホストが答える)を出すだけ — ホストの数えと handler の数えが食い違った事を見せる。前は handler が
;;; 上限で手番を走らせていない一番古い process を黙って止め、全部が手番を走らせていれば空くまで起動を待った(ホストの知らない CLI が
;;; 止まり、ターンが止めの終わりを待った)。
;;; 上限を宣言しないホスト(live-limit が None — #4282): 使い手が同時に生かす本数を別の物差し(機体の memory の余白)で決める時は、
;;; この層に本数を渡さない。None のホストは起動の後の本数を上限と比べる所を通らず、log の行も知らせも出さない(None を 0 や大きい数に
;;; 言い換えない)。整数を渡すホストの検査と振る舞いは同じ。
;;;
;;; 不変条件(fake と共通 — tests/test_scenarios.hy が両方に当てる):
;;;   1 つの会話に走っている手番は多くとも 1 つ・生きた process は多くとも 1 つ(降りる途中の process は待ってから起こす)。
;;;   ClaudeStartTurn 1 回に終わりはちょうど 1 つ。行の seq は会話の中で単調増加。
;;;
;;; 状態は ClaudeCodeHost(composition root が 1 つ作って handler に渡す)が持つ。読み手の thread と handler の節は
;;; 会話ごとの lock で状態機械(dialogue.hy)の値を差し替える。待つ所(init・出来事・降りるの待ち)は呼び鈴
;;; (Doorbell — CreateExternalPromise の約束)を掛けてから条件を読み直し、WaitWithin で上限の秒まで待つ。状態を変えた側(読み手の
;;; thread・process の終わり・handler の節)が呼び鈴を鳴らす(時間で起きて確かめない — 行が来てから待ちが抜けるまでに刻みの遅れが無い)。
;;;
;;; 借りた資格(card ki-4ffdafa021e5): 宣言(ClaudeSessionSpec)が借りた資格(credential)を持てば、その process だけの設定
;;; dir(資格の家 — credential_home.hy)を家の設定 dir の下に作って .credentials.json に置き、子の CLAUDE_CONFIG_DIR をそこへ向ける
;;; (子の env に token を置かない)。家は Binding が持ち、process が降りたら消す(on-down)。資格だけが替わった手番は起動条件のキー
;;; (argv.hy の launch-key — token と期限を入れない)が同じなので生きた process を使い回し、その手番の資格を家の file に書く
;;; (reuse-turn)。走っている process の資格は ClaudeReplaceSessionCredential で替える(replace-session-credential)。家の file を書くのと
;;; 消すのは会話の lock の中だけ(書いている最中に消さない)。宣言に credential が無い起動は今までどおり家の設定 dir の資格で起こす。
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
(require doeff-hy.macros [defhandler defk deff <- val var])
(require doeff-hy.record [defrecord])
(import bisect)
(import collections.abc [Callable])
(import dataclasses [dataclass replace])
(import datetime [datetime])
(import math)
(import operator [attrgetter])
(import os)
(import os.path)
(import signal)
(import threading)
(import uuid)
(import doeff [run])
(import doeff_core_effects.effects [slog])
(import doeff_core_effects.scheduler [CreateExternalPromise ExternalPromise])
(import doeff_core_effects.offloaded_call [ThreadPerCall offloaded run-detached keep-nothing])
(import doeff_time [GetMonotonic GetTime WaitWithin epoch-ms-of])
(import doeff_claude_code.values [ClaudeTurn ClaudeHome ClaudeSessionSpec TurnInput FreshSession ResumeSession ForkSession
                                  LinkFromHome Rebuilt IMAGE-MIMES LentCredential])
(import doeff_claude_code.lines [ClaudeStreamLine Completed Failed Interrupted BackendLost Init PartialMessage DeltaKind RequestTiming
                                 TimedPhase TurnResult parse-record classify-record recorded-cost])
(import doeff_claude_code.effects [ClaudeStartTurn ClaudeInjectInput ClaudeInterruptTurn ClaudeReadTurnEvents
                                   ClaudeAnswerPermission ClaudeCloseSession ClaudeSessionStatus ClaudeExportSession
                                   TurnStarted InputQueued InterruptRequested InputAlreadyRead TurnEventPage Answered SessionClosed
                                   SessionStatus SessionExported SessionNotFound Idle TurnRunning Closed TranscriptPresent TranscriptAbsent
                                   CarryRefused LaunchFailed AttachmentRefused NoTurnInFlight UnknownTurn NoSuchRequest
                                   ProcessStillAlive ClaudeWarmSession SessionWarmed ClaudeLiveLimitExceeded
                                   ClaudeReplaceSessionCredential CredentialReplaced NoSuchSession SessionProcessDown
                                   HomeCredentialSession])
(import json)
(import doeff_claude_code.faults [ClaudeDropProcess ClaudeLiveProcess ClaudeEmitOutsideTurn LiveProcess NoLiveProcess StopReason])
(import doeff_claude_code.dialogue :as dialogue)
(import doeff_claude_code.dialogue [DialogueState])
(import doeff_claude_code.decision [SessionView Refuse Reuse Launch start-decision retire-time credential-due])
(import doeff_claude_code.argv [transcript-dir transcript-path launch-argv launch-key cold-resume-argv process-env])
(import doeff_claude_code.process [ClaudeProcess EOF-GRACE-SECONDS TERM-GRACE-SECONDS])
(import doeff_claude_code.credential_home [open-credential-home replace-credential close-credential-home])
(import doeff_claude_code.rotation [ROTATE-ABOVE-BYTES TranscriptKept TranscriptRotated TranscriptUnrotatable TranscriptRotationFailed
                                    rotate-transcript])

;; 会話が持ち続ける手番の記録(stdout の行の全部)の本数。読み手(上の層)は今の手番と、止めて生き残った入力の続きの手番しか読まない
;; ので、続きを数本たどる間だけ持つ。閉じた会話は最後の手番だけを持ち(SessionRuntime.keep-only-current)、読み手がその終わりを
;; 読み、process も降りたら、生きた状態ごとホストから外して小さな記録(ClosedSession)だけを残す(ClaudeCodeHost.release)。16 本だった
;; 時は、生きた会話ごとに 16 手番分の行と、閉じた会話の 16 手番分の行をホストの process が持ち続けた(lt-KATGT933TATG1ESPPSW9QGFM7K)。
;; 最後の 1 本を持ち続けた時も、閉じた会話の数だけ行が溜まり、CLI の数が減っても turn-host の memory が減らなかった(10-19 の本番の
;; pid 1518593: CLI 96→57 本で ClaudeStreamLine +49,501 個・RssAnon +55 MB — lt-5611P8B4MJS6CZQZ1809H94JPQ)。
(setv KEPT-TURNS 4)
(setv RETIRE-WAIT-SECONDS (+ EOF-GRACE-SECONDS (* 2 TERM-GRACE-SECONDS) 2.0))
(setv COLD-RESUME-TIMEOUT-SECONDS 600.0)
;; 計時の行の名(頭の註 — #3605)。行を拾う時はこの名と欄 event で引く。
(val CLI-TIMING-LOG "claude CLI の起動の計時")
;; 生かす本数の上限を越えて起動した時の log の行の名(#4072 の E1b — 同じ事を知らせ ClaudeLiveLimitExceeded でホストへも届ける)。
(val LIVE-LIMIT-LOG "claude CLI の生かす本数の上限を越えた起動")
;; 続きの前に大きな transcript を最後の compact の境から後だけに置き直した(か置き直せなかった)所の log の行の名(card ki-d96c2ca8a5a1 —
;; rotation.hy の頭の註)。
(val TRANSCRIPT-ROTATION-LOG "claude CLI の transcript の差し替え")
;; transcript の読み書き(額の起点の読み・書き出し・持ち込み・様子)1 つに thread 1 本(card ki-24ee30105583): transcript の jsonl は数十 MB に
;; なり、待ち行列の浅い詰まった disk では 1 回の読みが秒の単位で待つ。handler の節は scheduler の thread の上で走るので、そこで読むと
;; 同じ process の全部の手番が止まる。待つのは読みを頼んだ task だけにする(doeff_core_effects の offloaded — 外側に scheduled が要るのは
;; 呼び鈴の待ちと同じ)。待ちが取り消された後の答えは値か持ち込みの終わりの印だけ — 後始末は要らない。
(val TRANSCRIPT-THREADS (ThreadPerCall))


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
   終わりの計時の行を出したか(頭の註の計時の行)・end-read = 読み手がこの手番を読み終えた(終わりを頁で渡した — 頁は after-seq より
   後の行を全部載せるので、終わりを渡した時点で行も全部渡している。読み手は終わりの後にその手番を読まない)か、読み手がこの手番を
   受けていない(起動の失敗 — 上の層は手番の参照でなく LaunchFailed を受ける)。"
  (defn __init__ [self]
    (setv #^ (get list ClaudeStreamLine) self.lines [])
    (setv #^ (| Completed Failed Interrupted BackendLost None) self.end None)
    (setv #^ bool self.end-read False)
    (setv #^ (| float None) self.launched-at None)
    (setv #^ (| datetime None) self.launched-wall None)
    (setv #^ bool self.reply-noted False)
    (setv #^ bool self.text-noted False)
    (setv #^ bool self.end-noted False)))

(defclass Binding []
  "1 つの process と、その process が今走らせている手番の番号(生き残った入力の手番へ進む)。
   process は起こすまで None。credential-home = その process だけの資格の家の path(借りた資格で起こした時だけ — 家の資格なら None。
   process が降りて消したら None に戻す)。書き換えるのは会話の lock の中だけ。"
  (defn __init__ [self #^ int turn-seq #^ (| str None) [credential-home None]]
    (setv #^ (| ClaudeProcess None) self.process None)
    (setv #^ int self.turn-seq turn-seq)
    (setv #^ (| str None) self.credential-home credential-home)))

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
    ;; retire-after = 今の process を止める刻(資格の期限 − 床・epoch 秒 — 期限を知らなければ None・D2)。
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
    self.current-seq)

  (defn keep-only-current [self]
    "閉じた会話の手番の記録を最後の手番だけにする(上の層が閉じた後に読むのは最後の手番の終わりだけ — 読み終えたら会話ごと外す
     ClaudeCodeHost.release)。会話の lock の中で呼ぶ。"
    (for [old (list self.turns)]
      (when (!= old self.current-seq)
        (del (get self.turns old)))))

  (defn note-read [self #^ ClaudeTurn turn #^ TurnEventPage page]
    "読み手に渡した頁を手番の記録に記すため(終わりを渡したら、読み手はその手番をもう読まない — 閉じた会話を外してよいかの材料)。
     会話の lock の中で呼ぶ。"
    (setv log (.get self.turns turn.turn-seq))
    (when (and (is-not log None) (is-not page.end None))
      (setv log.end-read True)))

  (defn #^ bool drained [self]
    "閉じて、読み手が読み終えていない手番の記録が無く、process も降りたか(生きた状態をホストから外してよいか)。会話の lock の中で呼ぶ。"
    (and self.closed
         (all (gfor log (.values self.turns) log.end-read))
         (or (is self.process None) (not (.alive self.process)))))

  (defn closed-record [self #^ (| float None) cost-mark]
    "外す時に残す小さな記録を作るため(cost-mark = 次に起こす process の額の起点の控え — release-closed が前の process の終わり方から
     決めた物)。会話の lock の中で呼ぶ。"
    (ClosedSession :session-id self.session-id :home self.home :canonical-cwd self.canonical-cwd :current-seq self.current-seq
                   :next-line-seq self.next-line-seq :launches self.launches :stopped-because self.stopped-because
                   :cost-mark cost-mark :warmed-fresh self.warmed-fresh)))


(defrecord ClosedSession
  "閉じて、読み手が手番を読み終え、process も降りた会話の小さな記録(手番の記録 — stdout の行 — を持たない)。生きた状態を外した後も
   同じに答えるのに要る物だけを持つ: 閉じた様子(ClaudeSessionStatus の Closed)・process の見え方(ClaudeLiveProcess の起こした数と
   降ろした訳)・使用済みの id(同じ id の FreshSession を断る — warmed-fresh = 入力の前に事前起動しただけで、まだ使っていない)と、
   続き(ResumeSession)・事前起動で生きた状態を作り直す材料(手番の番号・行の seq・額の起点 cost-mark — 前の process の終わり方で
   決めた控え。transcript の最後の額が在ればそちらが先 — launch-cost-mark)。"
  (#^ str session-id)
  (#^ ClaudeHome home)
  (#^ str canonical-cwd)
  (#^ int current-seq)
  (#^ int next-line-seq)
  (#^ int launches)
  (#^ (| StopReason None) stopped-because)
  (#^ (| float None) cost-mark)
  (#^ bool warmed-fresh))


(defclass ClaudeCodeHost []
  "handler の状態: 会話の id → SessionRuntime(生きた状態)と、会話の id → ClosedSession(閉じて生きた状態を外した会話の小さな記録 —
   release)。lock の順は ClaudeCodeHost → 会話(release だけが両方を取る)。command = 実行ファイルと前置きの引数(例: #(\"claude\"))・clock = 行の時刻を
   刻む関数(clock.clock-of)・live-limit = 同時に生かす process の本数の上限(走っている手番の process も数える — 機体の memory の
   予算 ÷ 1 本の memory。#3672 の D2。越える起動は止めずに知らせる — 頭の註。None = 上限を宣言しない: 使い手が別の物差し — 機体の
   memory の余白 — で決める時で、本数を上限と比べず、log の行も知らせも出さない・#4282)・credential-floor-seconds = 資格の期限(ClaudeSessionSpec.credential の expires-at)の手前で
   process を止める床の秒・launch-timeout = init の行を待つ上限(秒)・rotate-above-bytes = 続きの前に transcript を最後の compact の
   境から後だけに置き直す大きさの下限(byte — rotation.hy の頭の註。CLI は init の前に transcript を全部読むので、大きな transcript は
   launch-timeout を越える)。上限と床は呼び手の宣言から受ける(既定を持たない — 上限を宣言しない呼び手も None を明示で渡す)。"
  (defn __init__ [self #^ tuple command clock #^ (| int None) live-limit #^ float credential-floor-seconds [launch-timeout 120.0]
                  [rotate-above-bytes ROTATE-ABOVE-BYTES]]
    ;; 数の型は注記が持つ。bool は int の子なので名指しで断り、値の範囲を断る。
    ;; None(上限を宣言しない)は比べる数が無いので、検査を通さずそのまま持つ(fake の FakeClaudeWorld と同じ)。
    (when (and (is-not live-limit None) (or (isinstance live-limit bool) (< live-limit 1)))
      (raise (ValueError (.format "ClaudeCodeHost.live_limit は 1 以上の整数か None: {!r}" live-limit))))
    (when (or (isinstance credential-floor-seconds bool) (< credential-floor-seconds 0))
      (raise (ValueError (.format "ClaudeCodeHost.credential_floor_seconds は 0 以上の秒: {!r}" credential-floor-seconds))))
    (when (or (isinstance rotate-above-bytes bool) (not (isinstance rotate-above-bytes int)) (< rotate-above-bytes 1))
      (raise (ValueError (.format "ClaudeCodeHost.rotate_above_bytes は 1 以上の整数: {!r}" rotate-above-bytes))))
    (setv self.command command
          self.clock clock
          self.live-limit live-limit
          self.credential-floor-seconds (float credential-floor-seconds)
          self.launch-timeout (float launch-timeout)
          self.rotate-above-bytes rotate-above-bytes
          self.lock (threading.Lock))
    (setv #^ (get dict #(str SessionRuntime)) self.runtimes {})
    (setv #^ (get dict #(str ClosedSession)) self.closed {}))

  (defn runtime [self #^ str session-id]
    (with [self.lock] (.get self.runtimes session-id)))

  (defn closed-session [self #^ str session-id]
    "閉じて生きた状態を外した会話の小さな記録を引くため(生きた状態が在る・知らない会話は None)。"
    (with [self.lock] (.get self.closed session-id)))

  (defn register [self #^ SessionRuntime runtime]
    (with [self.lock]
      (setv (get self.runtimes runtime.session-id) runtime)
      (.pop self.closed runtime.session-id None)))

  (defn forget [self #^ str session-id #^ SessionRuntime runtime]
    (with [self.lock]
      (when (is (.get self.runtimes session-id) runtime)
        (del (get self.runtimes session-id)))))

  (defn #^ bool release [self #^ SessionRuntime runtime #^ (| float None) cost-mark]
    "閉じて読み終えた会話の生きた状態を外し、小さな記録(ClosedSession)だけを残すため(頭の註の KEPT-TURNS)。外した状態の手番の記録も
     空にする — 降りた process の stdout に残った行を読み手の thread が後から渡しても、記録に積まない(on-line は手番の記録が無ければ
     積まない)。外す前に lock の中で drained を読み直す(間に続きが同じ状態で手番を始めていれば外さない)。答え = 外したか。"
    (with [self.lock]
      (with [runtime.lock]
        (setv session-id runtime.session-id)
        (setv released (and (is (.get self.runtimes session-id) runtime) (.drained runtime)))
        (when released
          (setv (get self.closed session-id) (.closed-record runtime cost-mark))
          (del (get self.runtimes session-id))
          (.clear runtime.turns))))
    released)

  (defn #^ SessionRuntime reopen [self #^ ClosedSession closed]
    "外した会話の続き・事前起動のために、小さな記録から生きた状態(閉じた印つき・手番の記録なし)を作り直して覚えるため。手番の番号・
     行の seq・起こした数は記録から続ける。並んだ要求が先に作り直していれば、その生きた状態を答える。"
    (with [self.lock]
      (setv known (.get self.runtimes closed.session-id))
      (when (is-not known None)
        (return known))
      (when (is (.get self.closed closed.session-id) closed)
        (del (get self.closed closed.session-id)))
      (setv runtime (SessionRuntime closed.session-id closed.home closed.canonical-cwd :cost-mark closed.cost-mark))
      (setv runtime.closed True
            runtime.current-seq closed.current-seq
            runtime.next-line-seq closed.next-line-seq
            runtime.launches closed.launches
            runtime.stopped-because closed.stopped-because
            runtime.warmed-fresh closed.warmed-fresh)
      (setv (get self.runtimes closed.session-id) runtime)
      runtime)))


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
    ;; 手番の境で、資格の期限 − 床を過ぎていれば止める(D2 — 生き残った入力の手番が続く時は境ではない)。
    (when (and (is-not transition.end None) (not transition.continues) (is transition.retire None)
               (credential-due runtime.retire-after (.timestamp at)))
      (setv runtime.stopped-because StopReason.CREDENTIAL-FLOOR)
      (.retire (process-of binding)))))

(defn on-exit [#^ SessionRuntime runtime #^ Binding binding exit-code #^ str stderr-tail]
  (with [runtime.lock]
    (when (is runtime.binding binding)
      (apply-transition runtime binding (dialogue.on-exit runtime.state exit-code stderr-tail)))))

(deff on-down [#^ SessionRuntime runtime #^ Binding binding]  ; defk にできない: process の見張りの thread が呼ぶ callback(ClaudeProcess の on-down)
  {:pre [(: runtime SessionRuntime) (: binding Binding)] :post [(: % None)] :tags {:context "claude-code" :role "process"}}
  "process が降りた(か降ろす梯子を踏み終えた)合図で、降りていればその process の資格の家を消し、会話の呼び鈴を鳴らすため(鳴らすのは
   消した後 — 呼び鈴で起きた待ち手は家が消えた後を読む)。家は今の binding かを問わず消す(家はその process だけの物 — 前の process
   の家も、その process が降りた時に消える)。消すのは会話の lock の中(資格を書いている最中に消さない)。梯子を踏んでも降りて
   いなければ消さない(次の合図で消す)。"
  (with [runtime.lock]
    (setv process binding.process
          home binding.credential-home)
    (when (and (is-not home None) (is-not process None) (not (.alive process)))
      (setv binding.credential-home None)
      (run (close-credential-home home))))
  (.ring runtime.doorbell)
  None)


;; --- I/O の小さな道具(handler の中だけ) --------------------------------------------------------------

(defn #^ bool file-present [#^ str path] (os.path.exists path))

;; 費用の起点を探す transcript の末尾の幅(byte・狭い順 — card ki-a264a73efcce)。CLI は降りる時に cost-state の行を末尾へ書き、起点を読む
;; のは前の process が降りた後なので、ふつうは最初の幅で見つかる。最後の幅にも無ければ None(分からない — 0 を発明しない)。
(val COST-TAIL-BYTES #(65536 1048576 16777216))

(defrecord TranscriptBytes
  "transcript の jsonl の読み: data = 読んだ byte(末尾だけの読みは行の頭から)・whole = file の頭から読んだか。"
  (#^ bytes data)
  (#^ bool whole))

(defn #^ (| TranscriptBytes None) read-transcript-bytes [#^ str path #^ (| int None) tail-bytes]
  "transcript の jsonl を、全部か末尾だけ byte で読むため(tail-bytes = 末尾の何 byte まで・None = 全部 — card ki-a264a73efcce。
   100 MB 級の transcript を、最近の行だけが要る読みで丸ごと読まない)。末尾だけの読みは、切り口の後の最初の行の頭から返す(切り口の
   直前の 1 byte も読み、それが改行なら切り口の行も残す — 行の途中から始めない)。file が無い・file でない = None。"
  (when (not (os.path.isfile path))
    (return None))
  (try
    (with [handle (open path "rb")]
      (setv size (. (os.fstat (.fileno handle)) st-size))
      (when (or (is tail-bytes None) (<= size tail-bytes))
        (return (TranscriptBytes :data (.read handle) :whole True)))
      (.seek handle (- size tail-bytes 1))
      (setv chunk (.read handle))
      (setv newline (.find chunk b"\n"))
      (TranscriptBytes :data (if (< newline 0) b"" (cut chunk (+ newline 1) None)) :whole False))
    (except [FileNotFoundError] None)))

(defn #^ (| bytes None) read-transcript-from [#^ str path #^ int from-byte]
  "transcript の jsonl の from-byte byte 目から末尾までを読むため(前に取り出した写しを持つ使い手が伸びた分だけを読む —
   card ki-3b521394a063)。読むのは from-byte から後ろだけ(seek)。file が無い・file でない・from-byte より短い = None。"
  (when (not (os.path.isfile path))
    (return None))
  (try
    (with [handle (open path "rb")]
      (when (< (. (os.fstat (.fileno handle)) st-size) from-byte)
        (return None))
      (.seek handle from-byte)
      (.read handle))
    (except [FileNotFoundError] None)))

(defk transcript-cost-mark [#^ str path]
  {:pre [(: path str)] :post [(: % (| float None))] :tags {:context "claude-code" :role "foundation"}}
  "transcript の file path を読み、最後の cost-state の額を読むため(recorded-cost-mark が TRANSCRIPT-THREADS の thread の新しい VM で回す —
   読みも行の解きも scheduler の thread の外)。読むのは末尾だけ(COST-TAIL-BYTES の幅を狭い順に — card ki-a264a73efcce。前は 200 MB の
   transcript を丸ごと読み、詰まった disk の上で起こすたびに 15〜74 秒かかった)。file が無い・読めない・最後の幅にも行が無い・値が数で
   なければ None。"
  (for [limit COST-TAIL-BYTES]
    (val read (try
                (read-transcript-bytes path limit)
                (except [OSError] None)))
    (when (is read None)
      (return None))
    (val text (try
                (.decode read.data "utf-8")
                (except [UnicodeDecodeError] None)))
    (when (is text None)
      (return None))
    (<- mark (recorded-cost text))
    (when (or (is-not mark None) read.whole)
      (return mark)))
  None)

(defk recorded-cost-mark [#^ ClaudeHome home #^ str canonical-cwd #^ str session-id]
  {:pre [(: home ClaudeHome) (: canonical-cwd str) (: session-id str)] :post [(: % (| float None))]
   :tags {:context "claude-code" :role "foundation"}}
  "手番の額の起点を、この handler が前の process を見ていない会話(新しい host での続き・持ち込んだ transcript・枝の親)でも知るため、
   CLI が数え始める額 = その会話の transcript の最後の cost-state の額を読む(置き場は transcript-present と同じ transcript-path)。
   読むのは TRANSCRIPT-THREADS の thread(頭の註の下の値の註)。file が無い・読めない・行が無い・値が数でなければ None。"
  (val path (transcript-path home.config-dir canonical-cwd session-id))
  (<- mark (| float None) (offloaded TRANSCRIPT-THREADS (fn [] (run-detached (transcript-cost-mark path))) keep-nothing))
  mark)

(defk rotate-before-resume [#^ ClaudeCodeHost host #^ ClaudeHome home #^ str canonical-cwd #^ str session-id]
  {:pre [(: host ClaudeCodeHost) (: home ClaudeHome) (: canonical-cwd str) (: session-id str)]
   :post [(: % (| TranscriptKept TranscriptRotated TranscriptUnrotatable TranscriptRotationFailed))]
   :tags {:context "claude-code" :role "foundation"}}
  "続き(--resume)の CLI が init の前に読む transcript を、上限(host.rotate-above-bytes)の内に戻すため(rotation.hy の頭の註 — card
   ki-d96c2ca8a5a1)。前の process が降りた後に呼ぶ。読みと書きは TRANSCRIPT-THREADS の thread。置き直した・置き直せなかった時は
   log の 1 行(TRANSCRIPT-ROTATION-LOG)を出す(上限の内で置き直さなかった時は出さない)。"
  (val path (transcript-path home.config-dir canonical-cwd session-id))
  (<- began (GetMonotonic))
  (<- at (GetTime))
  (<- wall-ms (wall-ms-of at))
  (<- outcome (| TranscriptKept TranscriptRotated TranscriptUnrotatable TranscriptRotationFailed)
      (offloaded TRANSCRIPT-THREADS (fn [] (run-detached (rotate-transcript path host.rotate-above-bytes wall-ms))) keep-nothing))
  (<- ended (GetMonotonic))
  (<- ms (elapsed-ms began ended))
  (match outcome
    (TranscriptRotated :before before :after after :archive archive)
      (<- (slog TRANSCRIPT-ROTATION-LOG :level "info" :event "rotated" :wall-ms wall-ms :session-id session-id :before-bytes before
                :after-bytes after :archive archive :ms ms))
    (TranscriptUnrotatable :size size :searched searched)
      (<- (slog TRANSCRIPT-ROTATION-LOG :level "warning" :event "unrotatable" :wall-ms wall-ms :session-id session-id :size-bytes size
                :searched-bytes searched :ms ms))
    (TranscriptRotationFailed :reason reason)
      (<- (slog TRANSCRIPT-ROTATION-LOG :level "warning" :event "rotation-failed" :wall-ms wall-ms :session-id session-id
                :reason reason :ms ms))
    (TranscriptKept) None)
  outcome)

(defk unrotatable-note [started #^ (| TranscriptUnrotatable None) unrotatable]
  {:pre [(: started (| TurnStarted LaunchFailed)) (: unrotatable (| TranscriptUnrotatable None))] :post [(: % (| TurnStarted LaunchFailed))]
   :tags {:context "claude-code" :role "foundation"}}
  "上限を越えるのに境が無くて置き直せなかった transcript の続きが起動に失敗した時、利用者に分かる訳(transcript が大きすぎて縮められ
   なかった)を stderr-tail に足すため(card ki-d96c2ca8a5a1)。始まった手番・置き直せた・上限の内の続きの失敗はそのまま。"
  (match #(started unrotatable)
    #((LaunchFailed :stderr-tail tail) (TranscriptUnrotatable :size size :searched searched))
      (replace started :stderr-tail (.format "{} (transcript of {} bytes has no usable compact boundary in its last {} bytes, so it was not shortened before --resume)"
                                            tail size searched))
    _ started))

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

(defrecord TranscriptPlace
  "起こす前の transcript の置き場の読み(transcript-place の答え)。canonical = cwd の realpath・refused = 持ち込みを断った訳(None = 持ち込めた・
   既に在る・持ち込む物が無い)・present = 持ち込みの後に transcript が在るか(断った時は False)。"
  (#^ str canonical)
  (#^ (| CarryRefused None) refused)
  (#^ bool present))

(defk read-transcript-place [home cwd session-id carry]
  {:pre [(: home ClaudeHome) (: cwd str) (: session-id str) (: carry (| Rebuilt LinkFromHome None))] :post [(: % TranscriptPlace)] :tags {:context "claude-code" :role "foundation"}}
  "cwd を realpath にし、transcript を持ち込み(apply-carry)、その後に transcript が在るかを読むため(どれも file system を待つ)。"
  (val canonical (os.path.realpath cwd))
  (val refused (apply-carry home canonical session-id carry))
  (TranscriptPlace :canonical canonical :refused refused
                   :present (and (is refused None) (file-present (transcript-path home.config-dir canonical session-id)))))

(defk transcript-place [#^ ClaudeHome home #^ str cwd #^ str session-id #^ (| Rebuilt LinkFromHome None) carry]
  {:pre [(: home ClaudeHome) (: cwd str) (: session-id str) (: carry (| Rebuilt LinkFromHome None))] :post [(: % TranscriptPlace)] :tags {:context "claude-code" :role "foundation"}}
  "起こすか使い回すかを決める前の transcript の置き場の読みと持ち込みを、TRANSCRIPT-THREADS の thread で済ませるため(頭の註の下の値の註 —
   持ち込みは数十 MB の jsonl を書くことがある)。"
  (<- place TranscriptPlace (offloaded TRANSCRIPT-THREADS (fn [] (run-detached (read-transcript-place home cwd session-id carry))) keep-nothing))
  place)

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

(defk observed-view [runtime closed]
  {:pre [(: runtime (| SessionRuntime None)) (: closed (| ClosedSession None))] :post [(: % SessionView)]
   :tags {:context "claude-code" :role "foundation"}}
  "起こすか使い回すかの判断へ渡す観測を、生きた状態が在ればそこから、閉じて外した会話なら小さな記録から作るため(外した会話も
   この handler が知る会話 — 同じ id の FreshSession を断る。process は降りているので使い回しも降りる途中の process も無い)。"
  (if (is closed None)
      (session-view runtime)
      (SessionView :known True :warmed-fresh closed.warmed-fresh)))

(defn refused-attachment [#^ TurnInput input]
  (setv refused (lfor item input.attachments :if (not-in item.mime IMAGE-MIMES) item.mime))
  (if refused (AttachmentRefused (get refused 0)) None))

(defk lent-credential-home [#^ ClaudeSessionSpec spec]
  {:pre [(: spec ClaudeSessionSpec)] :post [(: % (| str None))] :tags {:context "claude-code" :role "process"}}
  "起こす process の設定 dir を決めるため: 宣言が借りた資格を持てば、その process だけの資格の家を家の設定 dir の下に作って path を
   返す(credential_home.hy)。持たなければ None(家の設定 dir の資格のまま起こす — 手で開いた機体の会話の路)。"
  (when (is spec.credential None)
    (return None))
  (<- home (open-credential-home spec.home.config-dir spec.credential))
  home)


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
  "冷えた続きの前の 1 回きりの命令を走らせて終わりを待つ。答え = 走り終えたか(失敗しても手番は起こす — 最適化の命令)。宣言が借りた
   資格を持てば、この命令の process だけの資格の家を作って向け、終わったら消す(手番の process と同じ資格の渡し方)。"
  (val done (threading.Event))
  (val doorbell (Doorbell))
  (<- home (lent-credential-home spec))
  (try
    (setv process (ClaudeProcess (cold-resume-argv host.command spec session-id) spec.cwd
                                 (process-env spec.home (or home spec.home.config-dir))
                                 (fn [raw] None) (fn [code tail] (.set done) (.ring doorbell)) (fn [] (.ring doorbell))))
    (except [OSError]
      (<- (close-credential-home home))
      (return False)))
  (.close-stdin process)
  (<- finished (wait-until (fn [bell] (.hang doorbell bell)) (fn [] (.is-set done)) COLD-RESUME-TIMEOUT-SECONDS))
  (when (not finished) (.drop process))
  (<- (close-credential-home home))
  finished)


;; --- 計時の行(頭の註 — #3605) --------------------------------------------------------------

(defk wall-ms-of [at]
  {:pre [(: at (| datetime None))] :post [(: % (| int None))] :tags {:context "claude-code" :role "foundation"}}
  "計時の行の壁の時刻の欄を、他の process の行と並べられる epoch ミリ秒の整数で綴るため(時刻が無ければ None)。丸めは doeff-time の
   epoch-ms-of の 1 つ(床へ)— 行を並べる上の層と同じ丸めなので、329.6 ms に書いた刻が 329.9 ms の刻より後に見えない
   (#3855 — 以前は四捨五入で 330 と綴り、床で 329 と綴る側と順が逆に見えた)。"
  (if (is at None) None (epoch-ms-of at)))

(defk elapsed-ms [since until]
  {:pre [(: since (| float None)) (: until float)] :post [(: % (| int None))] :tags {:context "claude-code" :role "foundation"}}
  "GetMonotonic の 2 つの読みの差を、計時の行の経過の欄のミリ秒の整数で綴るため(起点が無ければ None)。2 つの読みをそれぞれミリ秒へ
   床へ丸めてから引く — 続く 2 つの区間の和が通しの区間と同じになる(差を四捨五入すると 0.4 + 0.4 が 0 + 0 で通しの 0.8 は 1 になる・#3855)。"
  (if (is since None) None (- (math.floor (* until 1000)) (math.floor (* since 1000)))))

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
  (<- wall-ms (wall-ms-of at))
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
  (<- wall-ms (wall-ms-of writing-wall))
  (<- before-write-ms (elapsed-ms requested writing))
  (<- (slog CLI-TIMING-LOG :level "info" :event "reused" :wall-ms wall-ms :before-write-ms before-write-ms
            :session-id runtime.session-id :turn-seq turn-seq))
  None)

(defk phases-text [phases measure]
  {:pre [(: phases (get tuple #(TimedPhase ...))) (: measure str)] :post [(: % str)] :tags {:context "claude-code" :role "foundation"}}
  "CLI が名乗った区間の列を、計時の行の 1 欄に空白の無い JSON(名 → ミリ秒・CLI の順)で綴るため — 行は「名=値」を空白で区切るので、
   値に空白を入れない。measure = 綴る値(\"ms\" = 長さ・\"start-ms\" = 始まり — 始まりを名乗らない区間は飛ばす)。"
  (val values (match measure
                "ms" (gfor phase phases #(phase.name phase.ms))
                "start-ms" (gfor phase phases :if (is-not phase.start-ms None) #(phase.name phase.start-ms))))
  (json.dumps (dict values) :separators #("," ":") :ensure-ascii False))

(defk startup-fields [init]
  {:pre [(: init (| Init None))] :post [(: % dict)] :tags {:context "claude-code" :role "foundation"}}
  "init の計時の行に、CLI が名乗った起動の区間の欄を足すため(#3855 — process を起こしてから init までを区間ごとに割る)。
   cli-startup-origin-ms = CLI の process の時刻の起点(epoch ミリ秒 — spawned の行の wall_ms との差が exec と node の起き上がり)・
   cli-startup-phases = 区間の長さ・cli-startup-phase-starts = 区間の始まり(起点からのミリ秒)。区間を名乗らない init は欄を出さない。"
  (when (or (is init None) (not init.startup-phases))
    (return {}))
  (<- lengths (phases-text init.startup-phases "ms"))
  (<- starts (phases-text init.startup-phases "start-ms"))
  {"cli_startup_origin_ms" init.startup-origin-ms "cli_startup_phases" lengths "cli_startup_phase_starts" starts})

(defk note-init [runtime turn-seq outcome]
  {:pre [(: runtime SessionRuntime) (: turn-seq int) (: outcome (| TurnStarted LaunchFailed))] :post [(: % None)]
   :tags {:context "claude-code" :role "foundation"}}
  "init の行を受けた(か受けずに起動を諦めた)所の計時の行を出すため。since-launch-ms = process を起こし始めてから待ちが init を見るまで
   (待ちは読み手の thread が init の行で鳴らす呼び鈴で起きるので、刻みの遅れは無い)・line-at-ms = 読み手の thread が init の行を読んだ壁の時刻(無ければ None)。
   CLI が起動の区間を名乗った init なら、その欄も載せる(startup-fields)。"
  (<- now (GetMonotonic))
  (<- at (GetTime))
  (val log (with [runtime.lock] (.get runtime.turns turn-seq)))
  (val launched (if (is log None) None log.launched-at))
  (val init-line (if (is log None)
                     None
                     (with [runtime.lock] (next (gfor line log.lines :if (isinstance line.kind Init) line) None))))
  (<- wall-ms (wall-ms-of at))
  (<- since-launch-ms (elapsed-ms launched now))
  (<- line-at-ms (wall-ms-of (if (is init-line None) None init-line.at)))
  (<- startup (startup-fields (if (is init-line None) None init-line.kind)))
  (<- (slog CLI-TIMING-LOG :level "info" :event (if (isinstance outcome TurnStarted) "init" "launch-failed") :wall-ms wall-ms
            :since-launch-ms since-launch-ms :line-at-ms line-at-ms :session-id runtime.session-id :turn-seq turn-seq #** startup))
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
  (<- wall-ms (wall-ms-of at))
  (<- since-launch-ms (elapsed-ms log.launched-at now))
  (<- line-at-ms (wall-ms-of reply.at))
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
   (モデルが考えた秒)・request-to-stream-start-ms = その stream の行が名乗る ttft_ms(CLI が API へ要求を送ってから message_start を
   受けるまで — stream-start-at-ms までのうち API の側の分・#3855)。stream の行が無ければ後の 3 つは None。差分の本文は載せない。"
  (val log (with [runtime.lock] (.get runtime.turns turn.turn-seq)))
  (when (is log None)
    (return None))
  (<- claimed (claimed-first-text runtime log page.next-seq))
  (when (is claimed None)
    (return None))
  (<- now (GetMonotonic))
  (<- at (GetTime))
  (<- wall-ms (wall-ms-of at))
  (<- since-launch-ms (elapsed-ms log.launched-at now))
  (<- line-at-ms (wall-ms-of claimed.text.at))
  (val stream-start claimed.stream-start)
  (<- stream-start-at-ms (if (is stream-start None) None (wall-ms-of stream-start.at)))
  (<- after-stream-start-ms (wall-elapsed-ms (if (is stream-start None) None stream-start.at) claimed.text.at))
  (<- (slog CLI-TIMING-LOG :level "info" :event "first-text" :wall-ms wall-ms :since-launch-ms since-launch-ms
            :line-at-ms line-at-ms :stream-start-at-ms stream-start-at-ms :after-stream-start-ms after-stream-start-ms
            :request-to-stream-start-ms (if (is stream-start None) None stream-start.kind.ttft-ms)
            :session-id turn.session-id :turn-seq turn.turn-seq))
  None)

(defk wall-elapsed-ms [since until]
  {:pre [(: since (| datetime None)) (: until (| datetime None))] :post [(: % (| int None))]
   :tags {:context "claude-code" :role "foundation"}}
  "GetTime の読み(手番の記録の起こし始めの壁の時刻)と、読み手の thread が行に刻んだ壁の時刻の差を、計時の行の経過の欄のミリ秒の
   整数で綴るため(どちらかが無ければ None)。2 つの読みをそれぞれ doeff-time の epoch-ms-of で床へ丸めた値の差 — 行の刻の欄
   (wall-ms-of)の差と同じになり、順が逆に見えない(#3855)。epoch の物差しで引くので、時差の付き方が違う 2 つの読みでも例外にしない。"
  (if (or (is since None) (is until None)) None (- (epoch-ms-of until) (epoch-ms-of since))))

(defrecord TurnEndLines
  "手番の終わりの計時の行の材料: deltas = その手番で CLI から受けた差分の行(PartialMessage — 受けた順)/ timing = その手番の最初の
   result の行の計時の欄(RequestTiming — 入力を読んでから要求を送り、答え始めるまでを CLI が数えた分。result の行が無ければ None)。
   最初の行を取るのは、送った入力で動き始めた CLI の手番の分だから(生き残った注入の手番の result は後に続く — #3855)。"
  (#^ (get tuple #(ClaudeStreamLine ...)) deltas)
  (#^ (| RequestTiming None) timing))

(defk claimed-turn-end [runtime log]
  {:pre [(: runtime SessionRuntime) (: log TurnLog)] :post [(: % (| TurnEndLines None))]
   :tags {:context "claude-code" :role "foundation"}}
  "手番の終わりを上の層へ初めて渡す時に、その手番で CLI から受けた差分の行(PartialMessage — 種類は DeltaKind)と最初の result の行の
   計時の欄を 1 度だけ取り出すため(取り出したら手番の記録に印を付け、2 度目からは None)。手番の記録は手番ごとなので、生きた process を
   使い回す手番でも前の手番の行は入らない。"
  (with [runtime.lock]
    (when log.end-noted
      (return None))
    (setv log.end-noted True)
    (TurnEndLines :deltas (tuple (gfor line log.lines :if (isinstance line.kind PartialMessage) line))
                  :timing (next (gfor line log.lines :if (isinstance line.kind TurnResult) line.kind.timing) None))))

(defk request-timing-fields [timing]
  {:pre [(: timing (| RequestTiming None))] :post [(: % dict)] :tags {:context "claude-code" :role "foundation"}}
  "手番の終わりの計時の行に、CLI が result の行で名乗った計時の欄を足すため(#3855 — 送ってから最初の字までのうち、入力を読んでから
   要求を送るまでと、要求から答え始めるまでを、CLI の数で割る)。欄は CLI の名に cli_ を付けた物で、名乗らなければ None。要求までの区間
   (cli_request_phases — 和が cli_time_to_request_ms)は名乗った時だけ出す。"
  (val known (if (is timing None) (RequestTiming) timing))
  (<- lengths (phases-text known.request-phases "ms"))
  (val phases (if known.request-phases {"cli_request_phases" lengths} {}))
  (| {"cli_time_to_request_ms" known.time-to-request-ms
      "cli_ttft_stream_ms" known.ttft-stream-ms
      "cli_first_content_frame_ms" known.first-content-frame-ms
      "cli_ttft_ms" known.ttft-ms
      "cli_duration_ms" known.duration-ms
      "cli_duration_api_ms" known.duration-api-ms}
     phases))

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
   起点は入力を書いた刻。CLI が result の行で名乗った計時の欄も載せる(request-timing-fields — #3855)。"
  (when (is page.end None)
    (return None))
  (val log (with [runtime.lock] (.get runtime.turns turn.turn-seq)))
  (when (is log None)
    (return None))
  (<- claimed (claimed-turn-end runtime log))
  (when (is claimed None)
    (return None))
  (val deltas claimed.deltas)
  (val partials (tuple (gfor line deltas :if (and (= line.kind.delta DeltaKind.TEXT) line.kind.text-delta) line)))
  (val thinking (tuple (gfor line deltas :if (= line.kind.delta DeltaKind.THINKING) line)))
  (val tool-input (tuple (gfor line deltas :if (= line.kind.delta DeltaKind.TOOL-INPUT) line)))
  (<- now (GetMonotonic))
  (<- at (GetTime))
  (<- wall-ms (wall-ms-of at))
  (<- since-launch-ms (elapsed-ms log.launched-at now))
  (<- first-partial-since-launch-ms (wall-elapsed-ms log.launched-wall (if partials (. (get partials 0) at) None)))
  (<- first-thinking-since-launch-ms (wall-elapsed-ms log.launched-wall (if thinking (. (get thinking 0) at) None)))
  (<- first-tool-input-since-launch-ms (wall-elapsed-ms log.launched-wall (if tool-input (. (get tool-input 0) at) None)))
  ;; 最初の刻の欄は差分が在る時だけ出す(本文の差分の欄 first_partial_since_launch_ms は今までどおり None でも出す)。
  (val firsts (dfor #(name ms) [#("first_thinking_since_launch_ms" first-thinking-since-launch-ms)
                                #("first_tool_input_since_launch_ms" first-tool-input-since-launch-ms)]
                    :if (is-not ms None)
                    name ms))
  (<- cli-timing (request-timing-fields claimed.timing))
  (<- (slog CLI-TIMING-LOG :level "info" :event "turn-end" :wall-ms wall-ms :since-launch-ms since-launch-ms
            :partial-lines (len partials) :first-partial-since-launch-ms first-partial-since-launch-ms
            :thinking-deltas (len thinking) :tool-input-deltas (len tool-input)
            :session-id turn.session-id :turn-seq turn.turn-seq #** firsts #** cli-timing))
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
    _ (if (is-not recorded None)
          recorded
          (<- (process-cost-mark had-previous previous-exit cost-mark start-mark)))))

(defk process-cost-mark [#^ bool had-previous #^ (| int None) previous-exit #^ (| float None) cost-mark #^ (| float None) start-mark]
  {:pre [(: had-previous bool) (: previous-exit (| int None)) (: cost-mark (| float None)) (: start-mark (| float None))]
   :post [(: % (| float None))] :tags {:context "claude-code" :role "judgment"}}
  "transcript に額の記録が無い時の、次の process の CLI が数え始める額を、前の process の終わり方から決めるため(launch-cost-mark の
   後半 — 閉じた会話を外す時に、生きた状態の代わりに残す控えもこれで決める・release-closed)。"
  (cond
    (or (not had-previous) (= previous-exit 0)) cost-mark
    (= previous-exit (- signal.SIGKILL)) start-mark
    True None))

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
  (<- home (lent-credential-home spec))
  (with [runtime.lock]
    (setv runtime.closed False
          runtime.init-seen False
          runtime.warmed-fresh False
          runtime.state (DialogueState :session-id runtime.session-id :cost-mark mark :start-mark mark))
    (val turn-seq (.open-turn runtime))
    (val binding (Binding turn-seq home))
    (setv runtime.binding binding)
    (val transition (dialogue.begin-turn runtime.state input))
    (try
      (setv binding.process
            (ClaudeProcess (launch-argv host.command spec origin) spec.cwd (process-env spec.home (or home spec.home.config-dir))
                           (fn [raw] (on-line runtime binding host.clock raw))
                           (fn [code tail] (on-exit runtime binding code tail))
                           (fn [] (on-down runtime binding))))
      (setv runtime.launches (+ runtime.launches 1))
      (setv runtime.launch-key key)
      (setv runtime.retire-after (retire-time spec.credential host.credential-floor-seconds))
      (except [error OSError]
        (setv runtime.binding None)
        (setv binding.credential-home None)
        (run (close-credential-home home))
        ;; 上の層はこの手番の参照でなく LaunchFailed を受ける — 読み手の居ない手番は読み終えた扱い(閉じた会話を外す妨げにしない)。
        (setv (. (.open-log runtime) end) (Interrupted :process-kept False)
              (. (.open-log runtime) end-read) True)
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
   読み取りの thread が読んだ行はターンの記録に入らない(ターンが無い)。費用の起点・起動条件のキー・資格の期限の余裕はターンの process
   と同じ規則。新しい会話なら warmed-fresh のマークを付ける(最初のターンは同じ id の FreshSession)。結果 = None(起動した)か
   LaunchFailed(実行ファイルが無い等)。"
  (<- mark (launch-mark-of runtime origin recorded))
  (<- home (lent-credential-home spec))
  (with [runtime.lock]
    (setv runtime.closed False
          runtime.init-seen False
          runtime.warmed-fresh (isinstance origin FreshSession)
          runtime.state (DialogueState :session-id runtime.session-id :cost-mark mark :start-mark mark
                                       :awaiting-first-input True))
    (val binding (Binding runtime.current-seq home))
    (setv runtime.binding binding)
    (try
      (setv binding.process
            (ClaudeProcess (launch-argv host.command spec origin) spec.cwd (process-env spec.home (or home spec.home.config-dir))
                           (fn [raw] (on-line runtime binding host.clock raw))
                           (fn [code tail] (on-exit runtime binding code tail))
                           (fn [] (on-down runtime binding))))
      (setv runtime.launches (+ runtime.launches 1))
      (setv runtime.launch-key key)
      (setv runtime.retire-after (retire-time spec.credential host.credential-floor-seconds))
      (except [error OSError]
        (setv runtime.binding None)
        (setv binding.credential-home None)
        (run (close-credential-home home))
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
          ;; 上の層はこの手番の参照でなく LaunchFailed を受ける — 読み手の居ない手番は読み終えた扱い(spawn-turn と同じ)。
          (setv log.end-read True)
          (.ring runtime.doorbell))
        (when fresh-runtime (.forget host runtime.session-id runtime))
        (LaunchFailed :exit-code (.exit-code process)
                      :stderr-tail (if seen (.stderr-tail process)
                                       (.format "no init line within {} seconds" host.launch-timeout))))))

(defk reuse-turn [#^ SessionRuntime runtime #^ ClaudeSessionSpec spec #^ TurnInput input #^ float requested #^ float floor-seconds]
  {:pre [(: runtime SessionRuntime) (: spec ClaudeSessionSpec) (: input TurnInput) (: requested float) (: floor-seconds float)]
   :post [(: % (| TurnStarted None))]
   :tags {:context "claude-code" :role "foundation"}}
  "生きて手番を待つ process に、この手番の入力を書くため(#3672 — 起こさず init も待たない。CLI は入力ごとに init を出し直すが、
   書いた後なので手番の中の行として受ける)。経過の起点は入力を書いた刻。この手番の spec の資格の期限で止める刻を決め直す(同じ
   token の貸与が延びれば期限も延びる — D2)。spec が借りた資格を持てば、入力を書く前にその資格を process の資格の家の file へ書く
   (資格だけが替わった手番も起こし直さない — card ki-4ffdafa021e5。起動条件のキーが同じなので借りたか家のかは
   process と spec で揃っている)。判断の後に process が降りた・降り始めた・手番を走らせているなら None(呼び手が降りるのを待って
   起こす)。"
  (<- writing (GetMonotonic))
  (<- writing-wall (GetTime))
  (with [runtime.lock]
    (setv binding runtime.binding)
    (setv process runtime.process)
    (when (or (is binding None) (is process None) (not (.alive process)) (is-not process.retiring None)
              (is-not (.running-turn runtime) None))
      (return None))
    (val home binding.credential-home)
    (when (!= (is spec.credential None) (is home None))
      (raise (RuntimeError (.format "会話 {} の使い回す process と手番の宣言で、借りた資格か家の資格かが食い違う(launch-key の誤り)"
                                    runtime.session-id))))
    (when (and (is-not spec.credential None) (is-not home None))
      (run (replace-credential home spec.credential)))
    (setv turn-seq (.open-turn runtime))
    (setv binding.turn-seq turn-seq)
    (setv log (.open-log runtime))
    (setv log.launched-at writing log.launched-wall writing-wall)
    ;; ターンを始めたので、入力なしで事前起動した新しい会話のマークは消す(事前起動した process を最初のターンが使い回した時もここ)。
    (setv runtime.warmed-fresh False)
    (setv runtime.retire-after (retire-time spec.credential floor-seconds))
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


;; --- 生かす本数の上限と資格の床(#3672 の D2・#4072 の E1b)------------------------------------------------------------

(defn #^ bool counted-live [#^ SessionRuntime runtime]
  "生かす本数に数える process(生きて降りる途中でない物)を会話が持つかを読むため。"
  (with [runtime.lock]
    (setv process runtime.process)
    (and (is-not process None) (.alive process) (is process.retiring None))))

(defk note-over-declared-limit [#^ ClaudeCodeHost host #^ int limit #^ str session-id #^ bool warm]
  {:pre [(: host ClaudeCodeHost) (: limit int) (: session-id str) (: warm bool)] :post [(: % None)]
   :tags {:context "claude-code" :role "foundation"}}
  "新しい process を起こす直前に、起こした後の生きた process の本数(降りる途中の物は数えない)がホストの宣言した上限 limit を越えるなら、
   log の 1 行(LIVE-LIMIT-LOG)を出し、知らせ ClaudeLiveLimitExceeded をホストへ出すため(頭の註 — 止めない・待たない・失敗に
   しない)。session-id = 起こす会話(枝分かれは元の会話の id — 枝の id は init まで決まらない)・warm = 入力の前の事前起動か。"
  (val runtimes (with [host.lock] (tuple (.values host.runtimes))))
  (val live (+ 1 (len (lfor runtime runtimes :if (counted-live runtime) runtime))))
  (when (<= live limit)
    (return None))
  (<- at (GetTime))
  (<- wall-ms (wall-ms-of at))
  (<- (slog LIVE-LIMIT-LOG :level "warning" :event "live-limit-exceeded" :wall-ms wall-ms :session-id session-id :live live
            :limit limit :warm warm))
  (<- (ClaudeLiveLimitExceeded :session-id session-id :live live :limit limit :warm warm))
  None)

(defk note-over-limit [#^ ClaudeCodeHost host #^ str session-id #^ bool warm]
  {:pre [(: host ClaudeCodeHost) (: session-id str) (: warm bool)] :post [(: % None)]
   :tags {:context "claude-code" :role "foundation"}}
  "新しい process を起こす直前に、ホストが生かす本数の上限を宣言しているかで分けるため(#4282)。宣言していない(host.live-limit が None)
   なら、比べる数が無いので本数を数えず、log の行も知らせも出さない。宣言していれば、その数で note-over-declared-limit を通す
   (None でない値は全部こちら — 黙って「宣言なし」に寄せない)。session-id・warm は note-over-declared-limit と同じ。"
  (match host.live-limit
    None None
    limit (<- (note-over-declared-limit host limit session-id warm)))
  None)

(defn retire-if-due [#^ SessionRuntime runtime #^ float now]
  "手番を走らせていない生きた process の資格が床を切っていれば止めるため(訳 CREDENTIAL-FLOOR)。見回りは層 2 の効果のたび(上の層が
   CLI の行ごとに撃つ読みを含む)に host の全部の会話を回るので、床を切っていない会話は lock も process の生き死にの確かめ(poll —
   system call)も取らずに通す — 読み 1 回の費用を会話の数に比例させない(card ki-07d7059e1eea —
   tests/test_page_reads.hy)。止める刻 retire-after は lock の中で替わり得るので、lock を取った後に同じ判じを読み直す。"
  (when (not (credential-due runtime.retire-after now))
    (return None))
  (with [runtime.lock]
    (setv process runtime.process)
    (when (and (is-not process None) (.alive process) (is process.retiring None) (is (.running-turn runtime) None)
               (credential-due runtime.retire-after now))
      (setv runtime.stopped-because StopReason.CREDENTIAL-FLOOR)
      (.retire process))))

(defk retire-under-floor [#^ ClaudeCodeHost host]
  {:pre [(: host ClaudeCodeHost)] :post [(: % None)] :tags {:context "claude-code" :role "foundation"}}
  "host が呼ばれるたびに、手番を走らせていない生きた process のうち資格の期限 − 床を過ぎた物を止めるため(D2 — 走っている手番の
   process は手番の境で on-line が止める。呼び手はこの訳を読んで借りた資格を返す)。"
  (<- now datetime (GetTime))
  (with [host.lock]
    (setv runtimes (tuple (.values host.runtimes))))
  (for [runtime runtimes]
    (retire-if-due runtime (.timestamp now)))
  None)

(defrecord LaunchReady
  "新しい process を起動する前の準備が済んだ: recorded = 続き・枝の親の transcript の最後の cost-state の額(CLI が数え始める額 —
   無ければ None)・unrotatable = 続きの transcript が上限を越えるのに置き直せなかった(起動が期限を越えた時に訳を足す — 無ければ None)。"
  (#^ (| float None) recorded)
  (#^ (| TranscriptUnrotatable None) unrotatable))

(defk prepare-launch [#^ ClaudeCodeHost host runtime #^ ClaudeSessionSpec spec origin #^ str canonical #^ str target-id
                      #^ Launch decision #^ bool warm]
  {:pre [(: host ClaudeCodeHost) (: runtime (| SessionRuntime None)) (: spec ClaudeSessionSpec)
         (: origin (| FreshSession ResumeSession ForkSession)) (: canonical str) (: target-id str) (: decision Launch) (: warm bool)]
   :post [(: % (| LaunchReady LaunchFailed))] :tags {:context "claude-code" :role "foundation"}}
  "新しい process を起動する前の準備を、ターン(start-turn)と入力の前の事前起動(warm-session)で同じ順に行うため: 生きて待っている
   process を停止する(理由 LAUNCH-CHANGED)→ 停止中の process の終了を待つ → 続き(ResumeSession)なら大きな transcript を置き直す
   (rotate-before-resume — 前の process が降りた後)→ transcript の最後の額を読む → 冷えた続きの前の命令 →
   生かす本数を数える(越えるなら知らせる — note-over-limit。warm = 入力の前の事前起動か)。結果 = LaunchReady か、前の process が
   終了しない時の LaunchFailed。"
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
  ;; 続きの CLI は init の前に transcript を全部読む — 上限を越える transcript は最後の compact の境から後だけに置き直す(card
  ;; ki-d96c2ca8a5a1)。枝(ForkSession)の親は置き直さない(親の process が生きているかをここは知らない)。
  (var unrotatable None)
  (when (isinstance origin ResumeSession)
    (<- rotated (rotate-before-resume host spec.home canonical target-id))
    (when (isinstance rotated TranscriptUnrotatable)
      (:= unrotatable rotated)))
  ;; CLI が数え始める額 = 続き・枝の親の transcript の最後の cost-state の額(前の process が終了して額を記録した後 = 終了を待った後に
  ;; 読む)。冷えた続きの前の命令より前に読む — その命令が使った額もこのターンの費用に数える。新しい会話は transcript が無いので None。
  (<- recorded (recorded-cost-mark spec.home canonical target-id))
  (when decision.cold-resume
    (<- (run-cold-resume host spec target-id)))
  (<- (note-over-limit host target-id warm))
  (LaunchReady :recorded recorded :unrotatable unrotatable))

(defk start-turn [#^ ClaudeCodeHost host #^ ClaudeStartTurn request]
  {:pre [(: host ClaudeCodeHost) (: request ClaudeStartTurn)] :post [(: % "StartTurnOutcome")]}
  ;; 計時の行の起点(頼まれた刻 — 頭の註)。
  (<- requested (GetMonotonic))
  (<- (retire-under-floor host))
  (setv origin request.origin spec request.spec input request.input)
  (setv refused (refused-attachment input))
  (when (is-not refused None) (return refused))
  (setv target-id (if (isinstance origin ForkSession) origin.parent-session-id origin.session-id))
  (<- place TranscriptPlace (transcript-place spec.home spec.cwd target-id (if (isinstance origin FreshSession) None origin.carry)))
  (when (is-not place.refused None) (return place.refused))
  (setv canonical place.canonical)
  (setv runtime (if (isinstance origin ForkSession) None (.runtime host target-id)))
  ;; 閉じて生きた状態を外した会話は、小さな記録から観る(process は降りている — 使い回しは無い)。
  (setv closed (if (or (isinstance origin ForkSession) (is-not runtime None)) None (.closed-session host target-id)))
  (<- key (launch-key host.command spec))
  (<- view (observed-view runtime closed))
  (var decision (start-decision origin view
                                place.present
                                (is-not spec.cold-resume-prompt None)
                                key))
  (when (isinstance decision Refuse) (return decision.outcome))
  (when (isinstance decision Reuse)
    (when (is runtime None)
      (raise (RuntimeError (.format "使い回す会話 {} の状態が無い(start-decision の誤り)" target-id))))
    (<- reused (reuse-turn runtime spec input requested host.credential-floor-seconds))
    (when (is-not reused None) (return reused))
    ;; 判断の後に process が降りた・降り始めた(手番の外で出力した)— 降りるのを待ってから起こす。
    (:= decision (Launch :wait-retire True)))
  (<- ready (prepare-launch host runtime spec origin canonical target-id decision False))
  (when (isinstance ready LaunchFailed) (return ready))
  ;; 外した会話の続きは、記録から生きた状態を作り直す(手番の番号・行の seq・起こした数・額の起点の控えを続ける)。
  (when (is-not closed None)
    (setv runtime (.reopen host closed)))
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
  (<- spawned (spawn-turn host runtime spec origin input ready.recorded key))
  (when (isinstance spawned LaunchFailed)
    (when unused (.forget host target-id runtime))
    (return spawned))
  (<- (note-spawned runtime spawned origin requested launching launching-wall))
  (<- started (await-init host runtime spawned unused))
  (<- (note-init runtime spawned started))
  (<- outcome (unrotatable-note started ready.unrotatable))
  outcome)

(defk warm-session [#^ ClaudeCodeHost host #^ ClaudeWarmSession request]
  {:pre [(: host ClaudeCodeHost) (: request ClaudeWarmSession)] :post [(: % "WarmSessionOutcome")]
   :tags {:context "claude-code" :role "foundation"}}
  "会話の process を最初の入力の前に起動して待たせるため(ClaudeWarmSession)。起動するか・同じ起動条件の process が既に待っているので
   足りるかはターンと同じ start-decision の 1 か所で決め(Reuse = 起動しない)、拒否もターンと同じ型。起動前の準備はターンと同じ
   prepare-launch。起動できなかった新しい会話の状態は忘れる(id は未使用の扱い)。"
  (<- (retire-under-floor host))
  (val origin request.origin)
  (val spec request.spec)
  (val target-id origin.session-id)
  (<- place TranscriptPlace (transcript-place spec.home spec.cwd target-id (match origin (ResumeSession :carry carry) carry _ None)))
  (when (is-not place.refused None) (return place.refused))
  (val canonical place.canonical)
  (val held (.runtime host target-id))
  ;; 閉じて生きた状態を外した会話は、小さな記録から観る(start-turn と同じ)。
  (val closed (if (is held None) (.closed-session host target-id) None))
  (<- key (launch-key host.command spec))
  (<- view (observed-view held closed))
  (val decision (start-decision origin view
                                place.present
                                (is-not spec.cold-resume-prompt None)
                                key))
  (match decision
    (Refuse :outcome outcome) (return outcome)
    (Reuse) (return (SessionWarmed :session-id target-id))
    _ None)
  (<- ready (prepare-launch host held spec origin canonical target-id decision True))
  (when (isinstance ready LaunchFailed) (return ready))
  ;; 外した会話は、記録から生きた状態を作り直す(start-turn と同じ)。
  (val known (if (is closed None) held (.reopen host closed)))
  (val runtime (if (is known None)
                   (SessionRuntime target-id spec.home canonical :cost-mark (if (isinstance origin FreshSession) 0.0 None))
                   known))
  (when (is known None) (.register host runtime))
  ;; ターンを 1 度も始めていない会話(新しく作る・事前起動しただけ)は、起動できなかった時に忘れる(start-turn と同じ)。
  (val unused (or (is known None) known.warmed-fresh))
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

(defn interrupt-turn [#^ ClaudeCodeHost host #^ ClaudeTurn turn #^ (| str None) for-input]
  "走っている手番を止めるため。for-input(早く読ませたい足した入力の ref)を名指した止めは、CLI がその入力をもう読んでいれば止めずに
   InputAlreadyRead で答える — 読んだかの判断は CLI の行を読む同じ lock の中で対話の状態(dialogue.input-already-read)から取る。"
  (setv runtime (.runtime host turn.session-id))
  (when (is runtime None) (return (NoTurnInFlight turn.session-id)))
  (with [runtime.lock]
    (setv in-flight (is-not (in-flight-log runtime turn) None))
    (when (and in-flight (is-not for-input None) (dialogue.input-already-read runtime.state for-input))
      (return (InputAlreadyRead for-input)))
    (setv transition (if in-flight (dialogue.interrupt runtime.state (str (uuid.uuid4))) None))
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
  "名指した手番の after-seq より後の行と終わり(知らない手番は None)。上の層の源の task は CLI の行が届くたびに読むので、読みの費用は
   手番の長さに比例させない: 手番の記録の行は seq の昇順(足すのは on-line の 1 か所 — 会話の lock の中で next-line-seq を進めて
   末尾に足す)なので、読み始めの位置を二分探しで引き、その後ろだけを写す(card ki-07d7059e1eea —
   tests/test_page_reads.hy)。"
  (with [runtime.lock]
    (setv log (.get runtime.turns turn.turn-seq))
    (when (is log None) (return None))
    (setv lines (tuple (cut log.lines (bisect.bisect-right log.lines after-seq :key (attrgetter "seq")) None)))
    (TurnEventPage lines (if lines (. (get lines -1) seq) after-seq) log.end)))

(defk read-events [#^ ClaudeCodeHost host #^ ClaudeReadTurnEvents request]
  {:pre [(: host ClaudeCodeHost) (: request ClaudeReadTurnEvents)] :post [(: % (| TurnEventPage UnknownTurn))]}
  (<- (retire-under-floor host))
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
    (<- (note-turn-end runtime turn page))
    ;; 終わりを渡した手番は読み終えた — 閉じた会話の最後の手番なら、ここで会話ごと外す(閉じた時に走っていた手番の終わりは、上の層が
    ;; 閉じた後に読む)。
    (with [runtime.lock]
      (.note-read runtime turn page))
    (<- (release-closed host runtime)))
  (if (is page None) (UnknownTurn turn) page))

(defk release-closed [#^ ClaudeCodeHost host #^ SessionRuntime runtime]
  {:pre [(: host ClaudeCodeHost) (: runtime SessionRuntime)] :post [(: % bool)] :tags {:context "claude-code" :role "foundation"}}
  "閉じて、読み手が手番を読み終え、process も降りた会話の生きた状態をホストから外し、小さな記録(ClosedSession)だけを残すため(頭の註の
   KEPT-TURNS — 閉じた会話の数だけ手番の記録を持ち続けない)。記録の額の起点は、生きた状態が持つ前の process の終わり方から今決める
   (作り直した状態は前の process を持たない)。まだ外せない会話は何もしない(閉じていない・読み手が終わりを読んでいない・process が
   降りていない — 次の close-session か read-events が読み直す)。答え = 外したか。"
  (when (not (with [runtime.lock] (.drained runtime)))
    (return False))
  ;; process は降りているので、終わり方はもう変わらない(外す時に release が lock の中で drained を読み直す)。
  (val process (with [runtime.lock] runtime.process))
  (val state (with [runtime.lock] runtime.state))
  (<- mark (process-cost-mark (is-not process None) (if (is process None) None (.exit-code process)) state.cost-mark
                              state.start-mark))
  (.release host runtime mark))

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
    (.keep-only-current runtime)
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
  ;; 読み手が最後の手番の終わりを読み終えていれば、ここで会話ごと外す(まだなら、終わりを渡す read-events が外す)。
  (<- (release-closed host runtime))
  (SessionClosed was-running))

(defk read-transcript-state [request]
  {:pre [(: request ClaudeSessionStatus)] :post [(: % (| TranscriptPresent TranscriptAbsent))] :tags {:context "claude-code" :role "foundation"}}
  "会話の transcript が在るかと最後に書かれた刻を file system から読むため。"
  (val path (transcript-path request.home.config-dir (os.path.realpath request.cwd) request.session-id))
  (if (os.path.exists path) (TranscriptPresent (os.path.getmtime path)) (TranscriptAbsent)))

(defk session-status [#^ ClaudeCodeHost host #^ ClaudeSessionStatus request]
  {:pre [(: host ClaudeCodeHost) (: request ClaudeSessionStatus)] :post [(: % SessionStatus)] :tags {:context "claude-code" :role "foundation"}}
  "会話の今の様子(走っている手番・閉じたか)と transcript の在る無しを答えるため(transcript の様子は TRANSCRIPT-THREADS の thread で読む)。"
  (<- transcript (| TranscriptPresent TranscriptAbsent) (offloaded TRANSCRIPT-THREADS (fn [] (run-detached (read-transcript-state request))) keep-nothing))
  (setv runtime (.runtime host request.session-id))
  (setv state
        (if (is runtime None)
            ;; 閉じて生きた状態を外した会話も閉じた様子で答える。
            (if (is (.closed-session host request.session-id) None) (Idle) (Closed))
            (with [runtime.lock]
              (setv running (.running-turn runtime))
              (cond
                (is-not running None) (TurnRunning running)
                runtime.closed (Closed)
                True (Idle)))))
  (SessionStatus state transcript))

(defk read-session-export [request]
  {:pre [(: request ClaudeExportSession)] :post [(: % (| SessionExported SessionNotFound))] :tags {:context "claude-code" :role "foundation"}}
  "transcript の jsonl 1 つを utf-8 で読むため(置き場は session-status と同じ規則・tail-bytes が在れば末尾だけを行の頭から —
   card ki-a264a73efcce・from-byte が在ればその byte 目から後ろだけ — card ki-3b521394a063)。無い・空白だけ = SessionNotFound(空白の
   判定は strip で写し直さず isspace で探すだけ)。from-byte より短い・from-byte が字の途中も SessionNotFound。"
  (val path (transcript-path request.home.config-dir (os.path.realpath request.cwd) request.session-id))
  (when (is-not request.from-byte None)
    (val grown (read-transcript-from path request.from-byte))
    (when (is grown None) (return (SessionNotFound request.session-id)))
    (val after (try
                 (.decode grown "utf-8")
                 (except [UnicodeDecodeError] None)))
    (return (if (and after (not (.isspace after))) (SessionExported after) (SessionNotFound request.session-id))))
  (val read (read-transcript-bytes path request.tail-bytes))
  (when (is read None) (return (SessionNotFound request.session-id)))
  (val text (.decode read.data "utf-8"))
  (if (and text (not (.isspace text))) (SessionExported text) (SessionNotFound request.session-id)))

(defk export-session [#^ ClaudeExportSession request]
  {:pre [(: request ClaudeExportSession)] :post [(: % (| SessionExported SessionNotFound))]}
  "transcript を書き出すため(ClaudeExportSession)。数十 MB の jsonl の読みは TRANSCRIPT-THREADS の thread で待つ(頭の註の下の値の註)。"
  (<- exported (| SessionExported SessionNotFound) (offloaded TRANSCRIPT-THREADS (fn [] (run-detached (read-session-export request))) keep-nothing))
  exported)

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
  (when (is runtime None)
    ;; 閉じて生きた状態を外した会話は、記録の起こした数と降ろした訳で答える(外す前と同じ見え方)。
    (val closed (.closed-session host session-id))
    (return (if (is closed None)
                (NoLiveProcess :launches 0 :stopped-because None)
                (NoLiveProcess :launches closed.launches :stopped-because closed.stopped-because))))
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


(defk replace-session-credential [#^ ClaudeCodeHost host #^ ClaudeReplaceSessionCredential request]
  {:pre [(: host ClaudeCodeHost) (: request ClaudeReplaceSessionCredential)]
   :post [(: % (| CredentialReplaced NoSuchSession SessionProcessDown HomeCredentialSession))]
   :tags {:context "claude-code" :role "foundation"}}
  "会話の生きた process の資格を替えるため(ClaudeReplaceSessionCredential): 会話の lock の中で、生きて降りる途中でない process の資格の
   家の .credentials.json を別名に書いて rename で替え(credential_home.hy の replace-credential)、止める刻を新しい期限から決め直す。
   知らない会話 = NoSuchSession・生きた process が無い(降りた・降りる途中)= SessionProcessDown・家の資格で起こした process =
   HomeCredentialSession。手番を走らせているかは問わない(手番の途中でも、CLI は次の API の呼びから新しい token を使う)。"
  (val session-id request.session-id)
  (val runtime (.runtime host session-id))
  (when (is runtime None)
    ;; 閉じて生きた状態を外した会話の process は降りている(外す前と同じ答え)。
    (return (if (is (.closed-session host session-id) None)
                (NoSuchSession :session-id session-id)
                (SessionProcessDown :session-id session-id))))
  (with [runtime.lock]
    (val binding runtime.binding)
    (when (or (is binding None) (is binding.process None) (not (.alive binding.process))
              (is-not binding.process.retiring None))
      (return (SessionProcessDown :session-id session-id)))
    (val home binding.credential-home)
    (when (is home None)
      (return (HomeCredentialSession :session-id session-id)))
    (run (replace-credential home request.credential))
    (setv runtime.retire-after (retire-time request.credential host.credential-floor-seconds)))
  (CredentialReplaced :session-id session-id))


;; --- handler -----------------------------------------------------------------------------------

(defhandler claude-code-handler [host]
  (ClaudeStartTurn [origin spec input]
    (<- outcome (start-turn host effect))
    (resume outcome))
  (ClaudeInjectInput [turn input]
    (resume (inject-input host turn input)))
  (ClaudeInterruptTurn [turn for-input]
    (resume (interrupt-turn host turn for-input)))
  (ClaudeReadTurnEvents [turn after-seq wait-up-to]
    (<- page (read-events host effect))
    (resume page))
  (ClaudeAnswerPermission [turn request-id answer]
    (resume (answer-permission host effect)))
  (ClaudeCloseSession [session-id reason]
    (<- closed (close-session host effect))
    (resume closed))
  (ClaudeSessionStatus [home cwd session-id]
    (<- (retire-under-floor host))
    (<- status (session-status host effect))
    (resume status))
  (ClaudeExportSession [home cwd session-id]
    (<- exported (export-session effect))
    (resume exported))
  (ClaudeWarmSession [origin spec]
    (<- warmed (warm-session host effect))
    (resume warmed))
  (ClaudeReplaceSessionCredential [session-id credential]
    (<- replaced (replace-session-credential host effect))
    (resume replaced))
  (ClaudeDropProcess [session-id]
    (resume (drop-process host session-id)))
  (ClaudeLiveProcess [session-id]
    (<- view (live-process host session-id))
    (resume view))
  (ClaudeEmitOutsideTurn [session-id]
    (<- emitted (emit-outside host session-id))
    (resume emitted)))
