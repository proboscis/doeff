;; 条 C4b stopped-job-leaves-no-descendant(packages/doeff-cluster/architecture.hy・worker/core/invariants・#2940 の 2 段目): job を止め切った
;; 後、job の子孫は 1 つも生きていない。守るのは入れ物 shim(worker/entry/shim)— job を起こす前に自分を子孫の引き取り手
;; (PR_SET_CHILD_SUBREAPER)にし、止めの合図・worker の消失(標準入力の EOF)・job の自分での終わりの 3 つの道で、同じ片づけを 1 度だけ
;; 通してから終わる。
;; 本物の process で確かめる(模擬の SimWorker は process を起こさないので、OS の引き取りを模せない)。範囲は Linux の worker。筋書きは本番の
;; 組み立てのまま(tests/host_rig の run-on-host — process-host と本物の答え手)で、job は tests/fixtures/sleeper の --detached-grandchild
;; (別の session に、止めの合図を捨てて眠る孫を起こす)。方針は短くして(停止の猶予 1 秒・KILL の猶予 1 秒・shim の猶予 0.5 秒)1 file を
;; 60 秒に収める — 数は本番を写さず、関係(止め切りの時刻)だけを確かめる。
;; 失敗ケース = 引き取りを外した shim の変種(tests/fixtures/shim_without_adoption — 「使えない」と答える部品を渡した本物の shim)を
;; StartProcess の命令に差すと、筋書き 1 で孫が止め切りの後も残り、C4b が名指す。同じ検で、使えない理由の 1 行が shim の標準エラー
;; (job の log)に出て、process group への合図だけの止めで job が止まることも見る。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "program"})
(import os)
(import sys)
(import time)
(import pathlib [Path])
(import doeff_core_effects.os_process [STARTED-CHILDREN])
(import doeff_core_effects.process_effects [StartProcess])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.worker.intent.worker_model [WorkerPolicy StartJob SignalJob ReapJob Outcome StopStage ProcessView])
(import doeff_cluster.worker.protocol.observations [ObserveProcesses])
(import doeff_cluster.worker.protocol.process_host [HostSettings])
(import doeff_cluster.worker.core.invariants [DescendantLife DescendantOutlivedTheStop stopped-job-leaves-no-descendant])
(import doeff_cluster.worker.core.shim_timing [shim-deadline-ms])
(import tests.host_rig [host-settings run-on-host job-ended])
(import tests.test_entry_probe [wait-pid process-alive])

(val LINUX (.startswith sys.platform "linux"))
(val NOT-LINUX "子孫の引き取り(PR_SET_CHILD_SUBREAPER)と /proc は Linux だけ — 条 C4b の範囲は Linux の worker")
(val HY (str (/ (. (Path sys.executable) parent) "hy")))
;; job の木 = この package の dir(sleeper を tests.fixtures.sleeper として読める)。
(val PACKAGE (str (. (Path __file__) parent parent)))
;; 短い方針(検の時間のため): 停止の猶予 1000 ms・KILL の猶予 1000 ms・掃除の余裕 500 ms → shim の猶予 500 ms。
(val SHORT (WorkerPolicy :stop-grace-ms 1000 :kill-grace-ms 1000 :shim-sweep-margin-ms 500))
;; 止め切りの後に孫を見張る長さ(ms)— 残った孫を「止め切りの後に生きていた」と見るため。
(val WATCH-AFTER-MS 300)
(val SHIM "doeff_cluster.worker.entry.shim")
(val VARIANT "tests.fixtures.shim_without_adoption")


(defk epoch-ms []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "今の時刻(epoch ms)— 止め切りの時刻と子孫を見た時刻を同じ時計で記すため。"
  (int (* 1000 (time.time))))


