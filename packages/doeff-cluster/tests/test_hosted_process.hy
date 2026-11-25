;; 預かりの process(shared/intent/hosted_model — worker が job の process の木の外に起こす process)を、本物の process で確かめる。
;; 宿は worker/protocol/hosted_host の hosted-host、口は worker/entry/hosted_socket と foundation/line_socket(本物の unix socket)、
;; job は本物の入れ物 shim(worker/entry/shim — PR_SET_CHILD_SUBREAPER で job の子孫を全部片づける)の下の python。範囲は Linux の worker。
;;
;; 筋書き 1: job が口で預かりの process を起こし、自分の子としても同じ命令を起こす(直す前の形)。job を KILL すると、shim は job の子を
;;          片づけるが、預かりの process は残る。後から起きた同じ持ち主の job(入れ替えの新しい process)は、口の一覧で同じ id を見る。
;;          口の 1 往復(起こすの答えまで)の秒も job の中で測る。
;; 筋書き 2: worker の起き直し — 前の宿の表を持たない新しい宿が、置き場の dir を読んで覚え直し、止める頼みで止めて置き場を消す。
;; 筋書き 3: 持ち主不在の止め — 宣言から消えた持ち主の process は止めて置き場を消し、宣言に在る持ち主・退いた process が残る持ち主の
;;          process は残す。退いた世代の宣言では止めない。層を頼んだ process はその層の cgroup.procs へ移ってから起きる。
;;
;; 宿の外側の答え手は、本番の worker の組(worker/entry/main の production-handlers が返す組)をそのまま使う。差し替えるのは外の世界に
;; 触れる拍の宣言の読み(coordinator-link)と世界の観測(local-host)の答えだけで、宿のすぐ外側に代役を挟む。組を検の中で並べ直すと、
;; 本番の組に無い答え手(前は宿の id の乱数 RandomBytes の答え手)を検が補ってしまい、欠けが見えない。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "program"})
(import json)
(import os)
(import shutil)
(import signal)
(import sys)
(import pathlib [Path])
(import doeff [Program with-handlers])
(import doeff_core_effects [scheduled Spawn])
(import doeff_core_effects [os-proc-table-handler])
(import doeff_core_effects [FileFailed PathKind PathStat ReadText StatPath])
(import doeff_core_effects [EnvEntry EnvMode PollProcess ProcessExited ProcessNotChild ProcessRunning ProcessStarted ProcessNotStarted StartProcess
                            StopProcess ReadProcTable])
(import doeff_time [Delay])
(import doeff_cluster [JobSpec])
(import doeff_cluster._shared.intent.hosted_model [HostedLaunch HostedProcess HostedRefused LaunchHosted ListHosted StopHosted])
(import doeff_cluster._worker.intent.worker_model [CodeLayout DesiredJobs ProcessView ReadDesired ObserveWorld WorkerPolicy WorldView])
(import doeff_cluster._worker.core.places [WorkerPlaces worker-places])
(import doeff_cluster._worker.core.shim_timing [ShimSpans shim-spans])
(import doeff_cluster._worker.protocol.coordinator_link [LinkState])
(import doeff_cluster._worker.protocol.code_store [CodeSettings PREPARE-TOOL])
(import doeff_cluster._worker.protocol.env_store [EnvSettings])
(import doeff_cluster._worker.protocol.memory_watch [MemoryCell])
(import doeff_cluster._worker.protocol.probes [ProbeSettings])
(import doeff_cluster._worker.protocol.process_host [HostSettings])
(import doeff_cluster._worker.protocol.warm_host [WarmSettings])
(import doeff_cluster._shared.protocol.coordinator_route [CoordinatorRoute RouteCell RouteOptions route-of])
(import doeff_cluster._foundation.host_contract [HOST-CONTRACT])
(import doeff_cluster._worker.entry.main [production-handlers])
(import doeff_cluster._worker.core.hosted_rules [hosted-root-of hosted-socket-of hosted-socket-dir-of])
(import doeff_cluster._worker.protocol.hosted_host [HostedCell HostedSettings])
(import doeff_cluster._worker.entry.hosted_socket [hosted-serve-loop])
(import doeff_cluster._foundation.line_socket [LineInbox open-line-inbox])
(import pytest)

