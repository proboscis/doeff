;;; doeff worker の composition root。coordinator から job と task を受け、子 process として管理する。
;;;
;;;   hy -m doeff_cluster.worker.entry.main --coordinator URL --name NAME [--provides a,b] [--exclusive a] --repo REPO --state-dir DIR
;;;     --task-reserve N --env-roots-cap BYTES --env-min-free BYTES
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
(import sys)
(import pathlib [Path])
(import doeff [run with-handlers])
(import doeff_core_effects.handlers [await-handler slog-handler state :as session-store])
(import doeff_core_effects.file_effects [WriteText file-done])
(import doeff_core_effects.os_file [os-file-handler offloaded-tree-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.os_warm_process [os-warm-process-handler])
(import doeff_core_effects.os_random [os-random-handler])
(import doeff_core_effects.process_effects [EnvEntry ReadEnvironment])
(import doeff_core_effects.random_effects [RandomBytes])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [async-time-handler sync-time-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.worker.protocol.stop [stop-flag StopState])
(import doeff_cluster.foundation.coordinator_inbox [stop-on-signals])
(import doeff_cluster.worker.protocol.tick_pauses [tick-pauses])
(import doeff_cluster.worker.protocol.coordinator_link [LinkState coordinator-link])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_cluster.foundation.coordinator_http [REPLY-SECONDS CONNECT-SECONDS PREFERRED-RECHECK-SECONDS RESEND-PAUSE-SECONDS])
(import doeff_cluster.shared.core.resend [IDEMPOTENT-DEADLINE-SECONDS])
(import doeff_cluster.shared.protocol.coordinator_route [RouteCell RouteOptions route-of])
(import doeff_cluster.worker.protocol.lease_release [lease-release])
(import doeff_cluster.foundation.process_versions [this-process-versions clock-ticks])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.shared.core.timing_rules [SelfStopSpans ReassignTooEarly timing-outlasts-the-self-stop])
(import doeff_cluster.shared.core.capabilities [capabilities-of])
(import doeff_cluster.worker.core.program [run-worker])
(import doeff_cluster.worker.intent.worker_model [WorkerPolicy WorkerState CodeLayout])
(import doeff_cluster.worker.core.shim_timing [ShimSpans ShimOutlastsTheKill shim-spans shim-ends-before-the-kill])
(import doeff_cluster.worker.protocol.process_host [HostSettings process-host])
(import doeff_cluster.worker.protocol.warm_host [WarmSettings warm-host])
(import doeff_cluster.worker.core.warm_rules [warm-dir-of])
(import doeff_cluster.worker.protocol.probes [ProbeSettings probe-host])
(import doeff_cluster.worker.protocol.code_store [CodeSettings code-host PREPARE-TOOL])
(import doeff_cluster.worker.protocol.world [local-host])
(import doeff_cluster.worker.protocol.env_store [EnvSettings env-host])
(import doeff_cluster.worker.protocol.process_clock [process-clock])
(import doeff_cluster.worker.core.boot_timing [BOOT-STARTED-VAR BOOT-EXEC-VAR read-boot-marks])
(import doeff_cluster.shared.core.native_wheel [current-platform])
(import doeff_cluster.worker.protocol.status_file [status-file])
(import doeff_cluster.foundation.host_contract [HOST-CONTRACT])
(import doeff_cluster.shared.core.run_context_rules [worker-context-environ])


;; この process の世代を書く Pod の中の file の名(無ければ書かない — readinessProbe が読む)。
(val BOOT-FILE-VAR "DOEFF_WORKER_BOOT_FILE")


(defk boot-file-written [path boot]
  {:pre [(: path (| str None)) (: boot str)] :post [(: % None)] :tags {:context "worker" :role "main"}}
  "この process の世代を Pod の中の file へ書くため(BOOT-FILE-VAR — 無ければ書かない)。readinessProbe が「coordinator の見る worker が
   この Pod の物か」を比べる(drain_client.ready-of)— 同じ node の前の Pod と名が同じなので、名だけでは見分けられない。書きは file の
   effect(置き換えで書く — 読み手が書きかけを見ない)で、答え手は入口が並べる os-file-handler。"
  (when path
    (<- (file-done (WriteText path (+ boot "\n") :replace True))))
  None)


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


