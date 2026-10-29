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
                                                       ServiceObserved UnplacedView WorkerObserved TaskObserved RolloutObserved ResourceView ResourceList VersionVerdict ErrorReply RowConflict
                                                       BoardUsage BoardRead BoardWritten BoardConflict BoardRefused
                                                       TaskRecord TaskAccepted TaskProgress TaskMissing TaskResultTaken TaskDropped
                                                       DetachedSubmitted DetachedProgress DetachedUnknown DetachedWarming DetachedCancelled DetachedReleased
                                                       ProgramRow ProgramStored DeploymentSeen DeploymentUnreadable ReadinessReport])
(import doeff_cluster.shared.intent.warm_model [WarmState])
(import doeff_cluster.shared.core.warm_rules [warm-state->json])
(import doeff_hy._wire [dump])
(import doeff_cluster.shared.intent.semaphore_model [LeaseAnswer])
(import doeff_cluster.coordinator.core.cluster_policy [job-to-json status-row-to-json])
(import doeff_cluster.coordinator.protocol.state_json [audit-event-to-json])
(import doeff_cluster.shared.core.detached_rules [group-limit-spec task-limits-json])
(import doeff_cluster.coordinator.core.resource_policy [needs-override-spec])
(import doeff_cluster.shared.intent.detached_model [DetachedOutbox DetachedOutboxAcked])
(import doeff_cluster.coordinator.protocol.cluster_json [ended-to-json])


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
     (if task.detached {"detached" True "key" task.key} {})
     ;; 名乗った同時数の上限の組(待つ理由を外から読む ReadDetachedQueue が groupLimits と照らす)— 名乗る task だけ。
     (if task.limits {"limits" (task-limits-json task.limits)} {})))


