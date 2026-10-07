;; 実行環境の root の掃除の数え(MeasureTree)と消し(RemoveTree)が worker の調整ループを止めない形の検(#3715)。
;;
;; 実例(2026-10-06 05:24:56〜05:36:19): screen-worker の掃除が root 25 個を全部 MeasureTree で数え、選んだ root を RemoveTree で消す処理を
;; ループの中で同期して走らせ、Longhorn の I/O の詰まりで約 11 分 23 秒ループが止まった。heartbeat が途絶え、戻った最初の拍で
;; desired-after-silence が途絶(fence 20 秒)と判じて、走っていた画面の job を自分で止めた。だから:
;;   - 数えと消しはループの外の task で走り、走っている間もループの拍(heartbeat = ReadDesired)は fence の内で続き、job は止まらない
;;   - 消す root の選びは数えの答えが届いた拍の固定で行う(数えの間に固定になった root は消さない)
;;   - 走っている掃除は同時に 1 つ(数えか消しの間は次の掃除を起こさない)
;;   - 拍が TICK-LAG-MS を越えた時だけ、拍の遅れの 1 行(越えた ms・いちばん長く待った effect の名)を出す
;; 反例 = 直す前の形(数えと消しをループの中で同期して待つ env-host)は、1 木 60 秒の台で heartbeat の間が fence を越え、job を途絶で止め、
;; 数えの間の固定も見ずに消す — 下の断言が赤(2026-10-06 に前の env_store.hy に差し替えて確かめた)。
;; 土台: 子 process = 台本(起こした子は止めるまで走る)・file system = memory(木の数えと消しは仮想の時計で 60 秒ずつ待つ slow-trees)・
;; 時計 = 仮想の時計・coordinator への口・コードの準備の観測 = 台本の宿(sweep-world — 途絶の自己停止は本番と同じ desired-after-silence)。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import json)
(import pathlib [Path])
(import doeff [Program run with-handlers])
(import doeff_core_effects.effects [SlogEffect])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.stop_signal_effects [StopRequested])
(import tests.stop_fixtures [stop-signal-never-comes])
(import doeff_core_effects.memory_file [memory-file-handler])
(import doeff_core_effects.file_effects [MemoryFile MemoryFiles MeasureTree RemoveTree StatPath PathKind FileFailed])
(import doeff_core_effects.process_effects [ProcessOutcome RunProcess timed-out-outcome])
(import doeff_core_effects.scripted_process [ScriptedCommand ProcessScript scripted-process-handler])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.env_marker_model [ENV-MARKER])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.worker.intent.worker_model [CodeView CodeState WorldView WorkerPolicy DesiredJobs DesiredUnreadable ReadDesired ObserveWorld
                                                 EnvReport PublishStatus PrepareCode ReleaseLeases SweepEnvs EnvDisk])
(import doeff_cluster.worker.protocol.observations [ObserveProcesses ObserveEnvDisk])
(import doeff_cluster.worker.protocol.process_host [HostSettings STOP-TIMING-LOG process-host])
(import doeff_cluster.worker.protocol.code_store [PREPARE-TOOL])
(import doeff_cluster.worker.protocol.env_store [EnvSettings env-host])
(import doeff_cluster.worker.core.worker_rules [code-key])
(import doeff_cluster.worker.protocol.tick_pauses [tick-pauses])
(import tests.wake_fixtures [wakes-every])
(import doeff_cluster.worker.core.heartbeat_rules [desired-after-silence])
(import doeff_cluster.worker.core.program [run-worker TICK-LAG-LOG TICK-LAG-MS ACTIONS-TO-PUBLISH])
(import tests.host_rig [host-settings])

