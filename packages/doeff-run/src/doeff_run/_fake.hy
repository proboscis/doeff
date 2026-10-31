;;; fake の handler — 本物の handler(docker・process・k3s)と同じ公開 effect に、memory の上で答える。
;;;
;;; 世界(FakeRunWorld)= 名 → run の記録。run の状態は「世界が持つ状態の値」で、変わり目は仮想の時計(doeff-time の GetMonotonic・
;;; GetTime — sim-time-handler の下では待ちも一瞬で進む)の刻で決まる。run の振る舞いは FakeBehavior(起きるまでの秒・走る秒・
;;; 終わり方・止めの合図への応じ方)が決め、世界が RunSpec から振る舞いを引く関数(behavior-of)で選ぶ。
;;;
;;; 状態は effect を受けた時と読みの待ちが起きた時に、今の仮想の刻まで進める(時間で見に行かない)。読みの待ちは、次の変わり目の刻か
;;; 期限まで眠り、待ちの外から状態が変わった時(止める・消える・片付ける)は呼び鈴で起きる — 読み手は次の変わり目の刻まで眠り続けない。
;;;
;;; 本番と共通の不変条件(名の重なりは持ち主の取っ手で断る・受け付けは起きるを待たない・出来事は 1 から欠けずに増える・止めは冪等・
;;; 止めの答えが返る時は run がもう走っていない・消えた run は RunGone・片付けは止まった run だけ)は、契約の検(tests/test_contract.hy)が
;;; 同じ筋書きで確かめる。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import datetime [datetime timedelta])
(import doeff_time [GetMonotonic GetTime WaitWithin])
(import doeff_core_effects [CreateExternalPromise])
(import doeff_run._values [RunSpec RunHandle RunPending RunLive RunStopped RunGone RunState StopCause])
(import doeff_run._effects [RunStart RunStop RunRemove RunStatus RunReadEvents RunAccepted RunNameInUse RunRefused RefusalWhy
                            StopFailed RunStillAlive RunEvent RunEventPage])
(import doeff_run._faults [RunVanish])

;; 仮想の時計は datetime(マイクロ秒の刻み)なので、秒の小数の足し算の端数で「期限の直前」に留まらないように、期限の比べに刻み 1 つ分の
;; 余裕を置き、眠りは刻み以上にする。
(val CLOCK-TICK 1e-6)
(val MIN-SLEEP 1e-3)
;; 強制で止められた run の終了 code(128 + SIGKILL の 9 — メモリで殺された run も同じ)。
(val KILLED-EXIT-CODE 137)
(val UNKNOWN-HANDLE "取っ手の run を世界が知らない")