(defk spec-json [#^ JobSpec spec]
  {:pre [(: spec JobSpec)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "worker へ渡す job の宣言 → heartbeat の返事の jobs の 1 つの JSON の形(Program・環境変数・置き場・実行環境・入れ替えの欄は在る時だけ)。"
  (| {"name" spec.name "entry" spec.entry "args" (list spec.args) "revision" spec.revision "once" spec.once}
     ;; Program の job だけ(改訂 1 の F・G): 詰めた Program の置き場のキー(worker が /programs/<sha> から取る)・宣言の版(子の入口が
     ;; 比べる送り手の版 — worker は Program の cache の file をこの版ごとに分ける・card ki-172e63fed4c7)と子の環境変数。
     (if spec.program {"program" spec.program "versions" (dict spec.versions)} {})
     (if spec.environ {"environ" (dict spec.environ)} {})
     ;; 中身の鍵の在る job だけ(card ki-81bf84ac6590): worker は版と commit の代わりにこの鍵で宣言の変わりを判じる。欄を読まない古い worker は
     ;; 今どおり版で判じる(coordinator は版の指紋の報告も受ける — job_rules.spec-hash-matches)。
     (if (is spec.content None) {} {"content" spec.content})
     (if (is spec.placement None) {} {"placement" spec.placement})
     ;; 実行環境の job だけ: 宣言の JSON(worker が env の root を準備し、版を env のキーへ置き換える)。
     (if (is spec.runtime-env None) {} {"runtimeEnv" (json.loads spec.runtime-env)})
     ;; 入れ替え(handoff)の job だけ: 形と、coordinator が Ready と数えている process の世代の名(worker は旧をこの後に止める)。
     (if spec.handoff {"handoff" True "readyInstance" spec.ready-instance} {})
     ;; 入れ替えを諦めた job だけ(2026-09-26 — handoff_policy の期限): worker は新を止めて起こし直さず、旧を動かし続ける。
     (if (and spec.handoff spec.handoff-abandoned) {"handoffAbandoned" True} {})
     ;; 世代をまたぐ入れ替えの印を持つ handoff の job だけ(AL-D2 — 退いた世代への返事): worker は今の process を止めずに退かせ、起こし直さ
     ;; ない。欄を読まない古い worker は今どおり動かし続ける(Pod の停止で止める)。
     (if (and spec.handoff (is-not spec.superseded-by None)) {"supersededBy" spec.superseded-by} {})
     ;; 前の世代が動かしている印を持つ recreate の job だけ(AL-D2f — 前の世代がまだ走っている新しい世代への返事): worker は準備と検めだけを
     ;; して起こさない。欄を読まない古い worker は今どおり起こす(前の世代と並びうる — 世代の重なりを名乗る worker だけが印を受ける)。
     (if spec.elder-runs {"elderRuns" True} {})
     ;; 退いた process の寿命の上限を宣言した handoff の job だけ(#4072 の D-2): worker は新の Ready で退いた旧を止めず、旧が自分で
     ;; 終わるか、退いてからこの ms を越えた時に止める。欄を読まない古い worker は今どおり新の Ready で止める。
     (if (and spec.handoff (is-not spec.retired-ms None)) {"retiredMs" spec.retired-ms} {})
     ;; 退いた process を同時に残す数の上限 R を宣言した handoff の job だけ(#4072 の D-3 の改め): worker は退いた process が R に達して
     ;; いる間、今の process を退かせずに待つ。欄を読まない古い worker は既定の WorkerPolicy.retired-limit を使う。
     (if (and spec.handoff (is-not spec.retired-limit None)) {"retiredLimit" spec.retired-limit} {})
     ;; 退きの保ちを持つ退いた process が在る handoff の job だけ(card ki-e3531030eb37): worker は列の世代の退いた process を寿命の上限で
     ;; 止めない。欄を読まない古い worker は今どおり上限で止める。
     (if (and spec.handoff spec.retired-held) {"retiredHeld" (list spec.retired-held)} {})
     ;; 途絶しても動かし続けてよい印の在る job だけ(#2804 — cluster_policy.keep-marked): worker は coordinator に届かない間もこの job を
     ;; 止めない(長い方の柵 keep-fence-ms まで)。欄を読まない古い worker は今までどおり fence で止める — 旧い worker(e06b01411 の
     ;; handlers.declared-job-spec)は知っている鍵だけを .get で読むので、この欄は捨てられる。
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
     (if reply.superseded {"superseded" True} {})
     (if reply.handover-pending {"handoverPending" True} {})))


(defk state-view-json [#^ StateView view]
  {:pre [(: view StateView)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "状態の画面 → JSON の形(#2595 の前に cluster_policy.state-view が組んでいた形と同じ)。coordinatorCommit = この coordinator の process が
   走っている doeff の版(起動の時に読んだ WORKER_DOEFF_COMMIT — #3772)。読めなかった時は欄を書かない(空の文字や null を版として書かない)。
   足すだけの欄で、知らない欄を読まない読み手(欄を名で引く読み手)はそのまま読める。"
  (| (! (state-view-body view))
     (if (is view.coordinator-commit None) {} {"coordinatorCommit" view.coordinator-commit})))


(defk state-view-body [#^ StateView view]
  {:pre [(: view StateView)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "状態の画面の、版の欄を除く本体 → JSON の形(state-view-json が版の欄を足す)。"
  {"now" view.now
   "jobs" (lfor s view.services (| (job-to-json s.job) {"resourceVersion" s.resource-version}))
   ;; memoryMiB = 枠の file の行の常駐の job の memory の予算(card ki-ced3c45980a5 — 名乗った worker だけに載せる)。
   "workers" (dfor w view.workers
                   w.info.name (| {"provides" (list w.info.provides) "exclusive" (list w.info.exclusive) "derived" (list w.info.derived)
                                   "node" w.info.node "capacity" w.info.capacity "taskReserve" w.info.task-reserve "taskRoom" w.task-room
                                   "silentMs" w.silent-ms
                                   "versions" (dict w.info.versions) "live" w.live "draining" w.draining}
                                  (if (is w.info.memory-mib None) {} {"memoryMiB" w.info.memory-mib})))
   "placements" (dfor #(k v) (.items view.placements) k (asdict v))
   "unplaced" view.unplaced
   "statuses" (dfor #(n s) (.items view.statuses)
                    n {"at" s.report.at "endpoint" s.report.endpoint "jobs" (lfor row s.report.jobs (status-row-to-json row))
                       "stale" s.stale})
   "tasks" (! (spelled-each task-summary view.tasks))
   "boardKeys" view.board-keys
   "surges" (dfor #(k v) (.items view.surges) k (asdict v))
   "events" (list view.events)
   "revision" view.revision
   ;; 途絶しても動かし続けてよい印の約束(#2883): job の名の順の列 [{job worker boot sinceMs}] — 担い手 worker の世代 boot へ時刻 sinceMs から
   ;; 渡してある。約束の在る job は、担い手が印を手放すか、Worker が消されるか、担い手の沈黙が約束の期限(kept-reassign-after-ms)を
   ;; 越えるまで他へ移らない。GET で返すだけ(約束を書き換える要求は無い)。
   "keepMarks" (lfor m view.keep-marks {"job" m.job "worker" m.worker "boot" m.boot "sinceMs" m.since-ms})
   ;; 組の同時数の上限(2026-10-12・card ki-b0e8028c8c8d): 組の名の順の列 [{group scope limit perPart resourceVersion held queued}] —
   ;; 系が宣言した値と、置いている数の升 held([{worker? part? count}] — 範囲 worker の組だけ worker・区分ごとの組だけ part)と、
   ;; その組を名乗って待っている task の数。運用者が値の割れや詰まりを 1 か所で読むため。
   "groupLimits" (lfor g view.group-limits
                       (| {"group" g.limit.group "resourceVersion" g.resource-version "queued" g.queued
                           "held" (lfor h g.held (| {"count" h.count}
                                                     (if (is h.worker None) {} {"worker" h.worker})
                                                     (if h.part {"part" h.part} {})))}
                          (group-limit-spec g.limit)))
   ;; 置き先の一時の上書き(2026-10-27・card ki-1523bc0aba4f): Service の名の順の列 [{service needs reason expiresAtMs writer sinceMs}] —
   ;; 宣言の needs の代わりに今効いている needs と、その訳・期限・書き手。宣言の spec は書き換えないので、ここで読む。
   "needsOverrides" (lfor o view.needs-overrides (| {"service" o.service} (needs-override-spec o)))
   ;; 置き先の無い Service の読み(2026-10-27・card ki-53b8677f1374): Service の名 → {kind needs missing pin sinceMs}(unplaced と同じ鍵)—
   ;; 種類(no-eligible-worker など)・効く needs・足りない能力(生きていて drain 中でない worker のどれも名乗らない能力)・固定・置き先が
   ;; 無いと最初に見た刻。外の見張りが理由の文を読み解かずに、どの Service がいつから何の能力が足りずに置けないかを読むため。
   "unplacedDetail" (dict (zip (gfor u view.unplaced-detail u.service) (! (spelled-each unplaced-view-json view.unplaced-detail))))})


(defk unplaced-view-json [#^ UnplacedView view]
  {:pre [(: view UnplacedView)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "置き先の無い Service 1 つの読み → JSON の形 {kind needs missing pin sinceMs}(GET /state の unplacedDetail の値と、Service の資源の
   status.unplaced が同じ形を読むため — card ki-53b8677f1374)。kind = UnplacedKind の値(no-eligible-worker・no-room・waiting-previous-holder)。"
  {"kind" view.kind.value "needs" (list view.needs) "missing" (list view.missing) "pin" view.pin "sinceMs" view.since-ms})


(defk drain-progress-json [#^ DrainProgress drain]
  {:pre [(: drain DrainProgress)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "drain の進み → JSON の形(#2595 の前に drain_policy.drain-view・superseded-worker-view が組んでいた形と同じ — 頼みの記録は退いた世代の
   答えに無く、superseded は退いた世代の答えにだけ書く)。unmovable = 待っても移せないので待たない job の名の列(#3669)。"
  (| {"worker" drain.worker}
     (if drain.superseded
         {"boot" drain.boot "superseded" True}
         {"sinceMs" drain.since-ms "untilMs" drain.until-ms "boot" drain.boot "actor" drain.actor})
     {"phase" drain.phase.value "drained" (= drain.phase DrainPhase.DRAINED) "remaining" (list drain.remaining)
      "moving" (dict drain.moving) "blocked" (dict drain.blocked) "unmovable" (list drain.unmovable)
      "movingReady" (dict drain.moving-ready)}))


(defk worker-drain-view-json [#^ WorkerDrainView view]
  {:pre [(: view WorkerDrainView)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "worker 1 つの画面 → JSON の形(#2595 の前に drain_policy.worker-view・superseded-worker-view が組んでいた形と同じ)。今の世代の doeff の
   版 doeffCommit と起動の刻 bootAt は、名乗った worker だけに載せる(card ki-6e83e03d146d — 同じ Pod の中の版の入れ替えを読む)。"
  (val w view.info)
  (| {"name" w.name "alive" view.alive "silentMs" view.silent-ms "boot" w.boot "provides" (list w.provides) "exclusive" (list w.exclusive)
      "derived" (list w.derived) "node" w.node "draining" (is-not view.drain None)}
     (if w.doeff-commit {"doeffCommit" w.doeff-commit} {})
     (if (is w.boot-at None) {} {"bootAt" w.boot-at})
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
       "version" (! (version-json observed.version observed.running))
       ;; 置き先が無ければ {kind needs missing pin sinceMs}・置かれていれば null(card ki-53b8677f1374)。
       "unplaced" (if (is observed.unplaced None) None (! (unplaced-view-json observed.unplaced)))}
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
                    DetachedSubmitted DetachedProgress DetachedUnknown DetachedWarming DetachedCancelled DetachedReleased
                    DetachedOutbox DetachedOutboxAcked ResourceView
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
    ;; 切り離した task の終わりの出口(card ki-e7945a2b2d00 — 読み手の DetachedOutboxWire と同じ形)。
    (isinstance body DetachedOutbox) {"entries" (lfor e body.entries (ended-to-json e))}
    (isinstance body DetachedOutboxAcked) {"id" body.id "acked" body.acked}
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
