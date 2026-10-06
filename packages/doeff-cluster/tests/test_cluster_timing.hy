;; 条 C4 timing-outlasts-the-self-stop(architecture.hy・shared/core/timing_rules・#2806): coordinator が連絡の途絶えた worker の印の
;; 無い job を他へ移す時刻(ClusterTiming.reassign-after-ms)は、その worker が自分で job を止め切る最悪の時刻(fence + heartbeat の返事の
;; 上限 + 接続の上限 + 子の停止の猶予)より後。値は本番の定数から集め、数を検に写さない(定数を動かした時に、この検が追随して判じる)。
;; 失敗ケース = 定数を 1 つずつ動かすと破りを名指す検と、破る起動を worker の入口の組み立て(timing-checked)が job を走らせる前に断る検。
(require doeff-hy.macros [deftest defk <- val])
(import dataclasses [replace])
(import pytest)
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.shared.core.timing_rules [SelfStopSpans ReassignTooEarly self-stop-ms timing-outlasts-the-self-stop])
(import doeff_cluster.foundation.coordinator_http [CONNECT-SECONDS])
(import doeff_cluster.worker.intent.worker_model [WorkerPolicy])
(import doeff_cluster.worker.entry.main [timing-checked])

;; 本番の時間の設定と worker の既定の停止の猶予。
(val T (ClusterTiming))
(val P (WorkerPolicy))


(defk production-spans []
  {:pre [] :post [(: % SelfStopSpans)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本番の既定(ClusterTiming の fence と返事の上限・coordinator_http の接続の上限・WorkerPolicy の停止の猶予)から、worker の止め切りの内訳を
   作るため — 数を検に写さない。"
  (SelfStopSpans :fence-ms T.fence-ms :reply-ms T.client-reply-ms :connect-ms (int (* CONNECT-SECONDS 1000))
                  :stop-grace-ms P.stop-grace-ms :kill-grace-ms P.kill-grace-ms))


(deftest test-the-production-timing-outlasts-the-self-stop
  ;; 条 C4 の確かめ: 本番の移し替えは、本番の定数で作った止め切りより後(破りの列が空)。
  (<- spans SelfStopSpans (production-spans))
  (<- broken (get tuple #(ReassignTooEarly ...)) (timing-outlasts-the-self-stop T.reassign-after-ms spans))
  (assert (= broken #()) #(T.reassign-after-ms broken)))


(deftest test-moving-one-constant-past-the-self-stop-is-named
  ;; 失敗ケース: 定数を 1 つずつ動かすと破りを 1 つ名指す — 移し替えを止め切りの 1 ms 前に縮める・返事の上限を延ばす・停止の猶予を延ばす。
  ;; (移し替えを 45 秒に戻した #2806 の前の値も、ここの「縮める」に入る — 止め切りは本番の定数で 45 秒より長い。)
  (<- spans SelfStopSpans (production-spans))
  (<- needed int (self-stop-ms spans))
  (<- early (get tuple #(ReassignTooEarly ...)) (timing-outlasts-the-self-stop (- needed 1) spans))
  (assert (= (len early) 1) early)
  (assert (= #((. (get early 0) reassign-ms) (. (get early 0) needed-ms)) #((- needed 1) needed)) early)
  (val slack (- T.reassign-after-ms needed))
  (<- slow-reply (get tuple #(ReassignTooEarly ...))
      (timing-outlasts-the-self-stop T.reassign-after-ms (replace spans :reply-ms (+ spans.reply-ms slack 1))))
  (assert (= (len slow-reply) 1) slow-reply)
  (<- long-grace (get tuple #(ReassignTooEarly ...))
      (timing-outlasts-the-self-stop T.reassign-after-ms (replace spans :stop-grace-ms (+ spans.stop-grace-ms slack 1))))
  (assert (= (len long-grace) 1) long-grace))


(deftest test-the-worker-entry-refuses-a-start-that-breaks-the-timing
  ;; 組み立ての時に名指しで断る: worker の入口の timing-checked は、本番の組み立て(既定の fence と停止の猶予)を通し、停止の猶予を
  ;; 移し替えの余白より長くした起動を、job を走らせる前に ValueError で断る(文に C4 と内訳を名指す)。
  (<- passed SelfStopSpans (timing-checked T.fence-ms P T))
  (<- spans SelfStopSpans (production-spans))
  (assert (= passed spans) passed)
  (val slack (- T.reassign-after-ms (! (self-stop-ms spans))))
  (with [raised (pytest.raises ValueError)]
    (<- (timing-checked T.fence-ms (replace P :stop-grace-ms (+ P.stop-grace-ms slack 1)) T)))
  (assert (in "C4" (str raised.value)) raised.value))
