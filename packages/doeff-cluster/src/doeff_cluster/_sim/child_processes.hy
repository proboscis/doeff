;;; 模擬の子 process の答え手 — sim-cluster の job の process が StartProcess で起こす子(と、その子が起こす孫)を、本番の入れ物 shim
;;; (worker/entry/shim)と同じ寿命で走らせる(#2940 の条 C4b stopped-job-leaves-no-descendant)。
;;;
;;;   (sim-cluster system scenario :outside (SimOutside :handlers [...] :effects #() :children (SimChildren :commands #((ScriptedCommand …)))))
;;;
;;; 本番: shim は job を起こす前に自分を子孫の引き取り手(PR_SET_CHILD_SUBREAPER)にし、job の終わり・止めの合図・worker の消失で、引き
;;; 取った子孫を全部 SIGKILL する。別の session に切り離した孫(setsid -f・start_new_session)もこの片づけに当たる。worker の Pod が終われば
;;; (世代の終わり)、Pod の中の process は残らない。
;;; 模擬: 子 = 台本の命令(doeff-core-effects の ScriptedCommand — argv[0] の名で引く)の run を本体とする task。本体は時間をかけて
;;; よく(Delay)、file を書き(sim の外の世界の file の答え手)、孫を起こしてよい(本体の StartProcess も同じ答え手が受ける)。子の記録
;;; (SimDescendant — 起こした job の process・worker の世代・切り離したか)は sim の世界が持ち、世界が次の時に子を -9 で終わらせる
;;; (task を巻き戻さずに捨てる — Discard。finally は走らず、結果の file も知らせも書かない):
;;;   job の process の終わり   その process を根とする子孫の全部(版の入れ替えで退いた process の終わり・止め・Crash・自分での終わり —
;;;                             どれも同じ)。引き取りを外した変種(SimChildren の adoption 偽 — tests/fixtures/shim_without_adoption の
;;;                             代役)は、job の process group の中の子孫だけを終わらせ、切り離した子孫(とその子孫)は残す。
;;;   worker の世代の終わり      その世代で起きた子孫の全部(StopElder・StopWorker・死んだ worker — Pod ごと消える。引き取りに依らない)。
;;; 切り離した印: argv が setsid で始まる(続く - の旗は捨て、残りを子の argv として引く — 本番の setsid -f が返す pid は setsid の
;;; process だが、模擬は切り離した子そのものの pid を答える)か、StartProcess の lifetime が OUTLIVES-STARTER。
;;;
;;; 答える effect(job の process の柵の外側・sim の世界の内側 — 柵は CHILD-PROCESS-EFFECTS を通す):
;;;   StartProcess   台本に名の無い命令は ProcessNotStarted(本物と同じ文 — start-refusal)。出力の file は立てる前に末尾へ足す形で開き
;;;                  (開けなければ立てない)、子が自分で終わった時に台本の出力を足す。
;;;   PollProcess    自分が起こした子だけ(孫は ProcessNotChild)。走っている = ProcessRunning・終わった = ProcessExited(回収して忘れる)。
;;;   SignalProcess  走っている子を TERM = -15・KILL = -9 で終わらせる(台本の世界に信号の受け手は無い — 合図はどちらも即座の終わり)。
;;;   StopProcess    走っている子は -15 で終わらせて回収・終わった子はその終了 code で回収。猶予は台本の世界に無い。
;;; 本番との既知の差(検めない): cwd の在り処と env-mode の継ぎは確かめない(env は要求のまま台本へ渡す)・AwaitProcessExit と
;;; WriteProcessInput には答えない・子に止めの節は無い(TERM も即座の終わり)。
(require doeff-hy.macros [defk defhandler defeffect <- val var])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass replace])  ; dataclass = defrecord の展開が名指す
(import errno)
(import posixpath)
(import doeff [with-handlers])
(import doeff_core_effects [CreatePromise CompletePromise Wait Spawn Task Future Promise])
(import doeff_core_effects [AppendText FileFailed])
(import doeff_core_effects [ScriptedCommand ProcessOutcome RunProcess StartProcess PollProcess StopProcess SignalProcess ProcessSignal
                            ProcessStarted ProcessNotStarted ProcessRunning ProcessExited ProcessNotChild ProcessSignalled ChildLifetime
                            start-refusal])

;; 片づけで終わった子の終了 code(本物の SIGKILL と同じ負の値)と、TERM で終わった子の終了 code。
(val REAPED-CODE -9)
(val TERMINATED-CODE -15)
;; 切り離して起こす命令の名(argv[0] の basename)。
(val DETACHING-COMMAND "setsid")


(defrecord SimChildren
  "模擬の子 process の世界(sim-cluster の SimOutside の children): commands = 子の台本(argv[0] の basename で引く ScriptedCommand の tuple)・
   adoption = 入れ物の shim が子孫を引き取るか(真 = 本番の shim — job の終わりで子孫を全部片づける・偽 = 引き取りを外した変種 — 切り離した
   子孫は job の終わりの後も残る)。"
  (#^ (get tuple #(ScriptedCommand ...)) commands)
  (setv #^ bool adoption True))


(defrecord ChildOrigin
  "子孫を起こした job の process(root = sim の中の process の番号・job = job の名・worker = worker の名・boot = その process が走る worker の
   世代)。答え手の引数と子孫の記録が持つ。"
  (#^ int root)
  (#^ str job)
  (#^ str worker)
  (#^ str boot))


(defrecord SimDescendant
  "job の process の子孫 1 つ(DescendantsOf の答えの要素): pid = sim の中の番号・parent = 起こした process の番号(job の process なら
   origin.root)・argv = 引いた命令の argv(setsid を外した物)・escaped = job の process group の外か(切り離した子とその子孫)・started-ms /
   ended-ms = 起きた刻と終わった刻(走っている間は None)・exit-code = 終了 code(自分で終わった = 台本の答え・片づけ = -9・TERM = -15)・
   reaped = 片づけ(job の process の終わりか世代の終わり)で終わったか・collected = 起こした process が終わりを回収したか。"
  (#^ int pid)
  (#^ ChildOrigin origin)
  (#^ int parent)
  (#^ (get tuple #(str ...)) argv)
  (#^ bool escaped)
  (#^ int started-ms)
  (setv #^ (| int None) ended-ms None)
  (setv #^ (| int None) exit-code None)
  (setv #^ bool reaped False)
  (setv #^ bool collected False))


(defrecord ReapedDescendants
  "子孫を片づけた後の世界の欄の値(descendants = 番号 → SimDescendant・tasks = 走っている子孫の番号 → task)と、捨てる task の列。
   dict の欄は、sim の世界の session がその形で持つ欄の値をそのまま運ぶ(local.hy の ProcessEnds と同じ訳)。"
  (#^ dict descendants)
  (#^ dict tasks)
  (#^ (get tuple #(Task ...)) discarded))


;; --- sim の世界が答える effect --------------------------------------------------------------------------

(defeffect KeepDescendant
  "子孫 1 つを覚える(番号を配って記録を置き、task を持つ)。起こした job の process がもう終わっていて片づけに当たるか、世代がもう
   終わっていれば、その場で片づける。答え = 配った番号。"
  {:fields [(: origin ChildOrigin) (: parent int) (: argv (get tuple #(str ...))) (: escaped bool) (: task Task)] :answer int
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect DescendantEnded
  "子孫 pid が自分で終わった(終了 code code)。片づけ・合図で先に終わっていれば何もしない。"
  {:fields [(: pid int) (: code int)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect DescendantOf
  "子孫 pid の記録(無ければ None)。"
  {:fields [(: pid int)] :answer (| SimDescendant None) :tags {:context "doeff-cluster" :role "intent"}})

(defeffect SignalDescendant
  "走っている子孫 pid を終了 code code で終わらせる(task を捨てる)。答え = 走っていたか。"
  {:fields [(: pid int) (: code int)] :answer bool :tags {:context "doeff-cluster" :role "intent"}})

(defeffect CollectDescendant
  "子孫 pid の終わりを起こした process が回収した印を置く(その後の問いは ProcessNotChild)。"
  {:fields [(: pid int)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect DescendantsOf
  "検の effect: job name の process が起こした子孫の記録の列(SimDescendant の tuple・起きた順)。"
  {:fields [(: name str)] :answer (get tuple #(SimDescendant ...)) :tags {:context "doeff-cluster" :role "intent"}})


;; 柵が job の process から外へ通す子 process の effect の型(答え手 child-processes が答える物 — 足したら同じ変更でここも足す)。
(val CHILD-PROCESS-EFFECTS #(StartProcess PollProcess SignalProcess StopProcess))


;; --- 判断 -----------------------------------------------------------------------------------------------

(defrecord ChildArgv
  "StartProcess の argv を読んだ形(argv = 子として引く命令の argv・detached = setsid で切り離して起こすか)。"
  (#^ (get tuple #(str ...)) argv)
  (#^ bool detached))


(defk detached-argv [argv]
  {:pre [(: argv (get tuple #(str ...)))] :post [(: % ChildArgv)] :tags {:context "doeff-cluster" :role "judgment"}}
  "子として引く命令と切り離しの印を argv から読むため: setsid で始まる argv は、setsid と続く - の旗を外した残りを、切り離した子の argv にする。"
  (if (and argv (= (posixpath.basename (get argv 0)) DETACHING-COMMAND))
      (do (var rest (cut argv 1 None))
          (while (and rest (.startswith (get rest 0) "-"))
            (:= rest (cut rest 1 None)))
          (ChildArgv :argv rest :detached True))
      (ChildArgv :argv argv :detached False)))


(defk descendant-reaped? [descendant ended-roots generation adoption]
  {:pre [(: descendant SimDescendant) (: ended-roots frozenset) (: generation (| tuple None)) (: adoption bool)] :post [(: % bool)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "走っている子孫が片づけに当たるかを決めるため: 根の job の process が ended-roots に在り、引き取る(adoption)か job の process group の
   中(escaped でない)・または子孫の世代が終わった世代 generation(#(worker の名 世代) か None)。"
  (and (is descendant.ended-ms None)
       (or (and (in descendant.origin.root ended-roots) (or adoption (not descendant.escaped)))
           (and (is-not generation None)
                (= #(descendant.origin.worker descendant.origin.boot) generation)))))


(defk descendants-reaped [descendants tasks ended-roots generation adoption now]
  {:pre [(: descendants dict) (: tasks dict) (: ended-roots frozenset) (: generation (| tuple None)) (: adoption bool) (: now int)]
   :post [(: % ReapedDescendants)] :tags {:context "doeff-cluster" :role "judgment"}}
  "片づけに当たる子孫(descendant-reaped?)を刻 now に -9 で終わった形にし、その task を捨てる物に分けるため(job の process の終わり・
   世代の終わり・覚えた時の片づけが同じ判断を使う)。"
  (var reaped #())
  (for [d (.values descendants)]
    (<- hit bool (descendant-reaped? d ended-roots generation adoption))
    (when hit (:= reaped (+ reaped #(d.pid)))))
  (ReapedDescendants
    :descendants (dfor #(pid d) (.items descendants)
                       pid (if (in pid reaped) (replace d :ended-ms now :exit-code REAPED-CODE :reaped True) d))
    :tasks (dfor #(pid task) (.items tasks) :if (not-in pid reaped) pid task)
    :discarded (tuple (gfor pid reaped :if (in pid tasks) (get tasks pid)))))


(defk descendant-ended [descendants tasks pid code now]
  {:pre [(: descendants dict) (: tasks dict) (: pid int) (: code int) (: now int)] :post [(: % ReapedDescendants)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "走っている子孫 pid を刻 now に終了 code code で終わった形にするため(自分での終わり・合図)。捨てる task = 合図で終わらせる時の相手
   (走っていなければ何も変えない)。"
  (val d (.get descendants pid))
  (if (or (is d None) (is-not d.ended-ms None))
      (ReapedDescendants :descendants descendants :tasks tasks :discarded #())
      (ReapedDescendants :descendants (| descendants {pid (replace d :ended-ms now :exit-code code)})
                         :tasks (dfor #(k task) (.items tasks) :if (!= k pid) k task)
                         :discarded (if (in pid tasks) #((get tasks pid)) #()))))


;; --- 答え手 ---------------------------------------------------------------------------------------------

(defk outputs-opened [paths]
  {:pre [(: paths tuple)] :post [(: % (| ProcessNotStarted None))] :tags {:context "doeff-cluster" :role "program"}}
  "StartProcess の出力の file を、子を立てる前に末尾へ足す形で開く(空を足す)ため — 本物は Popen の前に開き、開けなければ立てない。"
  (var refused None)
  (for [path paths]
    (when (and (is refused None) (is-not path None))
      (<- appended (AppendText path ""))
      (when (isinstance appended FileFailed)
        (:= refused (ProcessNotStarted :detail appended.detail)))))
  refused)


(defk outputs-appended [stdout-path stderr-path outcome]
  {:pre [(: stdout-path (| str None)) (: stderr-path (| str None)) (: outcome ProcessOutcome)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "program"}}
  "自分で終わった子の台本の出力を、それぞれの file の末尾へ足すため(None は捨てる — 本物の DEVNULL。足せない file は捨てる — 子は終わった)。"
  (for [#(path text) #(#(stdout-path outcome.stdout) #(stderr-path outcome.stderr))]
    (when (and (is-not path None) text)
      (<- _appended (AppendText path text))))
  None)


(defk child-life [origin escaped children command request stdout-path stderr-path gate]
  {:pre [(: origin ChildOrigin) (: escaped bool) (: children SimChildren) (: command ScriptedCommand) (: request RunProcess)
         (: stdout-path (| str None)) (: stderr-path (| str None)) (: gate Future)]
   :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "子 1 つの一生: 世界が番号を配るのを待ち(gate)、台本の命令を本体として走らせ(本体の子 process の effect は同じ答え手が受ける — 孫は
   同じ job の process の子孫)、自分で終わったら出力を足して終わりを世界へ書く。片づけ・合図で捨てられた子はここへ戻らない。"
  (<- pid int (Wait gate))
  (<- outcome ProcessOutcome (with-handlers [(child-processes origin pid escaped children)]
                               ((. command run) children.commands request)))
  (<- (outputs-appended stdout-path stderr-path outcome))
  (<- (DescendantEnded pid outcome.exit-code))
  None)


(defk child-started [origin parent escaped children request]
  {:pre [(: origin ChildOrigin) (: parent int) (: escaped bool) (: children SimChildren) (: request StartProcess)]
   :post [(: % (| ProcessStarted ProcessNotStarted))] :tags {:context "doeff-cluster" :role "program"}}
  "StartProcess 1 つに答えるため: 台本に名の無い命令と開けない出力の file は立てず、立てる子は世界に覚えさせて番号を答える。子の task は
   答え手の節の外側(sim の世界・時計・外の世界)だけを持ち、job の process の柵と中の handler を持たない(本番の子は別の process)。"
  (<- split ChildArgv (detached-argv request.argv))
  (val argv split.argv)
  (val detached split.detached)
  (val name (if argv (posixpath.basename (get argv 0)) ""))
  (val found (next (gfor c children.commands :if (= c.name name) c) None))
  (when (is found None)
    (<- refusal str (start-refusal errno.ENOENT (if argv (get argv 0) (get request.argv 0))))
    (return (ProcessNotStarted :detail refusal)))
  (<- refused (| ProcessNotStarted None) (outputs-opened #(request.stdout-path request.stderr-path)))
  (when (is-not refused None)
    (return refused))
  (val outside (or escaped detached (= request.lifetime ChildLifetime.OUTLIVES-STARTER)))
  (val run (RunProcess :argv argv :cwd request.cwd :env request.env :env-mode request.env-mode :env-drop request.env-drop
                       :process-group request.process-group))
  (<- gate Promise (CreatePromise))
  (<- task Task (Spawn (child-life origin outside children found run request.stdout-path request.stderr-path gate.future)))
  (<- pid int (KeepDescendant origin parent argv outside task))
  (<- (CompletePromise gate pid))
  (ProcessStarted :pid pid))


(defk own-child [parent pid]
  {:pre [(: parent int) (: pid int)] :post [(: % (| SimDescendant None))] :tags {:context "doeff-cluster" :role "program"}}
  "pid が process parent の起こした子で、まだ回収していなければその記録を読むため(孫・他人の子・回収した子は None — ProcessNotChild)。"
  (<- seen (| SimDescendant None) (DescendantOf pid))
  (if (and (is-not seen None) (= seen.parent parent) (not seen.collected)) seen None))


(defhandler child-processes [#^ ChildOrigin origin #^ int parent #^ bool escaped #^ SimChildren children]
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 引数に残す理由: 子孫の記録の鍵(起こした job の process・世代)と問う process の番号は process ごとに違い、柵の外に在るので Program の
  ;; Ask で問えない。台本は筋書きごとに違う値(設定ではなく模擬の世界そのもの)。
  (StartProcess [argv cwd env env-mode env-drop stdout-path stderr-path process-group hold-stdin reap-group lifetime]
    (<- started (| ProcessStarted ProcessNotStarted) (child-started origin parent escaped children effect))
    (resume started))
  (PollProcess [pid]
    (<- seen (| SimDescendant None) (own-child parent pid))
    (cond
      (is seen None) (resume (ProcessNotChild :pid pid))
      (is seen.ended-ms None) (resume (ProcessRunning :pid pid))
      True (do (<- (CollectDescendant pid))
               (resume (ProcessExited :pid pid :exit-code seen.exit-code)))))
  (SignalProcess [pid signal]
    (<- seen (| SimDescendant None) (own-child parent pid))
    (if (is seen None)
        (resume (ProcessNotChild :pid pid))
        (do (<- delivered bool (SignalDescendant pid (if (= signal ProcessSignal.KILL) REAPED-CODE TERMINATED-CODE)))
            (resume (ProcessSignalled :pid pid :delivered delivered)))))
  (StopProcess [pid stop-grace]
    (<- seen (| SimDescendant None) (own-child parent pid))
    (if (is seen None)
        (resume (ProcessNotChild :pid pid))
        (do (<- _stopped bool (SignalDescendant pid TERMINATED-CODE))
            (<- after SimDescendant (DescendantOf pid))
            (<- (CollectDescendant pid))
            (resume (ProcessExited :pid pid :exit-code after.exit-code))))))
