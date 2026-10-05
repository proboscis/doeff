;; 退きの知らせの本番の路(#3672): worker の process-host が RetireJob・NoticeJob で shim の標準入力へ 1 行を書き(WriteProcessInput)、
;; shim がその行を job の知らせの pipe へ中継し(--notice-env)、job の中の本番の答え手 pipe-retirement-notices が AwaitRetirement に
;; 答える。本物の process で確かめる(模擬の SimWorker は process を起こさない)。筋書きは本番の組み立てのまま(tests/host_rig の
;; run-on-host — process-host と本物の答え手)で、job は tests/fixtures/notice_listener(待ち始めの印と、受けた知らせの型の名を file へ足す)。
;; 失敗ケース = process-host が起こす shim の命令から知らせの旗を抜く差し替えでは、同じ筋書きで job は何も受けない(答え手は在るが、
;; 知らせの pipe が無い)。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "program"})
(import sys)
(import time)
(import pathlib [Path])
(import doeff_core_effects.process_effects [StartProcess])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.worker.intent.worker_model [WorkerPolicy StartJob SignalJob ReapJob RetireJob NoticeJob Outcome StopStage ProcessView
                                                 Retired HandoffAbandoned])
(import doeff_cluster.worker.protocol.observations [ObserveProcesses])
(import doeff_cluster.worker.protocol.process_host [HostSettings])
(import tests.host_rig [host-settings run-on-host])
(import tests.test_shim_descendants [end-of])

(val LINUX (.startswith sys.platform "linux"))
(val NOT-LINUX "shim の子孫の引き取りは Linux だけ — worker の本番の範囲")
(val HY (str (/ (. (Path sys.executable) parent) "hy")))
;; job の木 = この package の dir(notice_listener を tests.fixtures.notice_listener として読める)。
(val PACKAGE (str (. (Path __file__) parent parent)))
;; 短い方針(検の時間のため): 停止の猶予 1000 ms・KILL の猶予 1000 ms・掃除の余裕 500 ms。
(val SHORT (WorkerPolicy :stop-grace-ms 1000 :kill-grace-ms 1000 :shim-sweep-margin-ms 500))
(val RETIRED-NAME "svc#retired-1")
;; job の起動(hy の子が起きて doeff を import する)を待つ上限・知らせを待つ上限・届かない事を見る長さ(秒)— 1 本の検の上限 60 秒
;; (pytest-timeout)の内に収める。
(val START-SECONDS 30.0)
(val NOTICE-SECONDS 10.0)
(val SILENCE-SECONDS 3.0)
(val NOTICE-FLAG "--notice-env")


(defk lines-of [path count seconds]
  {:pre [(: path Path) (: count int) (: seconds float)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "path に count 行が揃うまで 0.05 秒ずつ読み、行の列を返すため(seconds を過ぎたら、その時の行の列)。"
  (val deadline (+ (time.monotonic) seconds))
  (var seen #())
  (while (and (< (len seen) count) (< (time.monotonic) deadline))
    (:= seen (if (.exists path) (tuple (.splitlines (.read-text path :encoding "utf-8"))) #()))
    (when (< (len seen) count) (time.sleep 0.05)))
  seen)


(defk notices-through-the-pipe [spec out retired-wait]
  {:pre [(: spec JobSpec) (: out Path) (: retired-wait float)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: job を起こし、待ち始めの印を待ってから名から外し(RetireJob — 退く)、知らせの行を retired-wait 秒まで待ち、退きの取り消し
   (NoticeJob — HandoffAbandoned)を送ってもう 1 行を待ち、止めて回収する。#(退いた後の行 取り消しの後の行) を返すため。"
  (<- (StartJob spec 1 PACKAGE))
  (<- started tuple (ObserveProcesses))
  (val pid (. (get started 0) pid))
  (<- listening tuple (lines-of out 1 START-SECONDS))
  (val log (/ out.parent "logs" "svc.1.log"))
  (assert (= listening #("Listening")) #("job が待ち始めない" listening (if (.exists log) (.read-text log :encoding "utf-8") "log が無い")))
  (<- (RetireJob spec.name pid RETIRED-NAME))
  (<- retired tuple (lines-of out 2 retired-wait))
  (<- (NoticeJob RETIRED-NAME pid (HandoffAbandoned)))
  (<- withdrawn tuple (lines-of out 3 retired-wait))
  (<- (SignalJob RETIRED-NAME pid StopStage.TERM (Retired)))
  (<- ended ProcessView (end-of RETIRED-NAME (+ (int (* 1000 (time.time))) 5000)))
  (<- (ReapJob RETIRED-NAME pid Outcome.STOPPED ended.exit-code))
  #(retired withdrawn))


(defk listener-spec [out]
  {:pre [(: out Path)] :post [(: % JobSpec)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "知らせの型の名を out へ足す notice_listener の job を作るため。"
  (JobSpec "svc" "tests.fixtures.notice_listener" #((str out)) "rev-a"))


(deftest test-the-retiring-job-hears-the-worker-through-the-shim-pipe [tmp-path]
  {:skip-if (not LINUX) :skip-reason NOT-LINUX}
  ;; 本番の路: 名から外した job は「退く」を、取り消しの後に「退きを取り消した」を、この順に受ける(1 回の待ちで 1 つの知らせ)。
  (<- settings HostSettings (host-settings tmp-path :hy-command HY :policy SHORT))
  (val out (/ tmp-path "notices"))
  (<- spec JobSpec (listener-spec out))
  (<- got tuple (run-on-host settings (notices-through-the-pipe spec out NOTICE-SECONDS)))
  (assert (= (get got 0) #("Listening" "Retired")) got)
  (assert (= (get got 1) #("Listening" "Retired" "HandoffAbandoned")) got))


(defhandler notice-flag-dropped
  ;; 失敗ケースの差し替え: process-host が起こす shim の命令から知らせの旗(--notice-env <名>)を抜いてから本物へ渡す — 知らせの pipe の
  ;; 無い job(答え手は在る)。
  (StartProcess [argv cwd env env-mode env-drop stdout-path stderr-path process-group hold-stdin reap-group]
    (val at (if (in NOTICE-FLAG argv) (.index argv NOTICE-FLAG) None))
    (val kept (if (is at None) argv (+ (cut argv 0 at) (cut argv (+ at 2) None))))
    (<- answer (StartProcess :argv kept :cwd cwd :env env :env-mode env-mode :env-drop env-drop
                             :stdout-path stdout-path :stderr-path stderr-path :process-group process-group :hold-stdin hold-stdin
                             :reap-group reap-group))
    (resume answer)))


(deftest test-a-shim-without-the-notice-pipe-leaves-the-retiring-job-unaware [tmp-path]
  {:skip-if (not LINUX) :skip-reason NOT-LINUX}
  ;; 失敗ケース: 知らせの旗を抜いた shim の下の job は、名から外されても取り消されても何も受けない — 上の検が中継の欠けを赤にできることの確かめ。
  (<- settings HostSettings (host-settings tmp-path :hy-command HY :policy SHORT))
  (val out (/ tmp-path "notices"))
  (<- spec JobSpec (listener-spec out))
  (<- got tuple (run-on-host settings (notices-through-the-pipe spec out SILENCE-SECONDS) :around #(notice-flag-dropped)))
  (assert (= got #(#("Listening") #("Listening"))) got))
