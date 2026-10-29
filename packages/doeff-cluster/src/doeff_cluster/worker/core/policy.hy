;; worker の純粋な判断。宣言・観測・記憶・時刻から action と状態表示を導く。I/O はしない。
;;
;; 入れ替え(handoff・2026-09-24): spec の変わった job が handoff を宣言していれば、旧を止めずに名から外し(RetireJob)、新を同じ名で
;; 起こす。退いた旧は、coordinator が新の process を Ready と数えた(宣言の ready-instance = 新の世代の名)後に止める。新のコードの
;; 準備の間も旧は動かし続ける。並べるのは 1 つまで(退いた process が既に在る間の次の変更は、止めてから起こす)。
;; 退きの知らせ(#3672): 名から外す RetireJob が旧へ「退く」(Retired)を送る — 新の起動・新の Ready・旧の止めのどれよりも前。その後に
;; 入れ替えが諦められたら(旧は動き続ける)同じ旧へ「退きを取り消した」(HandoffAbandoned)、諦めが解けたらもう一度「退く」を NoticeJob で
;; 送る(notice-actions — 観測の notice と今の知らせの食い違いだけ)。
;;
;; recreate の入れ替えの順(2026-10-08): handoff でない job(task を除く)も、宣言の版が変わったら旧を動かしたまま新しい版の木を準備し、
;; 入口の検めが通ってから旧を止める(SpecChanged)。止め終えた次の周期で新しい版をすぐ起動する(準備と検めは済んでいる)。準備に失敗した・
;; 検めが落ちた間は旧を止めない(書き手の空白を作らない — handoff と同じ)。新旧の本体を同時に動かす事は無い(検めは入口の module の
;; import だけで本体を走らせない — worker_rules.probe-args)。以前は先に旧を止めてから準備と検めをしたので、本番の Pod 1 つの記録の service が
;; 新しい版の環境の準備の約 107 秒を含む約 130 秒書けなかった(02:45:30 に止め → 02:47:24 に起動)。判断は replace-step の 1 か所
;; (plan-job の止めの枝と、見送りの行の start-holds が読む)。宣言から消えた job・drain 中の版の据え置き・task は今までどおり。
;; 待ちの子から分ける root の service(card ki-de44efdb9aa4)は、新しい版の root の待ちの子が準備済みになるまでも旧を止めない(2026-10-29・
;; card ki-d8cc8191472e): 以前は検めが通った所で旧を止め、止め終えた後に新が待ちの子の準備(本番 zeus の 10-28〜29 の 30 回のうち 23 回・
;; 回収から起動まで 5.8〜23.3 秒)を待ったので、その間 job の仕事が止まった。待ちの子が準備済みなら、止め終えた次の周期に分けて起こす。
;;
;; 入口の検め(probe・2026-09-25): service の job は、木が揃った後に「worker の実行環境でその木の入口(factory と env)を読み込めるか」を
;; 先に試し(ProbeEntry)、PASSED になるまで起こさない(StartJob)・旧を名から外さない(RetireJob)。業務コード・定義・実行環境の組が崩れた
;; 木(例: 定義だけ進んで実行環境の doeff に無い名を import する)は、起こしては import で落ちる backoff を繰り返す代わりに、
;; 理由つきの probe-failed で止まる。入れ替えの旧は止めない(書き手の空白を作らない)。FAILED は code-retry-ms の後に撃ち直す。
;; 検めの間(走っている・同じ木の検めの終わりを待っている)は starting ではなく probing と出し、状態の行の probe に経過の秒・回数・
;; 直前の失敗の理由を載せる(2026-09-27 — 以前は 17 分 starting のままで、理由は FAILED から撃ち直すまでの 30 秒しか見えなかった)。
;; 同じ木の検めを 1 本にまとめる・時間切れで process group ごと止めるのは検めの process の持ち主(handlers.ProbeStore)。
(require doeff-hy.macros [defk val var <-])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import dataclasses [dataclass replace])  ; dataclass = defrecord の展開が名指す
(import signal)
(import doeff [run])
(import doeff_traverse [sequence])
(import doeff_cluster.worker.intent.worker_model [Action CodeState CodeView ProcessView WorldView StopStage JobStop ProbeState ProbeView ProbeStatus
  Outcome JobRecord JobNotStarted WorkerPolicy JobStatus PrepareCode PrepareEnv SweepEnvs StartJob SignalJob ReapJob RetireJob ReleaseLeases
  ProbeEntry ForgetProbes WarmChildView WarmLaunch StartWarmChild StopWarmChild ForgetWarmChild
  StopReason SpecChanged Undeclared HandoffAbandoned Retired StartHold NotYetRead DeclarationRead] doeff_cluster.shared.intent.job_model [JobSpec JobPhase] doeff_cluster.shared.core.job_rules [spec-hash] doeff_cluster.worker.core.worker_rules [code-key probed-job retired-name ready-path RETIRED-MARK ENV-KEY-PREFIX])
(import doeff_cluster.shared.intent.runtime_env_model [EnvFailure EnvFailureKind])
(import doeff_cluster.worker.intent.worker_model [NoticeJob])
(import doeff_cluster.worker.core.warm_rules [forks-from-warm-child warm-key-of warm-mark-clean warm-child-of warm-child-ready mark-refusal
  warm-launch warm-child-refused warm-refusal-failure service-warm-keys fork-candidate WarmStart warm-start])

;; 自己停止(2026-09-25): coordinator との連絡が fence(ClusterTiming.fence-ms)を越えて途絶えた worker は、自分の job を止めてきた
;; (coordinator は 45 秒で他へ移すので、同じ job が 2 つ動かないように)。ただし書き手(入れ替え handoff を宣言した job)は、旧と新が
;; 並んで動く前提で作られていて、外への書きは名前付きの lease の柵(semaphore_handlers.leases-fence)だけが守る。その柵は coordinator の
;; 時計の期限で締まるので、途絶で止める必要が無い — 止めると coordinator の作り直し(版の更新)のたびに書き手が止まった。
;; 書き手の停止は lease に一本化し、自己停止は lease を持たない job と task にだけ当てる。切り離した task(2026-09-25)も止めない
;; (lease は担い手の worker の heartbeat が延ばし、途絶が lease より長ければ coordinator がその task を lost にする)。
;; 途絶しても動かし続けてよい印(#2804): coordinator が「他に置ける worker が無い」と判じて返事の job に付けた印(JobSpec.keep-when-cut-off)
;; の在る job も止めない。coordinator は印を渡した担い手から、担い手が印を持たないと知らせる(heartbeat の keptWhenCutOff — keep-marks-held)
;; か Worker が消されるまで job を他へ移さないので、2 か所で走らない保証は時間の競争(fence < 移し替え)ではなく「移さない」で持つ。
;; ただし担い手の沈黙が約束の期限(ClusterTiming.kept-reassign-after-ms)を越えると coordinator は約束を外して他へ移す — その期限は、下の
;; 長い方の柵での止め切りの最悪より後(条 C4 と同じ形 — tests/test_cluster_timing.hy)。
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

(defn #^ tuple retired-of [#^ WorldView world #^ str name]
  "job name の、入れ替えで退いてまだ動いている process の列(退いた process の上限 R と、いちばん古く退いた process を決めるため —
   #4072 の D-3)。終わった process は回収を待つだけなので数えない。"
  (tuple (gfor p world.processes :if (and (= p.retired-from name) (is p.exit-code None)) p)))

(defn #^ int retired-at-of [#^ ProcessView process]
  "退いた process の退いた刻(寿命の上限を数える起点と、いちばん古く退いた process を選ぶ順の 1 か所)。退いた刻の無い観測(この欄を書く
   前の worker が退かせた process)は起こした刻から数える(実際に退いた刻より前 — 早めに止まる側)。"
  (if (is-not process.retired-at-ms None) process.retired-at-ms process.started-ms))

(defn #^ int backoff-ms [#^ JobRecord record #^ WorkerPolicy policy]
  "続けて予期せず終わった回数(exit code を問わない)に応じた、起こし直すまでの間(1 回目 = restart-backoff-ms・以後は倍・上限 restart-backoff-max-ms)。"
  (min policy.restart-backoff-max-ms
       (* policy.restart-backoff-ms (** 2 (max 0 (- record.unexpected-exits 1))))))

(defk ran-stably? [now record policy]
  {:pre [(: now int) (: record JobRecord) (: policy WorkerPolicy)] :post [(: % bool)] :tags {:context "worker" :role "judgment"}}
  "job の名で最後に起こした process が stable-run-ms 以上動いたか — 続けて落ちた回数を数え直す境 — を 1 か所で決めるため。記憶の数え
   record-after(終わりの回収 ReapJob と、名から外す RetireJob)と状態の報告 job-status が同じ述語を読む(card ki-3f4bbc55733a — 以前は
   報告だけがこの境を持ち、入れ替えで名から外した後の新しい process は、前の process が長く動いた後でも前の落ち続けの回数を引き継いだ)。"
  (and (is-not record.last-start-ms None) (>= (- now record.last-start-ms) policy.stable-run-ms)))

