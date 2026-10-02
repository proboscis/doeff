;; 入れ物 shim の期限(worker/core/shim_timing・#2940 の 1 段目): shim の猶予 + 掃除の余裕 ≤ 停止の猶予 — shim が job の子孫を片づけ終える
;; 前に worker の KILL が shim を殺さない。値は本番の方針(WorkerPolicy)から集め、数を検に写さない(方針を動かした時に、この検が追随して
;; 判じる)。失敗ケース = 値を 1 つずつ動かすと破りを名指す検と、破る起動を worker の入口の組み立て(timing-checked)が job を走らせる前に
;; 断る検(tests/test_cluster_timing.hy の C4 と同じ形)。worker の側で shim を止める道(終わりを観測していない子の回収)が、止めの合図から
;; shim の期限まで KILL を送らない事は、本物の子 process で確かめる(時間切れの検めの道は tests/test_entry_probe.hy)。
(require doeff-hy.macros [deftest defk <- val])
(import dataclasses [replace])
(import sys)
(import time)
(import pathlib [Path])
(import pytest)
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.worker.intent.worker_model [WorkerPolicy StartJob ReapJob Outcome])
(import doeff_cluster.worker.protocol.observations [ObserveProcesses])
(import doeff_cluster.worker.core.shim_timing [ShimSpans ShimOutlastsTheKill shim-spans shim-deadline-ms shim-ends-before-the-kill])
(import doeff_cluster.worker.entry.main [timing-checked])
(import tests.host_rig [host-settings run-on-host])
(import tests.test_entry_probe [wait-pid assert-gone])

;; 本番の時間の設定と worker の既定の方針。
(val T (ClusterTiming))
(val P (WorkerPolicy))
(val HY (str (/ (. (Path sys.executable) parent) "hy")))


(deftest test-the-production-shim-ends-before-the-kill
  ;; 確かめ: 本番の方針から導いた shim の期限は停止の猶予を越えず(破りの列が空)、job が止めの合図の後に終われる猶予が残る。
  (<- spans ShimSpans (shim-spans P))
  (<- late (get tuple #(ShimOutlastsTheKill ...)) (shim-ends-before-the-kill spans))
  (assert (= late #()) #(spans late))
  (assert (> spans.shim-grace-ms 0) spans)
  (<- deadline int (shim-deadline-ms spans))
  (assert (<= deadline P.stop-grace-ms) #(deadline P)))


(deftest test-moving-one-value-past-the-kill-is-named
  ;; 失敗ケース: 内訳の値を 1 つずつ動かすと破りを 1 つ名指す — shim の猶予を延ばす・掃除の余裕を延ばす・停止の猶予を縮める。
  (<- spans ShimSpans (shim-spans P))
  (<- deadline int (shim-deadline-ms spans))
  (val slack (- spans.stop-grace-ms deadline))
  (for [moved [(replace spans :shim-grace-ms (+ spans.shim-grace-ms slack 1))
               (replace spans :sweep-margin-ms (+ spans.sweep-margin-ms slack 1))
               (replace spans :stop-grace-ms (- spans.stop-grace-ms slack 1))]]
    (<- late (get tuple #(ShimOutlastsTheKill ...)) (shim-ends-before-the-kill moved))
    (assert (= (len late) 1) #(moved late))
    (<- moved-deadline int (shim-deadline-ms moved))
    (assert (= #((. (get late 0) deadline-ms) (. (get late 0) kill-ms)) #(moved-deadline moved.stop-grace-ms)) late)))


(deftest test-the-worker-entry-refuses-a-start-whose-shim-outlasts-the-kill
  ;; 組み立ての時に名指しで断る: worker の入口の timing-checked は本番の方針を通し、掃除の余裕より短い停止の猶予(--stop-grace を短く
  ;; し過ぎた)と、停止の猶予より長い掃除の余裕の起動を、job を走らせる前に ValueError で断る(文に shim の期限と内訳を名指す)。
  (<- (timing-checked T.fence-ms P T))
  (for [broken [(replace P :stop-grace-ms (- P.shim-sweep-margin-ms 1))
                (replace P :shim-sweep-margin-ms (+ P.stop-grace-ms 1))]]
    (with [raised (pytest.raises ValueError)]
      (<- (timing-checked T.fence-ms broken T)))
    (assert (in "shim の期限" (str raised.value)) raised.value)
    (assert (in (str broken.stop-grace-ms) (str raised.value)) raised.value)))


;; --- 本物の子 process: 終わりを観測していない子の回収(process-host の ReapJob)---------------------------------------------------

;; 短い方針(検の時間のため): shim の猶予 1000 ms・掃除の余裕 500 ms → shim の期限 1500 ms。
(val SHORT (WorkerPolicy :stop-grace-ms 1500 :shim-sweep-margin-ms 500))


(defk reaped-before-its-end [spec tree ready]
  {:pre [(: spec JobSpec) (: tree Path) (: ready Path)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "止めの合図を捨てる job を起こし、合図を捨てる構えができた(ready に pid を書いた)所で、終わりを観測しないまま回収し、回収に
   かかった ms を返すため(子の表は process-host の session の値 — 1 本の Program で回す)。"
  (<- (StartJob spec 1 (str tree)))
  (<- views tuple (ObserveProcesses))
  (val pid (. (get views 0) pid))
  (! (wait-pid ready))
  (val began (time.monotonic))
  (<- (ReapJob spec.name pid Outcome.STOPPED -9))
  (int (* 1000 (- (time.monotonic) began))))


(deftest test-reaping-an-unobserved-job-waits-for-the-shim-deadline-before-the-kill [tmp-path]
  ;; 失敗ケース(#2940): 終わりを観測していない子の回収は、止めの合図から shim の期限まで group へ KILL を送らない。以前は猶予 0 で
  ;; 合図と同時に KILL を送り(回収が 0 秒で終わる)、shim が子孫を片づける前に shim を殺した。2 段目から shim は合図を捨てる job を
  ;; 自分の猶予(期限より前)で KILL して片づけ、自分で終わるので、回収の終わりは shim の猶予の後 — 下限は shim の猶予(合図と同時の
  ;; KILL は赤のまま)。本体は残らない。
  (val tree (/ tmp-path "tree"))
  (.mkdir tree)
  (val ready (/ tmp-path "ignorer.pid"))
  (.write-text (/ tree "term_ignorer.py")
               (+ "import os, pathlib, signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                  "pathlib.Path(" (repr (str ready)) ").write_text(str(os.getpid()))\ntime.sleep(60)\n"))
  (<- settings (host-settings tmp-path :hy-command HY :policy SHORT))
  (<- deadline int (shim-deadline-ms settings.shim))
  (val waited (! (run-on-host settings (reaped-before-its-end (JobSpec "task/t1" "term_ignorer" #() "rev-a" :once True) tree ready))))
  (val grace settings.shim.shim-grace-ms)
  (assert (>= waited grace) (.format "回収が shim の猶予 {} ms より前に KILL を送った({} ms)" grace waited))
  (assert (< waited (+ deadline 10000)) waited)
  (! (assert-gone (int (.read-text ready)) "回収した job の本体")))
