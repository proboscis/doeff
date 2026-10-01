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
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import dataclasses [replace])
(import doeff_cluster.worker.intent.worker_model [Action CodeState CodeView ProcessView WorldView StopStage StopProgress ProbeState ProbeView ProbeStatus
  Outcome JobRecord WorkerPolicy JobStatus PrepareCode PrepareEnv SweepEnvs StartJob SignalJob ReapJob RetireJob ReleaseLeases
  ProbeEntry ForgetProbes] doeff_cluster.shared.intent.job_model [JobSpec JobPhase] doeff_cluster.shared.core.job_rules [spec-hash] doeff_cluster.worker.core.worker_rules [code-key probed-job retired-name ready-path RETIRED-MARK ENV-KEY-PREFIX])

;; 自己停止(2026-09-25): coordinator との連絡が fence(ClusterTiming.fence-ms)を越えて途絶えた worker は、自分の job を止めてきた
;; (coordinator は 45 秒で他へ移すので、同じ job が 2 つ動かないように)。ただし書き手(入れ替え handoff を宣言した job)は、旧と新が
;; 並んで動く前提で作られていて、外への書きは名前付きの lease の柵(semaphore_handlers.lease-fence)だけが守る。その柵は coordinator の
;; 時計の期限で締まるので、途絶で止める必要が無い — 止めると coordinator の作り直し(版の更新)のたびに書き手が止まった。
;; 書き手の停止は lease に一本化し、自己停止は lease を持たない job と task にだけ当てる。切り離した task(2026-09-25)も止めない
;; (lease は担い手の worker の heartbeat が延ばし、途絶が lease より長ければ coordinator がその task を lost にする)。
(defn #^ tuple kept-when-cut-off [#^ tuple jobs]
  "純粋: coordinator に届かない間も動かし続ける job(入れ替えを宣言した書き手と、切り離した task)。RemoteJob の task は含まない
   (呼び手が lease を持つ)。切り離した task は担い手の heartbeat が lease を延ばすので、途絶で止めない(2026-09-25)。"
  (tuple (gfor job jobs :if (or (and job.handoff (not job.once)) (and job.once job.detached)) job)))

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

