;;; coordinator の返事の本文を JSON の形に綴る 1 点(#2595)。core の判断は返事を型の値(EventsView・StateReply)で返し、ここが外の JSON の形
;;; にする。返事の型にまだしていない道の本文(JSON の object のまま)は、そのまま通す。
;;;   reply-json    返事の本文 → JSON の形(byte にするのは shared/protocol/inbox の encoded-reply)
;;;   reply-bodies  Reply の答え手: 本文を reply-json で綴って Reply を出し直す(本番と模擬の組のいちばん内側に置く)
;;; 綴りはどれも defk で、呼び手は <- / ! で受ける(ADR-DOE-HY-004 R10 — 検と模擬の入口 responded は request_bodies.hy で run する)。
(require doeff-hy.macros [defhandler defk <- val var])
(val MODULE-TAGS {:context "coordinator" :role "protocol"})
(import doeff_cluster.shared.intent.protocol [Reply PlainText])
(import dataclasses [asdict])
(import json)
(import collections.abc [Callable Iterable Mapping])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.coordinator.intent.cluster_model [EventsView StateReply StateView HeartbeatReply TaskOffer DrainPhase DrainProgress WorkerDrainView
                                                       ServiceObserved WorkerObserved TaskObserved RolloutObserved ResourceView ResourceList VersionVerdict ErrorReply RowConflict
                                                       BoardUsage BoardRead BoardWritten BoardConflict BoardRefused
                                                       TaskRecord TaskAccepted TaskProgress TaskMissing TaskResultTaken TaskDropped
                                                       DetachedSubmitted DetachedProgress DetachedUnknown DetachedWarming DetachedCancelled DetachedReleased
                                                       ProgramRow ProgramStored DeploymentSeen DeploymentUnreadable ReadinessReport])
(import doeff_cluster.shared.intent.warm_model [WarmState])
(import doeff_cluster.shared.core.warm_rules [warm-state->json])
(import doeff_hy.wire [dump])
(import doeff_cluster.shared.intent.semaphore_model [LeaseAnswer])
(import doeff_cluster.coordinator.core.cluster_policy [job-to-json status-row-to-json])
(import doeff_cluster.coordinator.protocol.state_json [audit-event-to-json])


