;;; doeff worker の composition root。coordinator から job と task を受け、子 process として管理する。
;;;
;;;   hy -m doeff_cluster.worker.entry.main --coordinator URL --name NAME [--provides a,b] [--exclusive a] --repo REPO --state-dir DIR
;;;     (--room-file FILE | --task-reserve N) --env-roots-cap BYTES --env-min-free BYTES --code-store DIR --uv-cache DIR
;;;
;;; --provides = この worker が提供する能力の名(`,` で並べる)・--exclusive = 専用の能力(provides の一部 — このどれかを要る job / task
;;; だけを受ける)。置き場所の名ではなく能力を名乗る(ADR-DOE-CLUSTER-001 R4b)。旧い --labels は受け付けない。
;;; worker が job を受けるのは coordinator からだけ — 宣言の file から生の entry と args の job を直に起こす口(旧い --desired)は無い
;;; (job は Program の値 1 つ・ADR-DOE-CLUSTER-001 R1・R7)。
;;;
;;; 入口の組み立ては 2 つに分ける: handler の組を選ぶ(production-handlers — 本番の組)と、その組の上で worker の
;;; Program を回す(worker-on)。模擬の環境(sim/local.hy の worker の世代)は、同じ worker-on を偽の宿の組(sim-host)の上で回す。
(require doeff-hy.macros [deff defk <- val])
(val MODULE-TAGS {:context "worker" :role "main"})
(import argparse)
(import hy)
(import sys)
(import pathlib [Path])
(import doeff [run with-handlers])
(import doeff_core_effects [await-handler slog-handler state :as session-store])
(import doeff_core_effects [FileFailed PathKind PathStat ReadText StatPath WriteText]
        doeff_core_effects._file_effects [file-done])
(import doeff_core_effects [os-file-handler offloaded-tree-handler offloaded-file-handler])
(import doeff_core_effects [subprocess-handler])
(import doeff_core_effects [os-warm-process-handler])
(import doeff_core_effects [os-proc-table-handler])
(import doeff_core_effects [process-exit-handler])
(import doeff_core_effects [os-random-handler])
(import doeff_core_effects [os-signal-stop-handler])
(import doeff_core_effects [EnvEntry ReadEnvironment])
(import doeff_core_effects [RandomBytes])
(import doeff_core_effects [scheduled Spawn])
(import doeff_time [GetMonotonic async-time-handler sync-time-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms monotonic-ms-of])
(import doeff_cluster.worker.protocol.tick_pauses [tick-pauses])
(import doeff_cluster.worker.protocol.worker_wakes [no-wakes])
(import doeff_cluster.worker.protocol.coordinator_link [LinkState coordinator-link lifeline-turn lifeline-settled])
(import doeff_cluster.worker.core.beat_policy [LIFELINE-PERIOD-SECONDS])
(import doeff_cluster.foundation.lifeline [LifelineSettings start-lifeline event-pause])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_cluster.foundation.coordinator_http [CONNECT-SECONDS PREFERRED-RECHECK-SECONDS RESEND-PAUSE-SECONDS])
(import doeff_cluster.shared.core.resend [IDEMPOTENT-DEADLINE-SECONDS])
(import doeff_cluster.shared.protocol.coordinator_route [RouteCell RouteOptions route-of])
(import doeff_cluster.shared.protocol.supplied_values [KUBE-API SERVICE-ACCOUNT-DIR SERVICE-ACCOUNT-TOKEN-FILE])
(import doeff_cluster.foundation.in_cluster_api [SERVICE-ACCOUNT-CA-PATH in-cluster-api-routed])
(import doeff_cluster.worker.protocol.pod_labels [PodLabelCell PodLabelSettings pod-job-labels])
(import doeff_cluster.shared.core.launch_rules [worker-commit-env-name])
(import doeff_cluster.worker.core.room_rules [RoomReading RoomState WorkerRoom room-of-text room-warm-services-of])
(import doeff_cluster.worker.protocol.lease_release [lease-release])
(import doeff_cluster.foundation.process_versions [this-process-versions clock-ticks])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.shared.core.timing_rules [SelfStopSpans ReassignTooEarly timing-outlasts-the-self-stop])
(import doeff_cluster.shared.core.capabilities [capabilities-of])
(import doeff_cluster.worker.core.program [run-worker])
(import doeff_cluster.worker.intent.worker_model [WorkerPolicy WorkerState CodeLayout])
(import doeff_cluster.worker.core.shim_timing [ShimSpans ShimOutlastsTheKill shim-spans shim-ends-before-the-kill])
(import doeff_cluster.worker.protocol.process_host [HostSettings process-host])
(import doeff_cluster.worker.protocol.memory_watch [MemoryWatchSettings MemoryCell memory-watch-loop])
(import doeff_cluster.worker.protocol.supplied_hold [SuppliedHoldSettings SuppliedHoldCell supplied-hold-loop])
(import doeff_cluster.worker.protocol.warm_host [WarmSettings warm-host])
(import doeff_cluster.worker.core.places [worker-places])
(import doeff_cluster.worker.protocol.probes [ProbeSettings probe-host])
(import doeff_cluster.worker.protocol.code_store [CodeSettings code-host PREPARE-TOOL])
(import doeff_cluster.worker.protocol.world [local-host])
(import doeff_cluster.worker.protocol.leaving_observer [leaving-observer])
(import doeff_cluster.worker.protocol.memory_observer [memory-observer])
(import doeff_cluster.worker.protocol.env_store [EnvSettings env-host])
(import doeff_cluster.worker.protocol.process_clock [process-clock])
(import doeff_cluster.worker.core.job_cgroup [CGROUP-ROOT job-cgroup-procs])
(import doeff_cluster.worker.protocol.job_cgroup [job-cgroup-placer])
(import doeff_cluster.worker.core.boot_timing [BOOT-STARTED-VAR BOOT-EXEC-VAR read-boot-marks])
(import doeff_cluster.shared.core.native_wheel [current-platform])
(import doeff_cluster.worker.protocol.status_file [status-file])
(import doeff_cluster.foundation.host_contract [HOST-CONTRACT])
(import doeff_cluster.shared.core.run_context_rules [worker-context-environ])
(import doeff_cluster.worker.entry.line_stamp [WORKER-LOG-ENV-NAMES worker-log-plan process-lines])