;; この file の待ちは実時間が対象(何が対象かは名簿 tests/realtime_tests.toml のこの file の行)。
(val pytestmark pytest.mark.realtime)

(val LINUX (.startswith sys.platform "linux"))
(val NOT-LINUX "job の子孫の片づけ(PR_SET_CHILD_SUBREAPER)と /proc は Linux だけ")
(val SHIM "doeff_cluster._worker.entry.shim")
;; 口の 1 往復(起こすの答えまで)の上限(ms)。
(val ROUND-TRIP-LIMIT-MS 500)
;; 本物の process を待つ上限(秒)。
(val WAIT-SECONDS 30)
;; 検の止めの猶予(短く — 本番の値を写さない)。
(val GRACE-MS 1000)

;; job の本体(素の python — 頼み手の口の綴りを標準 library だけで書く。cli-shim を起こす側と同じ立場): argv = 口の path・答えを書く
;; file・持ち主の名・op(launch か list)。launch は自分の子として眠る命令も起こし(直す前の形)、口へ同じ命令を頼む。答えの行と往復の
;; ms と自分の pid と自分の子の pid を file に書いてから眠り続ける(KILL を待つ)。list は一覧の答えを書いて終わる。
(val JOB-SCRIPT
  (.join "\n"
    ["import json, os, socket, subprocess, sys, time"
     "path, out, owner, op = sys.argv[1:5]"
     "child = subprocess.Popen(['sleep', '300']) if op == 'launch' else None"
     "request = {'v': 1, 'op': op}"
     "if op == 'launch':"
     "    request.update(argv=['sleep', '300'], env={'PATH': os.environ.get('PATH', '')}, owner=owner)"
     "began = time.monotonic()"
     "conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)"
     "conn.connect(path)"
     "conn.sendall((json.dumps(request) + '\\n').encode())"
     "data = b''"
     "while b'\\n' not in data:"
     "    chunk = conn.recv(65536)"
     "    if not chunk:"
     "        break"
     "    data += chunk"
     "ms = (time.monotonic() - began) * 1000"
     "body = {'answer': json.loads(data.split(b'\\n')[0]), 'ms': ms, 'job': os.getpid(), 'inTree': None if child is None else child.pid}"
     "with open(out + '.tmp', 'w') as handle:"
     "    json.dump(body, handle)"
     "os.rename(out + '.tmp', out)"
     "if op == 'launch':"
     "    time.sleep(600)"]))


(defclass Beat []
  "検の拍の答え(宣言と世界)の入れ物 — 筋書きの途中で替える。"
  (defn __init__ [self desired world]
    "宣言 desired と世界 world で始めるため。"
    (setv self.desired desired self.world world)))


