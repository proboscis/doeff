;;; process の handler — 公開 effect 5 つと故障の注入 RunVanish に、手元の子 process で答える(docker の無い手元の開発だけ。zeus の
;;; 本物の検は docker、本番の handler は k3s)。
;;;
;;; run 1 つ = 子 process 1 つ。StartProcess で新しい process group に立て(止めは group へ送るので孫も止まる)、立てた process
;;; と一緒に終わる(StartProcess の既定の寿命)。image は「実行 file の入った dir」と読む: host の表(ProcessRunHost.images)で image の名 →
;;; dir に引き、dir を子の PATH の先頭に置く(spec の env が PATH を持てばそれが勝つ — docker で env の PATH が image の PATH を置き換えるのと同じ)。
;;; 表に無い image は取り寄せられない image として、命令の無い run(process に image の既定の命令は無い)と命令が起こせない run は、
;;; 受け付けた後に起きなかった止まり(NEVER-BEGAN)で届く。
;;;
;;; process には run の中の file system も、子へ課す資源の上限も無い。volume と資源の上限は守れない設定として RunRefused(UNSUPPORTED)で
;;; 断る(黙って無視しない — 上限を無視した run は機体を食い尽くす)。volume の効果(VolumeEnsure・VolumeStatus)も同じ訳で VolumeRefused
;;; (UNSUPPORTED)と答える。止まった訳は EXITED と REQUESTED の 2 つで、OUT-OF-MEMORY は無い。
;;; 終了 code は docker・shell と同じ数にする: signal で死んだ子(戻り値が負)は 128 + signal の番号。
;;;
;;; 状態と出来事は host が覚える(OS は子の終わりの刻を残さない)。子は立てた時にもう走っているので、受け付けは RunLive まで進む
;;; (RunPending は無い)。止まりは handler が気づいた時に記録する: 読みの待ちは子の終わりで起き(AwaitProcessExit — pidfd・kqueue の fd が
;;; 読めた時。時間で見に行かない)、読んでいる間の止まりは終わりの刻に、誰も見ていない間の止まりは次に見た刻になる。
;;;
;;; 止めの最中(stopping)は、止める task か消す task だけが止まりを記録する — 読み手や状態を引く task は子を回収せず、止めの終わりの呼び鈴で
;;; 起きる。止まった訳は止めを頼んだ刻(requested-at)が決める(頼んだ後の止まりは REQUESTED)。止めの 2 度目の頼みは、始まった止めの終わりを
;;; 待つ。読みの途中で run が消えたら、その読みは RunGone を最後の出来事として返す(消えた後の読みは、知らない取っ手として空の頁と RunGone)。
;;;
;;; 外側に RunProcess の答え手でなく子の答え手(offloaded-subprocess-handler — StartProcess・PollProcess・StopProcess・ReadEnvironment)と、
;;; 終わりの待ち(process-exit-handler と await-handler)と時計(GetTime・GetMonotonic・RaceWithin)と scheduled が要る。
(require doeff-hy.macros [defhandler defk <- val var])
(import os)
(import types)
(import uuid)
(import collections.abc [Mapping])
(import datetime [datetime])
(import doeff_time [GetMonotonic GetTime RaceWithin])
(import doeff_core_effects [AwaitProcessExit Cancel CreateExternalPromise EnvEntry EnvMode PollProcess ProcessExited ProcessNotStarted
                            ReadEnvironment Spawn StartProcess StopProcess])
(import doeff_run._values [IMAGE-NAME RunSpec RunHandle RunLive RunStopped RunGone RunState StopCause])
(import doeff_run._effects [RunStart RunStop RunRemove RunStatus RunReadEvents RunAccepted RunNameInUse RunRefused RefusalWhy
                            StopFailed RunStillAlive RunEvent RunEventPage VolumeEnsure VolumeStatus VolumeRefused])
(import doeff_run._faults [RunVanish])