;; この process の世代を書く Pod の中の file の名(無ければ書かない — readinessProbe が読む)。
(val BOOT-FILE-VAR "DOEFF_WORKER_BOOT_FILE")
;; 子の job を置く兄弟の cgroup の名(無ければ子を本体と同じ cgroup で起こす — card ki-ed9dca403f58・worker/core/job_cgroup)。
(val JOB-CGROUP-VAR "DOEFF_WORKER_JOB_CGROUP")
;; 常駐の job の shim を置く兄弟の cgroup の名(無ければ置き分けない — card ki-cd8cb84fdcd2・worker/core/job_cgroup の service の類)。
(val SERVICE-CGROUP-VAR "DOEFF_WORKER_SERVICE_CGROUP")
;; 兄弟の cgroup.procs を書く cgroup v2 の見えの根(起動の script の層 — deploy/boot.sh の WORKER_CGROUP_LAYOUT が書ける見えを置く。無ければ
;; /sys/fs/cgroup)。在れば job の子へも渡す(子が自分の子を、機体の配備が宣言した層へ置けるように)。
(val CGROUP-DIR-VAR "DOEFF_WORKER_CGROUP_DIR")
;; 実行環境の service も、その root の待ちの子から分けて起こすか(card ki-de44efdb9aa4 — WorkerPolicy.warm-services)。値は on か off
;; (無ければ枠の file の自分の行の warmServices — card ki-67abc0cf71e9・それも無ければ off)。それ以外の綴りは起動を断る(黙って off に倒れない)。
(val WARM-SERVICES-VAR "DOEFF_WORKER_WARM_SERVICES")
;; この worker の Pod の metadata.uid(worker の雛形が downward API で置く — card ki-a66bb71f4152)。在り、node の名(--node)も在る時だけ、
;; 置かれた常駐の job を自分の Pod の label に写す(worker/protocol/pod_labels)。Pod の外の worker には無い。
(val POD-UID-VAR "POD_UID")


(defk pod-label-parts [pod-uid node]
  {:pre [(: pod-uid (| str None)) (: node str)] :post [(: % (| tuple None))] :tags {:context "worker" :role "main"}}
  "Pod の job の label の答え手(worker/protocol/pod_labels — card ki-a66bb71f4152)の設定と入れ物を、Pod の uid と node の名が揃った
   worker にだけ作るため(答え = #(設定 入れ物)・None = 写さない — Pod の外の worker)。身元と宛先は k8s が Pod に置く ServiceAccount の dir と
   自区画の API server。"
  (if (and pod-uid node)
      #((PodLabelSettings :api KUBE-API :token-file SERVICE-ACCOUNT-TOKEN-FILE
                          :namespace-file (+ SERVICE-ACCOUNT-DIR "/namespace") :node node :pod-uid pod-uid)
        (PodLabelCell))
      None))


(defk boot-file-written [path boot]
  {:pre [(: path (| str None)) (: boot str)] :post [(: % None)] :tags {:context "worker" :role "main"}}
  "この process の世代を Pod の中の file へ書くため(BOOT-FILE-VAR — 無ければ書かない)。readinessProbe が「coordinator の見る worker が
   この Pod の物か」を比べる(drain_client.ready-of)— 同じ node の前の Pod と名が同じなので、名だけでは見分けられない。書きは file の
   effect(置き換えで書く — 読み手が書きかけを見ない)で、答え手は入口が並べる os-file-handler。"
  (when path
    (<- (file-done (WriteText path (+ boot "\n") :replace True))))
  None)


(defn #^ (| bool str) warm-services-of [#^ (| str None) value]  ; defk にできない: 起動の setv の連なり(Program の外)で値を読む
  "WARM-SERVICES-VAR の値を方策の真偽に読むため。無い = 偽・on = 真・off = 偽。それ以外は起動を断る訳の文を返す。"
  (cond
    (is value None) False
    (= value "on") True
    (= value "off") False
    True (.format "{} は on か off で書く: {!r}" WARM-SERVICES-VAR value)))


(defk boot-warm-services [room-file name value]
  {:pre [(: room-file str) (: name str) (: value (| str None))] :post [(: % (| bool str))] :tags {:context "worker" :role "main"}}
  "起動の時に service も待ちの子から分けるかを決めるため(card ki-67abc0cf71e9): 環境変数の値(value — WARM-SERVICES-VAR)が在ればそれ、
   無ければ枠の file(room-file — 空 = 読まない)の自分の行の warmServices、どちらも無ければ偽。枠の file は名の固定した ConfigMap の
   file で、中身を変えても Pod は作り直されない — 版の file の入れ替えで起きる新しい世代が読むので、機体ごとに Pod を作り直さずに入れられる。
   綴りの誤りは起動を断る訳の文を返す。file を読めない時は無いと同じ(枠の読み — boot-room が断るか引数へ戻る)。"
  (var found None)
  (when (and (is value None) room-file)
    (<- text (ReadText room-file))
    (when (isinstance text str)
      (<- read (| bool None str) (room-warm-services-of text name))
      (:= found read)))
  (cond
    (is-not value None) (warm-services-of value)
    (is found None) False
    True found))


(defk boot-room [room-file name capacity task-reserve]
  {:pre [(: room-file str) (: name str) (: capacity (| int None)) (: task-reserve (| int None))] :post [(: % (| WorkerRoom str))]
   :tags {:context "worker" :role "main"}}
  "起動の時に名乗る枠を決めるため(card ki-6828eda880ce): 枠の file(room-file — 空 = 読まない)の自分の行が読めればその値、読めなければ
   引数の値(--capacity は既定 10)。引数の --task-reserve も無ければ起動を断る訳の文を返す — 既定の値で埋めない。file を読めない事は
   最初の heartbeat の前の読み直し(coordinator_link.room-noted)が log に出し、heartbeat の roomFile で名乗る。"
  (var reading None)
  (when room-file
    (<- text (ReadText room-file))
    (:= reading (RoomReading :state RoomState.UNREADABLE :room None :reason (if (isinstance text FileFailed) text.detail "")))
    (when (isinstance text str)
      (<- read RoomReading (room-of-text text name))
      (:= reading read)))
  (cond
    (and (is-not reading None) (is-not reading.room None)) reading.room
    (is-not task-reserve None) (WorkerRoom :capacity (if (is capacity None) 10 capacity) :task-reserve task-reserve)
    (is reading None) "--task-reserve を渡す(--room-file を渡さない時は必ず要る)"
    True (.format "枠の file {} を読めない({}: {})— 読めるようにするか、--task-reserve(と --capacity)を渡す"
                  room-file reading.state.value reading.reason)))