(defn #^ tuple job-names [#^ tuple desired #^ WorldView world]
  ;; 宣言から消えた job も、process が残る限り扱う(止めるまで忘れない)。退いた process も同じ(名 = <元の名>#retired-<世代>)。
  (setv names [])
  (for [name (+ (lfor spec desired spec.name) (lfor p world.processes p.name))]
    (when (not-in name names) (.append names name)))
  (tuple names))

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
  "木が READY の job を起こすため: 入口の検めが通るまで起こさず、backoff の間は待つ。"
  (setv gate (probe-actions now spec tree world policy))
  (cond
    (is-not gate None) gate
    (in-backoff now record policy) #()
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

(defn #^ tuple warm-actions [#^ int now #^ tuple warm #^ WorldView world #^ tuple job-actions #^ WorkerPolicy policy]
  "先読み(2026-09-26): 温める表の env を、job の準備を撃った後に準備し始める(準備済み・準備中なら何もしない・失敗は code-retry-ms の後に
   撃ち直す)。同じ拍に job が同じ root の準備を撃っていれば撃たない(job の準備が先に立つ)。"
  (setv requested (sfor a job-actions :if (isinstance a PrepareEnv) a.key))
  (tuple (gfor w warm
               :setv code (code-of world w.key)
               :if (and (not-in w.key requested)
                        (or (is code None)
                            (and (= code.state CodeState.FAILED)
                                 (>= (- now (or code.failed-ms 0)) policy.code-retry-ms))))
               (PrepareEnv w.key w.runtime-env :warm True))))

(defn #^ frozenset pinned-env-keys [#^ tuple desired #^ WorldView world #^ tuple warm]
  "掃除が消してはいけない root のキー(2026-09-26): 宣言の実行環境の job・走っている実行環境の process・温める表・準備中の root。
   project ごとの最新の root は掃除の係が完成マーカーから守る(env_upkeep.sweep-choice)。"
  (frozenset (+ (lfor spec desired :if spec.runtime-env (code-key spec))
                (lfor p world.processes :if p.spec.runtime-env (code-key p.spec))
                (lfor w warm w.key)
                (lfor c world.codes :if (and (.startswith c.revision ENV-KEY-PREFIX) (= c.state CodeState.PREPARING)) c.revision))))

(defn #^ tuple sweep-actions [#^ tuple desired #^ WorldView world #^ tuple warm]
  "掃除の係へ固定の集合を渡す action(固定の集合が変わった時と、空きが下限を切った時だけ)。実行環境を扱わない worker は撃たない。"
  (setv disk world.env-disk)
  (if (is disk None)
      #()
      (do (setv pinned (pinned-env-keys desired world warm))
          (if (or (!= pinned disk.pinned) (< disk.free disk.floor)) #((SweepEnvs pinned)) #()))))

(defn #^ tuple forget-probe-actions [#^ tuple desired #^ WorldView world]
  "入口の検めの持ち主へ今の宣言の spec の指紋を渡す action(宣言に無い spec の検めの記録が観測に在る拍だけ — 2026-09-27)。
   宣言に残る spec の記録(失敗の理由・回数)は落とさない。"
  (setv keep (frozenset (gfor spec desired (spec-hash spec))))
  (if (any (gfor probe world.probes (not-in probe.spec-hash keep))) #((ForgetProbes keep)) #()))

(defn #^ tuple plan [#^ int now #^ tuple desired #^ WorldView world #^ dict records #^ WorkerPolicy policy #^ tuple [warm #()]]
  "1 拍の action: job ごとの action → 温める表の準備(job より後)→ 掃除の係への固定の集合 → 検めの記録の片づけ。"
  (setv jobs (tuple (gfor name (job-names desired world)
                          action (plan-job now name desired world (.get records name (JobRecord name)) policy)
                          action)))
  (+ jobs (warm-actions now warm world jobs policy) (sweep-actions desired world warm) (forget-probe-actions desired world)))

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

(defn #^ dict records-after [#^ int now #^ dict records #^ tuple actions #^ WorkerPolicy [policy (WorkerPolicy)]]
  (setv result (dict records))
  (for [action actions]
    (setv name (cond
      (isinstance action StartJob) action.spec.name
      (isinstance action (| SignalJob ReapJob)) action.name
      True None))
    (when (is-not name None)
      (setv (get result name) (record-after now (.get result name (JobRecord name)) action policy)))
    ;; 退いた process の記憶は、回収した時に捨てる(名は世代ごとに違うので、残すと入れ替えのたびに溜まる)。
    (when (and (isinstance action ReapJob) (in RETIRED-MARK action.name))
      (.pop result action.name None)))
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
          True JobPhase.STARTING))))

(defn #^ tuple statuses [#^ int now #^ tuple desired #^ WorldView world #^ dict records #^ WorkerPolicy policy]
  (tuple (gfor name (job-names desired world)
    :setv want (desired-of desired name)
    :setv process (process-of world name)
    :setv record (.get records name (JobRecord name))
    :setv code (if (is want None) None (code-of world (code-key want)))
    :setv probe (if (is want None) None (probe-failure world want))
    :setv probing (if (is want None) None (probe-status now world want))
    :setv handing-off (and (is-not process None) (is-not want None) want.handoff (!= process.spec want) (is record.stopping None))
    :setv abandoned (and (is-not want None) want.handoff want.handoff-abandoned)
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
        ;; 入れ替えの途中(新のコードの準備・新の Ready 待ち)は、旧が動いていることを示す。
        handing-off
          (.format "入れ替えを待つ(新のコード {})" (if (is code None) "未準備" code.state.value))
        (and (is-not record.last-outcome None) (> record.failures 0))
          f"last={record.last-outcome.value} code={record.last-exit-code} failures={record.failures} backoff={(backoff-ms record policy)}ms"
        (is-not record.last-outcome None)
          f"last={record.last-outcome.value} code={record.last-exit-code}"
        True "")
      ;; 動いている process の世代(coordinator の readiness がこれと一致する報告だけを数える)。
      :instance (if (is process None) None process.instance)
      :spec-hash (if (is process None) None (spec-hash process.spec))
      :placement (if (is process None) None process.spec.placement)
      :retired-from (if (is process None) None process.retired-from)
      ;; 実行環境の root の準備の失敗(動いている process が無い時だけ — 動いていれば準備は済んでいる)。
      :failure (if (and (is process None) (is-not code None) (= code.state CodeState.FAILED)) code.failure None)
      ;; 宣言の spec の入口の検めの姿(走っている・待っている・失敗した間 — 入れ替えで旧が動いている行も)。
      :probe probing))))
