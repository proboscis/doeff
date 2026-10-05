;; 準備と worker の起動の計時(#3676 — #3671 の子 3)の検。
;;
;; 失敗ケース:
;;   1 準備 1 回の完成の印の stages に、処理ステージごとの書いた file の数(数えない処理ステージは null)と、木の処理ステージの repo ごとの
;;     秒と置き方が出て、処理ステージの秒の和が準備の全体の秒に合う。印に disk の種類(mount の表の行)と、起こしてから最初の処理ステージ
;;     までの秒が出る。準備の記録に計時の 1 行が出る。印に書く所・記録の 1 行を外すと赤。
;;   2 worker の起動の刻: process の始まりは /proc の file の読みから(ProcessStartedMs の答え手 process_clock)、boot.sh の刻は環境変数から。
;;   3 coordinator への口が、最初の heartbeat の答えの後に起動の内訳の 1 行を 1 度だけ出し、その行が 5 つの刻(Pod の起動 → boot.sh の
;;     始まり → exec → import の終わり → 最初の答え)を順に持つ。口の 1 行を消すと赤。
;;   4 boot.sh が worker を exec する時に、boot.sh の始まりと exec の刻を環境変数で渡す(起動の script の引き継ぎをまたいで始まりの刻を保つ)。
(require doeff-hy.macros [deftest defk deff <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import json)
(import os)
(import re)
(import subprocess)
(import time)
(import pathlib [Path])
(import httpx)
(import doeff [Program with-handlers])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.file_effects [MemoryFile MemoryFiles])
(import doeff_core_effects.memory_file [memory-file-handler])
(import doeff_time [SimClock sim-time-handler GetMonotonic])
(import doeff_cluster.shared.core.clock [datetime-of-epoch-ms now-epoch-ms])
(import doeff_cluster.shared.core.runtime_env_rules [env-key])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])
(import doeff_cluster.shared.intent.env_marker_model [ENV-MARKER])
(import doeff_cluster.worker.core.env_prepare [prepare-env volume-of-mountinfo])
(import doeff_cluster.shared.intent.runtime_env_model [EnvFailure])
(import doeff_cluster.worker.intent.env_prepare_model [PrepareRequest KnownRoot EnvReady VolumeKind])
(import doeff_cluster.worker.intent.worker_model [BootMarks])
(import doeff_cluster.worker.core.boot_timing [read-boot-marks boot-stamps stamps-out-of-order BOOT-STARTED-VAR BOOT-EXEC-VAR])
(import doeff_cluster.worker.protocol.process_clock [process-clock])
(import doeff_cluster.sim.env_world [env-world EnvWorld read-world-log world-files])
(import tests.env_fixtures [LOCK env-of base-world])
(import tests.link_rig [LinkRig])

(val PLATFORM "linux-x86_64")
(val NOW-MS 1759600000000)          ; 模擬の時計の初めの刻(epoch ミリ秒)
(val LAUNCH-LEAD-MS 2500)           ; 準備の process を起こしてから準備の Program が始まるまで(模擬)
(val STAGE-NAMES ["disk" "mirror" "tree" "lock" "native" "sync" "wheels" "roots" "bytecode" "probe"])


;; --- 1 準備の印の計時 -------------------------------------------------------------------------

(defk prepare-launched [env known launched-ms]
  {:pre [(: env RuntimeEnv) (: known tuple) (: launched-ms int)] :post [(: % (| EnvReady EnvFailure))]}
  "worker と同じ置き場に root を準備する(起こした刻 launched-ms を添えた要求で)。"
  (<- key str (env-key env PLATFORM))
  (<- result (| EnvReady EnvFailure) (prepare-env (PrepareRequest :env env :key key :platform PLATFORM :root (.format "/state/roots/{}" key)
                                          :known known :min-free-bytes 1024 :launched-ms launched-ms)))
  result)


(defk marker-of [ready]
  {:pre [(: ready EnvReady)] :post [(: % dict)]}
  "root の完成の印の JSON を読む。"
  (<- files tuple (world-files ready.root))
  (json.loads (next (gfor f files :if (= f.path (.format "{}/{}" ready.root ENV-MARKER)) f.text))))


