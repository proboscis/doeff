;;; worker が coordinator へ送る heartbeat の本文の形 — 実行環境の root の名乗り・生存と能力と版・状態の行と結果(本番の coordinator への口 と
;;; 手元の sim-cluster の宿 sim/local が同じ関数で作る — 本文を写さない)。handlers.hy から分けた(#2026)。判断は worker/core/heartbeat_rules。
(require doeff-hy.macros [defk deff <- val var])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import doeff_cluster._shared.intent.protocol [PROTOCOL-FORMAT])
(import doeff_cluster._worker.intent.worker_model [CodeState CodeView JobStatus])
(import doeff_cluster._worker.core.worker_rules [ENV-KEY-PREFIX])
(import doeff_cluster._worker.core.heartbeat_rules [finished-task-id])
(import doeff_cluster._worker.core.room_rules [announced-provides])
(import doeff_cluster._worker.core.load_rules [MachineLoad])
(import doeff_cluster._worker.core.memory_rules [MIB])


(deff env-report [#^ (get tuple #(CodeView ...)) views #^ str capacity #^ (get frozenset str) unmeasured #^ (get frozenset str) [ready-deps (frozenset)]]  ; defk にできない: worker の root の言い換え(env-host)と sim の宿が同じ形を作る純粋な判断
  {:pre [(: views (get tuple #(CodeView ...))) (: capacity str) (: unmeasured (get frozenset str)) (: ready-deps (get frozenset str))] :post [(: % (get dict #(str object)))] :tags {:context "worker" :role "protocol"}}
  "実行環境の root の観測(CodeView — 鍵が env- で始まる物だけを読む)と disk の条件を、heartbeat で名乗る root の姿(準備済み・準備中・
   失敗のキーを env- を外して・disk の条件)にするため。unmeasured = 先の組みを memory を測らずに始めた root のキー(#3748 — 観測に在る root だけを
   memoryUnmeasured に名乗る)・ready-deps = 準備済みの root の依存のキー(readyDeps — coordinator が温める表の行の依存の部分の揃いを照らす)。"
  (let [roots (lfor v views :if (.startswith v.revision ENV-KEY-PREFIX) v)
        bare (fn [k] (cut k (len ENV-KEY-PREFIX) None))]
    {"ready" (sorted (gfor v roots :if (= v.state CodeState.READY) (bare v.revision)))
     "preparing" (sorted (gfor v roots :if (= v.state CodeState.PREPARING) (bare v.revision)))
     ;; 先の組みの断り(REFUSED)も種類つきで名乗る(温める表の WarmFailure — 回の Program が AwaitWarm で読む)。
     "failed" (lfor v roots :if (and (in v.state #(CodeState.FAILED CodeState.REFUSED)) (is-not v.failure None))
                    {"key" (bare v.revision) "kind" v.failure.kind.value "detail" v.failure.detail
                     "retryable" v.failure.retryable})
     "memoryUnmeasured" (sorted (gfor v roots :if (in v.revision unmeasured) (bare v.revision)))
     "readyDeps" (sorted ready-deps)
     "capacity" capacity}))


(deff env-heartbeat-part [#^ (get dict #(str object)) report #^ str platform]  ; defk にできない: worker の coordinator への口(worker/protocol/coordinator_link)と sim の宿が同じ形を作る純粋な判断
  {:pre [(: report (get dict #(str object))) (: platform str)] :post [(: % (get dict #(str object)))] :tags {:context "worker" :role "protocol"}}
  "root の姿(env-report)を heartbeat の本文に足す欄(platform・envs・envCapacity)にするため。"
  {"platform" platform
   "envs" {"ready" (get report "ready") "preparing" (get report "preparing") "failed" (get report "failed")
           "memoryUnmeasured" (get report "memoryUnmeasured") "readyDeps" (get report "readyDeps")}
   "envCapacity" (get report "capacity")})


(defk heartbeat-body [* name provides exclusive node versions statuses endpoint boot boot-at tools kept [stopping False]
                      [overlaps-elder False] [doeff-commit ""] [pools #()] [memory-mib None] [measured None] [child-environ None]
                      [pod-ip ""]]
  {:pre [(: name str) (: provides (get tuple #(str ...))) (: exclusive (get tuple #(str ...))) (: node str) (: pod-ip str)
         (: versions (get dict #(str str))) (: statuses (get list (get dict #(str object)))) (: endpoint str) (: boot str) (: boot-at int)
         (: tools (get dict #(str object))) (: kept (get tuple #(str ...))) (: stopping bool)
         (: overlaps-elder bool) (: doeff-commit str) (: pools (get tuple #(str ...))) (: memory-mib (| int None))
         (: measured (| MachineLoad None)) (: child-environ (| (get tuple #(str ...)) None))]
   :post [(: % (get dict #(str object)))]
   :tags {:context "worker" :role "protocol"}}
  "POST /heartbeat の本文(生存・能力・版・状態の報告・世代・持っている印・止まり始め)を作るため。実行環境の root の名乗り(env-body)は
   本番の worker だけが足す。手書きの task の枠(taskReserve・backgroundRoom・capacity)は送らない — coordinator は task の置き先を measured から数える。
   kept = 途絶しても動かし続けてよい印を今持っている job の名(worker_policy.keep-marks-held — #2804)。欄を毎回
   書く(空でも)— coordinator は欄の在る worker だけを「印を知る worker」と数え、欄の無い本文(古い worker)には印の約束を持たない。
   stopping = この世代が止まり始めた(coordinator はこの世代へ新しく置かない — drain の頼みを通らない止めの名乗り・#2819)。
   overlaps-elder = この世代は同じ名の前の世代がまだ走っている間に起きた(起動の script が渡す — coordinator は新しい世代の名乗りで
   前の世代の process の lease を外さない・AL-D2e)。真の時だけ欄を書く(偽の本文は前と同じ形)。doeff-commit = この process の doeff の
   版(起動の script が自己起動した commit の WORKER_DOEFF_COMMIT — coordinator は Worker の spec.doeffCommit に
   出す)。空でない時だけ欄を書く(版を持たない worker の本文は前と同じ形)。pools = 枠の file の行の置ける組の印(
   provides の後ろに足して名乗る。起動の引数の能力との和で、coordinator は heartbeat ごとに Worker の行の provides を書き直すので、次の
   heartbeat から置き方に効く)。memory-mib = 常駐の job の memory の予算(MiB — 名乗る時だけ欄 memoryMiB を書く。coordinator は置いた job の
   settle の和 + 新しい job の settle がこの値以下の時だけ置き、名乗らない worker は memory で縛らない)。measured = 機体の測った空き
   (worker/core/load_rules の MachineLoad — 測れた時だけ欄 measured を書く。coordinator は保存せず、task の置き先をこの値から数え、
   GET /state の worker の行に出す。名乗らない worker には task を置かない)。child-environ = job の子へ渡す環境変数の名(名の順 —
   worker/core/launch の child-environ-names。名乗る時だけ欄 childEnviron を書く。coordinator は GET /state の worker の行に出す)。
   pod-ip = この世代の Pod の IP(k8s の downward API の status.podIP — 空でない時だけ欄 podIp を書く。coordinator は job の Service の
   振り先〔EndpointSlice〕に、Ready で active な process の在る世代の Pod の IP を書く — coordinator/core/endpoints_policy)。"
  (<- named (get tuple #(str ...)) (announced-provides provides pools))
  (var measured-part {})
  (when (is-not measured None)
    (<- row (get dict #(str object)) (measured-json measured))
    (:= measured-part {"measured" row}))
  {"name" name "provides" (list named) "exclusive" (list exclusive) "node" node
   "versions" versions
   "statuses" statuses "endpoint" endpoint "boot" boot "bootAt" boot-at
   "format" PROTOCOL-FORMAT
   "tools" tools
   "keptWhenCutOff" (list kept)
   "stopping" stopping
   #** (if overlaps-elder {"overlapsElder" True} {})
   #** (if doeff-commit {"doeffCommit" doeff-commit} {})
   #** (if (is memory-mib None) {} {"memoryMiB" memory-mib})
   #** (if (is child-environ None) {} {"childEnviron" (list child-environ)})
   #** (if pod-ip {"podIp" pod-ip} {})
   #** measured-part})


(defk measured-json [load]
  {:pre [(: load MachineLoad)] :post [(: % (get dict #(str object)))] :tags {:context "worker" :role "protocol" :spells "json"}}
  "機体の測った空きを heartbeat の欄 measured の JSON の形にするため(coordinator/intent/request_bodies の MeasuredLoad と同じ形 — 測れない
   欄は null)。"
  {"atMs" load.at-ms "cpus" load.cpus "cpuBusy" load.cpu-busy
   "memoryTotalMiB" load.memory-total-mib "memoryAvailableMiB" load.memory-available-mib
   "cpuSomeAvg10" load.cpu-some-avg10 "cpuFullAvg10" load.cpu-full-avg10
   "memorySomeAvg10" load.memory-some-avg10 "memoryFullAvg10" load.memory-full-avg10
   ;; nice の分と task の層の CPU の PSI(名乗る時だけ — 2026-11-05。無い欄は前の版の worker と同じに読まれる)。
   #** (if (is load.cpu-nice None) {} {"cpuNice" load.cpu-nice})
   #** (if (is load.task-cpu-some-avg10 None) {} {"taskCpuSomeAvg10" load.task-cpu-some-avg10})
   ;; task の層の今の空き(名乗る時だけ — 2026-11-09)。
   #** (if (is load.jobs-room-mib None) {} {"jobsRoomMiB" load.jobs-room-mib})
   ;; 会話のターンに残す線を外した急ぎの task の空き(線が空きを縮めた時だけ — 2026-11-10・worker/core/load_rules の頭の註)。
   #** (if (is load.urgent-available-mib None) {} {"urgentMemoryAvailableMiB" load.urgent-available-mib})
   #** (if (is load.urgent-jobs-room-mib None) {} {"urgentJobsRoomMiB" load.urgent-jobs-room-mib})
   ;; keeper が版の準備を待つ間の取り置き(待っている時だけ — 2026-11-10)。
   #** (if (is load.prepare-wait-mib None) {} {"prepareWaitMiB" load.prepare-wait-mib})
   ;; task ごとの memory の今の使い(名乗る時だけ — 2026-11-02)。
   #** (if (is load.task-uses None) {} {"taskMemoryMiB" (dfor u load.task-uses u.id u.mib)})
   ;; 常駐の job ごとの CPU の累計と memory の今(名乗る時だけ — 2026-11-10。jobUsesAtMs = 見張りの読みの刻・pssMiB・pssAtMs・
   ;; bodyAnonymousMiB は PSS を読めた job だけ・startedAtMs は根を起こした刻を知る job だけ)。
   #** (if (is load.job-meters None) {}
           {"jobUsesAtMs" load.job-meters-at-ms
            "jobUses" (lfor m load.job-meters
                            (| {"job" m.job "pid" m.pid "cpuMs" m.cpu-ms "rssMiB" (// m.rss-bytes MIB) "processes" m.processes}
                               (if (is m.pss-bytes None) {} {"pssMiB" (// m.pss-bytes MIB) "pssAtMs" m.pss-at-ms
                                                             "bodyAnonymousMiB" (// m.body-anonymous-bytes MIB)})
                               (if (is m.started-ms None) {} {"startedAtMs" m.started-ms})))})})


(defk status-report [statuses task-echo results]
  {:pre [(: statuses (get tuple #(JobStatus ...))) (: task-echo (get dict #(str (get dict #(str object))))) (: results (get dict #(str (| str None))))]
   :post [(: % (get list (get dict #(str object))))] :tags {:context "worker" :role "protocol" :spells "json"}}
  "状態の行の列を heartbeat の statuses にするため。終わった task には結果(results の task の id → 詰めた結果の文字列 か None =
   結果なし)を、切り離した task には置かれた時の返事の行(task-echo の id → 行 — 欄 task)を添える。"
  (<- rows (get tuple #((get dict #(str object)) ...)) (status-rows-json statuses))
  (lfor #(s row) (zip statuses rows)
    :setv echo (if (.startswith s.name "task/") (.get task-echo (cut s.name 5 None)) None)
    :setv row (if (is echo None) row (| row {"task" echo}))
    :setv done (finished-task-id s)
    (if (is done None) row (| row {"result" (.get results done)}))))


(defk status-rows-json [statuses]
  {:pre [(: statuses (get tuple #(JobStatus ...)))] :post [(: % (get tuple #((get dict #(str object)) ...)))] :tags {:context "worker" :role "protocol" :spells "json"}}
  "状態の行の列を、行ごとの JSON の形(status-row)の列に綴るため(heartbeat の statuses と状態の file の jobs が同じ綴りを使う)。"
  (var rows #())
  (for [s statuses]
    (<- row (get dict #(str object)) (status-row s))
    (:= rows (+ rows #(row))))
  rows)


(defk status-row [s]
  {:pre [(: s JobStatus)] :post [(: % (get dict #(str object)))] :tags {:context "worker" :role "protocol" :spells "json"}}
  "状態の行 1 つを、heartbeat と状態の file が載せる JSON の形に綴るため。"
  {"name" s.name "phase" s.phase.value "desiredRevision" s.desired-revision
   "runningRevision" s.running-revision "pid" s.pid "attempts" s.attempts "detail" s.detail
   ;; 落ちた事実(#3477): 続けて落ちた回数と最後の終わりの code と時刻。
   "failures" s.failures "lastExitCode" s.last-exit-code "lastExitAtMs" s.last-exit-at-ms
   ;; 動いている process の世代(coordinator の readiness と計器はこれと一致する報告だけを数える)。
   "instance" s.instance "specHash" s.spec-hash "placement" s.placement "retiredFrom" s.retired-from
   ;; 実行環境の準備の失敗(ENV-FAILED の行だけ): coordinator が置き直すか・答えの型を決める。
   #** (if (is s.failure None) {} {"failureKind" s.failure.kind.value "retryable" s.failure.retryable})
   ;; 入口の検めの姿(検めが通っていない間だけ — 2026-09-27): 状態・今の検めの経過の秒・回数・直前の失敗の理由。
   #** (if (is s.probe None) {} {"probe" {"state" s.probe.state "elapsedSeconds" s.probe.elapsed-seconds
                                          "attempts" s.probe.attempts "lastFailure" s.probe.last-failure}})})
