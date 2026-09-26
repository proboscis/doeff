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
(import dataclasses [replace])
(import .worker_model [JobSpec CodeState CodeView ProcessView WorldView StopStage StopProgress ProbeState ProbeView
  Outcome JobRecord WorkerPolicy JobPhase JobStatus PrepareCode PrepareEnv StartJob SignalJob ReapJob RetireJob ReleaseLeases ProbeEntry
  spec-hash code-key probed-job retired-name RETIRED-MARK])

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
  "key = worker_model.code-key(版そのもの、または土台に重ねた木の鍵)。"
  (for [code world.codes]
    (when (= code.revision key) (return code)))
  None)

(defn #^ (| ProbeView None) probe-of [#^ WorldView world #^ JobSpec spec]
  "spec の入口の検めの観測(鍵 = spec-hash)。"
  (setv key (spec-hash spec))
  (for [probe world.probes]
    (when (= probe.spec-hash key) (return probe)))
  None)

(defn #^ (| tuple None) probe-actions [#^ int now #^ JobSpec spec #^ CodeView code #^ WorldView world #^ WorkerPolicy policy]
  "入口の検めの門(木が READY の spec)。通れば None。通れなければ撃つ action — 初回・FAILED の code-retry-ms 後の撃ち直しは
   ProbeEntry、走っている間・撃ち直しの間は空(待つ)。検めの対象でない job(task・素の entry)はいつも通る。"
  (when (not (probed-job spec)) (return None))
  (setv probe (probe-of world spec))
  (cond
    (is probe None) #((ProbeEntry spec code.path))
    (= probe.state ProbeState.PASSED) None
    (= probe.state ProbeState.RUNNING) #()
    (>= (- now (or probe.failed-ms 0)) policy.code-retry-ms) #((ProbeEntry spec code.path))
    True #()))

(defn #^ (| ProbeView None) probe-failure [#^ WorldView world #^ JobSpec spec]
  "spec の入口の検めが FAILED なら、その観測(状態表示の理由)。"
  (setv probe (if (probed-job spec) (probe-of world spec) None))
  (if (and (is-not probe None) (= probe.state ProbeState.FAILED)) probe None))

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
  "続けて落ちた回数に応じた、起こし直すまでの間(1 回目 = restart-backoff-ms・以後は倍・上限 restart-backoff-max-ms)。"
  (min policy.restart-backoff-max-ms
       (* policy.restart-backoff-ms (** 2 (max 0 (- record.failures 1))))))

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
  (setv code (code-of world (code-key spec))
        gate (if (and (is-not code None) (= code.state CodeState.READY)) (probe-actions now spec code world policy) None))
  (cond
    ;; task は 1 度だけ走らせる。終わった後は宣言から外れるまで待つ(結果は状態の報告で運ぶ)。
    (and spec.once (is-not record.last-outcome None)) #()
    (or (is code None) (!= code.state CodeState.READY)) (prepare-actions now spec world policy)
    ;; 入口の検めが通るまで起こさない。
    (is-not gate None) gate
    (in-backoff now record policy) #()
    True #((StartJob spec (+ record.attempts 1) code.path))))

(defn #^ tuple handoff-actions [#^ int now #^ JobSpec want #^ ProcessView process #^ WorldView world #^ WorkerPolicy policy]
  "入れ替え: 新のコードが揃い、新の入口の検めが通るまでは旧を動かしたまま準備と検めだけ進め、通ったら旧を名から外す
   (次の拍で新を同じ名で起こす)。検めが FAILED の間は旧を外さない(書き手の空白を作らない)。"
  (setv code (code-of world (code-key want)))
  (if (and (is-not code None) (= code.state CodeState.READY))
      (do (setv gate (probe-actions now want code world policy))
          (if (is-not gate None)
              gate
              #((RetireJob process.name process.pid (retired-name process.name (or process.instance (str process.pid)))))))
      (prepare-actions now want world policy)))

(defn #^ tuple retired-actions [#^ int now #^ ProcessView process #^ tuple desired #^ WorldView world
                                #^ JobRecord record #^ WorkerPolicy policy]
  "退いた process: 元の job の新しい process が Ready と数えられたら止める。それまでは動かし続ける(書き手の空白を作らない)。
   元の job が宣言から消えた・handoff でなくなった時も止める。"
  (setv want (desired-of desired process.retired-from)
        current (process-of world process.retired-from))
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
         (if process.instance #((ReleaseLeases process.instance)) #()))
    (is-not process.retired-from None) (retired-actions now process desired world record policy)
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

(defn #^ tuple plan [#^ int now #^ tuple desired #^ WorldView world #^ dict records #^ WorkerPolicy policy]
  (tuple (gfor name (job-names desired world)
               action (plan-job now name desired world (.get records name (JobRecord name)) policy)
               action)))

(defn #^ JobRecord record-after [#^ int now #^ JobRecord record action [policy (WorkerPolicy)]]
  (cond
    (isinstance action StartJob) (replace record :attempts action.attempt :stopping None :last-start-ms now)
    (isinstance action SignalJob)
      (replace record :stopping
        (StopProgress (if (is record.stopping None) now record.stopping.requested-ms) action.stage now))
    (isinstance action ReapJob)
      (replace record :last-exit-ms now :last-outcome action.outcome :last-exit-code action.exit-code
               :stopping None
               :failures (cond
                           (!= action.outcome Outcome.EXITED) 0
                           (and (is-not record.last-start-ms None)
                                (>= (- now record.last-start-ms) policy.stable-run-ms)) 1
                           True (+ record.failures 1)))
    True record))

(defn #^ dict records-after [#^ int now #^ dict records #^ tuple actions [policy (WorkerPolicy)]]
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
          (in-backoff now record policy) JobPhase.BACKOFF
          True JobPhase.STARTING))))

(defn #^ tuple statuses [#^ int now #^ tuple desired #^ WorldView world #^ dict records #^ WorkerPolicy policy]
  (tuple (gfor name (job-names desired world)
    :setv want (desired-of desired name)
    :setv process (process-of world name)
    :setv record (.get records name (JobRecord name))
    :setv code (if (is want None) None (code-of world (code-key want)))
    :setv probe (if (is want None) None (probe-failure world want))
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
      :failure (if (and (is process None) (is-not code None) (= code.state CodeState.FAILED)) code.failure None)))))