(defk sleeper-spec [beat flags]
  {:pre [(: beat Path) (: flags tuple)] :post [(: % JobSpec)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "別の session に止めの合図を捨てる孫を起こす sleeper の job(flags = 足す旗)を作るため。"
  (JobSpec "svc" "tests.fixtures.sleeper" (+ #((str beat) "--detached-grandchild") flags) "rev-a"))


(defk end-of [name until-ms]
  {:pre [(: name str) (: until-ms int)] :post [(: % ProcessView)] :tags {:context "doeff-cluster-test" :role "program"}}
  "name の子の終わりを観測するまで 0.05 秒ずつ観測し、終わった子の観測を返すため(until-ms を過ぎたら AssertionError)。"
  (var ended None)
  (while (is ended None)
    (<- now int (epoch-ms))
    (when (> now until-ms) (raise (AssertionError (.format "{} が終わらない" name))))
    (<- views tuple (ObserveProcesses))
    (for [view views]
      (when (and (= view.name name) (is-not view.exit-code None)) (:= ended view)))
    (when (is ended None) (time.sleep 0.05)))
  ended)


(defk stopped-by-the-worker [spec detached limit-ms]
  {:pre [(: spec JobSpec) (: detached Path) (: limit-ms int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き 1(worker の止め): job を起こし、別の session の孫が pid を書いた後に止めの合図(TERM)を送り、終わりを観測して回収する。
   #(合図の時刻 終わりを観測した時刻 終わった子の観測 孫の pid) を返すため(終わりは合図から limit-ms の内)。"
  (<- (StartJob spec 1 PACKAGE))
  (<- started tuple (ObserveProcesses))
  (val pid (. (get started 0) pid))
  (val grandchild (! (wait-pid detached)))
  (<- signalled int (epoch-ms))
  (<- (SignalJob spec.name pid StopStage.TERM))
  (<- ended ProcessView (end-of spec.name (+ signalled limit-ms)))
  (<- seen int (epoch-ms))
  (<- (ReapJob spec.name pid Outcome.STOPPED ended.exit-code))
  #(signalled seen ended grandchild))


(defk orphaned-by-the-worker [spec detached limit-ms]
  {:pre [(: spec JobSpec) (: detached Path) (: limit-ms int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き 2(worker が消えた): job を起こし、孫が pid を書いた後に shim の標準入力の pipe を閉じ(worker の kill -9 と同じ EOF)、shim が
   自分で片づけて終わるのを観測して回収する。#(閉じた時刻 終わった子の観測 孫の pid) を返すため(終わりは閉じてから limit-ms の内)。"
  (<- (StartJob spec 1 PACKAGE))
  (<- started tuple (ObserveProcesses))
  (val pid (. (get started 0) pid))
  (val grandchild (! (wait-pid detached)))
  (<- closed int (epoch-ms))
  (.close (. (get (.find STARTED-CHILDREN pid) 0) stdin))
  (<- ended ProcessView (end-of spec.name (+ closed limit-ms)))
  (<- (ReapJob spec.name pid Outcome.EXITED ended.exit-code))
  #(closed ended grandchild))


(defk life-after [pid label until-ms]
  {:pre [(: pid int) (: label str) (: until-ms int)] :post [(: % DescendantLife)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "子孫 pid を、居なくなるか until-ms(epoch ms)を過ぎるまで 0.02 秒ずつ見て、生きているのを最後に見た時刻を記すため(条 C4b の記録)。"
  (var last None)
  (var watching True)
  (while watching
    (<- at int (epoch-ms))
    (<- alive bool (process-alive pid))
    (if alive
        (do (:= last at)
            (if (> at until-ms) (:= watching False) (time.sleep 0.02)))
        (:= watching False)))
  (DescendantLife :pid pid :label label :last-alive-ms last))


(defk left-grandchild-stopped [detached]
  {:pre [(: detached Path)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "検の後始末: 孫が pid を書いていて、まだ生きていれば止める(赤の検も、次の検と作業木に孫を残さない)。"
  (when (.exists detached)
    (val pid (int (.read-text detached)))
    (<- alive bool (process-alive pid))
    (when alive (os.kill pid 9)))
  None)


(defhandler shim-swapped
  ;; 失敗ケースの差し替え: process-host が起こす shim の module の名を、引き取りを外した変種へ替えてから本物へ渡す。
  (StartProcess [argv cwd env env-mode env-drop stdout-path stderr-path process-group hold-stdin reap-group]
    (<- answer (StartProcess :argv (tuple (gfor a argv (if (= a SHIM) VARIANT a))) :cwd cwd :env env :env-mode env-mode :env-drop env-drop
                             :stdout-path stdout-path :stderr-path stderr-path :process-group process-group :hold-stdin hold-stdin
                             :reap-group reap-group))
    (resume answer)))


(defk worker-stop-scene [tmp flags around]
  {:pre [(: tmp Path) (: flags tuple) (: around tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き 1 を本番の組み立てで回し、#(合図の時刻 止め切りの時刻 終わりを観測した時刻 終わった子の観測 孫の見え方 shim と job の log) を
   返すため。止め切りの時刻 = 止めの合図 + 停止の猶予 + KILL の猶予(worker がそこまでに止め切る)。孫は止め切りの後
   WATCH-AFTER-MS まで見張る。"
  (<- settings HostSettings (host-settings tmp :hy-command HY :policy SHORT))
  (val beat (/ tmp "beat"))
  (val limit-ms (+ SHORT.stop-grace-ms SHORT.kill-grace-ms))
  (<- spec JobSpec (sleeper-spec beat flags))
  (<- got tuple (run-on-host settings (stopped-by-the-worker spec (Path (+ (str beat) ".detached")) limit-ms) :around around))
  (val signalled (get got 0))
  (val stopped-ms (+ signalled limit-ms))
  (<- life DescendantLife (life-after (get got 3) "別の session の孫" (+ stopped-ms WATCH-AFTER-MS)))
  #(signalled stopped-ms (get got 1) (get got 2) life (.read-text (/ tmp "logs" "svc.1.log") :encoding "utf-8")))


(deftest test-c4b-names-each-descendant-seen-alive-after-the-stop
  ;; 判断だけ: 止め切りの前に居なくなった子孫・見張りの初めから居ない子孫・止め切りの時刻ちょうどに見た子孫は緑、後に見た子孫を
  ;; 1 つずつ名指す。
  (val gone (DescendantLife :pid 11 :label "止め切りの前に消えた孫" :last-alive-ms 900))
  (val unseen (DescendantLife :pid 12 :label "見張りの初めから居ない孫" :last-alive-ms None))
  (val edge (DescendantLife :pid 13 :label "止め切りの時刻ちょうどに見た孫" :last-alive-ms 1000))
  (val left (DescendantLife :pid 14 :label "残った孫" :last-alive-ms 1001))
  (<- green (get tuple #(DescendantOutlivedTheStop ...)) (stopped-job-leaves-no-descendant 1000 #(gone unseen edge)))
  (assert (= green #()) green)
  (<- red (get tuple #(DescendantOutlivedTheStop ...)) (stopped-job-leaves-no-descendant 1000 #(gone left unseen)))
  (assert (= red #((DescendantOutlivedTheStop :stopped-ms 1000 :life left))) red))


(deftest test-a-job-stopped-by-the-worker-leaves-no-descendant [tmp-path]
  {:skip-if (not LINUX) :skip-reason NOT-LINUX}
  ;; 筋書き 1(worker の止め): 止めの合図を受けた job は後始末して終わり、shim は job が別の session に残した孫を片づけてから終わる。
  ;; 以前は shim が job の終わりと同時に終わり、孫は PID 1 へ逃げて残った。
  (try
    (val got (! (worker-stop-scene tmp-path #() #())))
    (val ended (get got 3))
    (val life (get got 4))
    (<- broken (get tuple #(DescendantOutlivedTheStop ...)) (stopped-job-leaves-no-descendant (get got 1) #(life)))
    (assert (= broken #()) broken)
    ;; job の終了コード(合図で後始末して 0)をそのまま返す。
    (assert (= ended.exit-code 0) ended)
    (finally (! (left-grandchild-stopped (/ tmp-path "beat.detached"))))))


(deftest test-a-job-ignoring-the-stop-is-killed-by-the-shim-before-the-worker-kill [tmp-path]
  {:skip-if (not LINUX) :skip-reason NOT-LINUX}
  ;; 筋書き 1 の期限の道: 止めの合図を捨てる job は、合図から shim の猶予が過ぎた所で shim が孫ごと KILL して片づけ、shim は worker の
  ;; KILL(合図 + 停止の猶予)より前に終わる。終了コードは KILL の 128 + 9。
  (try
    (val got (! (worker-stop-scene tmp-path #("--ignore-term") #())))
    (val ended (get got 3))
    (val life (get got 4))
    (<- broken (get tuple #(DescendantOutlivedTheStop ...)) (stopped-job-leaves-no-descendant (get got 1) #(life)))
    (assert (= broken #()) broken)
    (assert (= ended.exit-code 137) ended)
    (assert (< (- (get got 2) (get got 0)) SHORT.stop-grace-ms)
            (.format "shim が worker の KILL({} ms)より前に終わらない: 合図から {} ms" SHORT.stop-grace-ms (- (get got 2) (get got 0))))
    (finally (! (left-grandchild-stopped (/ tmp-path "beat.detached"))))))


(deftest test-a-job-orphaned-by-the-worker-leaves-no-descendant [tmp-path]
  {:skip-if (not LINUX) :skip-reason NOT-LINUX}
  ;; 筋書き 2(worker が消えた): shim は標準入力の EOF で job へ止めの合図を送り、worker の KILL が来なくても自分の期限までに孫ごと
  ;; 片づけて終わる。止め切りの時刻 = EOF + shim の期限(shim の猶予 + 掃除の余裕)。
  (try
    (<- settings HostSettings (host-settings tmp-path :hy-command HY :policy SHORT))
    (<- deadline int (shim-deadline-ms settings.shim))
    (val beat (/ tmp-path "beat"))
    (<- spec JobSpec (sleeper-spec beat #()))
    (val got (! (run-on-host settings (orphaned-by-the-worker spec (Path (+ (str beat) ".detached")) (+ deadline 1000)))))
    (val stopped-ms (+ (get got 0) deadline))
    (<- life DescendantLife (life-after (get got 2) "別の session の孫" (+ stopped-ms WATCH-AFTER-MS)))
    (<- broken (get tuple #(DescendantOutlivedTheStop ...)) (stopped-job-leaves-no-descendant stopped-ms #(life)))
    (assert (= broken #()) broken)
    (assert (= (. (get got 1) exit-code) 0) (get got 1))
    (finally (! (left-grandchild-stopped (/ tmp-path "beat.detached"))))))


(deftest test-a-job-that-ends-by-itself-leaves-no-descendant [tmp-path]
  {:skip-if (not LINUX) :skip-reason NOT-LINUX}
  ;; 筋書き 3(job が自分で終わった): job が別の session に孫を残して終わると、shim は孫を片づけてから同じ終了コードで終わる — worker が
  ;; 終わりを観測した時(止め切りの時刻)には孫は居ない。以前は shim が job と同時に終わり、孫は PID 1 へ逃げて残った。
  (try
    (<- settings HostSettings (host-settings tmp-path :hy-command HY :policy SHORT))
    (<- spec JobSpec (sleeper-spec (/ tmp-path "beat") #("--once")))
    (<- ended ProcessView (run-on-host settings (job-ended spec PACKAGE 30)))
    (<- stopped-ms int (epoch-ms))
    (val grandchild (int (.read-text (/ tmp-path "beat.detached"))))
    (<- life DescendantLife (life-after grandchild "別の session の孫" (+ stopped-ms WATCH-AFTER-MS)))
    (<- broken (get tuple #(DescendantOutlivedTheStop ...)) (stopped-job-leaves-no-descendant stopped-ms #(life)))
    (assert (= broken #()) broken)
    (assert (= ended.exit-code 0) ended)
    (finally (! (left-grandchild-stopped (/ tmp-path "beat.detached"))))))


(deftest test-a-shim-without-adoption-leaves-the-grandchild-and-c4b-names-it [tmp-path]
  {:skip-if (not LINUX) :skip-reason NOT-LINUX}
  ;; 失敗ケース(条 C4b): 子孫の引き取りを「使えない」と答える部品を渡した shim(引き取りと片づけを外した変種)を筋書き 1 に差すと、
  ;; 別の session の孫が止め切りの後も生きていて、C4b がその孫を名指す。同じ変種で、使えない理由の 1 行が shim の標準エラー(job の
  ;; log)に出て、process group への合図だけの止めで job は止まる(合図で後始末して 0 で終わる — 黙って既定に落ちない)。
  (try
    (val got (! (worker-stop-scene tmp-path #() #(shim-swapped))))
    (val ended (get got 3))
    (val life (get got 4))
    (val log (get got 5))
    (<- broken (get tuple #(DescendantOutlivedTheStop ...)) (stopped-job-leaves-no-descendant (get got 1) #(life)))
    (assert (= (len broken) 1) #(broken life))
    (assert (= (. (get broken 0) life pid) life.pid) broken)
    (assert (in "shim: 子孫の引き取りを使えない" log) log)
    (assert (= ended.exit-code 0) ended)
    (finally (! (left-grandchild-stopped (/ tmp-path "beat.detached"))))))