(defhandler stub-beat [#^ Beat beat]
  "拍の宣言の読みと世界の観測に、入れ物の今の値で答える(宿 hosted-host の外側 — 本番の coordinator-link と local-host の代役)。"
  {:tags {:context "doeff-cluster-test" :role "entry"}}
  (ReadDesired [env-report stopping]
    (resume beat.desired))
  (ObserveWorld []
    (resume beat.world)))


(defk production-set [tmp hosted cell beat]
  {:pre [(: tmp Path) (: hosted HostedSettings) (: cell HostedCell) (: beat Beat)] :post [(: % list)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "本番の worker の組(production-handlers — 置き場は tmp の下・coordinator は届かない宛先)を作り、宿(組の一番内側)のすぐ外側に
   拍の代役 stub-beat を挟むため。組の残りは本番のまま — 宿が出す効果は本番と同じ答え手が答える。"
  (val state (str (/ tmp "worker")))
  (<- places WorkerPlaces (worker-places state state hosted.generation))
  (val policy (WorkerPolicy))
  (<- shim ShimSpans (shim-spans policy))
  (val layout (CodeLayout))
  (val hy-command (str (/ (. (Path sys.executable) parent) "hy")))
  (<- route CoordinatorRoute (route-of "http://127.0.0.1:9" 0))
  (val link (LinkState "hosted-test" #() 10000 places.tasks hosted.generation 0 0 0))
  (<- handlers list
      (production-handlers (HostSettings :log-dir places.logs :generation places.generation :jobs-dir places.jobs
                                         :program-dir places.programs :python sys.executable :hy-command hy-command :extra-env #()
                                         :layout layout :program-env HOST-CONTRACT.program-env :notice-env HOST-CONTRACT.notice-env
                                         :shim shim :warm-dir places.warm)
                           (ProbeSettings :python sys.executable :hy-command hy-command :layout layout :probe-dir places.probe :shim shim)
                           link (RouteCell route) (RouteOptions :reply-seconds 1.0 :watch-seconds 0.5 :connect-seconds 0.5 :connect-retries 0
                                                                       :recheck-ms 1000 :actor "hosted-test" :resend-deadline-seconds 1.0
                                                                       :resend-pause-seconds 0.1)
                           (RouteCell route) (RouteCell route) places.status
                           (CodeSettings :repo state :cache places.code :hy-command None :tool PREPARE-TOOL :layout layout)
                           (EnvSettings :state state :hy-command hy-command :platform "test" :code-prepare PREPARE-TOOL
                                        :roots-cap-bytes 0 :uv-cache state)
                           (WarmSettings :warm-dir places.warm :log-dir places.logs :generation places.generation :uv "uv")
                           None (MemoryCell) :hosted hosted :hosted-cell cell))
  (+ (cut handlers -1) [(stub-beat beat) (get handlers -1)]))


(defk on-real-host [tmp hosted cell program [beat None]]
  {:pre [(: tmp Path) (: hosted HostedSettings) (: cell HostedCell) (: program Program) (: beat (| Beat None))]
   :post [(: % (| tuple dict HostedProcess HostedRefused))] :tags {:context "doeff-cluster-test" :role "entry"}}
  "Program を本番の worker の組(宿は一番内側)の下で回すため — 子 process・process の表・file・乱数は本番の答え手が答える。"
  (<- handlers list (production-set tmp hosted cell (if (is beat None) (Beat None (WorldView :codes #() :processes #())) beat)))
  (<- answer (scheduled (with-handlers handlers program)))
  answer)


(defk settings-of [tmp [layers None]]
  {:pre [(: tmp Path) (: layers (| dict None))] :post [(: % HostedSettings)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "検の宿の設定(置き場の根は tmp の下)。"
  (<- root str (hosted-root-of (str tmp)))
  (HostedSettings :root root :generation "0123456789ab" :layers layers :stop-grace-ms GRACE-MS))


(defk live-pids []
  {:pre [] :post [(: % frozenset)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "今 生きている(終わって回収を待つ process を除く)pid の集まりを読むため。"
  (<- rows tuple (ReadProcTable))
  (frozenset (gfor row rows row.pid)))


(defk waited-json [path]
  {:pre [(: path str)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "job が書く答えの file が現れるまで待って読むため(上限 WAIT-SECONDS — 越えたら名指して落とす)。"
  (for [_ (range (* WAIT-SECONDS 20))]
    (<- text (| str FileFailed) (ReadText path))
    (when (isinstance text str)
      (return (json.loads text)))
    (<- (Delay 0.05)))
  (<- log (| str FileFailed) (ReadText (+ path ".log")))
  (raise (AssertionError (.format "{} が {} 秒の内に現れない — job の出力: {}" path WAIT-SECONDS log))))


(defk ended-code [pid]
  {:pre [(: pid int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "起こした子の終わりを待って終わりの code を返すため(上限 WAIT-SECONDS)。"
  (for [_ (range (* WAIT-SECONDS 20))]
    (<- polled (| ProcessRunning ProcessExited ProcessNotChild) (PollProcess pid))
    (when (isinstance polled ProcessExited)
      (return polled.exit-code))
    (<- (Delay 0.05)))
  (raise (AssertionError (.format "子 {} が {} 秒の内に終わらない" pid WAIT-SECONDS))))


(defk job-started [inbox out owner op]
  {:pre [(: inbox LineInbox) (: out str) (: owner str) (: op str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "job の本体を本物の shim の下に起こし、shim の pid を返すため(shim は標準入力の EOF を worker の消失と読むので、標準入力を握る)。"
  (<- started (| ProcessStarted ProcessNotStarted)
      (StartProcess :argv #(sys.executable "-m" SHIM "0.5" "--" sys.executable "-c" JOB-SCRIPT inbox.path out owner op)
                    :env #() :env-mode EnvMode.EXTEND :process-group True :hold-stdin True :stdout-path (+ out ".log") :stderr-path (+ out ".log")))
  (assert (isinstance started ProcessStarted) started)
  started.pid)


(defk job-end-and-handoff [inbox tmp]
  {:pre [(: inbox LineInbox) (: tmp Path)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き 1: 口の task を起こし、job 1 が口で預かりの process を起こす → job 1 を KILL して shim の終わりを待つ → 同じ持ち主の job 2 が
   口で一覧を読む。答え = #(job 1 の答えの file・job 2 の答えの file・shim 1 の終わりの code・job 2 の後に生きている pid)。"
  (<- (Spawn (hosted-serve-loop inbox) :daemon True))
  (val first (str (/ tmp "first.json")))
  (<- shim int (job-started inbox first "turn-host" "launch"))
  (<- got dict (waited-json first))
  (os.kill (get got "job") signal.SIGKILL)
  (<- code int (ended-code shim))
  (val second (str (/ tmp "second.json")))
  (<- shim2 int (job-started inbox second "turn-host" "list"))
  (<- listed dict (waited-json second))
  (<- (ended-code shim2))
  (<- live frozenset (live-pids))
  ;; 片づけ: 預かりの process を止める頼み(宿へ直に — 同じ scheduler の口へ頼むと答えを待って詰まる)。
  (<- stopped (| HostedProcess HostedRefused) (StopHosted (get (get (get got "answer") "process") "id")))
  #(got listed code live stopped))


(deftest test-a-hosted-process-outlives-the-job-end-and-a-handoff [tmp-path]
  {:skip-if (not LINUX) :skip-reason NOT-LINUX}
  (<- settings HostedSettings (settings-of tmp-path))
  (<- socket str (hosted-socket-of settings.root settings.generation))
  (<- socket-dir str (hosted-socket-dir-of settings.root))
  (val inbox (open-line-inbox socket))
  (try
    (val got (! (on-real-host tmp-path settings (HostedCell) (job-end-and-handoff inbox tmp-path))))
    (val first (get got 0))
    (val listed (get got 1))
    (val code (get got 2))
    (val live (get got 3))
    (val stopped (get got 4))
    (val process (get (get first "answer") "process"))
    ;; 直す前の形(job の子として起こした命令)は shim が片づけて居ない。
    (assert (not-in (get first "inTree") live) (.format "job の子 {} が shim の片づけの後も生きている" (get first "inTree")))
    ;; 預かりの process は job の KILL と shim の終わりの後も生きていて、同じ持ち主の次の job が同じ id で一覧に見る。
    (assert (in (get process "pid") live) (.format "預かりの process {} が job の終わりで止まった" process))
    (val seen (lfor p (get (get listed "answer") "processes") :if (= (get p "id") (get process "id")) p))
    (assert (= (len seen) 1) listed)
    (assert (= (get (get seen 0) "pid") (get process "pid")) seen)
    (assert (not (.get (get seen 0) "ended" False)) seen)
    (assert (= (get process "owner") "turn-host") process)
    (assert (!= code 0) code)
    ;; 口の 1 往復(接続から起こすの答えまで)。
    (print (.format "hosted launch round trip: {:.1f} ms" (get first "ms")))
    (assert (<= (get first "ms") ROUND-TRIP-LIMIT-MS) (.format "口の往復が {:.1f} ms" (get first "ms")))
    ;; 止める頼みで止まり、置き場が消える。
    (assert (isinstance stopped HostedProcess) stopped)
    (assert stopped.ended stopped)
    (assert (not (.exists (Path (get process "place")))) process)
    (finally
      (.close inbox)
      (shutil.rmtree socket-dir :ignore-errors True))))


(defk launched-one [owner [layer None]]
  {:pre [(: owner str) (: layer (| str None))] :post [(: % (| HostedProcess HostedRefused))] :tags {:context "doeff-cluster-test" :role "program"}}
  "眠る命令を預かりの process として 1 本起こすため。"
  (<- view (| HostedProcess HostedRefused)
      (LaunchHosted (HostedLaunch :argv #("sleep" "300") :owner owner :layer layer
                                  :env #((EnvEntry :name "PATH" :value (os.environ.get "PATH" ""))))))
  view)


(defk adopted-and-stopped [id]
  {:pre [(: id str)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き 2 の後半(新しい宿): 一覧で覚え直した 1 本を読み、止める頼みで止める。答え = #(一覧 止めた答え 止めた後の一覧)。"
  (<- listed (| tuple HostedRefused) (ListHosted))
  (<- stopped (| HostedProcess HostedRefused) (StopHosted id))
  (<- after (| tuple HostedRefused) (ListHosted))
  #(listed stopped after))


(deftest test-a-restarted-worker-adopts-a-hosted-process-from-its-place-and-stops-it [tmp-path]
  {:skip-if (not LINUX) :skip-reason NOT-LINUX}
  (<- settings HostedSettings (settings-of tmp-path))
  (val first (! (on-real-host tmp-path settings (HostedCell) (launched-one "turn-host"))))
  (assert (isinstance first HostedProcess) first)
  ;; 起き直した worker: 表を持たない新しい宿(新しい入れ物)。
  (val again (! (on-real-host tmp-path settings (HostedCell) (adopted-and-stopped first.id))))
  (val listed (get again 0))
  (val stopped (get again 1))
  (val after (get again 2))
  (assert (= (tuple (gfor p listed #(p.id p.pid p.owner p.ended))) #(#(first.id first.pid "turn-host" False))) listed)
  (assert (isinstance stopped HostedProcess) stopped)
  (assert stopped.ended stopped)
  (assert (= after #()) after)
  (assert (not (.exists (Path first.place))) first)
  (<- final frozenset (with-handlers [os-proc-table-handler] (live-pids)))
  (assert (not-in first.pid final) first))


(defk swept-until [beat id-gone seconds]
  {:pre [(: beat Beat) (: id-gone str) (: seconds int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "拍(宣言の読みと世界の観測)を回し、id-gone が一覧から消えるまで待つため。答え = 消えた後の一覧。"
  (for [_ (range (* seconds 20))]
    (<- (ReadDesired))
    (<- (ObserveWorld))
    (<- listed tuple (ListHosted))
    (when (not (any (gfor p listed (= p.id id-gone))))
      (return listed))
    (<- (Delay 0.05)))
  (raise (AssertionError (.format "持ち主の居ない {} が {} 秒の内に止まらない" id-gone seconds))))


(defk owner-absent-scene [beat procs]
  {:pre [(: beat Beat) (: procs str)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き 3: 持ち主の違う 3 本(宣言に在る kept・退いた process だけが残る old・宣言から消えた gone)と、知らない層の頼みを起こし、拍を
   回して gone だけが止まる事、退いた世代の宣言では止めない事、old の退いた process が終わると old も止まる事を見る。"
  (<- kept HostedProcess (launched-one "kept" "clis"))
  (<- old HostedProcess (launched-one "old"))
  (<- gone HostedProcess (launched-one "gone"))
  (<- refused (| HostedProcess HostedRefused) (launched-one "kept" "nope"))
  (<- placed str (ReadText procs))
  ;; 退いた世代の宣言(superseded)では止めない。
  (val retired (ProcessView :name "old#retired-0123" :spec (JobSpec :name "old" :entry "e" :args #() :revision "r") :attempt 1 :pid 1
                            :started-ms 0 :retired-from "old"))
  (setv beat.desired (DesiredJobs :jobs #((JobSpec :name "kept" :entry "e" :args #() :revision "r")) :superseded True)
        beat.world (WorldView :codes #() :processes #(retired)))
  (<- (ReadDesired))
  (<- (ObserveWorld))
  (<- (Delay 0.3))
  (<- (ObserveWorld))
  (<- held tuple (ListHosted))
  ;; 宣言を読めた拍: gone だけが止まる。
  (setv beat.desired (DesiredJobs :jobs #((JobSpec :name "kept" :entry "e" :args #() :revision "r"))))
  (<- after-gone tuple (swept-until beat gone.id WAIT-SECONDS))
  ;; old の退いた process が終わった: old も止まる。
  (setv beat.world (WorldView :codes #() :processes #()))
  (<- after-old tuple (swept-until beat old.id WAIT-SECONDS))
  (<- live frozenset (live-pids))
  (<- (StopHosted kept.id))
  {"kept" kept "old" old "gone" gone "refused" refused "placed" placed "held" held "afterGone" after-gone "afterOld" after-old
   "live" live})


(deftest test-the-worker-stops-a-hosted-process-whose-owner-is-gone-and-places-it-in-its-layer [tmp-path]
  {:skip-if (not LINUX) :skip-reason NOT-LINUX}
  ;; 層の cgroup.procs の代役(素の file — 置き方の sh が自分の pid を書く)。
  (val procs (str (/ tmp-path "clis.procs")))
  (.write-text (Path procs) "")
  (<- settings HostedSettings (settings-of tmp-path {"clis" procs}))
  (val beat (Beat None (WorldView :codes #() :processes #())))
  (val seen (! (on-real-host tmp-path settings (HostedCell) (owner-absent-scene beat procs) beat)))
  (val kept (get seen "kept"))
  (val old (get seen "old"))
  (val gone (get seen "gone"))
  ;; 層: kept は層の cgroup.procs へ自分の pid を書いてから起きた。知らない層は断る。
  (assert (= (.split (get seen "placed")) [(str kept.pid)]) seen)
  (assert (= kept.layer "clis") kept)
  (assert (isinstance (get seen "refused") HostedRefused) seen)
  ;; 退いた世代の宣言の間は 3 本とも残る。
  (assert (= (sorted (gfor p (get seen "held") p.id)) (sorted [kept.id old.id gone.id])) seen)
  ;; 宣言から消えた gone は止まり、置き場が消える。kept と old は残る。
  (assert (= (sorted (gfor p (get seen "afterGone") p.id)) (sorted [kept.id old.id])) seen)
  (assert (not (.exists (Path gone.place))) gone)
  ;; 退いた process も終わった old は止まる。kept は生きている。
  (assert (= (tuple (gfor p (get seen "afterOld") p.id)) #(kept.id)) seen)
  (assert (not (.exists (Path old.place))) old)
  (assert (in kept.pid (get seen "live")) seen)
  (assert (not-in gone.pid (get seen "live")) seen)
  (assert (not-in old.pid (get seen "live")) seen))


(defk ended-view [id seconds]
  {:pre [(: id str) (: seconds int)] :post [(: % HostedProcess)] :tags {:context "doeff-cluster-test" :role "program"}}
  "一覧の id の 1 本が終わるまで待って、その姿を返すため(上限 seconds 秒)。"
  (for [_ (range (* seconds 20))]
    (<- listed tuple (ListHosted))
    (val seen (lfor p listed :if (and (= p.id id) p.ended) p))
    (when seen
      (return (get seen 0)))
    (<- (Delay 0.05)))
  (raise (AssertionError (.format "預かりの {} が {} 秒の内に終わらない" id seconds))))


(defk inner-layer-scene []
  {:pre [] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き 4(子に controller を配る層): 層そのものへの頼みは起こす前に断られ、子を名指す頼みは子の cgroup.procs へ移ってから起き、
   子の cgroup.procs へ書けない頼みは命令を走らせずに終わって、移る先と断りの文が一覧の detail に載る。"
  (<- direct (| HostedProcess HostedRefused) (launched-one "turn-host" "clis"))
  (<- placed HostedProcess
      (LaunchHosted (HostedLaunch :argv #("sleep" "300") :owner "turn-host" :layer "clis" :subgroup "c-one"
                                  :env #((EnvEntry :name "PATH" :value (os.environ.get "PATH" ""))))))
  (<- broken HostedProcess
      (LaunchHosted (HostedLaunch :argv #("sleep" "300") :owner "turn-host" :layer "clis" :subgroup "c-broken"
                                  :env #((EnvEntry :name "PATH" :value (os.environ.get "PATH" ""))))))
  (<- unplaced HostedProcess (ended-view broken.id WAIT-SECONDS))
  (<- (StopHosted placed.id))
  (<- (StopHosted broken.id))
  {"direct" direct "placed" placed "unplaced" unplaced})


(deftest test-a-layer-that-hands-cpu-to-children-places-a-hosted-process-only-in-a-named-child [tmp-path]
  {:skip-if (not LINUX) :skip-reason NOT-LINUX}
  ;; 層 clis の代役: 子に cpu を配る cgroup(cgroup.subtree_control = cpu)。子 c-broken の cgroup.procs は dir にして書きを断らせる
  ;; (kernel の EBUSY の代役 — 書けない移る先)。
  (val layer (/ tmp-path "clis"))
  (.mkdir (/ layer "c-broken" "cgroup.procs") :parents True)
  (.write-text (/ layer "cgroup.subtree_control") "cpu\n")
  (.write-text (/ layer "cgroup.procs") "")
  (<- settings HostedSettings (settings-of tmp-path {"clis" (str (/ layer "cgroup.procs"))}))
  (val seen (! (on-real-host tmp-path settings (HostedCell) (inner-layer-scene))))
  (val direct (get seen "direct"))
  (val placed (get seen "placed"))
  (val unplaced (get seen "unplaced"))
  ;; 11-26 の形: 層そのものへの頼みは、起こす前に移る先の path と訳で断られる(待たせずに頼み手へ返る)。
  (assert (isinstance direct HostedRefused) direct)
  (assert (in (str layer) direct.detail) direct)
  (assert (= (.read-text (/ layer "cgroup.procs")) "") "層そのものに process が書かれた")
  ;; 子を名指す頼みは、子の cgroup.procs に自分の pid を書いてから起きる。
  (assert (= placed.subgroup "c-one") placed)
  (assert (= (.split (.read-text (/ layer "c-one" "cgroup.procs"))) [(str placed.pid)]) placed)
  ;; 書けない子: 命令を走らせずに終わり、移る先の path と断りの文が detail に載る。
  (assert (= unplaced.exit-code 125) unplaced)
  (assert (is-not unplaced.detail None) unplaced)
  (assert (in (str (/ layer "c-broken" "cgroup.procs")) unplaced.detail) unplaced))
