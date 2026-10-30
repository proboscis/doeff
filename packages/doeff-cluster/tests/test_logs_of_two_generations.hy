;; 同じ機体に同じ名の worker の 2 つの世代が並ぶ時(Deployment の RollingUpdate — AL-D2j・card ki-2f5186034976)、2 つの worker は job の log の
;; dir($WORK_DIR/state/logs — worker/core/places の logs)を分け合う。試みの番号は worker の process の記憶(JobRecord)で数えるので、新しい
;; 世代は 1 から数え直す — 名が job の名と試みの番号だけだと、2 つの世代が同じ file に書き足す(2026-10-11 の入れ替えで、新しい世代が前の
;; 世代の process と同じ <job の名>.1.log に書き足し、2 つの世代の行が 1 つの file に混ざった)。ここでは、2 つの世代(別の boot・同じ分け合う
;; dir)が同じ job を同じ試みの番号で動かした時に、出力が別の file に入る事を縛る:
;;   * shim の下で起こす job の log。
;;   * 待ちの子から分けて起こす task の log と exit の file(exit の file を取り違えると、居なくなった子の終わりに別の世代の値を読む)。
;;   * 待ちの子の log(root は世代をまたいで分け合うので、2 つの世代が同じ root のキーの待ちの子を起こす — 取り違えると終わりの訳に別の
;;     世代の子の行を読む)。
;; 置き場と世代の印は本番と同じ worker-places で 2 つの boot から作り、宿は本番の process-host・warm-host を、子 process・待ちの子・file を
;; 台本と memory で答える土台の上で回す。前の世代の筋書きの後の memory の世界を、次の世代の筋書きが受け継ぐ(同じ disk)。2 つの worker の
;; pid は台本の既定の 1(Pod ごとの pid の名前空間では、どちらの worker も同じ pid になりうる)。
;; 失敗ケース = log の名に世代の印を入れない形(直す前の形 — <log-dir>/<job の名>.<試みの番号>)は、下の 3 つの検の断言が赤。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import collections.abc [Callable])
(import dataclasses [dataclass replace])  ; defrecord の展開が名指す
(import functools [partial])
(import pathlib [Path])
(import doeff [run with-handlers])
(import doeff_core_effects [state slog-handler])
(import doeff_core_effects [memory-file-handler])
(import doeff_core_effects [MemoryFiles ReadMemoryFiles AppendText WriteText]
        doeff_core_effects._file_effects [file-done])
(import doeff_core_effects [EnvEntry ProcessOutcome RunProcess timed-out-outcome])
(import doeff_core_effects [ScriptedCommand ProcessScript scripted-process-handler])
(import doeff_core_effects [WarmScript WarmSocket WarmRun scripted-warm-process-handler])
(import doeff_core_effects [ForkFromWarm])
(import doeff_time [sync-time-handler])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster [StartJob StartWarmChild]
        doeff_cluster._worker.intent.worker_model [WarmLaunch])
(import doeff_cluster._worker.core.places [WorkerPlaces worker-places])
(import doeff_cluster._worker.core.warm_rules [WarmPlace warm-place])
(import doeff_cluster [process-host]
        doeff_cluster._worker.protocol.process_host [HostSettings])
(import doeff_cluster [warm-host]
        doeff_cluster._worker.protocol.warm_host [WarmSettings])
(import tests.host_rig [host-settings])
(import tests.test_warm_host [task-spec-on])