(defn #^ (| int None) backoff-from [#^ JobRecord record]  ; defk にできない: 純粋な判断の in-backoff(Program の外の関数)が呼ぶ
  "起こし直しの間(backoff)を数える起点を 1 か所で決める(in-backoff と、期限の worker_due.plan-due が読む): 最後の StartJob が起こせ
   なかった刻(card ki-e1612a12d0c3 — 終わりより後の出来事なので先に読む)、それ以外は停止を求めずに終わった刻。どちらも無ければ None。"
  (cond
    (is-not record.not-started None) record.not-started.at-ms
    (and (is-not record.last-exit-ms None) (= record.last-outcome Outcome.EXITED)) record.last-exit-ms
    True None))

(defn #^ bool in-backoff [#^ int now #^ JobRecord record #^ WorkerPolicy policy]
  (setv since (backoff-from record))
  (and (is-not since None) (< (- now since) (backoff-ms record policy))))

(defn #^ bool memory-given-up [#^ JobSpec spec #^ JobRecord record #^ WorkerPolicy policy]  ; defk にできない: 純粋な判断の phase-of(Program の外の関数)が呼ぶ
  "常駐の job spec が機体の memory の見張りに続けて止められ、起こし直しを止めたか(起こしの段 start-step と状態の行の phase-of・job-status が
   同じ述語を読む): memory-kill-limit 回に達し、最後に止められた process の spec が今の宣言と同じ(宣言が変われば新しい spec は起こす)。
   task は 1 度だけ走るので当たらない(止められた task は終わった task として返る)。"
  (and (not spec.once)
       (>= record.memory-kills policy.memory-kill-limit)
       (= record.memory-killed-spec (spec-hash spec))))