(defk timed-scenario []
  {:pre [] :post [(: % bool)]}
  "冷たい準備と、lib の木を写す温い準備の印に計時の欄が出て、処理ステージの秒の和が全体の秒に合う事を確かめる。"
  (<- now int (now-epoch-ms))
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- started float (GetMonotonic))
  (<- ready (prepare-launched env #() (- now LAUNCH-LEAD-MS)))
  (<- ended float (GetMonotonic))
  (assert (isinstance ready EnvReady) ready)
  (<- marker dict (marker-of ready))
  (val stages (get marker "stages"))
  (assert (= (lfor s stages (get s "name")) STAGE-NAMES))
  ;; 書いた file の数: roots = .pth の 1 つ・bytecode = 焼いた数 + 引き継いだ数・数を知らない処理ステージは null(0 で埋めない)。
  (val files (dfor s stages (get s "name") (get s "files")))
  (val baked (get marker "bytecode"))
  (assert (= (get files "roots") 1) files)
  (assert (= (get files "bytecode") (+ (get baked "compiled") (get baked "carried"))) #(files baked))
  (assert (> (get files "bytecode") 0) files)
  (assert (all (gfor n ["disk" "mirror" "tree" "lock" "native" "sync" "wheels" "probe"] (is (get files n) None))) files)
  ;; 木の処理ステージの repo ごとの秒と置き方(冷たい準備は両方とも展開)。
  (val tree (next (gfor s stages :if (= (get s "name") "tree") s)))
  (assert (= (lfor p (get tree "parts") #((get p "name") (get p "how"))) [#("app" "expand") #("lib" "expand")]) tree)
  (assert (<= (sum (gfor p (get tree "parts") (get p "seconds"))) (+ (get tree "seconds") 0.01)) tree)
  ;; 処理ステージの秒の和 = 準備の全体の秒(印の秒は 3 桁に丸める — 10 の処理ステージで 0.01 秒まで)。
  (val total (- ended started))
  (assert (> total 100.0) "冷たい sync と build で 120 秒(仮想の時間)")
  (assert (< (abs (- (sum (gfor s stages (get s "seconds"))) total)) 0.01) #(stages total))
  ;; disk の種類(模擬の mount の表で /state を含む最も深い mount)と、起こしてから最初の処理ステージまでの秒。
  (assert (= (get marker "volume") {"fsType" "ext4" "device" "/dev/nvme0n1p2" "mount" "/state"}) (get marker "volume"))
  (assert (= (get marker "startupSeconds") (/ LAUNCH-LEAD-MS 1000.0)) (get marker "startupSeconds"))
  ;; 準備の記録の計時の 1 行(env_tool の log へ出る行)。
  (<- log (read-world-log))
  (val lines (lfor n log.notes :if (.startswith n "計時:") n))
  (assert (= (len lines) 1) log.notes)
  (assert (in "起動→最初の処理ステージ 2.500 秒" (get lines 0)) lines)
  (assert (in "disk ext4 /dev/nvme0n1p2 (/state)" (get lines 0)) lines)
  (assert (in "tree=" (get lines 0)) lines)
  (assert (in "app:expand=" (get lines 0)) lines)
  ;; 温い準備: 変わらない lib の木は前の root から写す。
  (<- second-env RuntimeEnv (env-of "app-2" "lib-1" LOCK))
  (<- second (prepare-launched second-env #((KnownRoot :env ready.env :root ready.root :made-ms 0)) (- now LAUNCH-LEAD-MS)))
  (assert (isinstance second EnvReady) second)
  (<- again dict (marker-of second))
  (val warm-tree (next (gfor s (get again "stages") :if (= (get s "name") "tree") s)))
  (assert (= (lfor p (get warm-tree "parts") #((get p "name") (get p "how"))) [#("app" "expand") #("lib" "copy")]) warm-tree)
  True)


(deftest test-the-ready-marker-carries-per-stage-files-tree-parts-volume-and-startup
  (<- world EnvWorld (base-world))
  (<- handlers list (env-world world))
  (<- ok bool ((state) ((sim-time-handler :clock (SimClock (datetime-of-epoch-ms NOW-MS))) (with-handlers handlers (timed-scenario)))))
  (assert ok))


(deftest test-the-volume-is-the-deepest-mount-holding-the-path
  ;; mount の名の空白は \040 で書かれる・根だけの表は根・当たらない形の崩れた行は読まない。
  (val table (.join "\n" ["22 1 8:1 / / rw - ext4 /dev/sda1 rw"
                          "30 22 259:2 / /mnt/fast\\040ssd rw - xfs /dev/nvme0n1p1 rw"
                          "broken line"
                          "31 22 0:40 / /mnt/fast\\040ssd/net rw - nfs4 nas:/export rw"]))
  (<- deep (| VolumeKind None) (volume-of-mountinfo table "/mnt/fast ssd/kento/work"))
  (assert (= deep (VolumeKind :fs-type "xfs" :device "/dev/nvme0n1p1" :mount "/mnt/fast ssd")) deep)
  (<- net (| VolumeKind None) (volume-of-mountinfo table "/mnt/fast ssd/net/a"))
  (assert (= net.fs-type "nfs4") net)
  (<- root (| VolumeKind None) (volume-of-mountinfo table "/mnt/fastssd"))
  (assert (= root.device "/dev/sda1") root)
  (<- none (| VolumeKind None) (volume-of-mountinfo "" "/x"))
  (assert (is none None)))


;; --- 2 process の始まりの刻 ------------------------------------------------------------------

;; 模擬の /proc: 機体は NOW-MS の 1000 秒前に起き、PID 1 は起動の 900 秒後、この process は 950 秒後(tick = 100)に始まった。
(val UPTIME "1000.00 3000.00\n")
(val PID1-STAT "1 (boot sh) S 0 1 1 0 -1 4194560 100 0 0 0 1 1 0 0 20 0 1 0 90000 1000 100\n")
(val SELF-STAT "7 (hy (main)) S 1 1 1 0 -1 4194560 100 0 0 0 1 1 0 0 20 0 1 0 95000 1000 100\n")


(deftest test-the-boot-marks-read-the-process-starts-from-proc-and-the-script-stamps-from-env
  (val files (MemoryFiles :dirs #("/proc" "/proc/1" "/proc/self")
                          :files #((MemoryFile :path "/proc/uptime" :content (.encode UPTIME))
                                   (MemoryFile :path "/proc/1/stat" :content (.encode PID1-STAT))
                                   (MemoryFile :path "/proc/self/stat" :content (.encode SELF-STAT)))))
  (val environ {BOOT-STARTED-VAR (str (+ (- NOW-MS 1000000) 900100)) BOOT-EXEC-VAR (str (+ (- NOW-MS 1000000) 930000))})
  (<- marks BootMarks ((state) ((sim-time-handler :clock (SimClock (datetime-of-epoch-ms NOW-MS)))
                                (with-handlers [(memory-file-handler files) (process-clock 100)]
                                               (read-boot-marks environ (- NOW-MS 20000))))))
  (val booted (- NOW-MS 1000000))
  (assert (= marks (BootMarks :pod-ms (+ booted 900000) :script-ms (+ booted 900100) :exec-ms (+ booted 930000)
                              :process-ms (+ booted 950000) :imported-ms (- NOW-MS 20000)))
          marks)
  ;; /proc の無い機体(macOS)と、boot.sh を通らない起動は None(順の断言から外れる)。
  (<- bare BootMarks ((state) ((sim-time-handler :clock (SimClock (datetime-of-epoch-ms NOW-MS)))
                               (with-handlers [(memory-file-handler (MemoryFiles)) (process-clock 100)]
                                              (read-boot-marks {} NOW-MS)))))
  (assert (= bare (BootMarks :imported-ms NOW-MS)) bare)
  (<- stamps tuple (boot-stamps bare (+ NOW-MS 5)))
  (<- broken tuple (stamps-out-of-order stamps))
  (assert (= broken #()) broken))


(deftest test-a-stamp-earlier-than-its-predecessor-is-named
  (<- stamps tuple (boot-stamps (BootMarks :pod-ms 10 :script-ms 20 :exec-ms 15 :imported-ms 30) 40))
  (<- broken tuple (stamps-out-of-order stamps))
  (assert (= broken #(#("script" "exec"))) broken))


;; --- 3 最初の heartbeat の答えの後の 1 行 --------------------------------------------------------

(val STAMP-PATTERN (re.compile r"(?<= )(pod|script|exec|imported|answered)=(\d+|-)(?= )"))


(deff quiet-coordinator [#^ httpx.Request request]  ; defk にできない: 外の library(httpx の MockTransport)が呼ぶ callback
  {:pre [(: request httpx.Request)] :post [(: % httpx.Response)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "検の coordinator: heartbeat に空の宣言で答える(待ちの口は持たない)。"
  (httpx.Response 200 :json {"jobs" [] "tasks" [] "warm" []}))


(deftest test-the-first-heartbeat-answer-logs-the-boot-breakdown-once-in-order [tmp-path capsys]
  (val link (LinkRig "http://coord" "w" #() 1 0 20000 :task-dir (str (/ tmp-path "tasks"))
                        :transport (httpx.MockTransport quiet-coordinator)))
  (val now (int (* 1000 (time.time))))
  (setv link.state.boot-marks (BootMarks :pod-ms (- now 9000) :script-ms (- now 8000) :exec-ms (- now 3000)
                                         :process-ms (- now 8000) :imported-ms (- now 1000)))
  (.poll link)
  (.poll link)
  (val err (. (.readouterr capsys) err))
  (val lines (lfor l (.splitlines err) :if (in "worker: 起動の内訳" l) l))
  (assert (= (len lines) 1) err)
  (val stamps (lfor m (.finditer STAMP-PATTERN (get lines 0)) #((.group m 1) (.group m 2))))
  (assert (= (lfor s stamps (get s 0)) ["pod" "script" "exec" "imported" "answered"]) lines)
  (val values (lfor s stamps (int (get s 1))))
  (assert (= values (sorted values)) lines)
  (assert (in "process=" (get lines 0)) lines))


;; --- 4 boot.sh が渡す刻 ----------------------------------------------------------------------

(val BOOT-SH (str (/ (. (Path __file__) (resolve) parent parent) "deploy" "boot.sh")))
(val FAKE-HY "#!/bin/sh\necho \"started=$DOEFF_BOOT_STARTED_MS exec=$DOEFF_BOOT_EXEC_MS\"\n")


(deftest test-boot-sh-hands-the-script-start-and-exec-stamps-to-the-worker [tmp-path]
  ;; 偽の hy(渡された刻を出すだけ)を PATH の頭に置き、worker の役で起こす。boot.sh の始まりが既に在れば(引き継いだ先)置き直さない。
  (val bin (/ tmp-path "bin"))
  (.mkdir bin)
  (.write-text (/ bin "hy") FAKE-HY)
  (os.chmod (/ bin "hy") 0o755)
  (val base {"PATH" (+ (str bin) ":/usr/bin:/bin") "HOME" (str tmp-path) "ROLE" "worker" "WORK_DIR" (str (/ tmp-path "work"))
             "COORDINATOR_URL" "http://coord" "WORKER_NAME" "w" "WORKER_TASK_RESERVE" "0"})
  (.mkdir (/ tmp-path "work"))
  (val done (subprocess.run ["sh" BOOT-SH] :env base :capture-output True :text True :timeout 60))
  (assert (= done.returncode 0) done.stderr)
  (val found (re.search r"started=(\d+) exec=(\d+)" done.stdout))
  (assert (is-not found None) (+ done.stdout done.stderr))
  (assert (<= (int (.group found 1)) (int (.group found 2))) done.stdout)
  (val kept (subprocess.run ["sh" BOOT-SH] :env (| base {"DOEFF_BOOT_STARTED_MS" "123"}) :capture-output True :text True :timeout 60))
  (assert (in "started=123 exec=" kept.stdout) (+ kept.stdout kept.stderr)))