(val SHARED "/work/state")
(val LOGS (+ SHARED "/logs/"))
(val ROOT "/roots/env-a")
(val SERVICE (JobSpec "svc" "jobs.svc" #() "rev-a"))
(val LAUNCH (WarmLaunch :root ROOT :project (+ ROOT "/app") :preload #("app.jobs")))
;; 2 つの世代の土台の世界: 分け合う dir と root と版の木の置き場だけが在る(世代ごとの dir は宿が作る)。
(val WORLD (MemoryFiles :dirs #(SHARED ROOT "/work/code")))
(val SCRIPT-ENV #((EnvEntry :name "PATH" :value "/usr/bin") (EnvEntry :name "HOME" :value "/home/w")))


(defrecord Generation
  "筋書きの 1 つの世代: places = 本番と同じ worker-places の置き場(log の dir と世代の印を含む)・line = この世代の子が出力に書く 1 行
   (どの file に入ったかで世代を見分ける)・exit-code = この世代の分けた子が exit の file に書く値。"
  (#^ WorkerPlaces places)
  (#^ str line)
  (#^ int exit-code))


(defk generation-of [state boot line exit-code]
  {:pre [(: state str) (: boot str) (: line str) (: exit-code int)] :post [(: % Generation)] :tags {:context "doeff-cluster-test" :role "program"}}
  "状態の dir と boot から、本番と同じ置き場で 1 つの世代を作るため(分け合う dir は 2 つの世代で同じ SHARED)。"
  (<- places WorkerPlaces (worker-places state SHARED boot))
  (Generation :places places :line line :exit-code exit-code))


(defk speaks [line commands request]
  {:pre [(: line str) (: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "台本の子(shim・待ちの子の uv の代役): 自分の世代の 1 行を出力に書き、止めるまで走り続ける。"
  (<- running ProcessOutcome (timed-out-outcome (+ line "\n") ""))
  running)


(defhandler forked-child-speaks [#^ str line #^ int exit-code]
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 待ちの子から分けた子 A の代役の書き手: 分ける頼み(ForkFromWarm)の log の file へ自分の世代の 1 行を足し、exit の file へ自分の世代の値を
  ;; 書いてから、頼みを外側の台本の待ちの子へ渡す(本物の A は log を O_APPEND で開き、終わりに exit の file を置き換えで書く — warm_child)。
  ;; 引数に残す理由: 世代ごとに違う行と値(筋書きの世界そのもの)。
  (ForkFromWarm [socket-path entry args cwd env log-path exit-path grace-seconds]
    (<- (file-done (AppendText log-path (+ line "\n"))))
    (<- (file-done (WriteText exit-path (str exit-code) :replace True)))
    (<- answer effect)
    (resume answer)))


(defk made-then-read [make]
  {:pre [(: make Callable)] :post [(: % MemoryFiles)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き make が作る Program を回し、その後の memory の世界を読むため。"
  (<- (make))
  (<- seen MemoryFiles (ReadMemoryFiles))
  seen)


(defk ran-in [generation world make]
  {:pre [(: generation Generation) (: world MemoryFiles) (: make Callable)] :post [(: % MemoryFiles)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "世代 generation の宿(本番の process-host・warm-host を、この世代の置き場と世代の印で)を memory の世界 world の上で組み、筋書き make を
   回した後の世界を返すため。"
  (val places generation.places)
  (<- base HostSettings (host-settings (Path SHARED)))
  (val host (replace base :log-dir places.logs :generation places.generation :jobs-dir places.jobs :program-dir places.programs
                     :warm-dir places.warm))
  (val warm (WarmSettings :warm-dir places.warm :log-dir places.logs :generation places.generation :uv "uv"))
  (<- place WarmPlace (warm-place places.warm "env-a"))
  (val speaker (partial speaks generation.line))
  (val script (ProcessScript :commands #((ScriptedCommand :name (. (Path host.python) name) :run speaker)
                                         (ScriptedCommand :name "uv" :run speaker))
                             :env SCRIPT-ENV))
  (val forks (WarmScript :sockets #((WarmSocket :path place.socket)) :runs #((WarmRun :entry "doeff_cluster._worker.entry.job_entry"))))
  (val after (run (with-handlers [(state) (sync-time-handler) slog-handler (memory-file-handler world) (scripted-process-handler script)
                                  (scripted-warm-process-handler forks) (forked-child-speaks generation.line generation.exit-code)
                                  (warm-host warm) (process-host host)]
                    (made-then-read make))))
  after)


(defk logs-of [world]
  {:pre [(: world MemoryFiles)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "分け合う log の dir の直下の file を、名 → 中身(文字)の表にするため。"
  (dfor f world.files :if (.startswith f.path LOGS) (cut f.path (len LOGS) None) (.decode f.content "utf-8")))


(defk files-holding [logs text]
  {:pre [(: logs dict) (: text str)] :post [(: % frozenset)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "log の表のうち、中身に text の行を持つ file の名の集まりを求めるため。"
  (frozenset (gfor #(name content) (.items logs) :if (in text (.splitlines content)) name)))


(defk two-generations-ran [make]
  {:pre [(: make Callable)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "前の世代が筋書き make を回し、その後の disk の上で新しい世代が同じ筋書きを回した後の、分け合う log の dir の表を返すため。"
  (<- old Generation (generation-of "/work/gen/old/state" "0b6f5e2a9c1d4f3e8a7b6c5d4e3f2a1b" "前の世代の行" 3))
  (<- new Generation (generation-of "/work/gen/new/state" "5c1d9e8f7a6b5c4d3e2f1a0b9c8d7e6f" "新しい世代の行" 0))
  (assert (= old.places.logs new.places.logs) #(old.places.logs new.places.logs))
  (<- after-old MemoryFiles (ran-in old WORLD make))
  (<- after-new MemoryFiles (ran-in new after-old make))
  (<- logs dict (logs-of after-new))
  logs)


(defk each-generation-has-its-own-file [logs]
  {:pre [(: logs dict)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "2 つの世代の行が、それぞれ 1 つの file に入り、その 2 つが別の file である事を断言するため(混ざった file を名指しで赤にする)。"
  (<- old-files frozenset (files-holding logs "前の世代の行"))
  (<- new-files frozenset (files-holding logs "新しい世代の行"))
  (assert (= (len old-files) 1) #("前の世代の出力の file" old-files logs))
  (assert (= (len new-files) 1) #("新しい世代の出力の file" new-files logs))
  (assert (not (& old-files new-files)) #("2 つの世代の行が同じ file に混ざった" (& old-files new-files) logs))
  None)


(defk service-started []
  {:pre [] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "shim の下で job を試みの番号 1 で起こすため(新しい世代の worker の記憶は 1 から数える)。"
  (<- (StartJob SERVICE 1 "/work/code"))
  None)


(defk task-forked []
  {:pre [] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "待ちの子から task を試みの番号 1 で分けて起こすため。"
  (<- spec JobSpec (task-spec-on "env-a"))
  (<- (StartJob spec 1 ROOT :warm-key "env-a"))
  None)


(defk warm-child-started []
  {:pre [] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "分け合う root のキー env-a の待ちの子を起こすため。"
  (<- (StartWarmChild "env-a" LAUNCH))
  None)


(deftest test-two-generations-write-the-same-job-to-two-log-files
  (<- logs dict (two-generations-ran service-started))
  (<- (each-generation-has-its-own-file logs)))


(deftest test-two-generations-fork-the-same-task-into-two-log-and-exit-files
  (<- logs dict (two-generations-ran task-forked))
  (<- (each-generation-has-its-own-file logs))
  ;; exit の file も世代ごと: 前の世代の分けた子の値(3)を、新しい世代の子の終わりに読まない。
  (val exits (dfor #(name content) (.items logs) :if (.endswith name ".exit") name content))
  (assert (= (sorted (.values exits)) ["0" "3"]) exits))


(deftest test-two-generations-start-the-same-roots-warm-child-into-two-log-files
  (<- logs dict (two-generations-ran warm-child-started))
  (<- (each-generation-has-its-own-file logs)))
