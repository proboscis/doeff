;; worker の純粋な判断。宣言・観測・記憶・時刻から action と状態表示を導く。I/O はしない。
;;
;; 入れ替え(handoff・2026-09-24): spec の変わった job が handoff を宣言していれば、旧を止めずに名から外し(RetireJob)、新を同じ名で
;; 起こす。退いた旧は、coordinator が新の process を Ready と数えた(宣言の ready-instance = 新の世代の名)後に止める。新のコードの
;; 準備の間も旧は動かし続ける。並べるのは 1 つまで(退いた process が既に在る間の次の変更は、止めてから起こす)。
;;
;; 入口の検め(probe・2026-09-25): service の job は、木が揃った後に「worker の実行環境でその木の入口(factory と env)を読み込めるか」を
;; 先に試し(ProbeEntry)、PASSED になるまで起こさない(StartJob)・旧を名から外さない(RetireJob)。業務コード・定義・実行環境の組が崩れた
;; 木(例: 定義だけ進んで実行環境の doeff に無い名を import する)は、起こしては import で落ちる backoff を繰り返す代わりに、
;; 理由つきの probe-failed で止まる。入れ替えの旧は止めない(書き手の空白を作らない)。FAILED は code-retry-ms の後に撃ち直す。
;; 検めの間(走っている・同じ木の検めの終わりを待っている)は starting ではなく probing と出し、状態の行の probe に経過の秒・回数・
;; 直前の失敗の理由を載せる(2026-09-27 — 以前は 17 分 starting のままで、理由は FAILED から撃ち直すまでの 30 秒しか見えなかった)。
;; 同じ木の検めを 1 本にまとめる・時間切れで process group ごと止めるのは検めの process の持ち主(handlers.ProbeStore)。
(require doeff-hy.macros [defk val])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import dataclasses [replace])
(import doeff [run])
(import doeff_cluster.worker.intent.worker_model [Action CodeState CodeView ProcessView WorldView StopStage StopProgress ProbeState ProbeView ProbeStatus
  Outcome JobRecord WorkerPolicy JobStatus PrepareCode PrepareEnv SweepEnvs StartJob SignalJob ReapJob RetireJob ReleaseLeases
  ProbeEntry ForgetProbes WarmChildView StartWarmChild StopWarmChild ForgetWarmChild] doeff_cluster.shared.intent.job_model [JobSpec JobPhase] doeff_cluster.shared.core.job_rules [spec-hash] doeff_cluster.worker.core.worker_rules [code-key probed-job retired-name ready-path RETIRED-MARK ENV-KEY-PREFIX])
(import doeff_cluster.worker.core.warm_rules [forks-from-warm-child warm-key-of warm-mark-clean warm-child-of warm-child-ready mark-refusal
  warm-preload])

;; 自己停止(2026-09-25): coordinator との連絡が fence(ClusterTiming.fence-ms)を越えて途絶えた worker は、自分の job を止めてきた
;; (coordinator は 45 秒で他へ移すので、同じ job が 2 つ動かないように)。ただし書き手(入れ替え handoff を宣言した job)は、旧と新が
;; 並んで動く前提で作られていて、外への書きは名前付きの lease の柵(semaphore_handlers.lease-fence)だけが守る。その柵は coordinator の
;; 時計の期限で締まるので、途絶で止める必要が無い — 止めると coordinator の作り直し(版の更新)のたびに書き手が止まった。
;; 書き手の停止は lease に一本化し、自己停止は lease を持たない job と task にだけ当てる。切り離した task(2026-09-25)も止めない
;; (lease は担い手の worker の heartbeat が延ばし、途絶が lease より長ければ coordinator がその task を lost にする)。
;; 途絶しても動かし続けてよい印(#2804): coordinator が「他に置ける worker が無い」と判じて返事の job に付けた印(JobSpec.keep-when-cut-off)
;; の在る job も止めない。coordinator は印を渡した担い手から、担い手が印を持たないと知らせる(heartbeat の keptWhenCutOff — keep-marks-held)
;; か Worker が消されるまで job を他へ移さないので、2 か所で走らない保証は時間の競争(fence < 移し替え)ではなく「移さない」で持つ。
;; 印の在る job も長い方の柵(ClusterTiming.keep-fence-ms・既定 240 秒)を越えた途絶では止める — 同じ名の worker の新しい世代(k8s が届かない
;; node の Pod を追い出して作り直した物・早くても約 350 秒後)と重ならないため(数の前提は ClusterTiming.keep-fence-ms の註)。
;; 止めるかどうかの判断はこの述語 1 つ(fence の判断 desired-when-unreachable と、時間で周期ごとに判ずる側が同じ述語を呼ぶ)。
(defk kept-when-cut-off? [job silent-ms keep-fence-ms]
  {:pre [(: job JobSpec) (: silent-ms int) (: keep-fence-ms int)] :post [(: % bool)] :tags {:context "worker" :role "judgment"}}
  "coordinator に届かない間(最後に届いた返事から silent-ms)も job を動かし続けるかを 1 か所で決めるため: 入れ替えを宣言した書き手(lease の
   柵が書きを守る)・切り離した task(担い手の heartbeat が lease を延ばす)・途絶しても動かし続けてよい印の在る service の job(coordinator が
   他へ移さない — ただし途絶が keep-fence-ms を越えるまで)。RemoteJob の task は含まない(呼び手が lease を持つ)。"
  (or (and job.handoff (not job.once))
      (and job.once job.detached)
      (and job.keep-when-cut-off (not job.once) (<= silent-ms keep-fence-ms))))