(defk job-cgroup-placed [sibling [root CGROUP-ROOT] [var JOB-CGROUP-VAR]]
  {:pre [(: sibling (| str None)) (: root str) (: var str)] :post [(: % (| str None))] :tags {:context "worker" :role "main"}}
  "子の job を置く兄弟の cgroup の cgroup.procs の path を起動の時に 1 度だけ決めるため(var の値 — JOB-CGROUP-VAR か SERVICE-CGROUP-VAR・
   無ければ None で、子を包まない)。root = 書ける cgroup v2 の見えの根(CGROUP-DIR-VAR)。自分の cgroup は /proc/self/cgroup を file の
   効果で読む(答え手は入口が並べる os-file-handler)。読めない・兄弟が無い起動は断る(ValueError — 子を本体の cgroup で黙って走らせない。
   機体の配備が兄弟を作り忘れた事を起動の失敗で見せる)。"
  (if (is sibling None)
      None
      (do (<- own (| str FileFailed) (ReadText "/proc/self/cgroup"))
          (when (isinstance own FileFailed)
            (raise (ValueError (.format "{} を読めない: {}" own.path own.detail))))
          (<- procs str (job-cgroup-procs own sibling root))
          (<- seen (| PathStat FileFailed) (StatPath procs))
          (when (or (isinstance seen FileFailed) (!= seen.kind PathKind.FILE))
            (raise (ValueError (.format "{}={} の兄弟の cgroup が無い({})— 機体の配備が作る" var sibling procs))))
          procs)))


(defk cgroup-dir-environ [cgroup-dir]
  {:pre [(: cgroup-dir (| str None))] :post [(: % dict)] :tags {:context "worker" :role "main"}}
  "書ける cgroup v2 の見えの根(CGROUP-DIR-VAR)を job の子へ渡す env にするため(card ki-cd8cb84fdcd2 — 子が自分の子を、機体の配備が
   宣言した層へ置けるように)。無ければ渡さない。"
  (if (is cgroup-dir None) {} {CGROUP-DIR-VAR cgroup-dir}))


(defk pass-env-names [names]
  {:pre [(: names str)] :post [(: % tuple)] :tags {:context "worker" :role "main"}}
  "起動の引数 --pass-env(名を `,` で並べる)を、子 process へ渡す環境変数の名の列にするため。"
  (tuple (gfor n (.split names ",") :if (.strip n) (.strip n))))


(defk machine-environment [names]
  {:pre [(: names tuple)] :post [(: % dict)] :tags {:context "worker" :role "main"}}
  "起動の時に要る機体の環境変数(子へ渡す名・版の識別の env のキー・世代の file の名)を、在る分だけ名 → 値で読むため。読みは
   ReadEnvironment の effect で、答え手は入口が並べる subprocess-handler(本物 = os.environ)— 入口の層で os.environ を直に読まない(DOEFF106)。"
  (<- found (ReadEnvironment :names names))
  (dfor entry found entry.name entry.value))


(defk boot-name []
  {:pre [] :post [(: % str)] :tags {:context "worker" :role "main"}}
  "この process の世代の名(16 byte の乱数の 16 進 32 字 — 以前の uuid4 の hex と同じ長さ)を決めるため。乱数は RandomBytes の effect で、
   答え手は入口が並べる os-random-handler。"
  (<- noise (RandomBytes 16))
  (.hex noise))


(defk passed-environment [names environ]
  {:pre [(: names str) (: environ dict)] :post [(: % dict)]}
  "子 process へ渡す worker の環境変数(名を `,` で並べる)— 機体の設定(家や作業場所の path・預かり所の URL)を job に届けるため。
   実行環境の job の子は worker の環境を許可表でしか継がない(handlers.child-environment)ので、機体の設定は worker が名で宣言する。
   名乗った名が worker の環境に無ければ起動を止める(黙って欠いたまま job を走らせない)。資格の値そのものは渡さない(file の path を渡す)。"
  (<- wanted tuple (pass-env-names names))
  (val missing (lfor n wanted :if (not-in n environ) n))
  (when missing
    (raise (ValueError (+ "--pass-env の名が worker の環境に無い: " (.join "," missing)))))
  (dfor n wanted n (get environ n)))


(defk parse-labels [text]
  {:pre [(: text str)] :post [(: % dict)] :tags {:context "worker" :role "main" :reads "env"}}
  "起動の引数 `名=版,…`(--tools)を、heartbeat で名乗る道具の名 → 版にするため。"
  (dict (gfor kv (.split text ",") :if kv (.split kv "=" 1))))