(defrecord FakeBehavior
  "fake の run の振る舞い: begin-after = 受け付けてから起きるまでの秒(image を取り寄せる秒)・runs-for = 起きてから自分で終わるまでの
   秒(None = 止められるまで走る)・exit-code = 自分で終わる時の終了 code・out-of-memory = 自分で終わる代わりにメモリの上限を越えて
   殺される(runs-for の刻に OUT-OF-MEMORY・終了 code は 137)・stop-takes = 止めの合図を受けてから終わるまでの秒(None = 合図を無視して
   猶予が尽きるまで走る — 猶予より長くかかる run も同じく強制で止められる)・exit-on-stop = 合図で終わる時の終了 code(既定 143 = 128 + SIGTERM の 15)・
   refuses-to-begin = 起きずに止まる訳の文(image を取り寄せられない など。None = 起きる)・refuses-to-stop = 止めを断る訳の文
   (None = 止められる — 止めの答えは StopFailed)。"
  {:tags {:context "run" :role "type"}
   :check [(>= begin-after 0.0)
           (or (is runs-for None) (> runs-for 0.0))
           (and (isinstance exit-code int) (not (isinstance exit-code bool)))
           (or (not out-of-memory) (is-not runs-for None))
           (or (is stop-takes None) (>= stop-takes 0.0))
           (and (isinstance exit-on-stop int) (not (isinstance exit-on-stop bool)))
           (or (is refuses-to-begin None) (and (isinstance refuses-to-begin str) (bool refuses-to-begin)))
           (or (is refuses-to-stop None) (and (isinstance refuses-to-stop str) (bool refuses-to-stop)))]}
  (setv #^ float begin-after 0.1)
  (setv #^ (| float None) runs-for None)
  (setv #^ int exit-code 0)
  (setv #^ bool out-of-memory False)
  (setv #^ (| float None) stop-takes 0.1)
  (setv #^ int exit-on-stop 143)
  (setv #^ (| str None) refuses-to-begin None)
  (setv #^ (| str None) refuses-to-stop None))


(defclass FakeRun []
  "世界が覚える run 1 つ: handle・spec・behavior・begin-at(起きる刻 — 仮想の時計の秒)・state(今の状態の値)・live-since(起きた刻)・
   stop-at(止めが始まった時に決めた #(止まる刻 終了 code))・events(出来事の列)・bells(読みの待ちの呼び鈴)。"
  (defn __init__ [self #^ RunHandle handle #^ RunSpec spec #^ FakeBehavior behavior #^ float begin-at]
    (setv self.handle handle self.spec spec self.behavior behavior self.begin-at begin-at)
    (setv #^ RunState self.state (RunPending))
    (setv #^ (| float None) self.live-since None)
    (setv #^ (| tuple None) self.stop-at None)
    (setv #^ (get tuple #(RunEvent ...)) self.events #())
    (setv #^ tuple self.bells #())))


(defclass FakeRunWorld []
  "fake の世界。behavior-of = RunSpec → FakeBehavior の同期の関数(走らせ始める時に 1 度引く)・capacity = 同時に受け付けて止まっていない
   (起きていない・走っている)run の数の上限(None = 上限なし — 越える頼みは RunRefused(NO-CAPACITY))・unsupported = RunSpec → この
   世界が守れない訳の文か None の関数(None = 全部守れる — 文を返すと RunRefused(UNSUPPORTED))。"
  (defn __init__ [self * #^ (get Callable #([RunSpec] FakeBehavior)) behavior-of #^ (| int None) [capacity None] #^ (| (get Callable #([RunSpec] (| str None))) None) [unsupported None]]
    (when (not (callable behavior-of))
      (raise (ValueError (.format "FakeRunWorld.behavior_of は RunSpec → FakeBehavior の関数: {!r}" behavior-of))))
    ;; 数の型は注記が持つ。bool は int の子なので名指しで断り、値の範囲を断る。
    (when (and (is-not capacity None) (or (not (isinstance capacity int)) (isinstance capacity bool) (< capacity 1)))
      (raise (ValueError (.format "FakeRunWorld.capacity は 1 以上の整数か None: {!r}" capacity))))
    (when (not (or (is unsupported None) (callable unsupported)))
      (raise (ValueError (.format "FakeRunWorld.unsupported は RunSpec → 文か None の関数か None: {!r}" unsupported))))
    (setv self.behavior-of behavior-of self.capacity capacity self.unsupported unsupported)
    (setv #^ int self.counter 0)
    ;; 片付けるか消えるまで名を持つ run だけを覚える(消えた run は世界に跡を残さない)。
    (setv #^ (get dict #(str FakeRun)) self.runs {})))


;; --- 状態の変わり目 -----------------------------------------------------------------------------------------

(defn #^ bool terminal? [state]
  "状態が終わり(止まった・消えた)か — もう自分では変わらない。"
  (isinstance state #(RunStopped RunGone)))

(defn #^ RunStopped self-end-of [#^ FakeBehavior behavior]
  "run が自分で終わる時の止まった状態を決めるため: out-of-memory なら OOM で殺された形・でなければ自分の終了 code。"
  (if behavior.out-of-memory
      (RunStopped :exit-code KILLED-EXIT-CODE :cause StopCause.OUT-OF-MEMORY)
      (RunStopped :exit-code behavior.exit-code :cause StopCause.EXITED)))

(defn #^ tuple stop-plan [#^ FakeBehavior behavior #^ float now #^ float grace]
  "止めを頼まれた刻 now に、止まる刻と終了 code を決めるため: 合図で終わるのが猶予の内なら合図の応じ方どおり・合図を無視する(か
   猶予より長くかかる)なら猶予が尽きた刻に強制で止める。"
  (if (and (is-not behavior.stop-takes None) (<= behavior.stop-takes grace))
      #((+ now behavior.stop-takes) behavior.exit-on-stop)
      #((+ now grace) KILLED-EXIT-CODE)))

(defn #^ (| tuple None) live-change [#^ FakeRun run]
  "起きている run の次の変わり目(#(刻 状態))を決めるため: 自分で終わる刻と、始まった止めが終わる刻の早い方(同じ刻なら自分の終わりが
   先)。どちらも無ければ None(呼び手の頼みを待つだけ)。"
  (setv own-end (if (is run.behavior.runs-for None)
                    #()
                    #(#((+ run.live-since run.behavior.runs-for) (self-end-of run.behavior)))))
  (setv asked-end (if (is run.stop-at None)
                      #()
                      #(#((get run.stop-at 0) (RunStopped :exit-code (get run.stop-at 1) :cause StopCause.REQUESTED)))))
  (setv ends (+ own-end asked-end))
  (if ends (min ends :key (fn [end] (get end 0))) None))

(defn #^ (| tuple None) next-change [#^ FakeRun run]
  "run が次に変わる刻と、変わった先の状態(#(刻 状態))。もう変わらない(止まった)か、呼び手の頼みだけで変わる(止めの前の走り続ける
   run)なら None。起きる前は起きる(か、起きずに止まる)刻。"
  (match run.state
    (RunPending)
      #(run.begin-at (if (is-not run.behavior.refuses-to-begin None)
                         (RunStopped :exit-code None :cause StopCause.NEVER-BEGAN :detail run.behavior.refuses-to-begin)
                         (RunLive)))
    (RunLive)
      (live-change run)
    _ None))

(defn #^ None ring [#^ FakeRun run]
  "run に掛かった読みの呼び鈴を全部鳴らして外す(状態が変わった — 待っている読みは起きて読み直す)。"
  (setv bells run.bells)
  (setv run.bells #())
  (for [bell bells]
    (.complete bell None))
  None)

(defn #^ None enter [#^ FakeRun run #^ float at-time state #^ float now #^ datetime now-at]
  "run が状態 state に入った事を記録するため: 状態を替え、出来事を足し、待っている読みを起こす。at-time = 入った刻(仮想の時計の秒)・
   now・now-at = 今の刻(秒と時刻 — 出来事の刻は今から at-time までの差を引いて、入った刻に合わせる)。"
  (when (isinstance state RunLive)
    (setv run.live-since at-time))
  (setv run.state state)
  (setv run.events (+ run.events #((RunEvent :seq (+ (len run.events) 1) :at (- now-at (timedelta :seconds (- now at-time))) :state state))))
  (ring run)
  None)

(defk advance [#^ FakeRun run]
  {:pre [(: run FakeRun)] :post [(: % (type None))]}
  "run の状態を今の仮想の刻まで進めるため: 刻が来た変わり目を、刻の順に全部記録する(読みが長く眠った後でも出来事の刻が正しい)。"
  (<- now (GetMonotonic))
  (<- now-at (GetTime))
  (var change (next-change run))
  (while (and (is-not change None) (<= (get change 0) (+ now CLOCK-TICK)))
    (enter run (get change 0) (get change 1) now now-at)
    (:= change (next-change run)))
  None)

(defk wait-for-change [#^ FakeRun run #^ float limit]
  {:pre [(: run FakeRun) (: limit float)] :post [(: % (type None))]}
  "run の状態が変わる(次の変わり目の刻か、待ちの外から呼び鈴が鳴る)か、刻 limit が来るまで眠るため。呼び鈴は眠る前に掛け、
   起きたら外す。"
  (<- now (GetMonotonic))
  (val change (next-change run))
  (val wake (if (is change None) limit (min limit (get change 0))))
  (<- bell (CreateExternalPromise))
  (setv run.bells (+ run.bells #(bell)))
  (<- _woke (WaitWithin bell.future (max MIN-SLEEP (- wake now)) :park True))
  (setv run.bells (tuple (gfor other run.bells :if (is-not other bell) other)))
  None)

(defk await-end [#^ FakeRun run]
  {:pre [(: run FakeRun)] :post [(: % (type None))]}
  "run が終わる(止まる・消える)まで待つため。待つのは run が自分で進む変わり目(自分の終わり・始まった止め)か、待ちの外からの
   変わり。"
  (while True
    (<- (advance run))
    (when (terminal? run.state)
      (return None))
    (val change (next-change run))
    (assert (is-not change None) "終わっていない run に次の変わり目が無い(待ち続ける)")
    (<- (wait-for-change run (get change 0)))))


;; --- 世界の読み書き -------------------------------------------------------------------------------------------

(defn #^ (| FakeRun None) lookup [#^ FakeRunWorld world #^ RunHandle handle]
  "取っ手の run を世界から引くため: 名の持ち主で、run-id が同じ run だけ。名が空いている・別の run(走らせ直した)なら None。"
  (setv run (.get world.runs handle.name))
  (if (and (is-not run None) (= run.handle.run-id handle.run-id)) run None))

(defk fake-start [#^ FakeRunWorld world #^ RunStart request]
  {:pre [(: world FakeRunWorld) (: request RunStart)] :post [(: % (| RunAccepted RunNameInUse RunRefused))]}
  "走らせ始めを受け付けるため。名の持ち主が在れば取っ手を答え・守れない設定か空きが無ければ断り・それ以外は取っ手を決めて、起きる刻を
   予約する(起きるのは待たない)。"
  (val held (.get world.runs request.name))
  (when (is-not held None)
    (return (RunNameInUse :handle held.handle)))
  (val unsupported (if (is world.unsupported None) None (world.unsupported request.spec)))
  (when (is-not unsupported None)
    (return (RunRefused :name request.name :why RefusalWhy.UNSUPPORTED :detail unsupported)))
  (for [other (list (.values world.runs))]
    (<- (advance other)))
  (val active (len (lfor other (.values world.runs) :if (not (terminal? other.state)) other)))
  (when (and (is-not world.capacity None) (>= active world.capacity))
    (return (RunRefused :name request.name :why RefusalWhy.NO-CAPACITY
                        :detail (.format "同時に受け付けて止まっていない run は {} まで(fake)" world.capacity))))
  (val behavior (world.behavior-of request.spec))
  (when (not (isinstance behavior FakeBehavior))
    (raise (TypeError (.format "FakeRunWorld.behavior_of の答えは FakeBehavior: {!r}" behavior))))
  (+= world.counter 1)
  (val handle (RunHandle :name request.name :run-id (+ "fake-" (str world.counter))))
  (<- now (GetMonotonic))
  (setv (get world.runs request.name) (FakeRun handle request.spec behavior (+ now behavior.begin-after)))
  (RunAccepted :handle handle))

(defk fake-stop [#^ FakeRunWorld world #^ RunStop request]
  {:pre [(: world FakeRunWorld) (: request RunStop)] :post [(: % (| RunStopped RunGone StopFailed))]}
  "run を止めるため。止まった run にはその状態・起きる前の run は頼まれた止めで直ちに・走っている run には止めの刻と終了 code を
   決めて、終わるまで待つ(始まった止めは 2 度目の頼みも同じ終わりを待つ)。"
  (val run (lookup world request.handle))
  (when (is run None)
    (return (RunGone :detail UNKNOWN-HANDLE)))
  (<- (advance run))
  (when (terminal? run.state)
    (return run.state))
  (when (is-not run.behavior.refuses-to-stop None)
    (return (StopFailed :handle request.handle :detail run.behavior.refuses-to-stop)))
  (<- now (GetMonotonic))
  (<- now-at (GetTime))
  (cond
    (isinstance run.state RunPending)
      (enter run now (RunStopped :exit-code None :cause StopCause.REQUESTED) now now-at)
    (is run.stop-at None)
      (setv run.stop-at (stop-plan run.behavior now request.grace-seconds)))
  (<- (await-end run))
  run.state)

(defk fake-remove [#^ FakeRunWorld world #^ RunRemove request]
  {:pre [(: world FakeRunWorld) (: request RunRemove)] :post [(: % (| RunGone RunStillAlive))]}
  "止まった run の跡を片付けるため: 最後の出来事 RunGone を足して世界から外す(名が空く)。止まっていない run は断る。"
  (val run (lookup world request.handle))
  (when (is run None)
    (return (RunGone :detail UNKNOWN-HANDLE)))
  (<- (advance run))
  (when (not (isinstance run.state RunStopped))
    (return (RunStillAlive :handle run.handle)))
  (<- now (GetMonotonic))
  (<- now-at (GetTime))
  (val gone (RunGone :detail "片付けた"))
  (enter run now gone now now-at)
  (del (get world.runs run.handle.name))
  gone)

(defk fake-status [#^ FakeRunWorld world #^ RunStatus request]
  {:pre [(: world FakeRunWorld) (: request RunStatus)] :post [(: % (| RunPending RunLive RunStopped RunGone))]}
  "run の今の状態を、仮想の刻まで進めてから答えるため。"
  (val run (lookup world request.handle))
  (when (is run None)
    (return (RunGone :detail UNKNOWN-HANDLE)))
  (<- (advance run))
  run.state)

(defk fake-read-events [#^ FakeRunWorld world #^ RunReadEvents request]
  {:pre [(: world FakeRunWorld) (: request RunReadEvents)] :post [(: % RunEventPage)]}
  "after-seq より後の出来事を、新しい出来事が起きるか終わっているか期限が来るまで待って頁にするため。"
  (val run (lookup world request.handle))
  (when (is run None)
    (return (RunEventPage :events #() :next-seq request.after-seq :state (RunGone :detail UNKNOWN-HANDLE))))
  (<- started (GetMonotonic))
  (val deadline (+ started request.wait-up-to))
  (while True
    (<- (advance run))
    (val fresh (tuple (gfor event run.events :if (> event.seq request.after-seq) event)))
    (<- now (GetMonotonic))
    (when (or fresh (terminal? run.state) (>= now (- deadline CLOCK-TICK)))
      (return (RunEventPage :events fresh :next-seq (if fresh (. (get fresh -1) seq) request.after-seq) :state run.state)))
    (<- (wait-for-change run deadline))))

(defk fake-vanish [#^ FakeRunWorld world #^ RunVanish request]
  {:pre [(: world FakeRunWorld) (: request RunVanish)] :post [(: % bool)]}
  "run を世界の外の力で消すため(検の口): 最後の出来事 RunGone を足して世界から外す。止まった跡も消える。答え = 消す run が在ったか。"
  (val run (lookup world request.handle))
  (when (is run None)
    (return False))
  (<- (advance run))
  (<- now (GetMonotonic))
  (<- now-at (GetTime))
  (enter run now (RunGone :detail "backend から消えた(fake)") now now-at)
  (del (get world.runs run.handle.name))
  True)


;; --- handler -------------------------------------------------------------------------------------------------

(defhandler fake-run-handler [#^ FakeRunWorld world]
  "DoeffRun の fake の handler: 公開 effect 5 つと故障の注入 RunVanish に、世界 world の上で仮想の時計に沿って答える。"
  (RunStart [name spec]
    (<- outcome (fake-start world effect))
    (resume outcome))
  (RunStop [handle grace-seconds]
    (<- outcome (fake-stop world effect))
    (resume outcome))
  (RunRemove [handle]
    (<- outcome (fake-remove world effect))
    (resume outcome))
  (RunStatus [handle]
    (<- state (fake-status world effect))
    (resume state))
  (RunReadEvents [handle after-seq wait-up-to]
    (<- page (fake-read-events world effect))
    (resume page))
  (RunVanish [handle]
    (<- vanished (fake-vanish world effect))
    (resume vanished)))