(defk production-handlers [host probes link link-cell link-options watch-cell lease-cell status-path codes envs stop warm]
  {:pre [(: host HostSettings) (: probes ProbeSettings) (: link LinkState) (: link-cell RouteCell) (: link-options RouteOptions)
         (: watch-cell RouteCell) (: lease-cell RouteCell) (: status-path str) (: codes CodeSettings) (: envs EnvSettings) (: stop StopState)
         (: warm WarmSettings)]
   :post [(: % list)] :tags {:context "worker" :role "main"}}
  "本番の handler の組(外側が先 — with-handlers の順)。process-host・warm-host・probe-host・code-host・env-host の session の値(子の表・
   待ちの子の表・検めの記録・木と root の準備の記録)は外側の session-store が持つ。status-file は焼きの経過の秒を CodeTimings で問うので、
   code-host はその外側に置く。coordinator-link は状態の報告を受けた後、同じ効果を外側の status-file へ回す。待ちの子へ頼む効果
   (ForkFromWarm・PollWarmChild・SignalWarmChild — process-host が出す)の本物の答え手は os-warm-process-handler(#3646)。木の数えと消し
   (MeasureTree・RemoveTree)は offloaded-tree-handler が thread で待つ — env-host の掃除の task が詰まった disk の上で木を数え・消す間も、
   調整ループ(heartbeat)は回り続ける(#3715)。"
  [(await-handler) (async-time-handler) (http-production-handler) slog-handler (stop-flag stop) subprocess-handler os-warm-process-handler
   os-file-handler offloaded-tree-handler (session-store) (env-host envs) (code-host codes) (status-file status-path)
   (lease-release lease-cell link-options) (coordinator-link link link-cell link-options watch-cell)
   (probe-host probes) (warm-host warm) (process-host host) local-host tick-pauses])


(defk timing-checked [fence-ms policy timing]
  {:pre [(: fence-ms int) (: policy WorkerPolicy) (: timing ClusterTiming)] :post [(: % SelfStopSpans)]
   :tags {:context "worker" :role "main"}}
  "起動の組み立てが時間の不変条件を破るなら名指しで断るため — job を走らせる前に止める。答え = C4 の判じた内訳。
   C4(shared/core/timing_rules・#2806): 移し替え(timing の reassign-after-ms)が、この worker の止め切り(fence + heartbeat の返事の上限 +
   接続の上限 + 子の停止の猶予)より前になる起動(--fence や --stop-grace を長くし過ぎた等)。
   shim の期限(worker/core/shim_timing・#2940): 子の shim の期限(shim の猶予 + 掃除の余裕)が worker の KILL(停止の猶予)より後になる
   起動(--stop-grace を掃除の余裕より短くした等 — shim が子孫を片づける前に worker が shim を殺す)。"
  (val spans (SelfStopSpans :fence-ms fence-ms :reply-ms (int (* REPLY-SECONDS 1000)) :connect-ms (int (* CONNECT-SECONDS 1000))
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
  (.add-argument parser "--capacity" :type int :default 10)
  (.add-argument parser "--task-reserve" :type int :required True
                 :help "capacity のうち task のために空けておく数(0 以上 capacity 以下)— coordinator は常駐の job をこの分に置かない")
  (.add-argument parser "--fence" :type float :default (/ (. (ClusterTiming) fence-ms) 1000)
                 :help "連絡が途絶えて自分の job を止めるまでの秒(最初に coordinator へ届くまで。以後は coordinator の値)")
  (.add-argument parser "--repo" :required True :help "コードを取り出す git repo")
  (.add-argument parser "--state-dir" :required True :help "コードの cache・log・状態の置き場")
  (.add-argument parser "--no-warm" :action "store_true" :help "bytecode の準備を省く")
  (.add-argument parser "--stop-grace" :type float :default 10.0)
  (.add-argument parser "--import-roots" :default "."
                 :help "業務の repo の木の中の import の根(`,` で並べる・前が先)— worker_model.CodeLayout")
  (.add-argument parser "--base-pythonpath" :default ""
                 :help "土台の import の路(機体の絶対 path を `,` で並べる・木の根の後ろ)— worker_model.CodeLayout(pod は空)")
  (.add-argument parser "--repo-keys" :default ""
                 :help "実行環境の task の鍵の表(JSON の file — URL → deploy key の file。表に無い URL は鍵なしで clone する・空 = 鍵を使わない)")
  (.add-argument parser "--uv" :default "uv" :help "実行環境の準備と子の起動に使う uv の命令")
  ;; 実行環境の root の置き場の 2 つの量(#3732 — 既定の値は deploy/boot.sh の 1 か所・ここは必ずの引数)。
  (.add-argument parser "--env-roots-cap" :type int :required True
                 :help "実行環境の roots の合計の上限(byte・hardlink を重ねて数える)— 越えた時だけ固定されていない root を消す")
  (.add-argument parser "--env-min-free" :type int :required True
                 :help "root の置き場の在る共有の disk の空きの最低(byte)— 割った時は root を消さずに準備を disk-full で断る")
  (.add-argument parser "--tools" :default "" :help "この worker が名乗る道具(名=版,… — 実行環境の宣言の tools と照らす)")
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
  ;; task のために空けておく数を起動の時点で検める(coordinator の heartbeat の本文の型と同じ範囲 — 外れた値で名乗り続けて断られない)。
  (when (not (<= 0 args.task-reserve args.capacity))
    (.error parser (.format "--task-reserve {} は 0 以上 --capacity {} 以下で名乗る" args.task-reserve args.capacity)))
  (setv layout (CodeLayout :import-roots (tuple (gfor r (.split args.import-roots ",") :if r r))
                           :base-paths (tuple (gfor p (.split args.base-pythonpath ",") :if p p))))
  (setv state-dir (Path args.state-dir)
        hy-command (str (/ (. (Path sys.executable) parent) "hy"))
        policy (WorkerPolicy :stop-grace-ms (int (* args.stop-grace 1000)))
        ;; 子と検めの shim の時間(猶予と掃除の余裕 — 方針から導く 1 か所・#2940)。破る組は下の timing-checked が断る。
        shim (run (shim-spans policy))
        ;; 版ごとのコードの木の置き場と準備(worker/protocol/code_store の言い換えが読む — #2466)。
        codes (CodeSettings :repo args.repo :cache (str (/ state-dir "code")) :hy-command (if args.no-warm None hy-command) :tool PREPARE-TOOL
                            :layout layout)
        ;; 起動の時に要る機体の環境変数(子へ渡す名・世代の file の名)を在る分だけ 1 度読む(答え手 = subprocess-handler)。
        machine-env (run (with-handlers [subprocess-handler]
                           (machine-environment (+ (run (pass-env-names args.pass-env)) #(BOOT-FILE-VAR BOOT-STARTED-VAR BOOT-EXEC-VAR)))))
        ;; 起動の内訳の刻(Pod の起動・boot.sh の始まり・exec・import の終わり — 最初の heartbeat の答えの後に口が 1 行で出す・#3676)。
        ;; process の始まりは /proc を file の効果で読む(答え手 = os-file-handler と process_clock)。
        boot-marks (run (with-handlers [(sync-time-handler) os-file-handler (process-clock (run (clock-ticks)))]
                          (read-boot-marks machine-env imported-ms)))
        ;; 子 process(service の env)が coordinator と自分の名を知る口。資格は渡さない。
        host-env (| (run (passed-environment args.pass-env machine-env))
                    (run (worker-context-environ args.coordinator args.name)))
        ;; 待ちの子の置き場の根(#3646 — 起こす宿 warm_host と task を分ける宿 process_host が同じ値を読む)。
        warm-dir (run (warm-dir-of (str state-dir)))
        ;; job の子 process の置き場と起こし方(worker/protocol/process_host の言い換えが読む — #2464)。
        host (HostSettings :log-dir (str (/ state-dir "logs")) :jobs-dir (str (/ state-dir "jobs"))
                           :program-dir (str (/ state-dir "programs")) :python sys.executable :hy-command hy-command :uv args.uv
                           :extra-env (tuple (gfor k (sorted host-env) (EnvEntry :name k :value (get host-env k)))) :layout layout
                           :program-env HOST-CONTRACT.program-env :shim shim :warm-dir warm-dir
                           :notice-env HOST-CONTRACT.notice-env)
        ;; root ごとの待ちの子の置き場と起こし方(worker/protocol/warm_host の言い換えが読む — #3646)。
        warm (WarmSettings :warm-dir warm-dir :log-dir (str (/ state-dir "logs")) :uv args.uv)
        ;; 実行環境(runtime env)の root の準備(別の process・worker は再起動しない)。
        envs (EnvSettings :state (str state-dir) :hy-command hy-command :platform (current-platform) :code-prepare PREPARE-TOOL
                          :repo-keys args.repo-keys :uv args.uv :roots-cap-bytes args.env-roots-cap
                          :min-free-bytes args.env-min-free)
        ;; 入口の検め(service の job の木を worker の実行環境で読み込めるか — 起こす前に試す)。
        probes (ProbeSettings :python sys.executable :hy-command hy-command :uv args.uv :layout layout
                              :probe-dir (str (/ state-dir "probe")) :shim shim)
        stop (StopState))
  ;; 時間の不変条件 C4(#2806)と shim の期限(#2940)を破る起動は、job を走らせる前に名指しで断る。
  (run (timing-checked (int (* args.fence 1000)) policy (ClusterTiming)))
  (run (stop-on-signals stop))
  ;; coordinator への口(worker/protocol/coordinator_link — #2427)。拍から拍へ持ち越す値は入れ物 link に、宛先の状態は heartbeat と
  ;; 名指しの待ちと lease の返しで別の入れ物に置く(同じ並び)。送り方は一巡し直さない(前の httpx の client を持つ口と同じ —
  ;; 届かない拍は次の拍で送り直す)。世代(boot)は起動の時に 1 度だけ決め、Pod の中の file に書く(readinessProbe が比べる)。
  (setv started-ms (run (with-handlers [(sync-time-handler)] (now-epoch-ms)))
        boot (run (with-handlers [os-random-handler] (boot-name)))
        link (LinkState args.name provides args.capacity args.task-reserve (int (* args.fence 1000)) (str (/ state-dir "tasks")) boot
                        started-ms started-ms
                        :versions (run (this-process-versions)) :tools (run (parse-labels args.tools)) :handles-envs True :exclusive exclusive
                        :node args.node :boot-marks boot-marks
                        ;; heartbeat を拍から切り離し、desired の変化は名指しの待ちで受ける(#1933 — 待つ口の無い coordinator
                        ;; には拍ごとに送る)。
                        :watch True)
        link-options (RouteOptions :reply-seconds REPLY-SECONDS :connect-seconds CONNECT-SECONDS :resend-deadline-seconds IDEMPOTENT-DEADLINE-SECONDS :resend-pause-seconds RESEND-PAUSE-SECONDS :connect-retries 0
                                   :recheck-ms (int (* PREFERRED-RECHECK-SECONDS 1000)) :actor args.name)
        link-cell (RouteCell (run (route-of args.coordinator started-ms)))
        watch-cell (RouteCell (run (route-of args.coordinator started-ms)))
        lease-cell (RouteCell (run (route-of args.coordinator started-ms))))
  (run (with-handlers [os-file-handler] (boot-file-written (.get machine-env BOOT-FILE-VAR) boot)))
  ;; handler の組を選び(本番の組)、その組の上で worker の Program を回す。
  (setv handlers (run (production-handlers host probes link link-cell link-options watch-cell lease-cell
                                           (str (/ state-dir "status.json")) codes envs stop warm)))
  (print "worker: 起動します" :file sys.stderr :flush True)
  (try
    (run (scheduled (worker-on handlers policy)))
    (finally
      ;; 名指しの待ちの背景の task を止める(worker の終わり — 次の待ちを送らない)。
      (setv link.watch.closing True)))
  (print "worker: 全 job を回収しました" :file sys.stderr :flush True))


(when (= __name__ "__main__")
  (main))