(defk production-handlers [host probes link link-cell link-options watch-cell lease-cell status-path codes envs warm boot-file memory-cell
                           [job-procs None] [pod-labels None]]
  {:pre [(: host HostSettings) (: probes ProbeSettings) (: link LinkState) (: link-cell RouteCell) (: link-options RouteOptions)
         (: watch-cell RouteCell) (: lease-cell RouteCell) (: status-path str) (: codes CodeSettings) (: envs EnvSettings)
         (: warm WarmSettings) (: boot-file (| str None)) (: memory-cell MemoryCell) (: job-procs (| str None))
         (: pod-labels (| tuple None))]
   :post [(: % list)] :tags {:context "worker" :role "main"}}
  "本番の handler の組(外側が先 — with-handlers の順)。process-host・warm-host・probe-host・code-host・env-host の session の値(子の表・
   待ちの子の表・検めの記録・木と root の準備の記録)は外側の session-store が持つ。status-file は焼きの経過の秒を CodeTimings で問うので、
   code-host はその外側に置く。coordinator-link は状態の報告を受けた後、同じ効果を外側の status-file へ回す。待ちの子へ頼む効果
   (ForkFromWarm・PollWarmChild・SignalWarmChild・WriteWarmChildInput — process-host が出す)の本物の答え手は os-warm-process-handler(#3646)で、
   service の子の入力の pipe の書き口の表を session に持つので session-store の内側に置く(card ki-de44efdb9aa4)。木の数えと消しと
   様子つきの歩き(MeasureTree・RemoveTree・StatTree)は offloaded-tree-handler が thread で待つ — env-host の掃除の task が詰まった disk の
   上で木を数え・消す間も、10 万の file の bytecode の保存先を歩く間も、調整ループ(heartbeat)は回り続ける(#3715・card ki-0da6a0186492)。tick が触る同期の file の効果(WriteText・ReadText・StatPath・ListDirectory・MakeDirectory・RenamePath・ReadDiskFree)も offloaded-file-handler が thread で待つ(card ki-97da6e8be47f)。止めの合図(SIGTERM・SIGINT)は核の os-signal-stop-handler が答える — 信号の受け手を
   最初の止めの問いで据え、その箱を session-store に置く(#3871 の単位 3)。Pod の止まりの観測(ObserveLeaving — local-host が問う)は
   leaving-observer が世代の file(boot-file — 無ければ止まりを読まない)の隣の印を、この process の世代(link.boot)と比べて答える(AL-D2p —
   印の書き手と形の module leaving_mark は読まない)。機体の memory の見張りが読む process の表と、止める SignalPid は os-proc-table-handler
   が答える(card ki-7b934e2538f9 — 見張りが問う job の根 ObserveJobRoots は process-host・/proc/meminfo などの読みは offloaded-file-handler)。
   機体の memory の逼迫の観測(ObserveMemoryTight — local-host が問う)は memory-observer が、見張りと分ける入れ物 memory-cell の様子で答える
   (task の起動の見送り — policy の ready-tree-step)。子の job を置く兄弟の cgroup(job-procs — card ki-ed9dca403f58)が在れば、
   job-cgroup-placer を subprocess-handler のすぐ内側に置き、内側の宿が起こす子をすべて兄弟の cgroup へ入れる(本体は残る)。
   Pod の job の label(pod-labels = pod-label-parts の #(設定 入れ物) — card ki-a66bb71f4152)が在れば、pod-job-labels を coordinator-link の
   すぐ内側に置き(拍の宣言の読みの答えを受け取って label に写す)、自区画の API server 宛ての要求だけを区画の CA で検める
   in-cluster-api-routed を http-production-handler のすぐ内側に置く(coordinator への要求は外側の client のまま)。"
  (+
  [await-handler (async-time-handler) (http-production-handler)]
  (if (is pod-labels None) [] [(in-cluster-api-routed KUBE-API SERVICE-ACCOUNT-CA-PATH)])
  [slog-handler subprocess-handler]
  (if (is job-procs None) [] [(job-cgroup-placer job-procs)])
  [os-proc-table-handler
   process-exit-handler
   os-file-handler offloaded-tree-handler offloaded-file-handler (session-store) os-warm-process-handler os-signal-stop-handler no-wakes (env-host envs) (code-host codes) (status-file status-path)
   (lease-release lease-cell link-options) (coordinator-link link link-cell link-options watch-cell)]
  (if (is pod-labels None) [] [(pod-job-labels (get pod-labels 0) (get pod-labels 1))])
  [(probe-host probes) (warm-host warm) (process-host host) (leaving-observer boot-file link.boot) (memory-observer memory-cell) local-host tick-pauses]))


(defk timing-checked [fence-ms policy timing]
  {:pre [(: fence-ms int) (: policy WorkerPolicy) (: timing ClusterTiming)] :post [(: % SelfStopSpans)]
   :tags {:context "worker" :role "main"}}
  "起動の組み立てが時間の不変条件を破るなら名指しで断るため — job を走らせる前に止める。答え = C4 の判じた内訳。
   C4(shared/core/timing_rules・#2806): 移し替え(timing の reassign-after-ms)が、この worker の止め切り(fence + heartbeat の返事の上限 +
   接続の上限 + 子の停止の猶予)より前になる起動(--fence や --stop-grace を長くし過ぎた等)。
   shim の期限(worker/core/shim_timing・#2940): 子の shim の期限(shim の猶予 + 掃除の余裕)が worker の KILL(停止の猶予)より後になる
   起動(--stop-grace を掃除の余裕より短くした等 — shim が子孫を片づける前に worker が shim を殺す)。"
  (val spans (SelfStopSpans :fence-ms fence-ms :reply-ms timing.client-reply-ms :connect-ms (int (* CONNECT-SECONDS 1000))
                              :stop-grace-ms policy.stop-grace-ms :kill-grace-ms policy.kill-grace-ms))
  (<- broken (get tuple #(ReassignTooEarly ...)) (timing-outlasts-the-self-stop timing.reassign-after-ms spans))
  (when broken
    (val b (get broken 0))
    (raise (ValueError (.format "時間の不変条件 C4 を破る起動: 移し替え {} ms が worker の止め切り {} ms(fence {} + 返事の上限 {} + 接続の上限 {} + 停止の猶予 {} + {})より前"
                                b.reassign-ms b.needed-ms spans.fence-ms spans.reply-ms spans.connect-ms spans.stop-grace-ms spans.kill-grace-ms))))
  (<- shim ShimSpans (shim-spans policy))
  (<- late (get tuple #(ShimOutlastsTheKill ...)) (shim-ends-before-the-kill shim))
  (when late
    (val l (get late 0))
    (raise (ValueError (.format "shim の期限が worker の KILL より後になる起動: shim の期限 {} ms(shim の猶予 {} + 掃除の余裕 {})が停止の猶予 {} ms を越える"
                                l.deadline-ms shim.shim-grace-ms shim.sweep-margin-ms l.kill-ms))))
  spans)


(defk worker-on [handlers policy]
  {:pre [(: handlers list) (: policy WorkerPolicy)] :post [(: % WorkerState)] :tags {:context "worker" :role "main"}}
  "handler の組 handlers(外側が先)の上で worker の調整ループ(run-worker)を回すため — 止まれの合図で全 job を回収して終わる。
   組を選ぶのは composition root(本番 = main の production-handlers・模擬の環境 = sim/local.hy の偽の宿)。"
  (<- state WorkerState (with-handlers handlers (run-worker policy)))
  state)


(defk worker-watching-memory [policy watch cell hold link hold-cell]
  {:pre [(: policy WorkerPolicy) (: watch MemoryWatchSettings) (: cell MemoryCell) (: hold (| SuppliedHoldSettings None)) (: link LinkState)
         (: hold-cell SuppliedHoldCell)]
   :post [(: % WorkerState)] :tags {:context "worker" :role "main"}}
  "機体の memory の見張り(worker/protocol/memory_watch)と、値の写しの係(worker/protocol/supplied_hold — hold が在る時だけ・
   ADR-DOE-CLUSTER-004)を調整ループの外の背景の task として起こしてから、調整ループを回すため(card ki-7b934e2538f9)。どちらも同じ
   handler の組の下で走る(job の根は process-host・表は os-proc-table-handler・API server への要求は in-cluster-api-routed が答える)。"
  (<- (Spawn (memory-watch-loop watch cell) :daemon True))
  (when (is-not hold None)
    (<- (Spawn (supplied-hold-loop hold link hold-cell) :daemon True)))
  (<- state WorkerState (run-worker policy))
  state)


(defk worker-on-watching-memory [handlers policy watch cell hold link hold-cell]
  {:pre [(: handlers list) (: policy WorkerPolicy) (: watch MemoryWatchSettings) (: cell MemoryCell) (: hold (| SuppliedHoldSettings None))
         (: link LinkState) (: hold-cell SuppliedHoldCell)]
   :post [(: % WorkerState)] :tags {:context "worker" :role "main"}}
  "本番の組 handlers の上で、機体の memory の見張りと値の写しの係と調整ループを回すため(worker-on の本番の形 — 模擬の環境は見張りも
   係も持たない)。"
  (<- state WorkerState (with-handlers handlers (worker-watching-memory policy watch cell hold link hold-cell)))
  state)


(deff main []  ; defk にできない: console script の main(`hy -m doeff_cluster.worker.entry.main` の __main__ と boot.sh の旧い名の入口が素の関数として呼ぶ)
  {:pre [] :post [(: % None)] :tags {:context "worker" :role "main" :reads "env"}}
  "worker の process の入口: 起動の引数と機体の環境変数から組み立て、本番の handler の組の上で調整ループを回すため。"
  ;; import の終わりの刻(この関数は module の import が済んでから呼ばれる — 起動の内訳の 1 行の 4 番目の刻・#3676)。
  (setv imported-ms (run (with-handlers [(sync-time-handler)] (now-epoch-ms))))
  (setv parser (argparse.ArgumentParser :description "doeff worker(実験)"))
  (.add-argument parser "--coordinator" :required True
                 :help "job を割り当てる coordinator の URL。`,` で並べると前から順に試す(Mac は LAN・tailnet の順)")
  (.add-argument parser "--name" :required True :help "coordinator に名乗る worker の名前")
  (.add-argument parser "--provides" :default "" :help "提供する能力の名(a,b)")
  (.add-argument parser "--exclusive" :default "" :help "専用の能力(provides の一部・a,b)— このどれかを要る仕事だけを受ける")
  (.add-argument parser "--node" :default "" :help "この worker の置かれた k8s の node の名(coordinator が node の label から能力を導く)")
  (.add-argument parser "--labels" :default None :help "受け付けない(旧い形 — --provides / --exclusive で能力を名乗る)")
  (.add-argument parser "--capacity" :type int :default None
                 :help "同時の job と task の上限(--room-file が無ければ既定 10 — 在れば file の値が勝ち、file を読めない起動の時だけ使う)")
  (.add-argument parser "--task-reserve" :type int :default None
                 :help "capacity のうち task のために空けておく数(0 以上 capacity 以下)— coordinator は常駐の job をこの分に置かない。
                        --room-file が無ければ必ず渡す")
  (.add-argument parser "--room-file" :default ""
                 :help "枠の file(JSON — worker の名 → capacity・taskReserve)。渡すと起動の時と heartbeat の前に読み直し、自分の名の行の
                        値を名乗る — 枠の変更で worker を起こし直さない(card ki-6828eda880ce)。空 = 読まない")
  (.add-argument parser "--fence" :type float :default (/ (. (ClusterTiming) fence-ms) 1000)
                 :help "連絡が途絶えて自分の job を止めるまでの秒(最初に coordinator へ届くまで。以後は coordinator の値)")
  (.add-argument parser "--repo" :required True :help "コードを取り出す git repo")
  (.add-argument parser "--state-dir" :required True :help "コードの cache・状態の置き場(世代ごと)")
  (.add-argument parser "--shared-dir" :default None
                 :help "世代をまたいで分け合う置き場(log・Python・Rust の wheel・mirror と錠 — 同じ機体に同じ名の worker の 2 つの世代が並ぶ時・AL-D2。省けば --state-dir)")
  (.add-argument parser "--overlaps-elder" :action "store_true"
                 :help "同じ名の前の世代がまだ走っている間に起きた(起動の script が前の世代の dir の錠を持たれていると見た時に渡す — heartbeat に載せ、coordinator は前の世代の lease を外さない・AL-D2e)")
  (.add-argument parser "--no-warm" :action "store_true" :help "bytecode の準備を省く")
  (.add-argument parser "--stop-grace" :type float :default 10.0)
  (.add-argument parser "--import-roots" :default "."
                 :help "業務の repo の木の中の import の根(`,` で並べる・前が先)— worker_model.CodeLayout")
  (.add-argument parser "--base-pythonpath" :default ""
                 :help "土台の import の路(機体の絶対 path を `,` で並べる・木の根の後ろ)— worker_model.CodeLayout(pod は空)")
  (.add-argument parser "--repo-keys" :default ""
                 :help "実行環境の task の鍵の表(JSON の file — URL → deploy key の file。表に無い URL は鍵なしで clone する・空 = 鍵を使わない)")
  (.add-argument parser "--repo-access" :default ""
                 :help "鍵の表を組み直す起動の script(準備の前ごとに ROLE=access で撃ち、表の file の今の値で組み直す・空 = 組み直さない)")
  (.add-argument parser "--uv" :default "uv" :help "実行環境の準備と子の起動に使う uv の命令")
  ;; 実行環境の root の置き場の 2 つの量(#3732 — 既定の値は deploy/boot.sh の 1 か所・ここは必ずの引数)。
  (.add-argument parser "--env-roots-cap" :type int :required True
                 :help "実行環境の roots の合計の上限(byte・hardlink を重ねて数える)— 越えた時は固定されていない root を古い順に消す")
  (.add-argument parser "--env-min-free" :type int :required True
                 :help "root の dir の在る共有の disk の空きの最低(byte)— 割った時は固定されていない root を古い順に消し、戻らなければ準備を disk-full で断る")
  ;; bytecode の保存先の dir(#3858 — 値は deploy/boot.sh の DOEFF_HY_CODE_STORE の 1 か所・焼く道具は同じ値を環境変数で読む)。
  (.add-argument parser "--code-store" :required True
                 :help "source の中身で引く bytecode の保存先の dir(掃除が 7 日使われない entry を消す・`off` = 保存先を使わない)")
  ;; uv の cache の dir(#3858 — 値は deploy/boot.sh の DOEFF_UV_CACHE_DIR の 1 か所・実行環境の準備の uv の子の UV_CACHE_DIR)。
  (.add-argument parser "--uv-cache" :required True
                 :help "実行環境の準備の uv の子が使う cache の dir(UV_CACHE_DIR・空きが最低を割った時に prune する)")
  (.add-argument parser "--tools" :default "" :help "この worker が名乗る道具(名=版,… — 実行環境の宣言の tools と照らす)")
  (.add-argument parser "--memory-guard" :choices ["on" "off"] :default "on"
                 :help "機体の memory が尽きかけた時に、job の木のいちばん大きい単位を止めるか(off = 測りの log だけ — card ki-7b934e2538f9)")
  (.add-argument parser "--pass-env" :default ""
                 :help "子 process へ渡す worker の環境変数の名(`,` で並べる — 機体の設定の path や URL。無い名は起動を止める)")
  (setv args (.parse-args parser))
  ;; 能力の名乗りを起動の時点で検める(旧い --labels・名として受けられない値・provides の外の exclusive は起動しない)。
  (when (is-not args.labels None)
    (.error parser "旧い --labels は受け付けない — 置き場所の label ではなく、提供する能力を --provides(専用なら --exclusive)で名乗る"))
  (try
    (setv provides (capabilities-of (lfor n (.split args.provides ",") :if n n) "--provides")
          exclusive (capabilities-of (lfor n (.split args.exclusive ",") :if n n) "--exclusive"))
    (except [error ValueError]
      (.error parser (str error))))
  (when (not (<= (set exclusive) (set provides)))
    (.error parser (.format "--exclusive {} は --provides {} の一部で名乗る" (list exclusive) (list provides))))
  ;; 起動の時に名乗る枠(card ki-6828eda880ce): 枠の file が在れば file の自分の行(読めなければ引数 — 両方無ければ起動しない)、
  ;; 無ければ引数。以後の読み直しは coordinator への口(room-noted)。
  (setv room (run (with-handlers [os-file-handler] (boot-room args.room-file args.name args.capacity args.task-reserve))))
  (when (isinstance room str)
    (.error parser room))
  ;; task のために空けておく数を起動の時点で検める(coordinator の heartbeat の本文の型と同じ範囲 — 外れた値で名乗り続けて断られない)。
  (when (not (<= 0 room.task-reserve room.capacity))
    (.error parser (.format "--task-reserve {} は 0 以上 --capacity {} 以下で名乗る" room.task-reserve room.capacity)))
  (setv layout (CodeLayout :import-roots (tuple (gfor r (.split args.import-roots ",") :if r r))
                           :base-paths (tuple (gfor p (.split args.base-pythonpath ",") :if p p))))
  (setv state-dir (Path args.state-dir)
        shared-dir (Path (or args.shared-dir args.state-dir))
        ;; この process の世代(boot)は起動の時に 1 度だけ決める — 置き場(log の名の世代の印)・coordinator への名乗り・Pod の中の file が
        ;; 同じ値を読む。
        boot (run (with-handlers [os-random-handler] (boot-name)))
        ;; 世代ごとの置き場と分け合う置き場と、分け合う log の dir の中の名に入れる世代の印(AL-D2c・AL-D2j — worker/core/places の 1 か所で決める)。
        places (run (worker-places (str state-dir) (str shared-dir) boot))
        hy-command (str (/ (. (Path sys.executable) parent) "hy"))
        ;; job と検めの shim を起こす素の python(card ki-869cdf21ec85)。worker は hy の命令で動くので sys.executable は hy の script
        ;; (hy の cmdline が差し替え、元の interpreter を hy.sys_executable に残す)— shim は .hy を読まないので Hy を読み込まずに起こす
        ;; (shim の import が 1 本 約 7 MB 軽い)。hy の命令を通さずに起きた時は sys.executable が素の python。
        interpreter (getattr hy "sys_executable" sys.executable)
        ;; 版ごとのコードの木の置き場と準備(worker/protocol/code_store の言い換えが読む — #2466)。
        codes (CodeSettings :repo args.repo :cache places.code :hy-command (if args.no-warm None hy-command) :tool PREPARE-TOOL
                            :layout layout)
        ;; この process の doeff の版を持つ環境変数の名(起動の script が自己起動した commit — heartbeat の doeffCommit・card ki-6e83e03d146d)。
        commit-var (run (worker-commit-env-name))
        ;; 起動の時に要る機体の環境変数(子へ渡す名・世代の file の名・自分の行の送り先・自分の版)を在る分だけ 1 度読む(答え手 = subprocess-handler)。
        machine-env (run (with-handlers [subprocess-handler]
                           (machine-environment (+ (run (pass-env-names args.pass-env))
                                                   #(BOOT-FILE-VAR BOOT-STARTED-VAR BOOT-EXEC-VAR JOB-CGROUP-VAR SERVICE-CGROUP-VAR CGROUP-DIR-VAR
                                                     WARM-SERVICES-VAR POD-UID-VAR commit-var)
                                                   WORKER-LOG-ENV-NAMES))))
        ;; service も待ちの子から分けるか(card ki-de44efdb9aa4 — 機体の配備が WARM-SERVICES-VAR か枠の file の自分の行の warmServices で
        ;; 入れる・card ki-67abc0cf71e9・無ければ今までどおり)。
        warm-services (run (with-handlers [os-file-handler]
                             (boot-warm-services args.room-file args.name (.get machine-env WARM-SERVICES-VAR))))
        policy (WorkerPolicy :stop-grace-ms (int (* args.stop-grace 1000))
                             :warm-services (if (isinstance warm-services bool) warm-services (.error parser warm-services)))
        ;; 子と検めの shim の時間(猶予と掃除の余裕 — 方針から導く 1 か所・#2940)。破る組は下の timing-checked が断る。
        shim (run (shim-spans policy))
        ;; 起動の内訳の刻(Pod の起動・boot.sh の始まり・exec・import の終わり — 最初の heartbeat の答えの後に口が 1 行で出す・#3676)。
        ;; process の始まりは /proc を file の効果で読む(答え手 = os-file-handler と process_clock)。
        boot-marks (run (with-handlers [(sync-time-handler) os-file-handler (process-clock (run (clock-ticks)))]
                          (read-boot-marks machine-env imported-ms)))
        ;; 子 process(service の env)が coordinator と自分の名を知る口。資格は渡さない。書ける cgroup の見えの根が在れば子へも渡す。
        host-env (| (run (passed-environment args.pass-env machine-env))
                    (run (worker-context-environ args.coordinator args.name))
                    (run (cgroup-dir-environ (.get machine-env CGROUP-DIR-VAR))))
        ;; 兄弟の cgroup.procs を書く cgroup v2 の見えの根(card ki-cd8cb84fdcd2 — 起動の script の層が置く・無ければ /sys/fs/cgroup)。
        cgroup-root (.get machine-env CGROUP-DIR-VAR CGROUP-ROOT)
        ;; 常駐の job の shim を置く兄弟の cgroup(card ki-cd8cb84fdcd2 — 機体の配備が名を渡した時だけ)。
        service-procs (run (with-handlers [os-file-handler]
                             (job-cgroup-placed (.get machine-env SERVICE-CGROUP-VAR) cgroup-root SERVICE-CGROUP-VAR)))
        ;; 待ちの子の置き場の根(#3646 — 起こす宿 warm_host と task を分ける宿 process_host が同じ値を読む)。
        warm-dir places.warm
        ;; job の子 process の置き場と起こし方(worker/protocol/process_host の言い換えが読む — #2464)。
        host (HostSettings :log-dir places.logs :generation places.generation :jobs-dir places.jobs
                           :program-dir places.programs :python interpreter :hy-command hy-command
                           :extra-env (tuple (gfor k (sorted host-env) (EnvEntry :name k :value (get host-env k)))) :layout layout
                           :program-env HOST-CONTRACT.program-env :shim shim :warm-dir warm-dir
                           :notice-env HOST-CONTRACT.notice-env :service-procs service-procs)
        ;; root ごとの待ちの子の置き場と起こし方(worker/protocol/warm_host の言い換えが読む — #3646)。
        warm (WarmSettings :warm-dir warm-dir :log-dir places.logs :generation places.generation :uv args.uv)
        ;; 実行環境(runtime env)の root の準備(別の process・worker は再起動しない)。
        envs (EnvSettings :state (str state-dir) :hy-command hy-command :platform (current-platform) :code-prepare PREPARE-TOOL
                          :repo-keys args.repo-keys :repo-access args.repo-access :uv args.uv :uv-cache args.uv-cache :roots-cap-bytes args.env-roots-cap
                          :min-free-bytes args.env-min-free
                          ;; bytecode の保存先の dir(掃除が 7 日使われない entry を消す — `off` は保存先を使わない設定)。
                          :code-store (if (= args.code-store "off") None args.code-store)
                          ;; 世代をまたいで分け合う dir(Python・wheel・mirror と錠 — AL-D2)。
                          :shared (str shared-dir))
        ;; 入口の検め(service の job の木を worker の実行環境で読み込めるか — 起こす前に試す)。
        probes (ProbeSettings :python interpreter :hy-command hy-command :layout layout
                              :probe-dir places.probe :shim shim))
  ;; この process の Python の出力(sys.stdout・sys.stderr)の行を、job の log と同じ送り先へも送る(card ki-35923a5895b2 — 機体の上で
  ;; 直に動く worker の log は機体の file にしか無かった)。job の名 worker/<名>・世代 boot で名乗る。送り先が在り、stdout と stderr が
  ;; 同じ file の時だけ差し替え、以後の行は file に刻つきで書く(Pod の worker は今のまま)。fd 1・2 は替えない — 子孫は元の file を継ぐ。
  ;; 終わりは process の終わり — 下の断りの traceback も送る。
  (process-lines (worker-log-plan machine-env args.name boot))
  ;; 時間の不変条件 C4(#2806)と shim の期限(#2940)を破る起動は、job を走らせる前に名指しで断る。
  (run (timing-checked (int (* args.fence 1000)) policy (ClusterTiming)))
  ;; coordinator への口(worker/protocol/coordinator_link — #2427)。拍から拍へ持ち越す値は入れ物 link に、宛先の状態は heartbeat と
  ;; 名指しの待ちと lease の返しで別の入れ物に置く(同じ並び)。送り方は一巡し直さない(前の httpx の client を持つ口と同じ —
  ;; 届かない拍は次の拍で送り直す)。世代(boot — 上で決めた値)は Pod の中の file にも書く(readinessProbe が比べる)。
  ;; 起動の刻は壁の時計と単調な時計の両方で読む(最後の連絡とみなす初めの刻 — 沈黙は 2 つの時計の長い方で計る・card ki-069f4a5c07f7)。
  (setv started-ms (run (with-handlers [(sync-time-handler)] (now-epoch-ms)))
        started-mono-ms (monotonic-ms-of (run (with-handlers [(sync-time-handler)] (GetMonotonic))))
        link (LinkState args.name provides room.capacity room.task-reserve (int (* args.fence 1000)) places.tasks boot
                        started-ms started-ms started-mono-ms
                        :versions (run (this-process-versions)) :tools (run (parse-labels args.tools)) :handles-envs True :exclusive exclusive
                        :node args.node :boot-marks boot-marks :overlaps-elder args.overlaps-elder
                        ;; ServiceAccount の token の古さを heartbeat で名乗る(card ki-d31166f84ad0 — Pod の外の worker は file が無く、名乗らない)。
                        :token-file SERVICE-ACCOUNT-TOKEN-FILE
                        ;; 枠の file を heartbeat の前に読み直す(card ki-6828eda880ce — 空 = 読まない)。
                        :room-file (or args.room-file None)
                        ;; 自分の doeff の版を heartbeat で名乗る(card ki-6e83e03d146d — 無ければ名乗らない)。
                        :doeff-commit (.get machine-env commit-var "")
                        ;; heartbeat を拍から切り離し、desired の変化は名指しの待ちで受ける(#1933 — 待つ口の無い coordinator
                        ;; には拍ごとに送る)。
                        :watch True)
        link-options (RouteOptions :reply-seconds (/ (. (ClusterTiming) client-reply-ms) 1000.0)
                                   :watch-seconds (/ (. (ClusterTiming) watch-max-ms) 1000.0) :connect-seconds CONNECT-SECONDS :resend-deadline-seconds IDEMPOTENT-DEADLINE-SECONDS :resend-pause-seconds RESEND-PAUSE-SECONDS :connect-retries 0
                                   :recheck-ms (int (* PREFERRED-RECHECK-SECONDS 1000)) :actor args.name)
        link-cell (RouteCell (run (route-of args.coordinator started-ms)))
        watch-cell (RouteCell (run (route-of args.coordinator started-ms)))
        lease-cell (RouteCell (run (route-of args.coordinator started-ms))))
  (run (with-handlers [os-file-handler] (boot-file-written (.get machine-env BOOT-FILE-VAR) boot)))
  ;; 機体の memory の見張り(card ki-7b934e2538f9 — 5 秒ごとに job ごとの memory を数え、尽きかけたら大きい単位を止める)の入れ物は、
  ;; 見張りの背景の task と、逼迫の観測の答え手 memory-observer(本番の組)が分けるので、組より先に作る。
  (setv watch (MemoryWatchSettings :guard (= args.memory-guard "on"))
        memory-cell (MemoryCell)
        ;; 子の job を置く兄弟の cgroup(card ki-ed9dca403f58 — 機体の配備が名を渡した時だけ)。
        job-procs (run (with-handlers [os-file-handler] (job-cgroup-placed (.get machine-env JOB-CGROUP-VAR) cgroup-root)))
        ;; 置かれた常駐の job を自分の Pod の label に写す(card ki-a66bb71f4152 — Pod の uid と node の名が在る worker だけ)。
        pod-labels (run (pod-label-parts (.get machine-env POD-UID-VAR) args.node))
        ;; 値の写しの係(ADR-DOE-CLUSTER-004 — 区画の CA で API server を検める口 in-cluster-api-routed を組む Pod の worker だけ。Pod の外の
        ;; worker は ServiceAccount を持たず、API server から値を読まない)。
        hold (if (is pod-labels None) None (SuppliedHoldSettings :worker args.name))
        hold-cell (SuppliedHoldCell))
  ;; handler の組を選び(本番の組)、その組の上で worker の Program を回す。
  (setv handlers (run (production-handlers host probes link link-cell link-options watch-cell lease-cell
                                           places.status codes envs warm (.get machine-env BOOT-FILE-VAR) memory-cell
                                           :job-procs job-procs :pod-labels pod-labels)))
  ;; 生存の合図の thread(card ki-9b58446e2ad6 — scheduler の外の OS の thread)。拍の側の送りが送信間隔 × 1.5 を過ぎても来ない時に、
  ;; 拍が最後に送った本文を送り直す(判断は coordinator への口の lifeline-turn)。返事の上限は生存の窓の半分(遅い返事を待つ間に次の
  ;; 送りを始められなくならない)。時計の読みは引数なしの sync-time-handler(拍と同じ機体の時計)。
  (setv stop-lifeline (run (start-lifeline (LifelineSettings :turn (fn [] (lifeline-turn link))
                                                             :settle (fn [status detail] (lifeline-settled link status detail))
                                                             :handlers [(sync-time-handler)] :period-seconds LIFELINE-PERIOD-SECONDS
                                                             :reply-seconds (/ (. (ClusterTiming) lease-ms) 2000.0)
                                                             :connect-seconds CONNECT-SECONDS :headers {"X-Actor" args.name}
                                                             :pause event-pause :transport None))))
  ;; 世代(boot)と log の名の世代の印を名乗る — log の読み手が、分け合う log の dir の file をこの process の物と見分ける(AL-D2j)。
  (print (.format "worker: 起動します boot={} log-generation={}" boot places.generation) :file sys.stderr :flush True)
  (try
    (run (scheduled (worker-on-watching-memory handlers policy watch memory-cell hold link hold-cell)))
    (finally
      ;; 名指しの待ちと拍の外の送りと memory の見張りの背景の task を止める(worker の終わり — 次の待ちも heartbeat も読みも送らない)。
      ;; 生存の合図の thread も止める(全 job を回収した worker の lease を延ばさない)。
      (setv link.watch.closing True link.beating.closing True memory-cell.closing True hold-cell.closing True)
      (stop-lifeline)))
  (print "worker: 全 job を回収しました" :file sys.stderr :flush True))


(when (= __name__ "__main__")
  (main))