(defk spelled-each [#^ Callable spell #^ Iterable items]
  {:pre [(: spell Callable) (: items Iterable)] :post [(: % list)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "列の 1 つずつを綴りの defk spell で JSON の形にした列(返事の中の列 — jobs・tasks・conflicts・items など — を綴る 1 点)。
   綴りは Program を返すので内包表記では呼べない: for と ! で順に綴り、tuple に組んでから JSON の配列の形(list)で返す。"
  (var spelled #())
  (for [item items]
    (:= spelled #(#* spelled (! (spell item)))))
  (list spelled))


(defk task-summary [#^ TaskRecord task]
  {:pre [(: task TaskRecord)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "task の行 → 状態の画面と資源の画面の JSON の形(結果は大きいので載せない — #2614 で core から移した)。切り離した task だけ呼び手の job id を足す
   (RemoteJob の task の形は以前と同じ)。"
  (| {"id" task.id "name" task.name "revision" task.revision "phase" task.phase
      "worker" task.worker "detail" task.detail "submittedMs" task.submitted-ms
      "startedMs" task.started-ms "finishedMs" task.finished-ms "leaseUntilMs" task.lease-until-ms}
     (if task.detached {"detached" True "key" task.key} {})))


(defk spec-json [#^ JobSpec spec]
  {:pre [(: spec JobSpec)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "worker へ渡す job の宣言 → heartbeat の返事の jobs の 1 つの JSON の形(Program・環境変数・置き場・実行環境・入れ替えの欄は在る時だけ)。"
  (| {"name" spec.name "entry" spec.entry "args" (list spec.args) "revision" spec.revision "once" spec.once}
     ;; Program の job だけ(改訂 1 の F・G): 詰めた Program の置き場のキー(worker が /programs/<sha> から取る)と子の環境変数。
     (if spec.program {"program" spec.program} {})
     (if spec.environ {"environ" (dict spec.environ)} {})
     (if (is spec.placement None) {} {"placement" spec.placement})
     ;; 実行環境の job だけ: 宣言の JSON(worker が env の root を準備し、版を env のキーへ置き換える)。
     (if (is spec.runtime-env None) {} {"runtimeEnv" (json.loads spec.runtime-env)})
     ;; 入れ替え(handoff)の job だけ: 形と、coordinator が Ready と数えている process の世代の名(worker は旧をこの後に止める)。
     (if spec.handoff {"handoff" True "readyInstance" spec.ready-instance} {})
     ;; 入れ替えを諦めた job だけ(2026-09-26 — handoff_policy の期限): worker は新を止めて起こし直さず、旧を動かし続ける。
     (if (and spec.handoff spec.handoff-abandoned) {"handoffAbandoned" True} {})
     ;; 途絶しても動かし続けてよい印の在る job だけ(#2804 — cluster_policy.keep-marked): worker は coordinator に届かない間もこの job を
     ;; 止めない。欄を読まない古い worker は今までどおり fence で止める。
     (if spec.keep-when-cut-off {"keepWhenCutOff" True} {})))


(defk task-offer-json [#^ TaskOffer offer]
  {:pre [(: offer TaskOffer)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "返事の task 1 つ → JSON の形(#2595 の前に cluster_policy.tasks-for が組んでいた形と同じ — 切り離した task の欄・実行環境・環境変数は在る時だけ)。"
  (| {"id" offer.id "name" offer.name "revision" offer.revision "versions" (dict offer.versions) "program" offer.program}
     (if offer.detached {"detached" True "key" offer.key "leaseMs" offer.lease-ms "retainMs" offer.retain-ms "needs" (list offer.needs)} {})
     (if (is-not offer.runtime-env None) {"runtimeEnv" offer.runtime-env} {})
     (if offer.environ {"environ" (dict offer.environ)} {})))


(defk heartbeat-reply-json [#^ HeartbeatReply reply]
  {:pre [(: reply HeartbeatReply)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "heartbeat の返事 → JSON の形(superseded は退いた世代への返事の時だけ書く)。"
  (| {"jobs" (! (spelled-each spec-json reply.jobs)) "tasks" (! (spelled-each task-offer-json reply.tasks))
      "warm" (lfor w reply.warm {"key" w.key "runtimeEnv" w.runtime-env}) "timing" (asdict reply.timing)
      "draining" reply.draining "formats" (list reply.formats) "revision" reply.revision}
     (if reply.superseded {"superseded" True} {})))


(defk state-view-json [#^ StateView view]
  {:pre [(: view StateView)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "状態の画面 → JSON の形(#2595 の前に cluster_policy.state-view が組んでいた形と同じ)。"
  {"now" view.now
   "jobs" (lfor s view.services (| (job-to-json s.job) {"resourceVersion" s.resource-version}))
   "workers" (dfor w view.workers
                   w.info.name {"provides" (list w.info.provides) "exclusive" (list w.info.exclusive) "derived" (list w.info.derived)
                                "node" w.info.node "capacity" w.info.capacity "silentMs" w.silent-ms
                                "versions" (dict w.info.versions) "live" w.live "draining" w.draining})
   "placements" (dfor #(k v) (.items view.placements) k (asdict v))
   "unplaced" view.unplaced
   "statuses" (dfor #(n s) (.items view.statuses)
                    n {"at" s.report.at "endpoint" s.report.endpoint "jobs" (lfor row s.report.jobs (status-row-to-json row))
                       "stale" s.stale})
   "tasks" (! (spelled-each task-summary view.tasks))
   "boardKeys" view.board-keys
   "surges" (dfor #(k v) (.items view.surges) k (asdict v))
   "events" (list view.events)
   "revision" view.revision})


(defk drain-progress-json [#^ DrainProgress drain]
  {:pre [(: drain DrainProgress)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "drain の進み → JSON の形(#2595 の前に drain_policy.drain-view・superseded-worker-view が組んでいた形と同じ — 頼みの記録は退いた世代の
   答えに無く、superseded は退いた世代の答えにだけ書く)。"
  (| {"worker" drain.worker}
     (if drain.superseded
         {"boot" drain.boot "superseded" True}
         {"sinceMs" drain.since-ms "untilMs" drain.until-ms "boot" drain.boot "actor" drain.actor})
     {"phase" drain.phase.value "drained" (= drain.phase DrainPhase.DRAINED) "remaining" (list drain.remaining)
      "moving" (dict drain.moving) "blocked" (dict drain.blocked) "movingReady" (dict drain.moving-ready)}))


(defk worker-drain-view-json [#^ WorkerDrainView view]
  {:pre [(: view WorkerDrainView)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "worker 1 つの画面 → JSON の形(#2595 の前に drain_policy.worker-view・superseded-worker-view が組んでいた形と同じ)。"
  (val w view.info)
  (| {"name" w.name "alive" view.alive "silentMs" view.silent-ms "boot" w.boot "provides" (list w.provides) "exclusive" (list w.exclusive)
      "derived" (list w.derived) "node" w.node "draining" (is-not view.drain None)}
     (if view.superseded {"superseded" True} {})
     {"drain" (if (is view.drain None) None (! (drain-progress-json view.drain))) "ready" view.ready}))


(defk version-json [#^ VersionVerdict verdict #^ tuple live]
  {:pre [(: verdict VersionVerdict) (: live tuple)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "status.version の JSON の形(資源の口の境界): {state reason running: [{revision retired}]}。"
  {"state" verdict.state.value "reason" verdict.reason
   "running" (lfor p live {"revision" p.revision "retired" p.retired})})


(defk deployment-observation-json [seen]
  {:pre [(: seen (| DeploymentSeen DeploymentUnreadable None))] :post [(: % (| (get Mapping #(str object)) None))]
   :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "Rollout の相手の Deployment 1 つの最後の観測を、資源の画面の status.observed の値の JSON にするため(#2728 の前に状態の deployments の
   行をそのまま写していた形と同じ — 読めた観測は specReplicas … annotations の 8 欄と at・読めなかった観測は {at error}・まだ読んで
   いなければ null)。"
  (match seen
    (DeploymentSeen :reading reading :at at)
      (do (<- reading-json (dump reading))
          ;; dump は defwire の値を JSON の object(欄の順は型の欄の順)に綴る — object でない答えは dump の欠陥なので黙って通さない。
          (match reading-json
            (dict) (| reading-json {"at" at})
            _ (raise (TypeError (.format "DeploymentReading の dump が JSON の object でない: {!r}" reading-json)))))
    (DeploymentUnreadable :error error :at at) {"at" at "error" error}
    _ None))


(defk readiness-report-json [report]
  {:pre [(: report (| ReadinessReport None))] :post [(: % (| (get Mapping #(str object)) None))]
   :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "Service の最後の準備の報告を、資源の画面の status.lastReadiness の値の JSON にするため(#2756 の前に状態の readiness の行をそのまま
   写していた形と同じ — 欄の順は worker・pid・revision・instance・attempt・specHash・placement・at・ready・reason・role、attempt は
   送られた型のまま、欠けた欄は null・報告が無ければ null)。"
  (match report
    (ReadinessReport :origin origin :ready ready :reason reason :role role)
      {"worker" origin.worker "pid" origin.pid "revision" origin.revision "instance" origin.instance "attempt" origin.attempt
       "specHash" origin.spec-hash "placement" origin.placement "at" origin.at "ready" ready "reason" reason "role" role}
    _ None))


(defk observed-json [#^ (| ServiceObserved WorkerObserved TaskObserved RolloutObserved None) observed]
  {:pre [(: observed (| ServiceObserved WorkerObserved TaskObserved RolloutObserved None))] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "資源の種類ごとの観測 → status に足す JSON の欄(#2595 の前に resource_policy.resource-json が足していた欄と同じ)。"
  (cond
    (isinstance observed ServiceObserved)
      {"readyReason" observed.ready-reason
       "lastReadiness" (! (readiness-report-json observed.last-readiness))
       "process" (if (is observed.process None) None (status-row-to-json observed.process))
       "version" (! (version-json observed.version observed.running))}
    (isinstance observed WorkerObserved) {"silentMs" observed.silent-ms "alive" observed.alive}
    (isinstance observed TaskObserved) (! (task-summary observed.task))
    ;; Rollout の相手の Deployment ごとの最後の観測(鍵 = 相手の鍵・値の形は deployment-observation-json)。
    (isinstance observed RolloutObserved)
      {"observed" (dict (zip (gfor row observed.observed row.key)
                             (! (spelled-each deployment-observation-json (gfor row observed.observed row.seen)))))}
    True {}))


(defk resource-view-json [#^ ResourceView view]
  {:pre [(: view ResourceView)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "資源 1 つの画面 → JSON の形(#2595 の前に resource_policy.resource-json が組んでいた形と同じ — status は比べる単位の status に観測を足した物)。"
  (val m view.meta)
  {"kind" view.kind "name" view.name
   "resourceVersion" (if m m.resource-version None) "generation" (if m m.generation None)
   "owner" (.get view.spec "owner")
   "createdBy" (if m m.created-by None) "createdMs" (if m m.created-ms None)
   "updatedBy" (if m m.updated-by None) "updatedMs" (if m m.updated-ms None)
   "spec" view.spec "status" (| view.status (! (observed-json view.observed)))})


(defk row-conflict-json [#^ RowConflict conflict]
  {:pre [(: conflict RowConflict)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "旧い一括の宣言で書けなかった行 1 つ → JSON の形({name error current?} — #2614 の前に core が組んでいた形と同じ)。"
  (| {"name" conflict.name "error" conflict.message}
     (if (is conflict.current None) {} {"current" conflict.current})))


(defk error-reply-json [#^ ErrorReply reply]
  {:pre [(: reply ErrorReply)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "断った要求の答え → JSON の形({error …} — #2614 の前に core が組んでいた形と同じ。付け足しの欄は在る時だけ書く)。"
  (| {"error" reply.message}
     (if (is reply.current None) {} {"current" reply.current})
     (if (is reply.conflicts None) {} {"conflicts" (! (spelled-each row-conflict-json reply.conflicts))})
     (if (is reply.open None) {} {"open" reply.open})
     (if reply.fault {"fault" True} {})))


(defk board-usage-json [#^ BoardUsage usage]
  {:pre [(: usage BoardUsage)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "盤の使い方と上限 → JSON の形(容量で断った答えの usage — #2614 の前に cluster_policy.board-usage が組んでいた形と同じ)。"
  {"rows" usage.rows "bytes" usage.bytes "expiring" usage.expiring
   "maxRows" usage.max-rows "maxBytes" usage.max-bytes "maxValueBytes" usage.max-value-bytes})


(defk board-answer-json [#^ (| BoardRead BoardWritten BoardConflict BoardRefused) answer]
  {:pre [(: answer (| BoardRead BoardWritten BoardConflict BoardRefused))] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "盤の口の答え → JSON の形(#2614 の前に api_policy.respond-board と cluster_policy.board-write が組んでいた形と同じ)。"
  (cond
    (isinstance answer BoardRead)
      (dfor e answer.entries e.key (if answer.with-versions {"value" e.value "resourceVersion" e.version} e.value))
    (isinstance answer BoardWritten) {"ok" True "resourceVersion" answer.version}
    (isinstance answer BoardConflict)
      (| {"ok" False "current" answer.current "resourceVersion" answer.version}
         (if (is answer.reason None) {} {"error" answer.reason}))
    True
      (| {"ok" False "error" answer.reason}
         (if (is answer.usage None) {} {"usage" (! (board-usage-json answer.usage))}))))


(defk task-answer-json [#^ (| TaskAccepted TaskProgress TaskMissing TaskResultTaken TaskDropped) answer]
  {:pre [(: answer (| TaskAccepted TaskProgress TaskMissing TaskResultTaken TaskDropped))] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "task の口の答え → JSON の形(#2614 の前に cluster_policy の submit-task・poll-task・absorb-task-result と api_policy が組んでいた形と同じ)。"
  (cond
    (isinstance answer TaskAccepted) {"task" answer.id}
    (isinstance answer TaskMissing) {"phase" "missing"}
    (isinstance answer TaskProgress)
      {"phase" answer.phase "worker" answer.worker "detail" answer.detail "result" answer.result
       "failureKind" answer.failure-kind "retryable" answer.retryable}
    (isinstance answer TaskResultTaken) {"accepted" answer.accepted "phase" answer.phase}
    True {"dropped" True}))


(defk detached-answer-json [#^ (| DetachedSubmitted DetachedProgress DetachedUnknown DetachedWarming DetachedCancelled DetachedReleased) answer]
  {:pre [(: answer (| DetachedSubmitted DetachedProgress DetachedUnknown DetachedWarming DetachedCancelled DetachedReleased))] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "切り離した task の口の答え → JSON の形(#2614 の前に detached_policy が組んでいた形と同じ)。"
  (cond
    (isinstance answer DetachedSubmitted) {"key" answer.key "task" answer.id "created" answer.created "phase" answer.phase}
    (isinstance answer DetachedProgress)
      {"key" answer.key "task" answer.id "phase" answer.phase "detail" answer.detail "result" answer.result "worker" answer.worker
       "failureKind" answer.failure-kind "retryable" answer.retryable}
    (isinstance answer DetachedUnknown) {"key" answer.key "phase" "unknown"}
    (isinstance answer DetachedWarming) {"key" answer.key "phase" answer.phase "error" answer.reason}
    (isinstance answer DetachedCancelled) {"key" answer.key "cancelled" answer.cancelled "phase" answer.phase}
    True {"key" answer.key "released" answer.released}))


(defk reply-json [body]
  {:pre [(: body (| EventsView StateReply HeartbeatReply WorkerDrainView ErrorReply BoardRead BoardWritten BoardConflict BoardRefused
                    LeaseAnswer WarmState ProgramStored ProgramRow TaskAccepted TaskProgress TaskMissing TaskResultTaken TaskDropped
                    DetachedSubmitted DetachedProgress DetachedUnknown DetachedWarming DetachedCancelled DetachedReleased ResourceView
                    ResourceList PlainText dict list tuple str int float bool None))]
   :post [(: % (| PlainText dict list tuple str int float bool None))] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "返事の本文の型の値を、外へ見せる JSON の形にする(#2595 の前に core が組んでいた形と同じ)。型にしていない本文(JSON の値と、文字のまま
   送る PlainText — byte にするのは shared/protocol/inbox の encoded-reply)はそのまま返す。"
  (cond
    (isinstance body EventsView)
      {"revision" body.revision "seq" body.seq "events" (lfor e body.events (audit-event-to-json e))}
    (isinstance body StateReply)
      (| (! (state-view-json body.view)) {"audit" (lfor e body.audit (audit-event-to-json e))
                                        "drains" (dict (zip (.keys body.drains)
                                                            (! (spelled-each drain-progress-json (.values body.drains)))))})
    (isinstance body HeartbeatReply) (! (heartbeat-reply-json body))
    (isinstance body WorkerDrainView) (! (worker-drain-view-json body))
    (isinstance body ErrorReply) (! (error-reply-json body))
    (isinstance body #(BoardRead BoardWritten BoardConflict BoardRefused)) (! (board-answer-json body))
    ;; lease の答えは wire の型(4 つの欄をいつも書く — semaphore_model.LeaseAnswer の註)。
    (isinstance body LeaseAnswer) (! (dump body))
    (isinstance body WarmState) (! (warm-state->json body))
    (isinstance body ProgramStored) {"program" body.sha}
    (isinstance body ProgramRow) {"blob" body.blob "versions" body.versions}
    (isinstance body #(TaskAccepted TaskProgress TaskMissing TaskResultTaken TaskDropped)) (! (task-answer-json body))
    (isinstance body #(DetachedSubmitted DetachedProgress DetachedUnknown DetachedWarming DetachedCancelled DetachedReleased)) (! (detached-answer-json body))
    (isinstance body ResourceView) (! (resource-view-json body))
    (isinstance body ResourceList)
      {"kind" body.kind "revision" body.revision "items" (! (spelled-each resource-view-json body.items))}
    True body))


(defhandler reply-bodies
  ;; 引数なし: 返事の本文の型だけから綴る(返事を送るのは外側の Reply の答え手)。
  (Reply [request status body]
    (<- spelled (reply-json body))
    (<- (Reply request status spelled))
    (resume None)))
