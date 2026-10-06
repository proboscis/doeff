;; coordinator の時間の設定(ClusterTiming)の順の検めと、worker を忘れる期限を設定の値で読む事(#3865)。
;;
;; - 移し替え(reassign-after-ms)は生死の窓(lease-ms)より長い。lease だけを延ばして移し替えより長くした設定は、作る時に断る
;;   (生きていると数える worker の job を、移し替えの判断が先に他へ移す形を作らせない)。
;; - worker を忘れる期限(worker-forget-ms)は移し替えより長い。短い設定は断る。
;; - 待ちの上限 < worker の client の打ち切り < 受付の打ち切り。延ばすのは scaled-timing(比 1 つ)だけ。
;; - 忘れる判断(forget-silent-workers)と、その期限(liveness-due)は設定の worker-forget-ms を読む(定数ではない — 模擬の世界が設定を
;;   丸ごと比を保って延ばす時に、忘れる期限も一緒に動く)。
(require doeff-hy.macros [deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import pytest)
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.shared.core.timing_rules [scaled-timing])
(import doeff_cluster.shared.intent.due_model [DueAt DueNow DueNever])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState WorkerInfo])
(import doeff_cluster.coordinator.core.cluster_policy [forget-silent-workers liveness-due note-liveness])


(deftest test-a-lease-longer-than-the-reassign-is-refused
  (with [(pytest.raises ValueError)]
    (ClusterTiming :lease-ms 70000)))


(deftest test-a-forget-window-shorter-than-the-reassign-is-refused
  (with [(pytest.raises ValueError)]
    (ClusterTiming :worker-forget-ms 30000)))


(deftest test-the-default-timing-and-a-proportionally-stretched-one-are-accepted
  ;; 本番の既定と、全部を同じ比で延ばした設定(模擬の世界の長い筋書きが渡す形)は通る。
  (val base (ClusterTiming))
  (val stretched (ClusterTiming :lease-ms (* 60 base.lease-ms) :fence-ms (* 60 base.fence-ms) :reassign-after-ms (* 60 base.reassign-after-ms)
                                :keep-fence-ms (* 60 base.keep-fence-ms) :worker-forget-ms (* 60 base.worker-forget-ms)))
  (assert (= base.worker-forget-ms (* 7 24 3600 1000)) base)
  (assert (= stretched.lease-ms 600000) stretched))


(deftest test-forgetting-a-silent-worker-reads-the-timing
  ;; 最後の連絡 0 の worker: 設定の忘れる期限 2 時間の刻の 1 ms 後に忘れ、その刻には残る。1.5 時間の刻の liveness-due もその刻 + 1 を返す。
  (val timing (ClusterTiming :worker-forget-ms (* 2 3600 1000)))
  (val worker (WorkerInfo :name "w" :provides #("cpu") :capacity 1 :last-seen-ms 0 :task-reserve 0))
  (val state (note-liveness (ClusterState :workers {"w" worker}) (* 3600 1000) timing))
  (<- kept ClusterState (forget-silent-workers state (* 2 3600 1000) timing))
  (<- gone ClusterState (forget-silent-workers state (+ (* 2 3600 1000) 1) timing))
  (assert (in "w" kept.workers) kept.workers)
  (assert (= gone.workers {}) gone.workers)
  (<- due (| DueAt DueNow DueNever) (liveness-due (note-liveness state 5400000 timing) 5400000 timing))
  (assert (= due (DueAt :at (+ (* 2 3600 1000) 1))) due))


;; --- 待ちの上限と HTTP の打ち切り(Mac の調整役の決定 2026-10-07 04:4x の選び 1)--------------------------------------------

(deftest test-the-watch-and-http-cutoffs-keep-their-order
  ;; 待ちの上限 < worker の client の打ち切り < 受付の thread の打ち切り。崩れた組は作る時に断る。既定は本番の値(10 秒・15 秒・30 秒)。
  (val base (ClusterTiming))
  (assert (= #(base.watch-max-ms base.client-reply-ms base.inbox-reply-ms) #(10000 15000 30000)) base)
  (with [(pytest.raises ValueError)]
    (ClusterTiming :watch-max-ms 15000))
  (with [(pytest.raises ValueError)]
    (ClusterTiming :client-reply-ms 30000)))


(deftest test-a-scaled-timing-stretches-every-window-by-one-ratio
  ;; 延ばす入口は 1 つ(scaled-timing): 本番の既定の全部の窓を同じ比で延ばす。順の検めは延ばした値にも当たる。
  (val base (ClusterTiming))
  (<- stretched ClusterTiming (scaled-timing 60))
  (assert (= #(stretched.lease-ms stretched.fence-ms stretched.reassign-after-ms stretched.keep-fence-ms stretched.worker-forget-ms
               stretched.silent-worker-wait-ms stretched.watch-max-ms stretched.client-reply-ms stretched.inbox-reply-ms)
             (tuple (gfor v #(base.lease-ms base.fence-ms base.reassign-after-ms base.keep-fence-ms base.worker-forget-ms
                              base.silent-worker-wait-ms base.watch-max-ms base.client-reply-ms base.inbox-reply-ms)
                          (* 60 v))))
          stretched))
