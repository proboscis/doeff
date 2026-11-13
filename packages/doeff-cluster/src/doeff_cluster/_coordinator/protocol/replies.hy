;;; coordinator の返事の本文を JSON の形に綴る 1 点(#2595)。core の判断は返事を型の値(EventsView・StateReply)で返し、ここが外の JSON の形
;;; にする。返事の型にまだしていない道の本文(JSON の object のまま)は、そのまま通す。
;;;   reply-json    返事の本文 → JSON の形(byte にするのは shared/protocol/inbox の encoded-reply)
;;;   reply-bodies  Reply の答え手: 本文を reply-json で綴って Reply を出し直す(本番と模擬の組のいちばん内側に置く)
;;; 綴りはどれも defk で、呼び手は <- / ! で受ける(ADR-DOE-HY-004 R10 — 検と模擬の入口 responded は request_bodies.hy で run する)。
(require doeff-hy.macros [defhandler defk <- val var])
(val MODULE-TAGS {:context "coordinator" :role "protocol"})
(import doeff_cluster._shared.intent.protocol [Reply PlainText SpelledJson])
(import dataclasses [asdict])
(import json)
(import collections.abc [Callable Iterable Mapping])
(import doeff_cluster._shared.intent.job_model [JobSpec])
(import doeff_cluster._coordinator.intent.cluster_model [EventsView StateReply StateView HeartbeatReply TaskOffer DrainPhase DrainProgress WorkerDrainView
                                                       ServiceObserved UnplacedView WorkerObserved TaskObserved RolloutObserved ResourceView ResourceList VersionVerdict ErrorReply RowConflict
                                                       BoardUsage BoardRead BoardRow BoardWritten BoardConflict BoardRefused
                                                       TaskRecord TaskUnplacedKind TaskAccepted TaskProgress TaskMissing TaskResultTaken TaskDropped
                                                       DetachedSubmitted DetachedProgress DetachedUnknown DetachedWarming DetachedCancelled DetachedReleased
                                                       ProgramRow ProgramStored DeploymentSeen DeploymentUnreadable ReadinessReport])
(import doeff_cluster._shared.intent.warm_model [WarmState])
(import doeff_cluster._shared.core.warm_rules [warm-state->json])
(import doeff_hy._wire [dump])
(import doeff_cluster._shared.intent.semaphore_model [LeaseAnswer])
(import doeff_cluster._coordinator.core.cluster_policy [job-to-json status-row-to-json])
(import doeff_cluster._coordinator.protocol.state_json [audit-event-to-json task-claim-spec])
(import doeff_cluster._shared.core.detached_rules [group-limit-spec task-limits-json])
(import doeff_cluster._shared.core.compartment_rules [machine-compartments-spec held-compartment-json])
(import doeff_cluster._coordinator.core.resource_policy [needs-override-spec])
(import doeff_cluster._shared.intent.detached_model [DetachedOutbox DetachedOutboxAcked])
(import doeff_cluster._coordinator.protocol.cluster_json [ended-to-json])


(defk spelled-each [#^ Callable spell #^ Iterable items]
  {:pre [(: spell Callable) (: items Iterable)] :post [(: % list)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "列の 1 つずつを綴りの defk spell で JSON の形にした列(返事の中の列 — jobs・tasks・conflicts・items など — を綴る 1 点)。
   綴りは Program を返すので内包表記では呼べない: for と ! で順に綴り、tuple に組んでから JSON の配列の形(list)で返す。"
  (var spelled #())
  (for [item items]
    (:= spelled #(#* spelled (! (spell item)))))
  (list spelled))


;; 待っている task を置けない訳の種類(TaskUnplacedKind)→ GET /state の task 行の unplacedKind の綴り(外へ見せる約束 — 外の読み手は
;; detail の文を照らさずこの名で分ける・2026-11-13)。綴りはここだけ。種類を足したらここにも足す(test_task_unplaced_kind が全ての値を pin する)。
(val TASK-UNPLACED-KIND-NAMES
  {TaskUnplacedKind.TASK-LIMITED "TASK-LIMITED"
   TaskUnplacedKind.TASK-UNDECLARED "TASK-UNDECLARED"
   TaskUnplacedKind.TASK-NO-COMPARTMENT "TASK-NO-COMPARTMENT"
   TaskUnplacedKind.TASK-COMPARTMENT-FULL "TASK-COMPARTMENT-FULL"})


(defk task-summary [#^ TaskRecord task]
  {:pre [(: task TaskRecord)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "task の行 → 状態の画面と資源の画面の JSON の形(結果は大きいので載せない — #2614 で core から移した)。切り離した task だけ呼び手の job id を足す
   (RemoteJob の task の形は以前と同じ)。"
  (| {"id" task.id "name" task.name "revision" task.revision "phase" task.phase
      "worker" task.worker "detail" task.detail "submittedMs" task.submitted-ms
      "startedMs" task.started-ms "finishedMs" task.finished-ms "leaseUntilMs" task.lease-until-ms}
     (if task.detached {"detached" True "key" task.key} {})
     ;; 名乗った同時数の上限の組(待つ理由を外から読む ReadDetachedQueue が groupLimits と照らす)— 名乗る task だけ。
     (if task.limits {"limits" (task-limits-json task.limits)} {})
     ;; memory の要り(MiB — 置き先が予約する 1 本の山・2026-11-02)— 名乗る task だけ。
     (if (is task.memory-mib None) {} {"memoryMiB" task.memory-mib})
     ;; 仕事の形と測った山(MiB — 置き先が要りに数える・2026-11-10)— 名乗る・測った task だけ。
     (if task.shape {"shape" task.shape} {})
     (if (is task.peak-mib None) {} {"peakMiB" task.peak-mib})
     ;; 測った空きの門の外の名乗り(2026-11-11)— 名乗る task だけ。
     (if task.ungated {"ungated" True} {})
     ;; 走らせ直しの名乗りと今の試行の番号と上限に数えた消失の数 — 名乗る task だけ。
     (if task.rerun {"rerun" True "attempt" task.attempt "losses" task.losses} {})
     ;; 場所を譲る名乗りと、この試行を積み直した訳が場所の譲りなら譲った相手(2026-11-11)— 名乗る・譲った task だけ。
     (if task.preemptible {"preemptible" True} {})
     (if task.preempted-by {"preemptedBy" task.preempted-by} {})
     ;; 線の内の借りとして置いた印と、この試行が借りの取り返しで積み直された印(2026-11-12)— 借りた・取り返した task だけ。
     (if task.borrowed {"borrowed" True} {})
     (if task.reclaimed {"reclaimed" True} {})
     ;; 置いた刻のその worker の測った空き(measured の memoryAvailableMiB と atMs・2026-11-12)— 測りの在る worker に置いた試行だけ。
     ;; worker の行の measured は今の 1 回だけなので、終わった task の置いた刻の空きはここで読む。
     (if (is task.placed-memory-available-mib None) {} {"placedMemoryAvailableMiB" task.placed-memory-available-mib})
     (if (is task.placed-measured-at-ms None) {} {"placedMeasuredAtMs" task.placed-measured-at-ms})
     ;; 待ちの期限(ms — queued のまま積んだ刻からこの長さを越えると置き先が取り消す・2026-11-12)— 名乗る task だけ。
     (if (is task.queue-limit-ms None) {} {"queueLimitMs" task.queue-limit-ms})
     ;; 置けない訳の種類(TASK-UNPLACED-KIND-NAMES の名 — detail の文と同じ判断・2026-11-13)。queued の行は欄を常に出す(種類の無い待ちは
     ;; null)— 外の読み手は欄の無い queued の行を、種類を出さない前の版の coordinator と読む。ほかの phase は種類を持つ行だけ。
     (cond (is-not task.unplaced-kind None) {"unplacedKind" (get TASK-UNPLACED-KIND-NAMES task.unplaced-kind)}
           (= task.phase "queued") {"unplacedKind" None}
           True {})))


(defk spec-json [#^ JobSpec spec]
  {:pre [(: spec JobSpec)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "worker へ渡す job の宣言 → heartbeat の返事の jobs の 1 つの JSON の形(Program・環境変数・置き場・実行環境・入れ替えの欄は在る時だけ)。"
  (| {"name" spec.name "entry" spec.entry "args" (list spec.args) "revision" spec.revision "once" spec.once}
     ;; Program の job だけ(改訂 1 の F・G): 詰めた Program の置き場のキー(worker が /programs/<sha> から取る)・宣言の版(子の入口が
     ;; 比べる送り手の版 — worker は Program の cache の file をこの版ごとに分ける)と子の環境変数。
     (if spec.program {"program" spec.program "versions" (dict spec.versions)} {})
     (if spec.environ {"environ" (dict spec.environ)} {})
     ;; 中身の鍵の在る job だけ: worker は版と commit の代わりにこの鍵で宣言の変わりを判じる。欄を読まない古い worker は
     ;; 今どおり版で判じる(coordinator は版の指紋の報告も受ける — job_rules.spec-hash-matches)。
     (if (is spec.content None) {} {"content" spec.content})
     (if (is spec.placement None) {} {"placement" spec.placement})
     ;; 実行環境の job だけ: 宣言の JSON(worker が env の root を準備し、版を env のキーへ置き換える)。
     (if (is spec.runtime-env None) {} {"runtimeEnv" (json.loads spec.runtime-env)})
     ;; 入れ替え(handoff)の job だけ: 形と、coordinator が Ready と数えている process の世代の名(worker は旧をこの後に止める)。
     (if spec.handoff {"handoff" True "readyInstance" spec.ready-instance} {})
     ;; 入れ替えを諦めた job だけ(2026-09-26 — handoff_policy の期限): worker は新を止めて起こし直さず、旧を動かし続ける。
     (if (and spec.handoff spec.handoff-abandoned) {"handoffAbandoned" True} {})
     ;; 条件を満たさなくなった担い手に残した handoff の job だけ(cluster_policy.relocating — 並べた先が Ready になるまで): worker は
     ;; 動いている旧い版をそのまま動かし、新しい版を準備も起動もしない。欄を読まない古い worker は今どおり自分の上でも入れ替える
     ;; (並べた先が Ready になると止められる — 無駄な準備と起動だけで、空白は作らない)。
     (if (and spec.handoff spec.hold-version) {"holdVersion" True} {})
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
     ;; 退きの保ちを持つ退いた process が在る handoff の job だけ: worker は列の世代の退いた process を寿命の上限で
     ;; 止めない。欄を読まない古い worker は今どおり上限で止める。
     (if (and spec.handoff spec.retired-held) {"retiredHeld" (list spec.retired-held)} {})
     ;; 途絶しても動かし続けてよい印の在る job だけ(#2804 — cluster_policy.keep-marked): worker は coordinator に届かない間もこの job を
     ;; 止めない(長い方の柵 keep-fence-ms まで)。欄を読まない古い worker は今までどおり fence で止める — 旧い worker(e06b01411 の
     ;; handlers.declared-job-spec)は知っている鍵だけを .get で読むので、この欄は捨てられる。
     (if spec.keep-when-cut-off {"keepWhenCutOff" True} {})
     ;; 宣言の memory の山を持つ job だけ(2026-11-11): worker の memory の見張りは、今の memory がこの値以下の常駐の job を
     ;; 止めない。欄を読まない古い worker は捨てる(今どおり平常の大きさで判じる)。
     (if (is spec.peak-mib None) {} {"peakMiB" spec.peak-mib})
     ;; CPU の取り分の名を宣言した job だけ: worker は取り分を宣言した機体で、process を services の層の下のその名の子に置く。
     ;; 欄を読まない古い worker は捨てる(今どおり services の層に置く)。
     (if (is spec.cpu-share None) {} {"cpuShare" spec.cpu-share})))


(defk task-offer-json [#^ TaskOffer offer]
  {:pre [(: offer TaskOffer)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "返事の task 1 つ → JSON の形(#2595 の前に cluster_policy.tasks-for が組んでいた形と同じ — 切り離した task の欄・実行環境・環境変数は在る時だけ)。"
  (| {"id" offer.id "name" offer.name "revision" offer.revision "versions" (dict offer.versions) "program" offer.program}
     (if offer.detached {"detached" True "key" offer.key "leaseMs" offer.lease-ms "retainMs" offer.retain-ms "needs" (list offer.needs)} {})
     (if (is-not offer.runtime-env None) {"runtimeEnv" offer.runtime-env} {})
     (if offer.environ {"environ" (dict offer.environ)} {})
     (if (is offer.memory-mib None) {} {"memoryMiB" offer.memory-mib})
     ;; 引き取った行が同じ約束で続けるための写し(worker は読まずに状態の報告へ添えて返す)。
     (if offer.rerun {"rerun" True "attempt" offer.attempt "losses" offer.losses} {})
     (if offer.notify {"notify" offer.notify} {})
     ;; 置き方の優先と場所を譲る名乗り(2026-11-11 — worker の memory の見張りは急ぎの優先の task を後に止める。欄を読まない古い worker は
     ;; 捨てる)。既定の値(優先 0・名乗らない)は欄を置かない。
     (if offer.priority {"priority" offer.priority} {})
     (if offer.preemptible {"preemptible" True} {})))


(defk heartbeat-reply-json [#^ HeartbeatReply reply]
  {:pre [(: reply HeartbeatReply)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "heartbeat の返事 → JSON の形(superseded は退いた世代への返事の時だけ・supplied と compartments は渡す時だけ書く)。"
  (| {"jobs" (! (spelled-each spec-json reply.jobs)) "tasks" (! (spelled-each task-offer-json reply.tasks))
      "warm" (lfor w reply.warm (| {"key" w.key "runtimeEnv" w.runtime-env} (if w.urgent {"urgent" True} {}))) "timing" (asdict reply.timing)
      "draining" reply.draining "formats" (list reply.formats) "revision" reply.revision}
     (if reply.superseded {"superseded" True} {})
     (if reply.handover-pending {"handoverPending" True} {})
     (if (is-not reply.supplied None) {"supplied" (list reply.supplied)} {})
     ;; 持つ区画(ADR-DOE-CLUSTER-005 R2 — 空の列 = 区画を持たない)。欄を読まない古い worker は捨てる。
     (if (is-not reply.compartments None) {"compartments" (lfor h reply.compartments (held-compartment-json h))} {})))


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
  ;; 機体の測った空きを測れた worker ごとに綴る(綴りは効果なので、下の dfor の外で先に綴る)。
  (var measured {})
  (for [w view.workers]
    (when (is-not w.info.measured None)
      (<- row (dump w.info.measured))
      (:= measured (| measured {w.info.name row}))))
  {"now" view.now
   "jobs" (lfor s view.services (| (job-to-json s.job) {"resourceVersion" s.resource-version}))
   ;; memoryMiB = 枠の file の行の常駐の job の memory の予算(名乗った worker だけに載せる)。measured = 機体の測った空き
   ;; (heartbeat の measured の写し {atMs cpus cpuBusy memoryTotalMiB memoryAvailableMiB cpuSomeAvg10 cpuFullAvg10 memorySomeAvg10
   ;; memoryFullAvg10 cpuNice taskCpuSomeAvg10 taskMemoryMiB jobsRoomMiB prepareWaitMiB jobUses jobUsesAtMs} — 測れた worker だけに載せる。
   ;; jobUses = 常駐の job ごとの [{job pid cpuMs rssMiB processes pssMiB pssAtMs bodyAnonymousMiB startedAtMs}])。taskRoom = 置き先の判断と同じ数えで、いま task を置ける数(測った空き
   ;; から — 測りの無い worker は 0)。taskMemoryRoomMiB = 置き先の判断と同じ数えで、いま置ける task の memory の要りの最大(測った空き −
   ;; 床 − 置いた task の「要り − 今の使い」の和 — 負は詰めすぎ)。childEnviron = worker が job の子へ渡す環境変数の名(名の順 — 名乗った worker だけに載せる。名乗らない旧い
   ;; worker に空の列を出さない: 空の列は「子へ何も渡さない」と読まれる)。prepare = 準備の姿(heartbeat の prepare の写し {state sinceMs stage
   ;; reason provides} — 準備の file を渡された worker だけに載せる。done の時も載せる)。
   "workers" (dfor w view.workers
                   w.info.name (| {"provides" (list w.info.provides) "exclusive" (list w.info.exclusive) "derived" (list w.info.derived)
                                   "node" w.info.node "taskRoom" w.task-room "taskMemoryRoomMiB" w.task-memory-room-mib
                                   "silentMs" w.silent-ms
                                   "versions" (dict w.info.versions) "live" w.live "draining" w.draining}
                                  (if (is w.info.memory-mib None) {} {"memoryMiB" w.info.memory-mib})
                                  (if (is w.info.child-environ None) {} {"childEnviron" (list w.info.child-environ)})
                                  (if (is w.info.prepare None) {}
                                      {"prepare" {"state" w.info.prepare.state "sinceMs" w.info.prepare.since-ms
                                                  "stage" w.info.prepare.stage "reason" w.info.prepare.reason
                                                  "provides" (list w.info.prepare.provides)}})
                                  (if (in w.info.name measured) {"measured" (get measured w.info.name)} {})))
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
   "keepMarks" (lfor m view.keep-marks {"job" m.job "worker" m.worker "boot" m.boot "sinceMs" m.since-ms "pinned" m.pinned})
   ;; 組の同時数の上限(2026-10-12): 組の名の順の列 [{group scope limit perPart resourceVersion held queued}] —
   ;; 系が宣言した値と、置いている数の升 held([{worker? part? count}] — 範囲 worker の組だけ worker・区分ごとの組だけ part)と、
   ;; その組を名乗って待っている task の数。運用者が値の割れや詰まりを 1 か所で読むため。
   "groupLimits" (lfor g view.group-limits
                       (| {"group" g.limit.group "resourceVersion" g.resource-version "queued" g.queued
                           "held" (lfor h g.held (| {"count" h.count}
                                                     (if (is h.worker None) {} {"worker" h.worker})
                                                     (if h.part {"part" h.part} {})))}
                          (group-limit-spec g.limit)))
   ;; 置き先の一時の上書き(2026-10-27): Service の名の順の列 [{service needs reason expiresAtMs writer sinceMs}] —
   ;; 宣言の needs の代わりに今効いている needs と、その訳・期限・書き手。宣言の spec は書き換えないので、ここで読む。
   "needsOverrides" (lfor o view.needs-overrides (| {"service" o.service} (needs-override-spec o)))
   ;; 置いた task の申告(2026-11-12 — ADR-DOE-CLUSTER-005 R4): task の id → {memoryMiB cpuMillis} — 置いた拍に決めて、置いている間は
   ;; 変えない量。task の行の memoryMiB は送り手の見当で、heartbeat の返事で worker へ渡すのはこの申告。
   "claims" (dfor c view.claims c.task (task-claim-spec c))
   ;; 置き先の無い Service の読み(2026-10-27): Service の名 → {kind needs missing pin sinceMs}(unplaced と同じ鍵)—
   ;; 種類(no-eligible-worker など)・効く needs・足りない能力(生きていて drain 中でない worker のどれも名乗らない能力)・固定・置き先が
   ;; 無いと最初に見た刻。外の見張りが理由の文を読み解かずに、どの Service がいつから何の能力が足りずに置けないかを読むため。
   "unplacedDetail" (dict (zip (gfor u view.unplaced-detail u.service) (! (spelled-each unplaced-view-json view.unplaced-detail))))
   ;; machine の用途の区画(ADR-DOE-CLUSTER-005 R1・R2): machine の名 → {resourceVersion compartments memoryMiB memoryLimitMiB cpuMillis
   ;; reportedTotalMiB?} — 宣言の区画の列と、その和と、区画を持つ worker が名乗った memory の総量(名乗った worker が在る時だけ)。
   "machineCompartments" (dfor m view.machine-compartments
                               m.row.machine (| {"resourceVersion" m.resource-version}
                                                (machine-compartments-spec m.row)
                                                {"memoryMiB" m.memory-mib "memoryLimitMiB" m.memory-limit-mib "cpuMillis" m.cpu-millis}
                                                (if (is m.reported-total-mib None) {} {"reportedTotalMiB" m.reported-total-mib})))
   ;; 名簿の worker ごとの持つ区画(heartbeat の返事の compartments と同じ写し — 空の列 = 区画を持たない)。
   "workerCompartments" (dfor #(n held) (.items view.worker-compartments) n (lfor h held (held-compartment-json h)))})


(defk unplaced-view-json [#^ UnplacedView view]
  {:pre [(: view UnplacedView)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "置き先の無い Service 1 つの読み → JSON の形 {kind needs missing pin sinceMs memory}(GET /state の unplacedDetail の値と、Service の資源の
   status.unplaced が同じ形を読むため)。kind = UnplacedKind の値(no-eligible-worker・no-room・waiting-previous-holder)・
   memory = worker ごとの memory で置けない訳の文の列(no-room の時)。"
  {"kind" view.kind.value "needs" (list view.needs) "missing" (list view.missing) "pin" view.pin "sinceMs" view.since-ms
   "memory" (list view.memory)})


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
   版 doeffCommit と起動の刻 bootAt は、名乗った worker だけに載せる(同じ Pod の中の版の入れ替えを読む)。"
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
       ;; job を今動かしている process の行に、その行を報告した世代(boot)と、退いた世代の行か(elder — 世代の受け渡しの間)を添える。
       "process" (if (is observed.process None)
                     None
                     (| (status-row-to-json observed.process) {"boot" observed.process-boot "elder" observed.process-elder}))
       "version" (! (version-json observed.version observed.running))
       ;; 置き先が無ければ {kind needs missing pin sinceMs}・置かれていれば null。
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


;; 盤の行ごとの JSON の断片の覚え(盤の行の object の id → #(行 鍵 版なしの断片 版つきの断片 版つきの値) — 2026-11-13)。盤の行は書きの
;; たびに新しい object になり、書かれない行は同じ object のまま盤に残るので、行の object ごとに 1 度だけ綴れば、変わらない行を読みの
;; たびに綴り直さない(GET /board は前置きに合う行を毎回全部返す — 2 万行の読みの綴りが本番の主 thread の 1 割を占めた)。行そのものを
;; 一緒に持つので、持っている間に id が別の object へ使い回されない。上限を越えたら全部を捨てて覚え直す(cluster_policy の
;; _PLACEMENT-ROWS と同じ形)。
(setv _BOARD-SPELLINGS {})
(setv BOARD-SPELLING-MEMO-MAX 131072)


(defn #^ tuple board-row-spelling [#^ str key #^ BoardRow row]  ; cannot be a defk: 覚えの dict を引く(綴りの board-answer-json の材料)
  "盤の行 1 つの JSON の object の断片(鍵: 値 と 鍵: {value resourceVersion} — json.dumps の既定の区切りと同じ綴り)を、行の object ごとに
   1 度だけ綴るため。答え = #(行 鍵 版なしの断片 版つきの断片 版つきの値)。"
  (setv kept (.get _BOARD-SPELLINGS (id row)))
  (when (and (is-not kept None) (is (get kept 0) row) (= (get kept 1) key))
    (return kept))
  (setv versioned {"value" row.value "resourceVersion" row.version}
        head (+ (json.dumps key :ensure-ascii False) ": ")
        spelled #(row key (+ head (json.dumps row.value :ensure-ascii False)) (+ head (json.dumps versioned :ensure-ascii False)) versioned))
  (when (>= (len _BOARD-SPELLINGS) BOARD-SPELLING-MEMO-MAX)
    (.clear _BOARD-SPELLINGS))
  (setv (get _BOARD-SPELLINGS (id row)) spelled)
  spelled)


(defk board-answer-json [#^ (| BoardRead BoardWritten BoardConflict BoardRefused) answer]
  {:pre [(: answer (| BoardRead BoardWritten BoardConflict BoardRefused))] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "盤の口の答え → JSON の形(#2614 の前に api_policy.respond-board と cluster_policy.board-write が組んでいた形と同じ)。読みの答えは、行ごとに
   1 度だけ綴った断片をつないだ文字列を先に持つ SpelledJson(送る byte は json.dumps と同じ — 綴り直さない)。"
  (cond
    (isinstance answer BoardRead)
      (do (setv spellings (lfor #(key row) answer.entries (board-row-spelling key row))
                at (if answer.with-versions 3 2))
          (SpelledJson (dfor s spellings (get s 1) (if answer.with-versions (get s 4) (. (get s 0) value)))
                       (+ "{" (.join ", " (gfor s spellings (get s at))) "}")))
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
    ;; 切り離した task の終わりの出口(読み手の DetachedOutboxWire と同じ形)。
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