;; 眠りの下限と期限の比べの余裕(秒)— 期限の直前で待ちを繰り返さない。
(val MIN-SLEEP 1e-3)
(val DEADLINE-SLACK 1e-3)
;; 2 度目の止めの頼みが、始まった止めの終わりを待つ上限に足す余裕の秒(止めは SIGTERM の猶予の後に SIGKILL で終わる)。
(val STOP-MARGIN 30.0)
;; 環境に PATH が無い時、image の dir の後ろに置く既定。
(val DEFAULT-PATH "/usr/local/bin:/usr/bin:/bin")
(val UNKNOWN-HANDLE "取っ手の run を host が知らない")
(val REMOVED "片付けた")
(val VANISHED "host の外から消えた")


(defclass ProcessRun []
  "host が覚える run 1 つ: handle・pid(起きなかった run は None)・state(今の状態の値)・events(出来事の列)・requested-at(止めを頼んだ刻 —
   止まった訳を決める)・stopping(止めか消しの最中 — 頭の註)・bells(待ちの呼び鈴 — 状態が変わると鳴らす)。"
  (defn __init__ [self #^ RunHandle handle #^ (| int None) pid #^ RunState state #^ tuple events]
    (setv self.handle handle self.pid pid self.state state self.events events)
    (setv #^ (| datetime None) self.requested-at None)
    (setv #^ bool self.stopping False)
    (setv #^ tuple self.bells #())))


(defclass ProcessRunHost []
  "process の handler が覚える物。images = image の名 → 実行 file の dir(絶対 path)の表(作る時に写し取る)・runs = 名 → ProcessRun
   (片付けるか消えるまで名を持つ)。live-pids = 走っている(と覚えている)子の pid(検の後始末が止める)。"
  (defn __init__ [self * #^ (get Mapping #(str str)) images]
    (when (not (isinstance images Mapping))
      (raise (TypeError (.format "ProcessRunHost.images は image の名 → dir の写像: {!r}" images))))
    (for [#(name directory) (.items images)]
      (when (not (and (isinstance name str) (isinstance directory str)))
        (raise (TypeError (.format "ProcessRunHost.images の名と dir は文字列: {!r} → {!r}" name directory))))
      (when (not (IMAGE-NAME.fullmatch name))
        (raise (ValueError (.format "ProcessRunHost.images の名は空白を含まない 1 語: {!r}" name))))
      (when (not (os.path.isabs directory))
        (raise (ValueError (.format "ProcessRunHost.images の dir は絶対 path: {!r} → {!r}" name directory)))))
    (setv #^ (get Mapping #(str str)) self.images (types.MappingProxyType (dict images)))
    (setv #^ (get dict #(str ProcessRun)) self.runs {}))
  (defn [property] #^ (get tuple #(int ...)) live-pids [self]
    (tuple (gfor run (.values self.runs) :if (and (isinstance run.state RunLive) (is-not run.pid None)) run.pid))))


;; --- 純な訳 --------------------------------------------------------------------------------------------------

(defn #^ (| str None) unsupported-of [#^ RunSpec spec]
  "process の handler が守れない設定の訳の文(守れるなら None): run の中の path に置けない volume・子に課せない資源の上限。env の値は写さない。"
  (for [volume spec.volumes]
    (return (.format "volume {!r} を run の中の {!r} に置けない(process の子は host の file system をそのまま見る)" volume.source volume.target)))
  (setv limits spec.limits)
  (when (is-not limits.memory-bytes None)
    (return (.format "メモリの上限 {} byte を課せない(process の子に上限を課す手立てが無い)" limits.memory-bytes)))
  (when (is-not limits.cpu-millis None)
    (return (.format "CPU の上限 {} ミリ core を課せない(process の子に上限を課す手立てが無い)" limits.cpu-millis)))
  None)

(defn #^ (| str None) image-dir-of [#^ ProcessRunHost host #^ RunSpec spec]
  "spec の image が引く実行 file の dir(host の表に無ければ None — 取り寄せられない image)。"
  (.get host.images spec.image))

(defn #^ str child-path [#^ str image-dir #^ (| str None) inherited]
  "子の PATH: image の dir を先頭に、継ぐ PATH(無ければ既定)を後ろに。"
  (+ image-dir os.pathsep (or inherited DEFAULT-PATH)))

(defn #^ int exit-code-of [#^ int returncode]
  "子の戻り値を終了 code に直す: signal で死んだ子(戻り値が負)は shell・docker と同じ 128 + signal の番号。"
  (if (< returncode 0) (- 128 returncode) returncode))

(defn #^ RunStopped stopped-state [#^ ProcessRun run #^ int returncode]
  "子が終わった時の止まった状態: 止めを頼んだ後なら REQUESTED・でなければ自分で終わった EXITED。"
  (RunStopped :exit-code (exit-code-of returncode)
              :cause (if (is-not run.requested-at None) StopCause.REQUESTED StopCause.EXITED)))

(defn #^ datetime not-before [#^ tuple events #^ datetime at]
  "出来事の刻は列の最後より前にならない(気づいた刻の時計が出来事を追い越さない)。"
  (if events (max at (. (get events -1) at)) at))

(defn #^ None ring [#^ ProcessRun run]
  "run に掛かった待ちの呼び鈴を全部鳴らして外す(状態が変わった — 待っている物は起きて読み直す)。"
  (setv bells run.bells)
  (setv run.bells #())
  (for [bell bells]
    (.complete bell None))
  None)

(defn #^ (| ProcessRun None) lookup [#^ ProcessRunHost host #^ RunHandle handle]
  "取っ手の run を host から引く: 名の持ち主で、run-id が同じ run だけ。名が空いている・別の run(走らせ直した)なら None。"
  (setv run (.get host.runs handle.name))
  (if (and (is-not run None) (= run.handle.run-id handle.run-id)) run None))


;; --- 子の呼びと記録 ------------------------------------------------------------------------------------------

(defk inherited-path []
  {:pre [] :post [(: % (| str None))]}
  "この process の PATH(環境に無ければ None)を、子の PATH の後ろに継ぐため。"
  (<- entries (ReadEnvironment #("PATH")))
  (if entries (. (get entries 0) value) None))

(defk child-ended [#^ int pid]
  {:pre [(: pid int)] :post [(: % (type None))]}
  "子 pid の終わりを待つ task の体(読みの待ちが期限と競わせる)。回収はしない — 終了 code は後の PollProcess が答える。"
  (<- _ended (AwaitProcessExit pid))
  None)

(defk conclude [#^ ProcessRun run #^ (| RunStopped RunGone) state]
  {:pre [(: run ProcessRun) (: state (| RunStopped RunGone))] :post [(: % (type None))]}
  "run が終わりの状態(止まった・消えた)に入った事を記録するため: 状態を替え、出来事を足し、待っている物を起こす。"
  (<- now-at (GetTime))
  (setv run.state state)
  (setv run.events (+ run.events #((RunEvent :seq (+ (len run.events) 1) :at (not-before run.events now-at) :state state))))
  (ring run)
  None)

(defk observe [#^ ProcessRun run]
  {:pre [(: run ProcessRun)] :post [(: % (type None))]}
  "走っている run の子が終わったかを待たずに問い、終わっていれば止まりを記録するため。止めか消しの最中は、止める task が記録するので問わない。"
  (when (or run.stopping (is run.pid None) (not (isinstance run.state RunLive)))
    (return None))
  (<- polled (PollProcess run.pid))
  (when (isinstance polled ProcessExited)
    (<- (conclude run (stopped-state run polled.exit-code))))
  None)

(defk state-of [#^ ProcessRunHost host #^ RunHandle handle]
  {:pre [(: host ProcessRunHost) (: handle RunHandle)] :post [(: % tuple)]}
  "取っ手の run の #(状態 出来事の列 run か None)。知らない取っ手は #(RunGone #() None)。"
  (val run (lookup host handle))
  (when (is run None)
    (return #((RunGone :detail UNKNOWN-HANDLE) #() None)))
  (<- (observe run))
  #(run.state run.events run))

(defk wait-change [#^ ProcessRun run #^ float seconds]
  {:pre [(: run ProcessRun) (: seconds float)] :post [(: % (type None))]}
  "run の状態が記録される(呼び鈴)か、子が終わるか、seconds 秒が過ぎるまで眠るため。子の終わりを待つのは止めの最中でない時だけ(止めの最中は
   止める task が記録して鳴らす — 回収済みの子を待たない)。呼び鈴は眠る前に掛け、起きたら外す。子の終わりを待つ task は負けたら取り消して捨てる。
   呼び鈴を掛ける前に記録が終わっていれば鳴らす者はもう居ないので、掛けた後に状態を見直して、終わっていれば眠らずに戻る。"
  (<- bell (CreateExternalPromise))
  (setv run.bells (+ run.bells #(bell)))
  (var watchers #())
  (try
    (when (isinstance run.state RunLive)
      (when (and (not run.stopping) (is-not run.pid None))
        (<- watcher (Spawn (child-ended run.pid) :daemon True))
        (:= watchers #(watcher)))
      (<- _woke (RaceWithin (+ watchers #(bell.future)) (max MIN-SLEEP seconds))))
    (finally
      (setv run.bells (tuple (gfor other run.bells :if (is-not other bell) other)))
      (for [watcher watchers]
        (<- (Cancel watcher)))))
  None)

(defk mark-unmade [#^ ProcessRunHost host #^ RunHandle handle #^ str detail]
  {:pre [(: host ProcessRunHost) (: handle RunHandle) (: detail str)] :post [(: % RunAccepted)]}
  "子を立てられなかった run を、起きなかった止まり(NEVER-BEGAN)として覚えて受け付けるため。片付けるまで名を持つ。"
  (<- now-at (GetTime))
  (val state (RunStopped :exit-code None :cause StopCause.NEVER-BEGAN :detail detail))
  (setv (get host.runs handle.name) (ProcessRun handle None state #((RunEvent :seq 1 :at now-at :state state))))
  (RunAccepted :handle handle))


;; --- effect の答え -------------------------------------------------------------------------------------------

(defk process-start [#^ ProcessRunHost host #^ RunStart request]
  {:pre [(: host ProcessRunHost) (: request RunStart)] :post [(: % (| RunAccepted RunNameInUse RunRefused))]}
  "走らせ始めを受け付けるため: 名の持ち主が在れば取っ手を答え・守れない設定は断り・image が引けない物と命令の無い物は起きなかった run として
   受け付け・それ以外は子を立てて起きた run として覚える(立てられない命令は起きなかった run)。"
  (val name request.name)
  (val spec request.spec)
  (val held (.get host.runs name))
  (when (is-not held None)
    (return (RunNameInUse :handle held.handle)))
  (val refusal (unsupported-of spec))
  (when (is-not refusal None)
    (return (RunRefused :name name :why RefusalWhy.UNSUPPORTED :detail refusal)))
  (val handle (RunHandle :name name :run-id (+ "process-" (. (uuid.uuid4) hex))))
  (val image-dir (image-dir-of host spec))
  (when (is image-dir None)
    (<- accepted (mark-unmade host handle (.format "image {!r} を取り寄せられない(host の images に無い)" spec.image)))
    (return accepted))
  (when (not spec.command)
    (<- accepted (mark-unmade host handle "命令が無い(process には image の既定の命令が無い)"))
    (return accepted))
  (<- inherited (inherited-path))
  (val env {"PATH" (child-path image-dir inherited)})
  (.update env spec.env)
  ;; 引数でなく環境で渡す(env の値は命令の引数に載らない)。呼び手の環境を継いだ上で足す。
  (<- started (StartProcess :argv spec.command
                            :env (tuple (gfor #(key value) (sorted (.items env)) (EnvEntry :name key :value value)))
                            :env-mode EnvMode.EXTEND :process-group True :reap-group True))
  (when (isinstance started ProcessNotStarted)
    (<- accepted (mark-unmade host handle started.detail))
    (return accepted))
  (<- live-at (GetTime))
  (setv (get host.runs name) (ProcessRun handle started.pid (RunLive) #((RunEvent :seq 1 :at live-at :state (RunLive)))))
  (RunAccepted :handle handle))

(defk process-volume-refusal [#^ str name]
  {:pre [(: name str)] :post [(: % VolumeRefused)]}
  "volume の効果(作る・問う)を断るため: process の子は host の file system をそのまま見るので、volume を持たない。名の形によらず UNSUPPORTED
   (「無い」と答えると、置き場が消えた事と見分けがつかない・作ったことにもしない)。"
  (VolumeRefused :name name :why RefusalWhy.UNSUPPORTED
                 :detail (.format "volume {!r} を作れず問えない(process の子は host の file system をそのまま見る)" name)))

(defk process-stop [#^ ProcessRunHost host #^ RunStop request]
  {:pre [(: host ProcessRunHost) (: request RunStop)] :post [(: % (| RunStopped RunGone StopFailed))]}
  "run を止めるため: 止まった run にはその状態・走っている run には止めを頼んだ刻を覚えてから StopProcess(SIGTERM → 猶予 → SIGKILL)で止めて
   止まった状態を記録して答える。止めの最中の頼みは、始まった止めの終わりを待つ。"
  (val handle request.handle)
  (val run (lookup host handle))
  (when (is run None)
    (return (RunGone :detail UNKNOWN-HANDLE)))
  (<- (observe run))
  (when (not (isinstance run.state RunLive))
    (return run.state))
  (when run.stopping
    (<- started (GetMonotonic))
    (val deadline (+ started request.grace-seconds STOP-MARGIN))
    (while (isinstance run.state RunLive)
      (<- now (GetMonotonic))
      (when (>= now (- deadline DEADLINE-SLACK))
        (break))
      (<- (wait-change run (- deadline now))))
    (return (if (isinstance run.state RunLive)
                (StopFailed :handle handle :detail "始まった止めが猶予の後も終わらない")
                run.state)))
  (setv run.stopping True)
  (<- now-at (GetTime))
  (setv run.requested-at now-at)
  (<- stopped (StopProcess :pid run.pid :stop-grace request.grace-seconds))
  (when (and (isinstance stopped ProcessExited) (isinstance run.state RunLive))
    (<- (conclude run (stopped-state run stopped.exit-code))))
  (if (isinstance run.state RunLive)
      (StopFailed :handle handle :detail "StopProcess の後も、子の終了 code を受け取れなかった")
      run.state))

(defk process-remove [#^ ProcessRunHost host #^ RunRemove request]
  {:pre [(: host ProcessRunHost) (: request RunRemove)] :post [(: % (| RunGone RunStillAlive))]}
  "止まった run の跡を片付けるため: host の覚えから外して名を空ける(子はもう居ない)。止まっていない run は断る。"
  (val run (lookup host request.handle))
  (when (is run None)
    (return (RunGone :detail UNKNOWN-HANDLE)))
  (<- (observe run))
  (when (not (isinstance run.state RunStopped))
    (return (RunStillAlive :handle run.handle)))
  (del (get host.runs run.handle.name))
  (RunGone :detail REMOVED))

(defk process-status [#^ ProcessRunHost host #^ RunStatus request]
  {:pre [(: host ProcessRunHost) (: request RunStatus)] :post [(: % RunState)]}
  "run の今の状態を、子に問って答えるため。"
  (<- seen (state-of host request.handle))
  (get seen 0))

(defk process-read-events [#^ ProcessRunHost host #^ RunReadEvents request]
  {:pre [(: host ProcessRunHost) (: request RunReadEvents)] :post [(: % RunEventPage)]}
  "after-seq より後の出来事を、新しい出来事が起きるか終わっているか期限が来るまで待って頁にするため。待ちは子の終わりか状態の呼び鈴で起きる。
   読みの途中で run が消えたら、RunGone を最後の出来事に足す(頭の註)。"
  (val after request.after-seq)
  (<- started (GetMonotonic))
  (val deadline (+ started request.wait-up-to))
  (var known None)
  (while True
    (<- seen (state-of host request.handle))
    (val state (get seen 0))
    (var events (get seen 1))
    (when (and (isinstance state RunGone) (is-not known None))
      ;; この読みが見ていた run が消えた: 見た列に消えた出来事を足す。
      (<- now-at (GetTime))
      (:= events (+ known #((RunEvent :seq (+ (len known) 1) :at (not-before known now-at) :state state)))))
    (:= known events)
    (val fresh (tuple (gfor event events :if (> event.seq after) event)))
    (<- now (GetMonotonic))
    (when (or fresh (isinstance state #(RunStopped RunGone)) (>= now (- deadline DEADLINE-SLACK)))
      (return (RunEventPage :events fresh :next-seq (if fresh (. (get fresh -1) seq) after) :state state)))
    (<- (wait-change (get seen 2) (- deadline now)))))

(defk process-vanish [#^ ProcessRunHost host #^ RunVanish request]
  {:pre [(: host ProcessRunHost) (: request RunVanish)] :post [(: % bool)]}
  "run を handler の知らない所で消すため(検の口): 走っている子は猶予なしで止め、覚えから外して、待っている読みを RunGone で起こす。
   答え = 消す run が在ったか。"
  (val run (lookup host request.handle))
  (when (is run None)
    (return False))
  (when (and (isinstance run.state RunLive) (is-not run.pid None))
    (setv run.stopping True)
    (<- (StopProcess :pid run.pid :stop-grace 0.0)))
  (del (get host.runs run.handle.name))
  (<- (conclude run (RunGone :detail VANISHED)))
  True)


;; --- handler -------------------------------------------------------------------------------------------------

;; 引数に残す理由: host は handler の覚え(名ごとの run・止めを頼んだ刻)を持つ資源で、検が同じ host の走っている子を後始末に読む
;; (fake-run-handler の world・docker-run-handler の host と同じ形)。
(defhandler process-run-handler [#^ ProcessRunHost host]
  "DoeffRun の process の handler: 公開 effect 7 つ(volume の 2 つは UNSUPPORTED で断る)と故障の注入 RunVanish に、host の子 process で答える。外側に子の答え手
   (offloaded-subprocess-handler)と終わりの待ち(process-exit-handler・await-handler)と時計と scheduled が要る。"
  (RunStart [name spec]
    (<- outcome (process-start host effect))
    (resume outcome))
  (RunStop [handle grace-seconds]
    (<- outcome (process-stop host effect))
    (resume outcome))
  (RunRemove [handle]
    (<- outcome (process-remove host effect))
    (resume outcome))
  (RunStatus [handle]
    (<- state (process-status host effect))
    (resume state))
  (RunReadEvents [handle after-seq wait-up-to]
    (<- page (process-read-events host effect))
    (resume page))
  (VolumeEnsure [name]
    (<- refused (process-volume-refusal effect.name))
    (resume refused))
  (VolumeStatus [name]
    (<- refused (process-volume-refusal effect.name))
    (resume refused))
  (RunVanish [handle]
    (<- vanished (process-vanish host effect))
    (resume vanished)))