(defn #^ tuple kept-when-cut-off [#^ tuple jobs #^ int silent-ms #^ int keep-fence-ms]
  "純粋: coordinator に届かない間(silent-ms)も動かし続ける job の列を、最後に受け取った宣言から選ぶため(判断は kept-when-cut-off? 1 つ)。"
  (tuple (gfor job jobs :if (run (kept-when-cut-off? job silent-ms keep-fence-ms)) job)))

(defn #^ (| ProcessView None) process-of [#^ WorldView world #^ str name]
  (for [process world.processes]
    (when (= process.name name) (return process)))
  None)

(defn #^ (| CodeView None) code-of [#^ WorldView world #^ str key]
  "key = worker_model.code-key(版そのもの、または実行環境の root の鍵)。"
  (for [code world.codes]
    (when (= code.revision key) (return code)))
  None)

(defn #^ (| ProbeView None) probe-of [#^ WorldView world #^ JobSpec spec]
  "spec の入口の検めの観測(鍵 = spec-hash)。"
  (setv key (spec-hash spec))
  (for [probe world.probes]
    (when (= probe.spec-hash key) (return probe)))
  None)

(defn #^ (| tuple None) probe-actions [#^ int now #^ JobSpec spec #^ str tree #^ WorldView world #^ WorkerPolicy policy]
  "入口の検めの門(木が READY の spec・tree = READY の木の path)。通れば None。通れなければ撃つ action — 初回・FAILED の code-retry-ms 後の撃ち直しは
   ProbeEntry、走っている間・撃ち直しの間は空(待つ)。検めの対象でない job(task・素の entry)はいつも通る。"
  (when (not (probed-job spec)) (return None))
  (setv probe (probe-of world spec))
  (cond
    (is probe None) #((ProbeEntry spec tree))
    (= probe.state ProbeState.PASSED) None
    (in probe.state #(ProbeState.RUNNING ProbeState.QUEUED)) #()
    (>= (- now (or probe.failed-ms 0)) policy.code-retry-ms) #((ProbeEntry spec tree))
    True #()))

(defn #^ (| ProbeView None) probe-failure [#^ WorldView world #^ JobSpec spec]
  "spec の入口の検めが FAILED なら、その観測(状態表示の理由)。"
  (setv probe (if (probed-job spec) (probe-of world spec) None))
  (if (and (is-not probe None) (= probe.state ProbeState.FAILED)) probe None))

(defn #^ (| ProbeView None) probe-in-flight [#^ WorldView world #^ JobSpec spec]  ; defk にできない: 純粋な判断の phase-of(Program の外の関数)が呼ぶ
  "spec の入口の検めが走っている・同じ木の検めの終わりを待っているなら、その観測(2026-09-27 — 状態の probing)。"
  (setv probe (if (probed-job spec) (probe-of world spec) None))
  (if (and (is-not probe None) (in probe.state #(ProbeState.RUNNING ProbeState.QUEUED))) probe None))

(defn #^ (| ProbeStatus None) probe-status [#^ int now #^ WorldView world #^ JobSpec spec]  ; defk にできない: 純粋な判断の statuses(Program の外の関数)が呼ぶ
  "状態の報告の検めの姿: 宣言の spec の検めが通っていない間(走っている・待っている・失敗した)だけ。経過の秒は今の検めを起こしてから
   (失敗した後は、その回を起こしてから失敗までではなく 0 — 次の撃ち直しまでの間は failed の理由で読む)。"
  (setv probe (if (probed-job spec) (probe-of world spec) None))
  (if (or (is probe None) (= probe.state ProbeState.PASSED))
      None
      (ProbeStatus :state probe.state.value
                   :elapsed-seconds (if (and (= probe.state ProbeState.RUNNING) (is-not probe.started-ms None))
                                        (max 0 (// (- now probe.started-ms) 1000))
                                        0)
                   :attempts probe.attempts
                   ;; 失敗した回の理由は、失敗の間は detail、撃ち直した後は last-failure が運ぶ。
                   :last-failure (if (= probe.state ProbeState.FAILED) probe.detail probe.last-failure))))

(defn #^ str probing-detail [#^ ProbeStatus probe]  ; defk にできない: 純粋な判断の statuses(Program の外の関数)が呼ぶ
  "検めの間の状態の 1 行(何回目か・何秒か・直前の失敗の理由)。"
  (+ (if (= probe.state ProbeState.QUEUED.value)
         (.format "入口の検めの順番待ち(同じ木の検めが走っている・{} 回目)" probe.attempts)
         (.format "入口の検め中({} 秒・{} 回目)" probe.elapsed-seconds probe.attempts))
     (if probe.last-failure (.format " — 前回: {}" probe.last-failure) "")))

(defn #^ (| JobSpec None) desired-of [#^ tuple desired #^ str name]
  (for [spec desired]
    (when (= spec.name name) (return spec)))
  None)

(defk job-names [desired world]
  {:pre [(: desired tuple) (: world WorldView)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "拍で扱う job の名の列(宣言の名 → process の名の順・同じ名は最初の 1 つ)を決めるため。宣言から消えた job も、process が残る限り扱う
   (止めるまで忘れない)。退いた process も同じ(名 = <元の名>#retired-<世代>)。"
  (tuple (dict.fromkeys (+ (lfor spec desired spec.name) (lfor p world.processes p.name)))))

(defn #^ bool retired-exists [#^ WorldView world #^ str name]
  (any (gfor p world.processes (= p.retired-from name))))

(defn #^ int backoff-ms [#^ JobRecord record #^ WorkerPolicy policy]
  "続けて予期せず終わった回数(exit code を問わない)に応じた、起こし直すまでの間(1 回目 = restart-backoff-ms・以後は倍・上限 restart-backoff-max-ms)。"
  (min policy.restart-backoff-max-ms
       (* policy.restart-backoff-ms (** 2 (max 0 (- record.unexpected-exits 1))))))

(defn #^ bool in-backoff [#^ int now #^ JobRecord record #^ WorkerPolicy policy]
  (and (is-not record.last-exit-ms None)
       (= record.last-outcome Outcome.EXITED)
       (< (- now record.last-exit-ms) (backoff-ms record policy))))

(defn #^ tuple stop-actions [#^ int now #^ ProcessView process #^ JobRecord record #^ WorkerPolicy policy]
  (setv stopping record.stopping)
  (cond
    (is stopping None) #((SignalJob process.name process.pid StopStage.TERM))
    (and (= stopping.stage StopStage.TERM) (>= (- now stopping.signalled-ms) policy.stop-grace-ms))
      #((SignalJob process.name process.pid StopStage.KILL))
    ;; KILL 後は待つだけ。確認できないまま置き換えを起動しない。
    True #()))

(defn #^ (| PrepareCode PrepareEnv) prepare-action [#^ JobSpec spec]
  "spec の置き場を用意する action: 実行環境の job は env の root(PrepareEnv)、それ以外は commit の木(PrepareCode)。"
  (if spec.runtime-env
      (PrepareEnv (code-key spec) spec.runtime-env)
      (PrepareCode (code-key spec))))

(defn #^ tuple prepare-actions [#^ int now #^ JobSpec spec #^ WorldView world #^ WorkerPolicy policy]
  "spec のコードの木を用意する action(用意できていれば空)。準備に失敗した版は、間を置いてから作り直す。"
  (setv code (code-of world (code-key spec)))
  (cond
    (is code None) #((prepare-action spec))
    (and (= code.state CodeState.FAILED)
         (>= (- now (or code.failed-ms 0)) policy.code-retry-ms)) #((prepare-action spec))
    True #()))

(defn #^ tuple start-actions [#^ int now #^ JobSpec spec #^ WorldView world #^ JobRecord record #^ WorkerPolicy policy]
  (setv tree (ready-path (code-of world (code-key spec))))
  (cond
    ;; task は 1 度だけ走らせる。終わった後は宣言から外れるまで待つ(結果は状態の報告で運ぶ)。
    (and spec.once (is-not record.last-outcome None)) #()
    (is tree None) (prepare-actions now spec world policy)
    True (start-on-ready-tree now spec tree world record policy)))

(defn #^ tuple start-on-ready-tree [#^ int now #^ JobSpec spec #^ str tree #^ WorldView world #^ JobRecord record
                                    #^ WorkerPolicy policy]
  "木が READY の job を起こすため: 入口の検めが通るまで起こさず、backoff の間は待つ。待ちの子から分かれる task(#3646)は、自分の root の
   待ちの子が準備済みになるまで起こさない(待ちの子を起こすのは warm-child-actions)— 道は 1 つで、入れ物 shim へ倒れない。"
  (setv gate (probe-actions now spec tree world policy))
  (cond
    (is-not gate None) gate
    (in-backoff now record policy) #()
    (forks-from-warm-child spec)
      (if (warm-child-ready (warm-child-of world (warm-key-of spec)))
          #((StartJob spec (+ record.attempts 1) tree :warm-key (warm-key-of spec)))
          #())
    True #((StartJob spec (+ record.attempts 1) tree))))

(defn #^ tuple handoff-actions [#^ int now #^ JobSpec want #^ ProcessView process #^ WorldView world #^ WorkerPolicy policy]
  "入れ替え: 新のコードが揃い、新の入口の検めが通るまでは旧を動かしたまま準備と検めだけ進め、通ったら旧を名から外す
   (次の拍で新を同じ名で起こす)。検めが FAILED の間は旧を外さない(書き手の空白を作らない)。"
  (setv tree (ready-path (code-of world (code-key want))))
  (if (is-not tree None)
      (do (setv gate (probe-actions now want tree world policy))
          (if (is-not gate None)
              gate
              #((RetireJob process.name process.pid (retired-name process.name (or process.instance (str process.pid)))))))
      (prepare-actions now want world policy)))

(defn #^ tuple retired-actions [#^ int now #^ ProcessView process #^ str origin #^ tuple desired #^ WorldView world
                                #^ JobRecord record #^ WorkerPolicy policy]
  "退いた process(origin = 退く前の job の名): 元の job の新しい process が Ready と数えられたら止める。それまでは動かし続ける
   (書き手の空白を作らない)。元の job が宣言から消えた・handoff でなくなった時も止める。"
  (setv want (desired-of desired origin)
        current (process-of world origin))
  (if (or (is-not record.stopping None)
          (is want None)
          (not want.handoff)
          (and (is-not current None) (is current.exit-code None) (= current.spec want)
               (is-not want.ready-instance None) (= want.ready-instance current.instance)))
      (stop-actions now process record policy)
      #()))

(defn #^ tuple plan-job [#^ int now #^ str name #^ tuple desired #^ WorldView world
                         #^ JobRecord record #^ WorkerPolicy policy]
  (setv want (desired-of desired name)
        process (process-of world name)
        ;; 入れ替えの諦め(2026-09-26 — coordinator の handoff_policy が期限で決め、heartbeat の返事で運ぶ)。
        abandoned (and (is-not want None) want.handoff want.handoff-abandoned))
  (cond
    ;; 諦めた入れ替えの新は起こし直さない(退いた旧が動き続ける)。宣言が変われば諦めは解け、次の拍で起こす。
    (is process None) (if (or (is want None) abandoned) #() (start-actions now want world record policy))
    (is-not process.exit-code None)
      (+ #((ReapJob name process.pid
             (if (is record.stopping None) Outcome.EXITED Outcome.STOPPED) process.exit-code))
         ;; 終わった process の lease は、期限を待たずに返す(次の担い手がすぐ取れる)。
         ;; 担い手の名は子が名乗った job の名(起こした spec の名 — 退いた process も元の名)と世代の名。
         (if process.instance #((ReleaseLeases process.spec.name process.instance)) #()))
    (is-not process.retired-from None) (retired-actions now process process.retired-from desired world record policy)
    ;; 諦めた入れ替え: 今の宣言の spec の新の process を止める(止め始めた process は止め終える)。前の宣言の process(まだ退いて
    ;; いない旧)は名から外さず、そのまま動かす — 新を起こさないので並べる理由が無い。
    abandoned
      (if (or (= want process.spec) (is-not record.stopping None))
          (stop-actions now process record policy)
          #())
    (and (= want process.spec) (is record.stopping None)) #()
    ;; spec が変わった handoff の job: 旧を止めずに新を並べる(退いた process が既に在る間は、並べずに止めてから起こす)。
    (and (is-not want None) want.handoff (is record.stopping None) (not (retired-exists world name)))
      (handoff-actions now want process world policy)
    ;; 宣言から消えた・版や引数が変わった → 先に止める(旧新の同時稼働をしない)。
    True (stop-actions now process record policy)))

(defk warm-actions [now warm world job-actions policy]
  {:pre [(: now int) (: warm tuple) (: world WorldView) (: job-actions tuple) (: policy WorkerPolicy)] :post [(: % tuple)]
   :tags {:context "worker" :role "judgment"}}
  "先読み(2026-09-26)の action を求めるため: 温める表の env を、job の準備を撃った後に準備し始める(準備済み・準備中なら何もしない・
   失敗は code-retry-ms の後に撃ち直す)。同じ拍に job が同じ root の準備を撃っていれば撃たない(job の準備が先に立つ)。"
  (val requested (sfor a job-actions :if (isinstance a PrepareEnv) a.key))
  (tuple (gfor w warm
               :setv code (code-of world w.key)
               :if (and (not-in w.key requested)
                        (or (is code None)
                            (and (= code.state CodeState.FAILED)
                                 (>= (- now (or code.failed-ms 0)) policy.code-retry-ms))))
               (PrepareEnv w.key w.runtime-env :warm True))))

(defk warm-stop-step [now view policy]
  {:pre [(: now int) (: view WarmChildView) (: policy WorkerPolicy)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "止め始めた待ちの子の次の一手を決めるため: TERM の後、停止の猶予を過ぎても終わらなければ KILL を 1 度送る(job の止めと同じ間)。"
  (if (and (is-not view.stop None) (= view.stop.stage StopStage.TERM) (>= (- now view.stop.signalled-ms) policy.stop-grace-ms))
      #((StopWarmChild view.key StopStage.KILL "止めの合図の後も終わらない"))
      #()))

(defk warm-child-step [now key root view preload policy]
  {:pre [(: now int) (: key str) (: root (| str None)) (: view (| WarmChildView None)) (: preload tuple) (: policy WorkerPolicy)]
   :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "root のキー 1 つの待ちの子の action を決めるため(判断の表 — tests/test_warm_child_policy.hy が全行を撃つ)。root = 要る root の READY
   の path(要らない・READY でなければ None)・view = 待ちの子の観測・preload = 起動で読む module の名。観測が変わらない限り同じ action を
   2 度出さない(起こした・止め始めた・忘れた事は次の観測に出る)。走っている task には何もしない(条 WC2)。"
  (cond
    ;; 要らない root: 走っていれば止め、終わっていれば観測から外す。
    (is root None)
      (cond
        (is view None) #()
        (is-not view.exit-code None) #((ForgetWarmChild key))
        (is view.stop None) #((StopWarmChild key StopStage.TERM "root の待ちの子が要らなくなった"))
        True (! (warm-stop-step now view policy)))
    (is view None) #((StartWarmChild key root preload))
    ;; 終わった待ちの子は、準備の失敗と同じ間(code-retry-ms)を置いてから起こし直す(落ちる入口を毎拍起こさない)。
    (is-not view.exit-code None)
      (if (>= (- now (or view.ended-ms 0)) policy.code-retry-ms) #((StartWarmChild key root preload)) #())
    (is-not view.stop None) (! (warm-stop-step now view policy))
    ;; 印が分かれる前の形でない(条 WC3)待ちの子は、準備済みに数えずに止める(終わった後に起こし直す)。
    (and (is-not view.mark None) (not (warm-mark-clean view.mark)))
      #((StopWarmChild key StopStage.TERM (mark-refusal view.mark)))
    True #()))

(defk warm-child-actions [now desired world warm policy]
  {:pre [(: now int) (: desired tuple) (: world WorldView) (: warm tuple) (: policy WorkerPolicy)] :post [(: % tuple)]
   :tags {:context "worker" :role "judgment"}}
  "待ちの子(#3646)を起こす・止める・観測から外す action を求めるため: 要る root = 宣言に分かれる task が在るか温める表に在る root の
   うち READY の物。要る root と観測に在る待ちの子を root のキーの順に 1 つずつ判じる(warm-child-step)。"
  (val wanted (frozenset (+ (tuple (gfor spec desired :if (forks-from-warm-child spec) (warm-key-of spec)))
                            (tuple (gfor w warm w.key)))))
  (val keys (sorted (| wanted (frozenset (gfor view world.warm-children view.key)))))
  (var actions #())
  (for [key keys]
    (<- preload tuple (warm-preload key desired warm))
    (<- step tuple (warm-child-step now key (if (in key wanted) (ready-path (code-of world key)) None)
                                    (warm-child-of world key) preload policy))
    (:= actions (+ actions step)))
  actions)

(defk pinned-env-keys [desired world warm]
  {:pre [(: desired tuple) (: world WorldView) (: warm tuple)] :post [(: % frozenset)] :tags {:context "worker" :role "judgment"}}
  "掃除が消してはいけない root のキー(2026-09-26)を決めるため: 宣言の実行環境の job・走っている実行環境の process・温める表・準備中の
   root。project ごとの最新の root は掃除の係が完成マーカーから守る(env_upkeep.sweep-choice)。"
  (frozenset (+ (lfor spec desired :if spec.runtime-env (code-key spec))
                (lfor p world.processes :if p.spec.runtime-env (code-key p.spec))
                (lfor w warm w.key)
                (lfor c world.codes :if (and (.startswith c.revision ENV-KEY-PREFIX) (= c.state CodeState.PREPARING)) c.revision))))

(defk sweep-actions [desired world warm]
  {:pre [(: desired tuple) (: world WorldView) (: warm tuple)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "掃除の係へ固定の集合を渡す action を求めるため(固定の集合が変わった時と、空きが下限を切った時だけ)。実行環境を扱わない worker は
   撃たない。"
  (val disk world.env-disk)
  (if (is disk None)
      #()
      (do (<- pinned frozenset (pinned-env-keys desired world warm))
          (if (or (!= pinned disk.pinned) (< disk.free disk.floor)) #((SweepEnvs pinned)) #()))))

(defk forget-probe-actions [desired world]
  {:pre [(: desired tuple) (: world WorldView)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "入口の検めの持ち主へ今の宣言の spec の指紋を渡す action を求めるため(宣言に無い spec の検めの記録が観測に在る拍だけ — 2026-09-27)。
   宣言に残る spec の記録(失敗の理由・回数)は落とさない。"
  (val keep (frozenset (gfor spec desired (spec-hash spec))))
  (if (any (gfor probe world.probes (not-in probe.spec-hash keep))) #((ForgetProbes keep)) #()))

(defk plan [now desired world records policy [warm #()]]
  {:pre [(: now int) (: desired tuple) (: world WorldView) (: records dict) (: policy WorkerPolicy) (: warm tuple)] :post [(: % tuple)]
   :tags {:context "worker" :role "judgment"}}
  "worker の 1 拍で撃つ action を決めるため: job ごとの action → 温める表の準備(job より後)→ 待ちの子の起こしと止め(#3646)→ 掃除の
   係への固定の集合 → 検めの記録の片づけ。job ごとの判断 plan-job は列の中で要素ごとに呼ぶので素の関数のまま(列を順に走らせる道具
   #2812 を待つ)。"
  (<- names tuple (job-names desired world))
  (val jobs (tuple (gfor name names
                         action (plan-job now name desired world (.get records name (JobRecord name)) policy)
                         action)))
  (<- warming tuple (warm-actions now warm world jobs policy))
  (<- children tuple (warm-child-actions now desired world warm policy))
  (<- sweeping tuple (sweep-actions desired world warm))
  (<- forgetting tuple (forget-probe-actions desired world))
  (+ jobs warming children sweeping forgetting))

(defk ready-followups [now desired before after records policy]
  {:pre [(: now int) (: desired tuple) (: before WorldView) (: after WorldView) (: records dict) (: policy WorkerPolicy)]
   :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "拍の action の後の観測(after)で木が揃った job を、次の拍を待たずに同じ判断(plan-job)で進める action を求めるため(#2719)。
   対象は拍の頭の観測(before)で木が READY でなく、after で READY になった宣言の job だけ — 準備がその拍のうちに揃う宿(模擬の
   prepare-seconds = 0・cache に完成品の在る版)で、最初の task の起動が拍 1 つ遅れる形をやめる。揃っていなければ空(今までどおり後の拍で
   揃いを観測してから起こす)。records = 拍の action を数えた後の記憶。"
  (tuple (gfor spec desired
               :if (and (is (ready-path (code-of before (code-key spec))) None)
                        (is-not (ready-path (code-of after (code-key spec))) None))
               action (plan-job now spec.name desired after (.get records spec.name (JobRecord spec.name)) policy)
               action)))

(defn #^ JobRecord record-after [#^ int now #^ JobRecord record #^ Action action #^ WorkerPolicy [policy (WorkerPolicy)]]
  (cond
    (isinstance action StartJob) (replace record :attempts action.attempt :stopping None :last-start-ms now)
    (isinstance action SignalJob)
      (replace record :stopping
        (StopProgress (if (is record.stopping None) now record.stopping.requested-ms) action.stage now))
    (isinstance action ReapJob)
      ;; 数え方は 2 つ: unexpected-exits = 停止を求めずに終わった回数(起こし直しの間を伸ばす・exit code を問わない)、
      ;; failures = そのうち exit code が 0 でなかった回数(失敗として表示する)。どちらも長く動いた後の終わりは 1 回目に数え直す。
      (do
        (setv exited (= action.outcome Outcome.EXITED)
              stable (and (is-not record.last-start-ms None)
                          (>= (- now record.last-start-ms) policy.stable-run-ms)))
        (replace record :last-exit-ms now :last-outcome action.outcome :last-exit-code action.exit-code
                 :stopping None
                 :unexpected-exits (cond (not exited) 0 stable 1 True (+ record.unexpected-exits 1))
                 :failures (cond (or (not exited) (= action.exit-code 0)) 0 stable 1 True (+ record.failures 1))))
    True record))

(defk records-after [now records actions [policy (WorkerPolicy)]]
  {:pre [(: now int) (: records dict) (: actions tuple) (: policy WorkerPolicy)] :post [(: % dict)] :tags {:context "worker" :role "judgment"}}
  "撃った action を job ごとの記憶(起こした回数・止め始め・終わり方)に数えた後の記憶を求めるため。1 つずつの数えは record-after
   (列の中で要素ごとに呼ぶので素の関数のまま — 列を順に走らせる道具 #2812 を待つ)。"
  (var result (dict records))
  (for [action actions]
    (val name (match action
      (StartJob) action.spec.name
      (| (SignalJob) (ReapJob)) action.name
      _ None))
    (when (is-not name None)
      (:= result (| result {name (record-after now (.get result name (JobRecord name)) action policy)})))
    ;; 退いた process の記憶は、回収した時に捨てる(名は世代ごとに違うので、残すと入れ替えのたびに溜まる)。
    (when (and (isinstance action ReapJob) (in RETIRED-MARK action.name))
      (:= result (dfor #(k v) (.items result) :if (!= k action.name) k v))))
  result)

(defn #^ JobPhase phase-of [#^ int now #^ (| JobSpec None) want #^ (| ProcessView None) process
                            #^ WorldView world #^ JobRecord record #^ WorkerPolicy policy]
  (cond
    (is-not process None)
      (cond
        (is record.stopping None) JobPhase.RUNNING
        (and (= record.stopping.stage StopStage.KILL)
             (>= (- now record.stopping.signalled-ms) policy.kill-grace-ms)) JobPhase.STOP-UNCONFIRMED
        True JobPhase.STOPPING)
    (is want None) JobPhase.STOPPED
    (and want.once (is-not record.last-outcome None)) JobPhase.FINISHED
    (and want.handoff want.handoff-abandoned) JobPhase.HANDOFF-ABANDONED
    True
      (do
        (setv code (code-of world (code-key want)))
        (cond
          (or (is code None) (= code.state CodeState.PREPARING)) JobPhase.PREPARING
          (and (= code.state CodeState.FAILED) (is-not code.failure None)) JobPhase.ENV-FAILED
          (= code.state CodeState.FAILED) JobPhase.CODE-FAILED
          (is-not (probe-failure world want) None) JobPhase.PROBE-FAILED
          (is-not (probe-in-flight world want) None) JobPhase.PROBING
          (in-backoff now record policy) JobPhase.BACKOFF
          ;; 待ちの子から分かれる task は、待ちの子が準備済みになるまで準備の段階のまま(段階の語は足さず、理由は状態の行の文 — #3646)。
          (and (forks-from-warm-child want) (not (warm-child-ready (warm-child-of world (warm-key-of want))))) JobPhase.PREPARING
          True JobPhase.STARTING))))

(defn #^ (| str None) warm-wait-detail [#^ WorldView world #^ (| JobSpec None) want #^ (| ProcessView None) process
                                        #^ JobRecord record]
  "待ちの子の準備を待っている task の状態の行の理由(#3646)。待っていなければ None。前の待ちの子が終わっていれば、その訳も添える。"
  (if (or (is want None) (is-not process None) (not (forks-from-warm-child want)) (is-not record.last-outcome None)
          (is (ready-path (code-of world (code-key want))) None))
      None
      (do
        (setv view (warm-child-of world (warm-key-of want)))
        (cond
          (warm-child-ready view) None
          (and (is-not view None) (is-not view.exit-code None) view.detail)
            (.format "待ちの子の準備中 — 前の待ちの子が終わった: {}" view.detail)
          (and (is-not view None) (is-not view.stop None) view.detail)
            (.format "待ちの子の準備中 — 前の待ちの子を止めている: {}" view.detail)
          True "待ちの子の準備中"))))

(defk statuses [now desired world records policy]
  {:pre [(: now int) (: desired tuple) (: world WorldView) (: records dict) (: policy WorkerPolicy)] :post [(: % tuple)]
   :tags {:context "worker" :role "judgment"}}
  "worker の状態の報告(job ごとの JobStatus の列)を作るため。job ごとの判断(phase-of・probe-status ほか)は列の中で要素ごとに呼ぶので
   素の関数のまま(列を順に走らせる道具 #2812 を待つ)。"
  (<- names tuple (job-names desired world))
  (tuple (gfor name names
    :setv want (desired-of desired name)
    :setv process (process-of world name)
    :setv record (.get records name (JobRecord name))
    :setv code (if (is want None) None (code-of world (code-key want)))
    :setv probe (if (is want None) None (probe-failure world want))
    :setv probing (if (is want None) None (probe-status now world want))
    :setv handing-off (and (is-not process None) (is-not want None) want.handoff (!= process.spec want) (is record.stopping None))
    :setv abandoned (and (is-not want None) want.handoff want.handoff-abandoned)
    :setv warm-wait (warm-wait-detail world want process record)
    ;; 今の process が stable-run-ms 以上動いていれば、続けて落ちた回数は 0 と報告する(次に終わった時に 1 から数え直す record-after と
    ;; 同じ境 — 拍ごとに組む報告から導くので、記憶を書き換える仕掛けも時刻の見張りも要らない・#3477)。
    :setv stable (and (is-not process None) (is-not record.last-start-ms None)
                      (>= (- now record.last-start-ms) policy.stable-run-ms))
    (JobStatus name (phase-of now want process world record policy)
      (if (is want None) None want.revision)
      (if (is process None) None process.spec.revision)
      (if (is process None) None process.pid)
      record.attempts
      (cond
        ;; 入れ替えの諦め(coordinator の期限)。Service の status.handoff に期限と理由が出る。
        abandoned "入れ替えを諦めた(新の process は止めて起こし直さない・旧は動かしたまま — 宣言が変わるまで)"
        (and (is-not code None) (= code.state CodeState.FAILED)) code.detail
        ;; 新の入口を読み込めない(入口の検めの理由)。入れ替えの途中なら旧が動いていることも示す。
        (and (is-not probe None) handing-off) (.format "入れ替えを待つ(旧は動かしたまま)— 新の入口を読み込めない: {}" probe.detail)
        (is-not probe None) (.format "新の入口を読み込めない: {}" probe.detail)
        ;; 入口の検めの間(撃ち直しの間も直前の失敗の理由を出す — 2026-09-27)。入れ替えの途中なら旧が動いていることも示す。
        (and (is-not probing None) handing-off) (.format "入れ替えを待つ(旧は動かしたまま)— {}" (probing-detail probing))
        (is-not probing None) (probing-detail probing)
        ;; 待ちの子の準備を待っている task(#3646)。
        (is-not warm-wait None) warm-wait
        ;; 入れ替えの途中(新のコードの準備・新の Ready 待ち)は、旧が動いていることを示す。
        handing-off
          (.format "入れ替えを待つ(新のコード {})" (if (is code None) "未準備" code.state.value))
        ;; 終わりの code と続けて落ちた回数は欄(failures・last-exit-code)が運ぶので、文には載せない(#3477)。
        (and (is-not record.last-outcome None) (> record.failures 0))
          f"last={record.last-outcome.value} backoff={(backoff-ms record policy)}ms"
        (is-not record.last-outcome None)
          f"last={record.last-outcome.value}"
        True "")
      :failures (if stable 0 record.failures)
      :last-exit-code record.last-exit-code
      :last-exit-at-ms record.last-exit-ms
      ;; 動いている process の世代(coordinator の readiness がこれと一致する報告だけを数える)。
      :instance (if (is process None) None process.instance)
      :spec-hash (if (is process None) None (spec-hash process.spec))
      :placement (if (is process None) None process.spec.placement)
      :retired-from (if (is process None) None process.retired-from)
      ;; 実行環境の root の準備の失敗(動いている process が無い時だけ — 動いていれば準備は済んでいる)。
      :failure (if (and (is process None) (is-not code None) (= code.state CodeState.FAILED)) code.failure None)
      ;; 宣言の spec の入口の検めの姿(走っている・待っている・失敗した間 — 入れ替えで旧が動いている行も)。
      :probe probing))))