(val STATE "/state")
(val SPEC (JobSpec "svc" "jobs.svc" #() "rev-a"))
;; 本番の拍(1 秒)・coordinator との途絶の柵(本番の 20 秒・240 秒)。
(val POLICY (WorkerPolicy :stop-grace-ms 1000 :kill-grace-ms 500))
(val FENCE-MS 20000)
(val KEEP-FENCE-MS 240000)
;; 遅い木の台: 木 1 つの数えか消しにかかる仮想の秒。
(val SLOW-SECONDS 60.0)
;; 筋書きの長さ(仮想の秒): root 4 つの数え(240 秒)と 2 つの消し(120 秒)が終わり、次の掃除が始まるまで。
(val RUN-MS 400000)
;; 同じ project の完成した root 4 つ(memory の置き場は時刻を持たないので、名の順で a と b が project の新しい 2 つとして残り — #3732 —
;; c と d が消してよい root)。
(val ROOT-A "aaaaaaaaaaaaaaaaaaaaaaaa")
(val ROOT-B "bbbbbbbbbbbbbbbbbbbbbbbb")
(val ROOT-C "cccccccccccccccccccccccc")
(val ROOT-D "dddddddddddddddddddddddd")
(val ALL-ROOTS #(ROOT-A ROOT-B ROOT-C ROOT-D))
(val MARKER {"env" {"project" {"repo" "r" "path" "p"} "repos" [{"name" "r" "url" "https://example.invalid/r"}]}})
;; roots の合計の上限を 0 に置いて、拍ごとに掃除の係へ回す(#3732)。
(val CAP 0)
;; memory の置き場の既定の空きと総量(MemoryFiles の既定と同じ)。
(val ROOMY-FREE (** 2 40))
(val DISK-TOTAL (** 2 41))


(defrecord TreeLoad
  "木の数えと消しの重なり: most = 同時に走っていた木の操作のいちばん多い数・measured = 数えた木の数・removed = 消した木の数。"
  (#^ int most)
  (#^ int measured)
  (#^ int removed))


(defrecord Beats
  "heartbeat(ReadDesired)の間: widest-ms = 続く 2 つの ReadDesired のいちばん広い間・cut-offs = 途絶の自己停止の宣言を返した回数。"
  (#^ int widest-ms)
  (#^ int cut-offs))


(defrecord LagLine
  "拍の遅れの行 1 つの欄。"
  (#^ int elapsed-ms)
  (#^ str slowest)
  (#^ int slowest-ms))


(defrecord SweepRun
  "筋書き 1 回の見え方: stop-reasons = job の止めの計時の行の訳(出た順)・lags = 拍の遅れの行・beats = heartbeat の間・load = 木の重なり・
   swept = 掃除の終わりの行の数・left = 筋書きの終わりに残っている root の名。"
  (#^ (get tuple #(str ...)) stop-reasons)
  (#^ (get tuple #(LagLine ...)) lags)
  (#^ Beats beats)
  (#^ TreeLoad load)
  (#^ int swept)
  (#^ (get tuple #(str ...)) left))


(defrecord RunLines
  "ここまでに出た行: stop-reasons = job の止めの計時の行の訳(出た順)・lags = 拍の遅れの行・swept = 掃除の終わりの行の数。"
  (#^ (get tuple #(str ...)) stop-reasons)
  (#^ (get tuple #(LagLine ...)) lags)
  (#^ int swept))


(defrecord SlowPublish
  "PublishStatus を待たせる台本: at-ms = この時刻を過ぎた最初の PublishStatus で 1 度だけ seconds 秒待つ。"
  (#^ int at-ms)
  (#^ float seconds))


(defeffect TreeBegan
  "木の操作(数えか消し)を始めた — tree-load が数える。"
  {:fields [(: measuring bool)] :answer None :tags {:context "doeff-cluster-test" :role "intent"}})


(defeffect TreeEnded
  "木の操作を終えた — tree-load が数える。"
  {:fields [] :answer None :tags {:context "doeff-cluster-test" :role "intent"}})


(defeffect NotedTreeLoad
  "ここまでの木の重なりを読む — 検だけの問い。"
  {:fields [] :answer TreeLoad :tags {:context "doeff-cluster-test" :role "intent"}})


(defeffect NotedBeats
  "ここまでの heartbeat の間を読む — 検だけの問い。"
  {:fields [] :answer Beats :tags {:context "doeff-cluster-test" :role "intent"}})


(defeffect NotedRunLines
  "ここまでに出た行(止めの訳・拍の遅れ・掃除の終わりの数)を読む — 検だけの問い。"
  {:fields [] :answer RunLines :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler tree-load
  "木の操作の重なりを数える外側の係(節は待たないので、記録は並んだ task の間で崩れない)。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var running 0)
  (session var most 0)
  (session var measured 0)
  (session var removed 0)
  (TreeBegan [measuring]
    (:= running (+ running 1))
    (:= most (max most running))
    (match measuring
      True (:= measured (+ measured 1))
      False (:= removed (+ removed 1)))
    (resume None))
  (TreeEnded []
    (:= running (- running 1))
    (resume None))
  (NotedTreeLoad []
    (resume (TreeLoad :most most :measured measured :removed removed))))


(defhandler slow-trees [#^ float measure-seconds #^ float remove-seconds]
  "遅い disk の代役: 木の数えと消しを、それぞれの仮想の秒だけ待ってから外側の memory の file system へ出し直す(始めと終わりを tree-load へ)。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 検ごとに数えと消しの遅さを変える(Ask で区別できない)。
  (MeasureTree [path]
    (<- (TreeBegan True))
    (<- (Delay measure-seconds))
    (<- answer effect)
    (<- (TreeEnded))
    (resume answer))
  (RemoveTree [path]
    (<- (TreeBegan False))
    (<- (Delay remove-seconds))
    (<- answer effect)
    (<- (TreeEnded))
    (resume answer)))


(defhandler run-lines-noted
  "外の世界の log の代役: 止めの計時の行の訳・拍の遅れの行を出た順に覚え、掃除の終わりの行を数え、他の行は受け流す。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var stops #())
  (session var lags #())
  (session var swept 0)
  (SlogEffect []
    (val fields effect.kwargs)
    (match effect.msg
      m :if (= m STOP-TIMING-LOG) (:= stops (+ stops #((get fields "reason"))))
      m :if (= m TICK-LAG-LOG) (:= lags (+ lags #((LagLine :elapsed-ms (get fields "elapsed_ms") :slowest (get fields "slowest")
                                                           :slowest-ms (get fields "slowest_ms")))))
      m :if (= m "worker: 掃除の終わり") (:= swept (+ swept 1))
      _ None)
    (resume None))
  (NotedRunLines []
    (resume (RunLines :stop-reasons stops :lags lags :swept swept))))


(defhandler sweep-world [#^ int stop-ms #^ (| SlowPublish None) slow]
  "本番の宿のうち coordinator への口・観測・準備の代役: 宣言は毎拍 SPEC が届く。ただし本番の coordinator への口と同じく、拍の頭で最後の
   返事から fence を越えていれば desired-after-silence の自己停止の宣言を返す。版の準備はすぐ READY・観測は子 process と root の disk。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 検ごとに止める刻と PublishStatus の遅れの台本を変える(Ask で区別できない)。
  (session var last-ok-ms 0)
  (session var asked False)
  (session var holding False)
  (session var widest 0)
  (session var cut-offs 0)
  (session var slowed False)
  (ReadDesired [env-report stopping]
    (<- now int (now-epoch-ms))
    ;; 間は最初の問いの後から数える(仮想の時計は刻 0 から始まるので、時刻 0 を「まだ問うていない」の印にしない)。
    (:= widest (if asked (max widest (- now last-ok-ms)) widest))
    (:= asked True)
    (val silenced (desired-after-silence (- now last-ok-ms) FENCE-MS KEEP-FENCE-MS holding #(SPEC) #()))
    (match silenced
      None (do (:= last-ok-ms now)
               (:= holding True)
               (resume (DesiredJobs #(SPEC))))
      _ (do (:= holding False)
            (:= cut-offs (+ cut-offs 1))
            (resume silenced))))
  (StopRequested []
    (<- now int (now-epoch-ms))
    (resume (if (>= now stop-ms) "signal 15" None)))
  (EnvReport [] (resume None))
  (PublishStatus [statuses note]
    (<- now int (now-epoch-ms))
    (match slow
      (SlowPublish) :if (and (not slowed) (>= now slow.at-ms))
        (do (:= slowed True)
            (<- (Delay slow.seconds)))
      _ None)
    (resume None))
  (ReleaseLeases [job instance] (resume None))
  (PrepareCode [revision] (resume None))
  (NotedBeats []
    (resume (Beats :widest-ms widest :cut-offs cut-offs)))
  (ObserveWorld []
    (<- processes tuple (ObserveProcesses))
    (<- disk EnvDisk (ObserveEnvDisk))
    (resume (WorldView #((CodeView SPEC.revision CodeState.READY STATE)) processes :env-disk disk))))


(defk runs-until-stopped [commands request]
  {:pre [(: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "台本の shim(job の子の代役): 止めるまで走り続ける。"
  (<- running ProcessOutcome (timed-out-outcome "" ""))
  running)


(defk roots-on-disk [free]
  {:pre [(: free int)] :post [(: % MemoryFiles)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "memory の置き場に同じ project の完成した root 4 つ(完成マーカーと中身の file 1 つずつ)を置いた中身を返すため(free = disk の空き)。"
  (val names ALL-ROOTS)
  (MemoryFiles :free free :total DISK-TOTAL :dirs (+ #(STATE (+ STATE "/roots")) (tuple (gfor n names (+ STATE "/roots/" n))))
               :files (tuple (+ (lfor n names (MemoryFile :path (+ STATE "/roots/" n "/" ENV-MARKER) :content (.encode (json.dumps MARKER))))
                                (lfor n names (MemoryFile :path (+ STATE "/roots/" n "/lib.py") :content b"x = 1\n"))))))


(defk sweep-settings [cap min-free]
  {:pre [(: cap int) (: min-free int)] :post [(: % EnvSettings)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "掃除させる env-host の設定を返すため(cap = roots の合計の上限・min-free = 共有の disk の空きの最低・prune の命令は台本に無い名 — 起きない)。"
  (EnvSettings :state STATE :uv-cache (+ STATE "/uv-cache") :hy-command "hy" :platform "test" :code-prepare PREPARE-TOOL :uv "no-uv" :roots-cap-bytes cap
               :min-free-bytes min-free))


(defk left-roots []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "root の置き場に残っている root の名を返すため。"
  (var left #())
  (for [name ALL-ROOTS]
    (<- seen (StatPath (+ STATE "/roots/" name)))
    (when (and (not (isinstance seen FileFailed)) (= seen.kind PathKind.DIRECTORY))
      (:= left (+ left #(name)))))
  left)


(defk seen-run []
  {:pre [] :post [(: % SweepRun)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの終わりに、出た行・heartbeat の間・木の重なり・残った root を 1 つの見え方にまとめるため。"
  (<- lines RunLines (NotedRunLines))
  (<- beats Beats (NotedBeats))
  (<- load TreeLoad (NotedTreeLoad))
  (<- left tuple (left-roots))
  (SweepRun :stop-reasons lines.stop-reasons :lags lines.lags :beats beats :load load :swept lines.swept :left left))


(defk worker-run []
  {:pre [] :post [(: % SweepRun)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の調整ループを止まれの合図まで回し、筋書きの見え方を返すため。"
  (<- (run-worker POLICY))
  (<- seen SweepRun (seen-run))
  seen)


(defk on-slow-disk [program measure-seconds remove-seconds [inner []] [cap CAP] [free ROOMY-FREE] [min-free 0]]
  {:pre [(: program Program) (: measure-seconds float) (: remove-seconds float) (: inner list) (: cap int) (: free int) (: min-free int)]
   :post [(: % "program の答え")] :tags {:context "doeff-cluster-test" :role "entry"}}
  "program を env-host・process-host と台本の子 process・遅い木の memory の file system・仮想の時計の下で回すため(行は外側の
   run-lines-noted が受ける)。inner = env-host の内側に置く handler(調整ループを回す筋書きは宿の代役と拍の間の眠り)・cap = roots の
   合計の上限・free = disk の空き・min-free = 空きの最低(既定 0 = 割らない)。"
  (<- host HostSettings (host-settings (Path STATE) :policy POLICY))
  ;; root の準備(env-host の PrepareEnv が nice で起こす)も止めるまで走る — 消えた root の作り直しは終わらず、観測の準備中に残る。
  (val script (ProcessScript :commands #((ScriptedCommand :name (. (Path host.python) name) :run runs-until-stopped)
                                         (ScriptedCommand :name "nice" :run runs-until-stopped))))
  (<- settings EnvSettings (sweep-settings cap min-free))
  (<- files MemoryFiles (roots-on-disk free))
  (run (scheduled (with-handlers [(state) (sim-time-handler :clock (SimClock)) tree-load run-lines-noted (memory-file-handler files)
                                  (slow-trees measure-seconds remove-seconds) (scripted-process-handler script) (process-host host)
                                  (env-host settings) #* inner]
                                 program))))


(defk kept-running [got]
  {:pre [(: got SweepRun)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "筋書きの間 heartbeat が fence の内で続き、job が途絶で止まらず(止めは終わりの worker の停止だけ)、拍の遅れの行が出なかった事を
   断言するため。"
  (assert (< got.beats.widest-ms FENCE-MS) got.beats)
  (assert (= got.beats.cut-offs 0) got.beats)
  (assert (= got.stop-reasons #("worker-stopping" "worker-stopping")) got.stop-reasons)
  (assert (= got.lags #()) got.lags)
  None)


(deftest test-a-slow-measure-keeps-the-heartbeat-and-the-job
  ;; 木 1 つの数えに 60 秒(root 4 つで 240 秒)。数えている間も拍は 1 秒ごとに続き、job は止まらない。c と d は消える。
  (<- got SweepRun (on-slow-disk (worker-run) SLOW-SECONDS 0.0 [(sweep-world RUN-MS None) stop-signal-never-comes tick-pauses (wakes-every 1000)]))
  (<- (kept-running got))
  (assert (>= got.swept 1) got)
  (assert (= got.left #(ROOT-A ROOT-B)) got.left))


(deftest test-a-slow-remove-keeps-the-heartbeat-and-the-job
  ;; 木 1 つの消しに 60 秒(選んだ root 2 つで 120 秒)。消している間も拍は続き、job は止まらない。
  (<- got SweepRun (on-slow-disk (worker-run) 0.0 SLOW-SECONDS [(sweep-world RUN-MS None) stop-signal-never-comes tick-pauses (wakes-every 1000)]))
  (<- (kept-running got))
  (assert (>= got.load.removed 2) got.load)
  (assert (= got.left #(ROOT-A ROOT-B)) got.left))


(defk pinned-while-measuring []
  {:pre [] :post [(: % SweepRun)] :tags {:context "doeff-cluster-test" :role "program"}}
  "固定の無い掃除を始め、数えている間(10 秒目)に c を固定にし、その後は c を固定のまま 1 秒ごとに掃除の係へ回すため(RUN-MS まで)。"
  (<- (SweepEnvs (frozenset)))
  (<- (Delay 10.0))
  (val pinned (frozenset #((+ "env-" ROOT-C))))
  (for [_ (range (// RUN-MS 1000))]
    (<- (SweepEnvs pinned))
    (<- (Delay 1.0)))
  (<- seen SweepRun (seen-run))
  seen)


(deftest test-a-root-pinned-while-measuring-is-not-removed
  ;; 数えの答えが届いた拍の固定で選ぶ: 数えの間に固定になった c は残り、d だけが消える(a と b は project の新しい 2 つ)。
  (<- got SweepRun (on-slow-disk (pinned-while-measuring) SLOW-SECONDS 0.0 [(sweep-world RUN-MS None)]))
  (assert (= got.left #(ROOT-A ROOT-B ROOT-C)) got.left))


(defk ten-ticks-of-changing-pins []
  {:pre [] :post [(: % SweepRun)] :tags {:context "doeff-cluster-test" :role "program"}}
  "拍を 10 回(0.1 秒ごと・毎回 固定の集合を変えて、すぐの掃除を求める)進め、その後 RUN-MS まで 1 秒ごとに掃除の係へ回すため。
   答え = 木の重なりと、10 回の拍にかかった仮想の ms(beats の widest-ms に入れる)。"
  (<- began int (now-epoch-ms))
  (for [i (range 10)]
    (<- (SweepEnvs (frozenset #((.format "env-pin-{}" i)))))
    (<- (Delay 0.1)))
  (<- ended int (now-epoch-ms))
  (for [_ (range (// RUN-MS 1000))]
    (<- (SweepEnvs (frozenset)))
    (<- (Delay 1.0)))
  (<- seen SweepRun (seen-run))
  (SweepRun :stop-reasons seen.stop-reasons :lags seen.lags :beats (Beats :widest-ms (- ended began) :cut-offs 0) :load seen.load
            :swept seen.swept :left seen.left))


(deftest test-ten-ticks-on-a-slow-disk-run-one-sweep-at-a-time
  ;; 1 木 60 秒の台で拍を 10 回進めても、走っている木の操作は同時に 1 つ — 10 回の拍は数えを待たずに 1 秒ほどで進む。
  (<- got SweepRun (on-slow-disk (ten-ticks-of-changing-pins) SLOW-SECONDS SLOW-SECONDS [(sweep-world RUN-MS None)]))
  (assert (<= got.beats.widest-ms 1100) got.beats)
  (assert (= got.load.most 1) got.load)
  (assert (>= got.swept 1) got))


(deftest test-a-tick-over-the-lag-threshold-names-the-slowest-effect
  ;; 30 秒目の拍の PublishStatus が 6 秒待つ(閾 TICK-LAG-MS = 5 秒を越える)— 拍の遅れの行が 1 つ出て、待った effect の名を名乗る。
  ;; 遅い木は無い(数えも消しも 0 秒)ので、他の拍は行を出さない。
  (<- got SweepRun (on-slow-disk (worker-run) 0.0 0.0 [(sweep-world 60000 (SlowPublish :at-ms 30000 :seconds 6.0)) stop-signal-never-comes tick-pauses (wakes-every 1000)]))
  (assert (= (len got.lags) 1) got.lags)
  (val lag (get got.lags 0))
  (assert (= lag.slowest ACTIONS-TO-PUBLISH) lag)
  (assert (> lag.elapsed-ms TICK-LAG-MS) lag)
  (assert (>= lag.slowest-ms 6000) lag))


(deftest test-a-tick-under-the-lag-threshold-has-no-lag-line
  ;; 4 秒の待ちは閾の内 — 行を出さない。
  (<- got SweepRun (on-slow-disk (worker-run) 0.0 0.0 [(sweep-world 60000 (SlowPublish :at-ms 30000 :seconds 4.0)) stop-signal-never-comes tick-pauses (wakes-every 1000)]))
  (assert (= got.lags #()) got.lags))


;; --- 起き直して宣言をまだ読めていない間の掃除(#3731)------------------------------------------------------------------------------
;; 起き直した worker は最初の宣言を読むまで、最後に読んだ宣言が「まだ読んでいない」(NotYetRead)。この間の固定の集合は準備中の root だけで、
;; 上限を越えた roots では止まった job の root(宣言に在る・まだ起こし直していない)まで消し、起こし直しが root の作り直しになった。
;; 反例 = 直す前の形(WorkerState の宣言の初期値が空の列で、掃除の判断が空の宣言と読んでいない宣言を区別しない)は、読めない 30 秒の間に
;; c と d を消す — 下の断言が赤(2026-10-06 に直す前の worker で確かめた)。

;; 止まった job: 実行環境の job で、root は c(env のキー = c)。宣言に在るが起こし直していない(root の準備の観測を返さない)。
(val ENV-SPEC (JobSpec "env-job" "jobs.env" #() "rev-e" :runtime-env "{}" :env-key ROOT-C))


(defhandler restart-world [#^ int read-from-ms #^ int stop-ms]
  "起き直した worker の宿の代役: read-from-ms までは coordinator に届かない(DesiredUnreadable)・その後は毎拍 ENV-SPEC を宣言する。観測は
   子 process と root の disk と、宣言を読んだ後の root c の準備中のまま終わらない観測(job は起こし直さない。本番の実行環境の handler は
   準備を頼まれた root を準備中と観測させるので、準備の頼みは 1 度だけ — 毎周撃ち直さない・#3871 の単位 4)。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 検ごとに読める刻と止める刻を変える(Ask で区別できない)。
  (ReadDesired [env-report stopping]
    (<- now int (now-epoch-ms))
    (resume (if (< now read-from-ms) (DesiredUnreadable "coordinator に届かない") (DesiredJobs #(ENV-SPEC)))))
  (StopRequested []
    (<- now int (now-epoch-ms))
    (resume (if (>= now stop-ms) "signal 15" None)))
  (EnvReport [] (resume None))
  (PublishStatus [statuses note] (resume None))
  (ReleaseLeases [job instance] (resume None))
  (NotedBeats [] (resume (Beats :widest-ms 0 :cut-offs 0)))
  (ObserveWorld []
    (<- processes tuple (ObserveProcesses))
    (<- disk EnvDisk (ObserveEnvDisk))
    (<- now int (now-epoch-ms))
    (val codes (if (< now read-from-ms) #() #((CodeView (code-key ENV-SPEC) CodeState.PREPARING None))))
    (resume (WorldView codes processes :env-disk disk))))


(deftest test-a-restarted-worker-does-not-sweep-before-the-first-declaration
  ;; 起き直してから 30 秒、宣言が読めない(読めるのは筋書きの後)。上限を越えた roots でも掃除しない — 止まった job の root c も d も残る。
  (<- got SweepRun (on-slow-disk (worker-run) 0.0 0.0 [(restart-world (* 2 RUN-MS) 30000) stop-signal-never-comes tick-pauses (wakes-every 1000)]))
  (assert (= got.swept 0) got)
  (assert (= got.left ALL-ROOTS) got.left))


(deftest test-the-first-declaration-starts-the-sweep-and-pins-the-declared-root
  ;; 30 秒目に最初の宣言を読んだ拍から今までどおり掃除する: 宣言の job の root c は固定で残り、固定でない d は消える(a と b は project の
  ;; 新しい 2 つ)。
  (<- got SweepRun (on-slow-disk (worker-run) 0.0 0.0 [(restart-world 30000 60000) stop-signal-never-comes tick-pauses (wakes-every 1000)]))
  (assert (>= got.swept 1) got)
  (assert (= got.left #(ROOT-A ROOT-B ROOT-C)) got.left))


;; --- 空きが最低の上なら、disk 全体の割合では root を消さない(#3732)---------------------------------------------------------------------------------
;; 実例(2026-10-06): agent-worker-2 の /work は node の root の disk(468G・他の物と共有)の上で空き 47G。掃除の下限は disk 全体の割合
;; (volume の 15% と準備を始める空きの大きい方 = 約 75 GB)で、root の外の物が常に下限を割らせ、組むたびに固定されていない root を
;; 消し続けた(root は全部で 933M — 消しても下限の上へ戻れない)— 戻し先の版の root も宣言の最中に消えた。
;; 反例 = 直す前の形(空きが割合の下限を切れば消す)は c と d(と b)を消す — 下の断言が赤(2026-10-06 に直す前の env_upkeep・env_store で
;; 確かめた)。

(deftest test-a-shared-disk-below-the-old-ratio-keeps-roots-within-the-cap
  ;; disk の空き 1 GB・総量 2 TiB(空きは割合の下限 15% を大きく割る)・roots の合計は上限の内。掃除の係は数えるが、root は 1 つも消さない。
  (<- got SweepRun (on-slow-disk (worker-run) 0.0 0.0 [(sweep-world RUN-MS None) stop-signal-never-comes tick-pauses (wakes-every 1000)] :cap (** 2 62) :free (** 10 9)))
  (<- (kept-running got))
  (assert (= got.load.removed 0) got.load)
  (assert (= got.left ALL-ROOTS) got.left))


;; --- 共有の disk の空きが最低を割ったら、候補の root を古い順に消す(#4051)------------------------------------------------------------
;; 実例(2026-10-08 00:33〜04:01・node k3s-0 の worker): 空き 24.96 GiB < 最低 25 GiB・roots の合計 0.86 GB ≤ 上限 20 GiB・
;; 候補 2 で、掃除の選びは毎回 chosen=0 — 準備は disk-full で断られ続け、宣言の job が起きなかった。空きの最低を割った時は、
;; 断る前に候補(固定でない・project の新しい 2 つの外)を古い順に消す。project の新しい 2 つ(今の版と戻し先 — #3732)は残す。
;; 反例 = 直す前の形(空きでは消さない)は 4 つとも残す — 下の断言が赤。

(deftest test-a-shared-disk-below-the-minimum-sweeps-the-candidates-and-keeps-the-newest-two
  ;; disk の空き 1 GB < 最低 2 GB・roots の合計は上限の内。memory の置き場の空きは消しても増えないので、候補 c・d を両方消し、
  ;; project の新しい 2 つ a・b は残す。
  (<- got SweepRun (on-slow-disk (worker-run) 0.0 0.0 [(sweep-world RUN-MS None) stop-signal-never-comes tick-pauses (wakes-every 1000)]
                                 :cap (** 2 62) :free (** 10 9) :min-free (* 2 (** 10 9))))
  (<- (kept-running got))
  (assert (= got.left #(ROOT-A ROOT-B)) got.left))