(defn #^ tuple stop-actions [#^ int now #^ ProcessView process #^ JobRecord record #^ WorkerPolicy policy #^ StopReason reason]
  "止める action(reason = 止め始める訳 — 止め始めた後は記憶の訳を持ち回り、KILL も TERM と同じ訳を名乗る・#3713)。"
  (setv stopping record.stopping)
  (cond
    (is stopping None) #((SignalJob process.name process.pid StopStage.TERM reason))
    (and (= stopping.stage StopStage.TERM) (>= (- now stopping.signalled-ms) policy.stop-grace-ms))
      #((SignalJob process.name process.pid StopStage.KILL stopping.reason))
    ;; KILL 後は待つだけ。確認できないまま置き換えを起動しない。
    True #()))

(defn #^ (| PrepareCode PrepareEnv) prepare-action [#^ JobSpec spec #^ (| int None) compile-jobs]
  "spec の置き場を用意する action: 実行環境の job は env の root(PrepareEnv)、それ以外は commit の木(PrepareCode)。compile-jobs = bytecode を
   焼く道具の並べる数(None = 道具の既定 — 旧い process が動いている間の準備だけ replace-step が方策の値を渡す・2026-10-08)。"
  (if spec.runtime-env
      (PrepareEnv (code-key spec) spec.runtime-env compile-jobs)
      (PrepareCode (code-key spec) compile-jobs)))

(defn #^ tuple prepare-actions [#^ int now #^ JobSpec spec #^ WorldView world #^ WorkerPolicy policy #^ (| int None) compile-jobs]
  "spec のコードの木を用意する action(用意できていれば空)。準備に失敗した版は、間を置いてから作り直す。compile-jobs は prepare-action と同じ。"
  (setv code (code-of world (code-key spec)))
  (cond
    (is code None) #((prepare-action spec compile-jobs))
    (and (= code.state CodeState.FAILED)
         (>= (- now (or code.failed-ms 0)) policy.code-retry-ms)) #((prepare-action spec compile-jobs))
    True #()))

(defrecord StartStep
  "宣言の job 1 つを新しい版の起動へ進める判断の答え(#3713): 動いている process の無い job を起動する start-step と、recreate の job の
   旧い版を新しい版へ移す replace-step(2026-10-08)が返す。actions = この周期に実行する action(準備・検め・StartJob・旧の止め)・
   hold = 新しい版を起動しない訳(StartJob を出す周期・旧を止める周期・終わった task は None)。"
  (#^ tuple actions)
  (#^ (| StartHold None) hold))


(defrecord JobHold
  "拍の終わりの宣言の job 1 つの起こしの見送り(#3713): name = job の名・hold = 見送りの訳(見送っていなければ None)。"
  (#^ str name)
  (#^ (| StartHold None) hold))


;; 準備・検めの失敗の後の撃ち直しの間も、失敗の訳を名乗り続ける(撃ち直しで準備中に戻るたびに行を出さない)。
(val RETRYING-HOLDS #(StartHold.PREPARE-FAILED StartHold.DISK-FULL))


(defk prepare-hold [code record]
  {:pre [(: code (| CodeView None)) (: record JobRecord)] :post [(: % StartHold)] :tags {:context "worker" :role "judgment"}}
  "木・root が READY でない job の見送りの訳を決めるため: 失敗(disk の空き不足は DISK-FULL)・失敗の後の撃ち直しの準備中は前の失敗の訳の
   まま・それ以外は準備待ち。"
  (match code
    (CodeView :state CodeState.FAILED :failure (EnvFailure :kind EnvFailureKind.DISK-FULL)) StartHold.DISK-FULL
    (CodeView :state CodeState.FAILED) StartHold.PREPARE-FAILED
    _ (if (in record.held RETRYING-HOLDS) record.held StartHold.PREPARING)))


(defk probe-hold [probe]
  {:pre [(: probe (| ProbeView None))] :post [(: % StartHold)] :tags {:context "worker" :role "judgment"}}
  "入口の検めの門で止まった job の見送りの訳を決めるため: 失敗した・失敗の後に撃ち直している検めは PROBE-FAILED、初回の検めは PROBING。"
  (match probe
    None StartHold.PROBING
    (ProbeView :state ProbeState.FAILED) StartHold.PROBE-FAILED
    (ProbeView :attempts 1) StartHold.PROBING
    _ StartHold.PROBE-FAILED))


(defn #^ bool elder-waits [#^ JobSpec spec]  ; defk にできない: 純粋な判断の phase-of(Program の外の関数)が呼ぶ
  "前の世代が動かしている印(elder-runs)を持つ常駐の recreate の job か(AL-D2f — 起こしの段 ready-tree-step と状態の行の phase-of が
   同じ述語を読む)。handoff と task は印を受けない。"
  (and spec.elder-runs (not spec.handoff) (not spec.once)))


(defn #^ bool elder-ready [#^ int now #^ JobSpec spec #^ (| CodeView None) code #^ WorldView world #^ WorkerPolicy policy]  ; defk にできない: 純粋な判断の phase-of(Program の外の関数)が呼ぶ
  "前の世代が動かしている印の job が、起こせる所まで揃ったか(AL-D2f — 状態の行の elder-runs): 木が READY で、入口の検めが通った
   (起こしの段 ready-tree-step が検めの action を返さない)。coordinator はこの行を見て前の世代から外す。"
  (setv tree (ready-path code))
  (and (elder-waits spec) (is-not tree None) (is (probe-actions now spec tree world policy) None)))


(defk ready-tree-step [now spec tree world record policy warm-keys]
  {:pre [(: now int) (: spec JobSpec) (: tree str) (: world WorldView) (: record JobRecord) (: policy WorkerPolicy) (: warm-keys frozenset)]
   :post [(: % StartStep)] :tags {:context "worker" :role "judgment"}}
  "木が READY の job を起こすため: 入口の検めが通るまで起こさず、backoff の間は待つ。待ちの子から分かれる task(#3646)と、service を分ける
   root(warm-keys — card ki-de44efdb9aa4)の service は、道を warm_rules の warm-start が決める: 待ちの子が準備済みになるまで起こさず
   (待ちの子を起こすのは warm-child-actions)、分ける時は StartJob に root のキーを載せる。黙って入れ物 shim へ倒れない(service が shim で
   起きるのは warm-start が名指す訳 — 古い版の待ちの子・断りの上限 — の時だけ)。"
  (val gate (probe-actions now spec tree world policy))
  (val path (warm-start spec world policy warm-keys))
  (cond
    (is-not gate None) (StartStep :actions gate :hold (! (probe-hold (probe-of world spec))))
    (in-backoff now record policy) (StartStep :actions #() :hold StartHold.BACKOFF)
    (= path WarmStart.WAIT) (StartStep :actions #() :hold StartHold.WARM-CHILD)
    ;; 同じ名の前の世代がこの job を動かしている(AL-D2f): 準備と検めは揃ったので、coordinator が印を外すまで起こさない(状態の行は
    ;; elder-runs — coordinator はそれを見て前の世代から外す)。
    (elder-waits spec) (StartStep :actions #() :hold StartHold.ELDER-RUNS)
    ;; 機体の memory が逼迫している間は、task(once)だけ起こさない(重い task を並べて機体が尽きるのを避ける — card ki-7b934e2538f9)。準備と
    ;; 検めは済んでいるので、逼迫が解けた次の拍にすぐ起こす。常駐の job は見送らない。
    (and spec.once world.memory-tight) (StartStep :actions #() :hold StartHold.MEMORY-TIGHT)
    True (StartStep :actions #((StartJob spec (+ record.attempts 1) tree :warm-key (if (= path WarmStart.FORK) (warm-key-of spec) None)))
                    :hold None)))


(defk start-step [now spec world record policy warm-keys]
  {:pre [(: now int) (: spec JobSpec) (: world WorldView) (: record JobRecord) (: policy WorkerPolicy) (: warm-keys frozenset)]
   :post [(: % StartStep)] :tags {:context "worker" :role "judgment"}}
  "動いている process の無い宣言の job を起こす action と、起こさない訳を 1 か所で決めるため(plan-job の起こしの枝と、拍の終わりの
   見送りの行 start-holds が同じ判断を読む — #3713)。warm-keys = service を待ちの子から分ける root(warm_rules の service-warm-keys)。"
  (val code (code-of world (code-key spec)))
  (val tree (ready-path code))
  (cond
    ;; この Pod の止まり(preStop の drain)が始まった(AL-D2p): handoff の job の新しい試行を起こさず、準備も撃たない。coordinator の返事の
    ;; 印 supersededBy が欠けた拍にも効く守り — 退かせた process が走っている間、返事は job を載せ続け、名が空いているので、印の欠けた
    ;; 拍ごとに新しい試行を前の process と並べていた(10-11 の zeus の画面の process の群)。続きは同じ名の新しい世代か、coordinator が
    ;; 移した先が起こす。
    (and spec.handoff world.leaving) (StartStep :actions #() :hold StartHold.LEAVING)
    ;; task は 1 度だけ走らせる。終わった後は宣言から外れるまで待つ(結果は状態の報告で運ぶ)。
    (and spec.once (is-not record.last-outcome None)) (StartStep :actions #() :hold None)
    ;; 機体の memory の見張りに続けて止められた常駐の job は起こし直さない(起動のたびに同じ memory の山を作る job は、起こし直しても
    ;; 同じ所で止められる)。状態の行は memory-killed(coordinator は版の判定を Blocked にする)。宣言の spec が変われば起こす。
    (memory-given-up spec record policy) (StartStep :actions #() :hold StartHold.MEMORY-KILLED)
    ;; 動いている process が無いので、焼く道具は既定の並べる数(cgroup の CPU の上限・天井 BAKE-JOBS-CEILING)で焼く。
    (is tree None) (StartStep :actions (prepare-actions now spec world policy None) :hold (! (prepare-hold code record)))
    True (! (ready-tree-step now spec tree world record policy warm-keys))))


(defk recreating? [want process record]
  {:pre [(: want (| JobSpec None)) (: process (| ProcessView None)) (: record JobRecord)] :post [(: % bool)]
   :tags {:context "worker" :role "judgment"}}
  "recreate の job の動いている旧い版を、宣言の新しい版へ移している途中かを 1 か所で決めるため(plan-job の止めの枝・start-holds・
   statuses が同じ述語を読む — 2026-10-08): 宣言に在り、handoff でも task でもなく、版を据え置いておらず(drain 中でない — #3684)、
   動いている process(終わっていない・退いていない)の spec が宣言と違い、まだ止め始めていない。"
  (and (is-not want None) (is-not process None)
       (not want.handoff) (not want.once) (not want.hold-version)
       (is process.exit-code None) (is process.retired-from None)
       (!= want process.spec) (is record.stopping None)))


(defk replace-step [now want process world record policy warm-keys]
  {:pre [(: now int) (: want JobSpec) (: process ProcessView) (: world WorldView) (: record JobRecord) (: policy WorkerPolicy)
         (: warm-keys frozenset)]
   :post [(: % StartStep)] :tags {:context "worker" :role "judgment"}}
  "recreate の job の旧い版を宣言の新しい版へ移す action と、新しい版をまだ起動しない訳を 1 か所で決めるため(plan-job の止めの枝と、
   周期の終わりの見送りの行 start-holds が同じ判断を読む — 2026-10-08): 新しい版の木が READY でなければ準備だけ(準備中・失敗の後の
   作り直しの間を含む — 訳は prepare-hold)、木が揃っても入口の検めが通っていなければ検めだけ(走っている・落ちた間は待つ — 訳は
   probe-hold)、新しい版を待ちの子から分ける service(warm-keys の root — warm_rules の warm-start が WAIT)は待ちの子が準備済みになるまで
   待つ(訳は WARM-CHILD — 待ちの子を起こすのは warm-child-actions・card ki-d8cc8191472e)。どの間も旧は動かしたまま。揃ったら旧を
   止める(SpecChanged)— 止め終えた次の周期で start-step が待たずに起動する(warm-start は旧の有無を読まないので、ここで WAIT でなければ
   start-step でも WAIT でない)。準備の action は方策の並べる数(compile-jobs-while-replacing)を載せる — 旧い service と焼きが同じ Pod の
   memory の上限を分け合うので、道具の既定の並べる数で焼かない。"
  (val code (code-of world (code-key want)))
  (val tree (ready-path code))
  (val gate (if (is tree None) None (probe-actions now want tree world policy)))
  (cond
    (is tree None) (StartStep :actions (prepare-actions now want world policy policy.compile-jobs-while-replacing)
                              :hold (! (prepare-hold code record)))
    (is-not gate None) (StartStep :actions gate :hold (! (probe-hold (probe-of world want))))
    (= (warm-start want world policy warm-keys) WarmStart.WAIT) (StartStep :actions #() :hold StartHold.WARM-CHILD)
    True (StartStep :actions (stop-actions now process record policy (SpecChanged)) :hold None)))

(defn #^ tuple handoff-actions [#^ int now #^ JobSpec want #^ ProcessView process #^ WorldView world #^ WorkerPolicy policy]
  "入れ替え: 新のコードが揃い、新の入口の検めが通るまでは旧を動かしたまま準備と検めだけ進め、通ったら旧を名から外す
   (次の拍で新を同じ名で起こす)。検めが FAILED の間は旧を外さない(書き手の空白を作らない)。退いた process が既に上限 R
   (retired-limit-for — 宣言の retiredLimit か policy.retired-limit)だけ居れば、外さずに待つ — 退いた process が自分で終わるか寿命の
   上限で止まった拍で外す(同時に動くのは R + 1 まで・退いた process が回している仕事は切らない・#4072 の D-3 の改め)。"
  (setv tree (ready-path (code-of world (code-key want))))
  (if (is-not tree None)
      (do (setv gate (probe-actions now want tree world policy))
          (cond
            (is-not gate None) gate
            (retired-room-full want process.name world policy) #()
            True #((RetireJob process.name process.pid (retired-name process.name (or process.instance (str process.pid)))))))
      ;; 焼く道具の並べる数は道具の既定のまま(絞るのは recreate の job の replace-step だけ — 2026-10-08)。
      (prepare-actions now want world policy None)))

(defn #^ tuple retired-actions [#^ int now #^ ProcessView process #^ str origin #^ tuple desired #^ WorldView world
                                #^ JobRecord record #^ WorkerPolicy policy]
  "退いた process(origin = 退く前の job の名): 元の job の新しい process が Ready と数えられたら止める。それまでは動かし続ける
   (書き手の空白を作らない)。宣言に寿命の上限(want.retired-ms — #4072 の D-2)が在れば新の Ready では止めず、旧が自分で終わる
   (plan-job の終わりの枝が回収する)か、退いてから上限を越えた時に止める(旧が持つ仕事を終わりまで回す)。元の job が宣言から消えた・
   handoff でなくなった時も止める。退いた process が上限 R に達している間に宣言が変わっても、退いた process は止めない — 今の
   process を退かせる番は handoff-actions が待たせる(#4072 の D-3 の改め: 以前はいちばん古く退いた process を止めて 1 つ空け、その
   process が回していた仕事を切っていた — 2026-10-10 15:01 UTC の本番)。
   退きの保ち(card ki-e3531030eb37): 寿命の上限は自分で終わらない退いた process の受け止めで、仕事の長さの上限ではない。退いた process
   が退きの保ち(readiness_model の RETIREMENT-HOLD — 元の job の want.retired-held にその世代の名が在る)を持つ間は、上限を越えても止めない
   (以前は上限の拍で止め、走っていた仕事を切っていた — 2026-10-16 04:28:49 JST の本番で 6 時間 7 分の仕事)。保ちが無くなれば(返した・
   延ばしが止まって切れた)、その拍から今どおり止める。
   世代をまたぐ入れ替え(AL-D2): 元の job に印 superseded-by が在れば、新の process は新しい世代の worker の上に在り、この worker の
   上には無い — 印を「新の Ready」と数える(coordinator は新しい世代の process が Ready か、この世代の Pod の止まりが始まった時だけ
   印を付ける)。数えなければ寿命の無い退いた process は止める時が来ず、旧い Pod の preStop が drain の上限まで待った(card ki-7c11722e3621
   — 10-12 の本番で 6 時間)。"
  (setv want (desired-of desired origin)
        current (process-of world origin)
        lifetime (if (is want None) None want.retired-ms)
        successor-ready (and (is-not want None)
                             (or (is-not want.superseded-by None)
                                 (and (is-not current None) (is current.exit-code None) (= current.spec want)
                                      (is-not want.ready-instance None) (= want.ready-instance current.instance))))
        held (and (is-not want None) (in process.instance want.retired-held))
        due (if (is lifetime None) successor-ready (and (>= (- now (retired-at-of process)) lifetime) (not held))))
  (if (or (is-not record.stopping None)
          (is want None)
          (not want.handoff)
          due)
      (stop-actions now process record policy (Retired))
      #()))


(defn #^ int retired-limit-for [#^ JobSpec want #^ WorkerPolicy policy]  ; defk にできない: worker の純粋な判断(handoff-actions — defn)が呼ぶ
  "入れ替えの job want が同時に残す退いた process の数の上限 R を決めるため: 宣言の readiness の retiredLimit(JobSpec.retired-limit)が
   在ればそれ、無ければ worker の既定 policy.retired-limit(#4072 の D-3 の改め)。"
  (if (is want.retired-limit None) policy.retired-limit want.retired-limit))

(defn #^ bool retired-room-full [#^ JobSpec want #^ str name #^ WorldView world #^ WorkerPolicy policy]
  ;; defk にできない: worker の純粋な判断(handoff-actions・phase の外の job-status の文 — defn の中)が呼ぶ
  "入れ替えの job name の退いてまだ動いている process が上限 R(retired-limit-for)に達しているか — 今の process を退かせずに待つ判断
   (handoff-actions)と、その待ちを状態の行の文に出す判断(job-status — 退いた process の空き待ち・AL-D1)が同じこの 1 つを読む。"
  (>= (len (retired-of world name)) (retired-limit-for want policy)))

(defk plan-job [now name desired world record policy absent]
  {:pre [(: now int) (: name str) (: desired tuple) (: world WorldView) (: record JobRecord) (: policy WorkerPolicy) (: absent StopReason)]
   :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "job 1 つの action(absent = 宣言に無い job を止める訳 — 宣言から外れた・途絶で絞った・worker の停止。#3713)。plan が名ごとに
   sequence で呼ぶ。"
  (val want (desired-of desired name))
  (val process (process-of world name))
  ;; 入れ替えの諦め(2026-09-26 — coordinator の handoff_policy が期限で決め、heartbeat の返事で運ぶ)。
  (val abandoned (and (is-not want None) want.handoff want.handoff-abandoned))
  ;; 世代をまたぐ入れ替え(AL-D2): 同じ名の worker の新しい世代の process が Ready — この(退いた)世代は退かせるだけで起こさない。
  (val superseded (and (is-not want None) want.handoff (is-not want.superseded-by None)))
  (cond
    ;; 諦めた入れ替えの新・新しい世代へ移った job は起こし直さない(退いた旧が動き続ける)。宣言が変われば諦めは解け、次の拍で起こす。
    ;; 止まりの始まった Pod の handoff の job も起こさない(印の有無に依らない — start-step の頭・AL-D2p)。
    (is process None) (if (or (is want None) abandoned superseded)
                          #()
                          (. (! (start-step now want world record policy (service-warm-keys desired policy))) actions))
    (is-not process.exit-code None)
      (+ #((ReapJob name process.pid
             (if (is record.stopping None) Outcome.EXITED Outcome.STOPPED) process.exit-code
             ;; 機体の memory の見張りがこの process の木を止めていれば、その spec の指紋(記憶が続けて止められた回数を数える)。
             (if (in #(name process.pid) world.memory-killed) (spec-hash process.spec) None)))
         ;; 終わった process の lease は、期限を待たずに返す(次の担い手がすぐ取れる)。
         ;; 担い手の名は子が名乗った job の名(起こした spec の名 — 退いた process も元の名)と世代の名。
         (if process.instance #((ReleaseLeases process.spec.name process.instance)) #()))
    (is-not process.retired-from None) (retired-actions now process process.retired-from desired world record policy)
    ;; 新しい世代の process が Ready になった job の今の process: 止めずに退かせる(退く知らせ → 走っている仕事を終えて自分で終わる・
    ;; 寿命の上限は retired-actions)。止め始めた process は止め終える(下の枝)。
    (and superseded (is record.stopping None))
      #((RetireJob process.name process.pid (retired-name process.name (or process.instance (str process.pid)))))
    ;; 諦めた入れ替え: 今の宣言の spec の新の process を止める(止め始めた process は止め終える)。前の宣言の process(まだ退いて
    ;; いない旧)は名から外さず、そのまま動かす — 新を起こさないので並べる理由が無い。
    abandoned
      (if (or (= want process.spec) (is-not record.stopping None))
          (stop-actions now process record policy (HandoffAbandoned))
          #())
    (and (= want process.spec) (is record.stopping None)) #()
    ;; 版を据え置く(#3684 — この worker が drain 中): spec が変わっても、止めていない process をそのまま動かす(新しい版の準備・入口の検め・
    ;; 退かせ・止めをしない)。移し先で新しい版が Ready になれば宣言から消え(want が None)、下の止めへ進む。drain が解けて印が偽に戻った
    ;; 拍から、下の入れ替えへ進む。宣言から消えた job(want が None)は今までどおり止める。
    ;; 止まりの Pod(AL-D2p)も handoff の job の版を入れ替えない: 退かせた今の process の後継は、止まりの Pod では起こさない(start-step)
    ;; ので、入れ替えると退いた process が自分で終わった後に誰も動かさない。返事の hold-version が欠けた拍(drain の頼みが届く前)にも効く。
    ;; 新しい世代が Ready の印 supersededBy で退かせる道は上の枝(止まりでも今どおり)。
    (and (is-not want None) (or want.hold-version (and want.handoff world.leaving)) (is record.stopping None)) #()
    ;; spec が変わった handoff の job: 旧を止めずに新を並べる(退いた process が既に在っても、今の process を退かせて並べる — 退いた
    ;; process の上限 R は handoff-actions と retired-actions が守る・#4072 の D-3)。
    (and (is-not want None) want.handoff (is record.stopping None))
      (handoff-actions now want process world policy)
    ;; spec が変わった recreate の job: 旧を動かしたまま新しい版の準備と入口の検めを進め、両方が済んでから旧を止める(2026-10-08 —
    ;; 新旧の本体を同時に動かさない・判断は replace-step)。
    (and (is-not want None) (! (recreating? want process record)))
      (. (! (replace-step now want process world record policy (service-warm-keys desired policy))) actions)
    ;; 宣言から消えた job・止め始めた process・宣言の変わった task → すぐ止める(止め始めた process は止め終える)。
    True (stop-actions now process record policy (if (is want None) absent (SpecChanged)))))

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
               ;; 焼く道具の並べる数は道具の既定のまま(絞るのは recreate の job の replace-step だけ — 2026-10-08)。
               (PrepareEnv w.key w.runtime-env None :warm True))))

(defk warm-stop-step [now view policy]
  {:pre [(: now int) (: view WarmChildView) (: policy WorkerPolicy)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "止め始めた待ちの子の次の一手を決めるため: TERM の後、停止の猶予を過ぎても終わらなければ KILL を 1 度送る(job の止めと同じ間)。"
  (if (and (is-not view.stop None) (= view.stop.stage StopStage.TERM) (>= (- now view.stop.signalled-ms) policy.stop-grace-ms))
      #((StopWarmChild view.key StopStage.KILL "止めの合図の後も終わらない"))
      #()))

(defk warm-child-step [now key launch view policy [refusal None]]
  {:pre [(: now int) (: key str) (: launch (| WarmLaunch None)) (: view (| WarmChildView None)) (: policy WorkerPolicy)
         (: refusal (| JobNotStarted None))]
   :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "root のキー 1 つの待ちの子の action を決めるため(判断の表 — tests/test_warm_child_policy.hy が全行を撃つ)。launch = 要る root の待ちの子の
   起こし方(要らない・root が READY でなければ None)・view = 待ちの子の観測・refusal = この root から task を分けるのを断られた最後の答え
   (無ければ None — card ki-e1612a12d0c3)。観測が変わらない限り同じ action を 2 度出さない(起こした・止め始めた・忘れた事は次の観測に
   出る)。走っている task には何もしない(条 WC2)。"
  (cond
    ;; 要らない root: 走っていれば止め、終わっていれば観測から外す。
    (is launch None)
      (cond
        (is view None) #()
        (is-not view.exit-code None) #((ForgetWarmChild key))
        (is view.stop None) #((StopWarmChild key StopStage.TERM "root の待ちの子が要らなくなった"))
        True (! (warm-stop-step now view policy)))
    (is view None) #((StartWarmChild key launch))
    ;; 起動の断りが上限に達した待ちの子は起こし直さない(分かれる task は phase-of が env-failed で終える)。
    ;; 観測は残し、task が終わって root が要らなくなった時に上の枝が外す(外すと数えも 0 に戻る)。
    (warm-child-refused view policy) #()
    ;; 終わった待ちの子は、準備の失敗と同じ間(code-retry-ms)を置いてから起こし直す(落ちる入口を毎拍起こさない)。
    (is-not view.exit-code None)
      (if (>= (- now (or view.ended-ms 0)) policy.code-retry-ms) #((StartWarmChild key launch)) #())
    (is-not view.stop None) (! (warm-stop-step now view policy))
    ;; 今の子を起こした後に task を分けるのを断った待ちの子(card ki-e1612a12d0c3)は止める。止めた子の終わりは断りに数え(fork-refused —
    ;; warm_rules の ended-refusing)、起こし直しと上限は起動の断りと同じ道(上の枝)。task は shim の道へ倒さず、backoff の後にこの root の
    ;; 次の待ちの子から分ける。
    (and (is-not refusal None) (>= refusal.at-ms view.started-ms))
      #((StopWarmChild key StopStage.TERM refusal.detail :refused True))
    ;; 印が分かれる前の形でない(条 WC3)待ちの子は、準備済みに数えずに止める(終わった後に起こし直す)。
    (and (is-not view.mark None) (not (warm-mark-clean view.mark)))
      #((StopWarmChild key StopStage.TERM (! (mark-refusal view.mark))))
    True #()))

(defk launch-of [key wanted desired world warm services]
  {:pre [(: key str) (: wanted frozenset) (: desired tuple) (: world WorldView) (: warm tuple) (: services frozenset)]
   :post [(: % (| WarmLaunch None))] :tags {:context "worker" :role "judgment"}}
  "root のキーの待ちの子の起こし方を求めるため(要らない root・READY でない root は None — 判断の 1 歩はそれを「要らない」と読む)。
   services = service を分ける root のキー(その root の service の module も前もって読む)。"
  (val root (if (in key wanted) (ready-path (code-of world key)) None))
  (if (is root None) None (! (warm-launch key root desired warm :services services))))

(defk fork-refusals [records]
  {:pre [(: records dict)] :post [(: % (get dict #(str JobNotStarted)))] :tags {:context "worker" :role "judgment"}}
  "root のキーごとに、task を分けるのを断られた最後の答えを job の記憶から集めるため(card ki-e1612a12d0c3 — 待ちの子の断りに数える
   warm-child-step が読む)。起こせた StartJob は記憶の not-started を消すので、残るのは起こせていない task の断りだけ。"
  (var found {})
  (for [record (.values records)]
    (val refusal record.not-started)
    (when (and (is-not refusal None) (is-not refusal.warm-key None)
               (or (not-in refusal.warm-key found) (> refusal.at-ms (. (get found refusal.warm-key) at-ms))))
      (:= found (| found {refusal.warm-key refusal}))))
  found)

(defk warm-child-actions [now desired world warm policy [records {}]]
  {:pre [(: now int) (: desired tuple) (: world WorldView) (: warm tuple) (: policy WorkerPolicy) (: records dict)] :post [(: % tuple)]
   :tags {:context "worker" :role "judgment"}}
  "待ちの子(#3646)を起こす・止める・観測から外す action を求めるため: 要る root = 宣言に分かれる task が在るか温める表に在る root と、
   service を分ける root(service-warm-keys — card ki-de44efdb9aa4)のうち READY の物。要る root と観測に在る待ちの子を root のキーの順に
   1 つずつ判じる(warm-child-step)。records = job の記憶(task を分けるのを断った待ちの子を止める材料 — fork-refusals・card ki-e1612a12d0c3)。"
  (val services (service-warm-keys desired policy))
  (val wanted (frozenset (+ (tuple (gfor spec desired :if (forks-from-warm-child spec) (warm-key-of spec)))
                            (tuple (gfor w warm w.key))
                            (tuple services))))
  (val keys (sorted (| wanted (frozenset (gfor view world.warm-children view.key)))))
  (<- refusals dict (fork-refusals records))
  (var actions #())
  (for [key keys]
    (<- launch (| WarmLaunch None) (launch-of key wanted desired world warm services))
    (<- step tuple (warm-child-step now key launch (warm-child-of world key) policy :refusal (.get refusals key)))
    (:= actions (+ actions step)))
  actions)

(defk pinned-env-keys [desired world warm]
  {:pre [(: desired tuple) (: world WorldView) (: warm tuple)] :post [(: % frozenset)] :tags {:context "worker" :role "judgment"}}
  "掃除が消してはいけない root のキー(2026-09-26)を決めるため: 宣言の実行環境の job・走っている実行環境の process・温める表・準備中の
   root。project ごとの新しい 2 つの root(今の版と戻し先の版)は掃除の係が置き場の材料から守る(env_upkeep.recent-per-project・#3732)。"
  (frozenset (+ (lfor spec desired :if spec.runtime-env (code-key spec))
                (lfor p world.processes :if p.spec.runtime-env (code-key p.spec))
                (lfor w warm w.key)
                (lfor c world.codes :if (and (.startswith c.revision ENV-KEY-PREFIX) (= c.state CodeState.PREPARING)) c.revision))))

(defk declared-jobs [declaration]
  {:pre [(: declaration (| NotYetRead DeclarationRead))] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "最後に読めた宣言の job の列を返すため(まだ一度も読めていなければ空 — 起こす job が無い)。"
  (match declaration
    (NotYetRead) #()
    (DeclarationRead :jobs jobs) jobs))

(defk declared-warm [declaration]
  {:pre [(: declaration (| NotYetRead DeclarationRead))] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "最後に読めた宣言の温める env の列を返すため(まだ一度も読めていなければ空)。"
  (match declaration
    (NotYetRead) #()
    (DeclarationRead :warm warm) warm))

(defk sweep-actions [declaration world]
  {:pre [(: declaration (| NotYetRead DeclarationRead)) (: world WorldView)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "掃除の係へ固定の集合を渡す action を求めるため(固定の集合が変わった時と、掃除の係が拍を求めている時 — EnvDisk.sweep-wanted — だけ)。実行環境を扱わない worker は
   撃たない。declaration = 最後に読めた宣言(拍の Program が worker の停止で空にした列ではない — 止まる worker も次に起きた時の宣言の
   root を消さない)。宣言をまだ一度も読めていない間(起き直した直後・DesiredUnreadable が続く間 — #3731)は撃たない: 固定の集合が
   宣言の root を含まず、止まった job の root を消して起こし直しが root の作り直しになる。一度読めた後の途絶は最後に読めた宣言で判じる。"
  (val disk world.env-disk)
  (match #(declaration disk)
    #((NotYetRead) _) #()
    #(_ None) #()
    #((DeclarationRead :jobs jobs :warm warm) _)
      (do (<- pinned frozenset (pinned-env-keys jobs world warm))
          (if (or (!= pinned disk.pinned) disk.sweep-wanted) #((SweepEnvs pinned)) #()))))

(defk forget-probe-actions [desired world]
  {:pre [(: desired tuple) (: world WorldView)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "入口の検めの持ち主へ今の宣言の spec の指紋を渡す action を求めるため(宣言に無い spec の検めの記録が観測に在る拍だけ — 2026-09-27)。
   宣言に残る spec の記録(失敗の理由・回数)は落とさない。"
  (val keep (frozenset (gfor spec desired (spec-hash spec))))
  (if (any (gfor probe world.probes (not-in probe.spec-hash keep))) #((ForgetProbes keep)) #()))

(defk wanted-notice [want]
  {:pre [(: want (| JobSpec None))] :post [(: % (| Retired HandoffAbandoned))] :tags {:context "worker" :role "judgment"}}
  "退いた process に今知らせておく退きの知らせを、元の job の宣言 want から決めるため(#3672): 入れ替えが諦められていれば
   HandoffAbandoned(旧は止められずに動き続ける — plan-job の諦めの枝と retired-actions)、それ以外(新の Ready を待つ・元の job が宣言から
   消えた・handoff でなくなった — どれも旧は止められる)は Retired。"
  (if (and (is-not want None) want.handoff want.handoff-abandoned) (HandoffAbandoned) (Retired)))

(defk notice-actions [desired world]
  {:pre [(: desired tuple) (: world WorldView)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "退いた process へ退きの知らせの変わり目を送る action を求めるため(#3672): 動いている退いた process のうち、既に知らせを受けていて
   (最初の「退く」は名から外す RetireJob が送る)、今の知らせ(wanted-notice)が観測の notice(最後に送った知らせ)と違う物へ NoticeJob。
   同じ知らせを 2 度送らない(送った事は次の観測の notice に出る)。"
  (var actions #())
  (for [p world.processes]
    (when (and (is-not p.retired-from None) (is p.exit-code None) (is-not p.notice None))
      (<- wanted (| Retired HandoffAbandoned) (wanted-notice (desired-of desired p.retired-from)))
      (when (!= wanted p.notice)
        (:= actions (+ actions #((NoticeJob p.name p.pid wanted)))))))
  actions)

(defk plan [now desired world records policy [warm #()] [absent (Undeclared)]]
  {:pre [(: now int) (: desired (get tuple #(JobSpec ...))) (: world WorldView) (: records (get dict #(str JobRecord))) (: policy WorkerPolicy)
         (: warm (get tuple #(WarmLaunch ...))) (: absent StopReason)]
   :post [(: % (get tuple #(Action ...)))] :tags {:context "worker" :role "judgment"}}
  "worker の 1 拍で撃つ action を決めるため: job ごとの action → 温める表の準備(job より後)→ 待ちの子の起こしと止め(#3646)→ 検めの
   記録の片づけ。掃除の係への固定の集合は最後に読めた宣言で判じる sweep-actions(#3731 — この拍の列は宣言を読めていない拍と読んだ空の
   宣言を分けない)。job ごとの判断 plan-job は名ごとに sequence で順に走らせる(最初の失敗で止まる — 黙って落とさない)。
   absent = 宣言に無い job を止める訳(既定 = 宣言から外れた・拍の Program が途絶の絞りと worker の停止を渡す — #3713)。"
  (<- names tuple (job-names desired world))
  (<- per-job tuple (sequence (gfor name names (plan-job now name desired world (.get records name (JobRecord name)) policy absent))))
  (val jobs (tuple (gfor actions per-job action actions action)))
  (<- warming tuple (warm-actions now warm world jobs policy))
  (<- children tuple (warm-child-actions now desired world warm policy :records records))
  (<- forgetting tuple (forget-probe-actions desired world))
  ;; 退いた process への退きの知らせの変わり目(入れ替えの諦めと、その解け — #3672)。
  (<- noticing tuple (notice-actions desired world))
  (+ jobs warming children forgetting noticing))

(defk ready-followups [now desired before after records policy [warm #()] [absent (Undeclared)]]
  {:pre [(: now int) (: desired tuple) (: before WorldView) (: after WorldView) (: records dict) (: policy WorkerPolicy) (: warm tuple)
         (: absent StopReason)]
   :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "拍の action の後の観測(after)で揃った物を、次の拍を待たずに同じ判断で進める action を求めるため(#2719)。対象は 2 つ:
   拍の頭の観測(before)で木が READY でなく after で READY になった・または待ちの子が準備済みでなく after で準備済みになった宣言の
   job(plan-job)と、after で READY になった root の待ちの子の起こし(warm-child-actions のうち StartWarmChild — #3646)。準備がその拍の
   うちに揃う宿(模擬の prepare-seconds = 0・起こした刻に準備済みの模擬の待ちの子・cache に完成品の在る版)で、最初の task の起動が拍
   1 つ遅れる形をやめる。揃っていなければ空(今までどおり後の拍で揃いを観測してから起こす)。records = 拍の action を数えた後の記憶・
   absent = plan と同じ止める訳。"
  (val warm-keys (service-warm-keys desired policy))
  (<- per-job tuple (sequence (gfor spec desired
                                    :if (and (is-not (ready-path (code-of after (code-key spec))) None)
                                             (or (is (ready-path (code-of before (code-key spec))) None)
                                                 (and (fork-candidate spec policy warm-keys)
                                                      (not (warm-child-ready (warm-child-of before (warm-key-of spec))))
                                                      (warm-child-ready (warm-child-of after (warm-key-of spec))))))
                                    (plan-job now spec.name desired after (.get records spec.name (JobRecord spec.name)) policy absent))))
  (val jobs (tuple (gfor actions per-job action actions action)))
  (<- children tuple (warm-child-actions now desired after warm policy :records records))
  (+ jobs (tuple (gfor action children
                       :if (and (isinstance action StartWarmChild) (is (ready-path (code-of before action.key)) None))
                       action))))

(defn #^ JobRecord record-after [#^ int now #^ JobRecord record #^ Action action #^ WorkerPolicy [policy (WorkerPolicy)]
                                 #^ (| JobNotStarted None) [not-started None]]
  "撃った action 1 つを job の記憶に数える(not-started = その action が StartJob で、起こせなかった答え — card ki-e1612a12d0c3)。"
  (cond
    ;; 起こせなかった StartJob: 落ちた時と同じく続けて落ちた回数(failures・unexpected-exits)に数え、backoff を断りの刻から数える
    ;; (backoff-from)。起こした刻は書かない(走った process が無い)・task を終えた事にしない(last-outcome を書かない — backoff の後に
    ;; 起こし直す)。
    (and (isinstance action StartJob) (is-not not-started None))
      (replace record :attempts action.attempt :stopping None :not-started not-started
               :failures (+ record.failures 1) :unexpected-exits (+ record.unexpected-exits 1))
    (isinstance action StartJob) (replace record :attempts action.attempt :stopping None :last-start-ms now :not-started None)
    (isinstance action SignalJob)
      (replace record :stopping
        (JobStop :requested-ms (if (is record.stopping None) now record.stopping.requested-ms) :stage action.stage :signalled-ms now
                 :reason action.reason))
    (isinstance action ReapJob)
      ;; 数え方は 3 つ: unexpected-exits = 停止を求めずに終わった回数(起こし直しの間を伸ばす・exit code を問わない)、
      ;; failures = そのうち exit code が 0 でなかった回数と見張りに止められた回数(失敗として表示する — 見張りが job の木の単位を止めた後に
      ;; 根が exit code 0 で終わっても、止められた終わりは失敗)、memory-kills = 機体の memory の見張りに続けて止められた
      ;; 回数(同じ spec の間だけ — memory-kill-limit に達したら起こし直さない)。どれも長く動いた後の終わりは 1 回目に数え直す。見張りに
      ;; 止められた終わりの「長く」は memory-kill-reset-ms(起動から約 1 分で止められる job を stable-run-ms で数え直さない)、それ以外は
      ;; stable-run-ms。見張りに止められずに終わった回は memory-kills を進めず、memory-kill-reset-ms 以上動いていた時だけ 0 に戻す。
      (do
        (setv exited (= action.outcome Outcome.EXITED)
              ran-ms (if (is record.last-start-ms None) None (- now record.last-start-ms))
              killed (is-not action.memory-killed-spec None)
              worked (and (is-not ran-ms None) (>= ran-ms policy.memory-kill-reset-ms))
              stable (if killed worked (run (ran-stably? now record policy))))
        (replace record :last-exit-ms now :last-outcome action.outcome :last-exit-code action.exit-code
                 :stopping None
                 :unexpected-exits (cond (not exited) 0 stable 1 True (+ record.unexpected-exits 1))
                 :failures (cond (or (not exited) (and (= action.exit-code 0) (not killed))) 0 stable 1 True (+ record.failures 1))
                 :memory-kills (cond (not killed) (if worked 0 record.memory-kills)
                                     (or worked (!= action.memory-killed-spec record.memory-killed-spec)) 1
                                     True (+ record.memory-kills 1))
                 :memory-killed-spec (if killed action.memory-killed-spec record.memory-killed-spec)))
    ;; 名から外す(入れ替えで新を同じ名で起こす・新しい世代へ退かせる): 外す process が stable-run-ms 以上動いていれば、続けて落ちた回数と
    ;; 続けて終わった回数を 0 から数え直す — 報告の境(job-status)と同じ。退いた process の終わりは退いた名の記憶で回収して捨てるので、
    ;; 二重には数えない。stable-run-ms に満たずに落ち続けている間の入れ替えは回数を引き継ぐ(落ち続けを隠さない)。
    ;; 以前は外しが記憶に触れず、1 時間 48 分 安定して動いた process を入れ替えた後の新しい process が、起動から 60 秒の間 前の落ち続けの
    ;; 回数 11 を報告した(card ki-3f4bbc55733a — 上に載る系の見張りが落ち続けと数えた)。
    (isinstance action RetireJob)
      (if (run (ran-stably? now record policy)) (replace record :failures 0 :unexpected-exits 0) record)
    True record))

(defk records-after [now records actions [policy (WorkerPolicy)] [not-started #()]]
  {:pre [(: now int) (: records (get dict #(str JobRecord))) (: actions (get tuple #(Action ...))) (: policy WorkerPolicy)
         (: not-started (get tuple #(JobNotStarted ...)))]
   :post [(: % (get dict #(str JobRecord)))] :tags {:context "worker" :role "judgment"}}
  "撃った action を job ごとの記憶(起こした回数・止め始め・終わり方)に数えた後の記憶を求めるため。not-started = 撃った StartJob のうち
   起こせなかった答え(card ki-e1612a12d0c3 — job の名で StartJob に結ぶ)。1 つずつの数えは record-after
   (効果を出さない純粋な畳み: 同じ job の action が続けば前の数えの答えを次が読むので、互いに独立な Program の列を順に走らせる
   sequence の形ではなく、素の関数のまま — #2812)。"
  (var result (dict records))
  (for [action actions]
    (val name (match action
      (StartJob) action.spec.name
      (| (SignalJob) (ReapJob) (RetireJob)) action.name
      _ None))
    (val refused (if (isinstance action StartJob) (next (gfor answer not-started :if (= answer.name name) answer) None) None))
    (when (is-not name None)
      (:= result (| result {name (record-after now (.get result name (JobRecord name)) action policy refused)})))
    ;; 退いた process の記憶は、回収した時に捨てる(名は世代ごとに違うので、残すと入れ替えのたびに溜まる)。
    (when (and (isinstance action ReapJob) (in RETIRED-MARK action.name))
      (:= result (dfor #(k v) (.items result) :if (!= k action.name) k v))))
  result)

;; 止めを求めていない終わりの行(card ki-cb9f34c49f83): 回収した拍に 1 行。以前は止めた終わりの計時の行(process_host の STOP-TIMING-LOG)
;; しか無く、job が SIGSEGV で落ちても worker の log に落ち方が残らなかった(終了 code は状態の file の lastExitCode を読むまで分からない)。
(defrecord ExitNote
  "止めを求めていない job の終わり 1 つ(回収の拍の行の欄): name = job の名(退いた process は退いた名)・pid = 子の pid・exit-code = 終了 code・
   signal = 終わらせた signal の名(exit-signal — signal で終わっていなければ None)・ran-ms = 起こしてから回収までの ms・backoff-ms = 起こし
   直すまでの間(起こし直さない終わり — task・退いた process・memory の見張りで起こし直しを止めた job — は None)・memory-killed = 機体の
   memory の見張りがこの process の木を止めたか。"
  (#^ str name)
  (#^ int pid)
  (#^ int exit-code)
  (#^ (| str None) signal)
  (#^ int ran-ms)
  (#^ (| int None) backoff-ms)
  (#^ bool memory-killed))

(defn #^ (| str None) exit-signal [#^ int exit-code]  ; defk にできない: 回収の行の欄を作る素の関数(exit-notes の gfor の中)
  "終了 code を、終わらせた signal の名にする(signal で終わっていなければ None)。負の code = 子が直に signal で終わった(subprocess の
   returncode・待ちの子の exit の file — -1 は待ちの子の終わりの印が無い時の値 WarmLost と重なるので読まない)。128 + 番号 = 入れ物 shim が
   job の signal の終わりを写した値(shim.exit-status — 139 = SIGSEGV)。名の無い番号は SIG<番号>。"
  (setv number (cond (< exit-code -1) (- exit-code)
                     (< 128 exit-code (+ 128 signal.NSIG)) (- exit-code 128)
                     True None))
  (cond (is number None) None
        (in number (sfor s signal.Signals s.value)) (. (signal.Signals number) name)
        True (.format "SIG{}" number)))

(defk exit-notes [now world records actions policy]
  {:pre [(: now int) (: world WorldView) (: records (get dict #(str JobRecord))) (: actions tuple) (: policy WorkerPolicy)]
   :post [(: % (get tuple #(ExitNote ...)))] :tags {:context "worker" :role "judgment"}}
  "撃った action のうち止めを求めていない終わりの回収(ReapJob の Outcome.EXITED)を、回収の拍の行の欄にするため。world = 回収を決めた観測
   (終わった子の起こした刻と spec)・records = その action を数えた後の記憶(起こし直すまでの間は数えた後の続けて終わった回数で決まる)。"
  (tuple
    (gfor action actions
          :if (and (isinstance action ReapJob) (= action.outcome Outcome.EXITED))
          :setv process (next (gfor p world.processes :if (and (= p.name action.name) (= p.pid action.pid)) p) None)
          :if (is-not process None)
          :setv record (.get records action.name (JobRecord action.name))
          :setv restarts (not (or process.spec.once (is-not process.retired-from None) (in RETIRED-MARK action.name)
                                  (memory-given-up process.spec record policy)))
          (ExitNote :name action.name :pid action.pid :exit-code action.exit-code :signal (exit-signal action.exit-code)
                    :ran-ms (- now process.started-ms) :backoff-ms (if restarts (backoff-ms record policy) None)
                    :memory-killed (is-not action.memory-killed-spec None)))))

(defk start-holds [now desired world records policy]
  {:pre [(: now int) (: desired tuple) (: world WorldView) (: records dict) (: policy WorkerPolicy)] :post [(: % tuple)]
   :tags {:context "worker" :role "judgment"}}
  "周期の終わりの観測(world)と記憶で、宣言の job ごとの新しい版の起動の見送りの訳(JobHold)を求めるため(#3713 — 起動を見送った行)。
   動いている process の無い job は、諦めた入れ替えなら HANDOFF-ABANDONED、それ以外は plan-job の起動の枝と同じ判断 start-step が訳を
   決める。recreate の job の旧い版を動かしたまま新しい版を準備・検めしている間(2026-10-08)は、plan-job の止めの枝と同じ判断
   replace-step が訳を決める。それ以外の動いている process の在る job と終わった task は見送っていない(None)。"
  (var holds #())
  (val warm-keys (service-warm-keys desired policy))
  (for [spec desired]
    (val record (.get records spec.name (JobRecord spec.name)))
    (val process (process-of world spec.name))
    (<- replacing bool (recreating? spec process record))
    (val hold (cond
                (and (is process None) spec.handoff spec.handoff-abandoned) StartHold.HANDOFF-ABANDONED
                (is process None) (. (! (start-step now spec world record policy warm-keys)) hold)
                replacing (. (! (replace-step now spec process world record policy warm-keys)) hold)
                True None))
    (:= holds (+ holds #((JobHold :name spec.name :hold hold)))))
  holds)

(defk noted-holds [records holds]
  {:pre [(: records dict) (: holds tuple)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "起こしの見送りのうち、行に出す物(JobHold)を選ぶため: 見送っていて、記憶の訳(前の拍までに名乗った訳)と違う job だけ — 同じ訳が
   続く間は出さず、訳が替われば出す(#3713)。"
  (tuple (gfor h holds
               :if (and (is-not h.hold None) (!= h.hold (. (.get records h.name (JobRecord h.name)) held)))
               h)))

(defk held-records [records holds]
  {:pre [(: records dict) (: holds tuple)] :post [(: % dict)] :tags {:context "worker" :role "judgment"}}
  "拍の終わりの見送りの訳を job の記憶へ書いた後の記憶を求めるため(次の拍の noted-holds が比べる元)。訳の変わった job の記憶だけ置き換える。"
  (| records (dfor h holds
                   :setv record (.get records h.name (JobRecord h.name))
                   :if (!= record.held h.hold)
                   h.name (replace record :held h.hold))))

(defn #^ JobPhase phase-of [#^ int now #^ (| JobSpec None) want #^ (| ProcessView None) process
                            #^ WorldView world #^ JobRecord record #^ WorkerPolicy policy #^ frozenset warm-keys]
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
    (memory-given-up want record policy) JobPhase.MEMORY-KILLED
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
          ;; 待ちの子が起動の断りの上限に達した task は、準備の失敗として終える。
          (and (forks-from-warm-child want) (warm-child-refused (warm-child-of world (warm-key-of want)) policy)) JobPhase.ENV-FAILED
          ;; 待ちの子から分かれる task と分ける root の service は、待ちの子が準備済みになるまで準備の段階のまま(段階の語は足さず、理由は
          ;; 状態の行の文 — #3646・card ki-de44efdb9aa4)。
          (= (warm-start want world policy warm-keys) WarmStart.WAIT) JobPhase.PREPARING
          ;; 前の世代が動かしている印の job(AL-D2f): 入口の検めまで揃った時だけ elder-runs(coordinator はこれで前の世代から外す)。
          (elder-ready now want code world policy) JobPhase.ELDER-RUNS
          True JobPhase.STARTING))))

(defn #^ (| WarmChildView None) refused-warm-child [#^ WorldView world #^ (| JobSpec None) want #^ (| ProcessView None) process
                                                   #^ JobRecord record #^ WorkerPolicy policy]
  "起動の断りの上限に達した待ちの子に結ばれた、まだ起きていない task の、その待ちの子の観測(状態の行の理由と準備の失敗の材料)。
   当たらなければ None。"
  (if (or (is want None) (is-not process None) (not (forks-from-warm-child want)) (is-not record.last-outcome None))
      None
      (do
        (setv view (warm-child-of world (warm-key-of want)))
        (if (warm-child-refused view policy) view None))))

(defn #^ (| str None) warm-wait-detail [#^ WorldView world #^ (| JobSpec None) want #^ (| ProcessView None) process
                                        #^ JobRecord record #^ WorkerPolicy policy #^ frozenset warm-keys]
  "待ちの子の準備を待っている task と service(card ki-de44efdb9aa4)の状態の行の理由(#3646)。待っていなければ None。前の待ちの子が
   終わっていれば、その訳も添える。"
  (if (or (is want None) (is-not process None) (is-not record.last-outcome None)
          (is (ready-path (code-of world (code-key want))) None)
          (!= (warm-start want world policy warm-keys) WarmStart.WAIT))
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

(defk job-status [now desired world records policy name]
  {:pre [(: now int) (: desired tuple) (: world WorldView) (: records dict) (: policy WorkerPolicy) (: name str)] :post [(: % JobStatus)]
   :tags {:context "worker" :role "judgment"}}
  "job 1 つの状態の報告 JobStatus を作るため(statuses が名ごとに sequence で呼ぶ)。判断(phase-of・probe-status ほか)は同じ拍の観測から導く。"
  (val want (desired-of desired name))
  (val process (process-of world name))
  (val record (.get records name (JobRecord name)))
  (val code (if (is want None) None (code-of world (code-key want))))
  (val probe (if (is want None) None (probe-failure world want)))
  (val probing (if (is want None) None (probe-status now world want)))
  (val handing-off (and (is-not process None) (is-not want None) want.handoff (!= process.spec want) (is record.stopping None)))
  (val abandoned (and (is-not want None) want.handoff want.handoff-abandoned))
  ;; 版を据え置いている(#3684 — drain 中で、新しい版を受けずに旧い版を動かしている — plan-job の据え置きと同じ形)。
  (val held (and (is-not process None) (is-not want None) want.hold-version (!= process.spec want) (is record.stopping None)))
  ;; recreate の job の旧い版を動かしたまま、新しい版を準備・検めしている(2026-10-08 — plan-job の止めの枝と同じ述語)。
  (<- replacing bool (recreating? want process record))
  (val warm-keys (service-warm-keys desired policy))
  (val warm-wait (warm-wait-detail world want process record policy warm-keys))
  (val refused (refused-warm-child world want process record policy))
  ;; 機体の memory の見張りに続けて止められ、起こし直しを止めた常駐の job(動いている process が無い時だけ — phase-of と同じ述語)。
  (val given-up (and (is process None) (is-not want None) (memory-given-up want record policy)))
  ;; 今の process が stable-run-ms 以上動いていれば、続けて落ちた回数は 0 と報告する(次に終わった時に 1 から数え直す record-after と
  ;; 同じ境 ran-stably? — 拍ごとに組む報告から導くので、動いている間は記憶を書き換える仕掛けも時刻の見張りも要らない・#3477)。記憶を
  ;; 0 に戻すのは、その process が終わった時(ReapJob)と名から外した時(RetireJob — card ki-3f4bbc55733a)の record-after。
  (<- ran-stably bool (ran-stably? now record policy))
  (val stable (and (is-not process None) ran-stably))
  (JobStatus name (phase-of now want process world record policy warm-keys)
      (if (is want None) None want.revision)
      ;; 動いている版: 宣言と同じ spec(中身の鍵が同じ — card ki-81bf84ac6590)の process は、版の違う宣言し直しでも起こし直さないので、
      ;; 宣言の版を仕事している版と数える(読む code が宣言の版と同じ)。鍵の無い宣言は spec が同じなら版も同じ(今どおり)。
      (cond (is process None) None
            (and (is-not want None) (= want process.spec)) want.revision
            True process.spec.revision)
      (if (is process None) None process.pid)
      record.attempts
      (cond
        ;; 入れ替えの諦め(coordinator の期限)。Service の status.handoff に期限と理由が出る。
        abandoned "入れ替えを諦めた(新の process は止めて起こし直さない・旧は動かしたまま — 宣言が変わるまで)"
        ;; drain 中で新しい版を受けていない(#3684)。新しい版の準備・検めの姿はこの worker では進めないので、ここで止める。
        held "drain 中 — 新しい版は drain の後"
        ;; 機体の memory の見張りに続けて止められた(起こし直さない — 宣言が変わるまで)。
        given-up (.format "機体の memory の見張りに {} 回続けて止められた — 起こし直さない(宣言の spec が変わるまで)" record.memory-kills)
        ;; recreate の job の新しい版の準備の失敗・入口を読み込めない・検めの間・準備の間は、旧い版が動いている事を頭に示す(2026-10-08)。
        (and replacing (is-not code None) (= code.state CodeState.FAILED))
          (.format "旧い版を動かしたまま — 新しい版を準備できない: {}" code.detail)
        (and replacing (is-not probe None)) (.format "旧い版を動かしたまま — 新の入口を読み込めない: {}" probe.detail)
        (and replacing (is-not probing None)) (.format "旧い版を動かしたまま — {}" (probing-detail probing))
        ;; 検めの後、新しい版の待ちの子の準備を待っている間(replace-step の WARM-CHILD — card ki-d8cc8191472e)。
        (and replacing (is-not (ready-path code) None) (= (warm-start want world policy warm-keys) WarmStart.WAIT))
          "旧い版を動かしたまま — 新しい版の待ちの子の準備中"
        replacing (.format "旧い版を動かしたまま — 新しい版の準備中(新のコード {})" (if (is code None) "未準備" code.state.value))
        (and (is-not code None) (= code.state CodeState.FAILED)) code.detail
        ;; 新の入口を読み込めない(入口の検めの理由)。入れ替えの途中なら旧が動いていることも示す。
        (and (is-not probe None) handing-off) (.format "入れ替えを待つ(旧は動かしたまま)— 新の入口を読み込めない: {}" probe.detail)
        (is-not probe None) (.format "新の入口を読み込めない: {}" probe.detail)
        ;; 入口の検めの間(撃ち直しの間も直前の失敗の理由を出す — 2026-09-27)。入れ替えの途中なら旧が動いていることも示す。
        (and (is-not probing None) handing-off) (.format "入れ替えを待つ(旧は動かしたまま)— {}" (probing-detail probing))
        (is-not probing None) (probing-detail probing)
        ;; 待ちの子が起動の断りの上限に達した task。
        (is-not refused None) (.format "待ちの子が {} 回続けて断った: {}" (+ refused.refusals 1) refused.detail)
        ;; 最後の StartJob が起こせなかった(card ki-e1612a12d0c3 — backoff の後に起こし直す)。
        (and (is process None) (is-not record.not-started None))
          (.format "起こせなかった({}): {} — backoff={}ms"
                   (if (is record.not-started.warm-key None) "shim" (+ "warm:" record.not-started.warm-key))
                   record.not-started.detail (backoff-ms record policy))
        ;; 待ちの子の準備を待っている task(#3646)。
        (is-not warm-wait None) warm-wait
        ;; 退いた process の空き待ち(AL-D1): 新のコードも入口の検めも揃ったが、退いた process が上限 R に達しているので今の process を
        ;; 退かせずに待っている(handoff-actions)。coordinator はこの文を Service の readyReason と版の判定の訳(Updating)に添え、
        ;; 宣言の道具の待ちの印字(最後の読み)もそれを出す — 「新が起きない」を準備の遅れと取り違えないため。
        (and handing-off (is-not code None) (= code.state CodeState.READY) (retired-room-full want name world policy))
          (.format "入れ替えを待つ — 退いた process の空き待ち(退いた {} 個が上限 {}・どれかが走っている仕事を終えて自分で終わるまで今の process を退かせない・旧は動かしたまま)"
                   (len (retired-of world name)) (retired-limit-for want policy))
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
      ;; 待ちの子が起動の断りの上限に達した task は、その断りを準備の失敗として運ぶ(coordinator がやり直さずに終える)。
      :failure (cond (and (is process None) (is-not code None) (= code.state CodeState.FAILED)) code.failure
                     (is-not refused None) (warm-refusal-failure refused)
                     True None)
      ;; 宣言の spec の入口の検めの姿(走っている・待っている・失敗した間 — 入れ替えで旧が動いている行も)。
      :probe probing))

(defk statuses [now desired world records policy]
  {:pre [(: now int) (: desired (get tuple #(JobSpec ...))) (: world WorldView) (: records (get dict #(str JobRecord))) (: policy WorkerPolicy)]
   :post [(: % (get tuple #(JobStatus ...)))] :tags {:context "worker" :role "judgment"}}
  "worker の状態の報告(job ごとの JobStatus の列)を作るため。job ごとの報告 job-status を名ごとに sequence で順に走らせる。"
  (<- names tuple (job-names desired world))
  (<- reported tuple (sequence (gfor name names (job-status now desired world records policy name))))
  reported)
