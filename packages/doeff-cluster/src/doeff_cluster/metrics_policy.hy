;;; worker の service の計器を coordinator が Prometheus の形で出す、純粋な判断。I/O はしない。
;;;
;;; - 受け取り(record-metrics): service の process が拍ごとに送る ReportMetrics(metrics_model.hy)を、readiness と同じく送り手の
;;;   process の世代つきで Service ごとに残す(世代ごとに最新 1 つ・直近 4 世代)。
;;; - 出す(metrics-text): GET /metrics。Service ごとに「今の宣言の spec で、担い手が running と報告している process」の最新の
;;;   報告だけを出す(resource_policy.running-process と report-matches — readiness と同じ判定)。止めた・前の設定の・前の担い手の
;;;   process の計器は出さない。報告が METRICS-STALE-MS より古ければ出さない(拍が止まった process の値を最新と見せない)。
;;;   label = service(宣言の名)・worker(担い手)。名は本番の Deployment と同じ(counter は _total・duration は _seconds_sum /
;;;   _seconds_count — Prometheus の text の慣習)なので、担い手が本番の Pod から worker へ移っても
;;;   同じ名の系列が続く(系列を分けるのは label だけ — 本番の Pod が替わる時と同じ)。
;;; - coordinator 自身の計器: doeff_worker_service_ready{service}(1 = Ready・0 = それ以外)・
;;;   doeff_worker_service_metrics_age_seconds{service,worker}(出した報告の古さ)。
;;;   2026-09-24 に足した(書き手の alert の材料 — deploy/monitoring/prometheus-rules-doeff-worker-writers.yml): doeff_worker_service_spec_replicas{service}・
;;;   doeff_worker_service_ready_replicas{service}(仕事をしている Ready だけ)・doeff_worker_service_standby{service}(lease を待つ待機の Ready)・
;;;   doeff_worker_service_unplaced{service}・doeff_worker_service_last_metrics_age_seconds{service}(どの process の物でも最新の計器の報告の古さ)・
;;;   doeff_worker_worker_heartbeat_age_seconds{worker,kind}(worker ごとの最後の heartbeat の古さ — coordinator 自身の alert の材料)。
(import dataclasses [replace])
(import math)
(import .cluster_model [ClusterState ClusterTiming PLACED-PHASES])
(import .resource_policy [refuse running-process current-report keep-report report-fields service-readiness])
(import .cluster_policy [unplaced-jobs board-usage])

(setv METRICS-STALE-MS 180000)   ; これより古い報告は出さない(書き手の拍は 5〜15 秒 + 読みの時間)
(setv MAX-NAMES 1000)            ; 1 回の報告の名の数の上限(大きすぎる報告を断る)
(setv NAME-CHARS (frozenset "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_:"))


(defn #^ bool metric-name? [name]
  (and (isinstance name str) (> (len name) 0) (all (gfor ch name (in ch NAME-CHARS))) (not (in (get name 0) "0123456789"))))


(defn #^ bool number? [v]
  (and (isinstance v #(int float)) (not (isinstance v bool)) (math.isfinite v)))


(defn #^ dict checked-metrics [metrics]
  "報告の metrics を検める(形・名・値)。合わなければ 400 で断る。"
  (when (not (isinstance metrics dict)) (refuse 400 "metrics は dict({counters gauges durations})"))
  (setv counters (.get metrics "counters" {}) gauges (.get metrics "gauges" {}) durations (.get metrics "durations" {}))
  (when (not (and (isinstance counters dict) (isinstance gauges dict) (isinstance durations dict)))
    (refuse 400 "metrics の counters・gauges・durations は dict"))
  (when (> (+ (len counters) (len gauges) (len durations)) MAX-NAMES)
    (refuse 400 (.format "metrics の名が多すぎる(上限 {})" MAX-NAMES)))
  (for [#(name v) (+ (list (.items counters)) (list (.items gauges)))]
    (when (not (and (metric-name? name) (number? v))) (refuse 400 (.format "計器の名か値が正しくない: {!r}={!r}" name v))))
  (for [#(name row) (.items durations)]
    (when (not (and (metric-name? name) (isinstance row dict) (number? (.get row "sum")) (number? (.get row "count"))))
      (refuse 400 (.format "duration の名か値が正しくない: {!r}={!r}" name row))))
  {"counters" (dict counters) "gauges" (dict gauges) "durations" (dict durations)})


(defn #^ ClusterState record-metrics [#^ ClusterState state #^ str name #^ dict body #^ int now]
  (when (not (any (gfor j state.jobs (= j.spec.name name))))
    (refuse 404 (+ "無い Service: " name)))
  (setv report (| (report-fields body now) {"metrics" (checked-metrics (.get body "metrics"))}))
  (replace state :metrics (| state.metrics {name (keep-report (.get state.metrics name) report)})))


;; --- 出す ----------------------------------------------------------------------------------------

(defn #^ str escape-label [v]
  (.replace (.replace (.replace (str v) "\\" "\\\\") "\n" "\\n") "\"" "\\\""))


(defn #^ str labels-text [#^ dict labels]
  (if labels
      (+ "{" (.join "," (gfor #(k v) (sorted (.items labels)) (.format "{}=\"{}\"" k (escape-label v)))) "}")
      ""))


(defn #^ str number-text [v]
  (if (isinstance v int) (str v) (str (float v))))


(defn add-sample [#^ dict families #^ str family #^ str kind #^ str sample #^ dict labels value]
  "family の型が既に別の型で在れば、その sample は捨てる(Prometheus の text は 1 つの名に 1 つの型)。"
  (setv f (.setdefault families family {"type" kind "samples" []}))
  (when (= (get f "type") kind)
    (.append (get f "samples") #(sample labels value))))


(defn #^ str render-families [#^ dict families]
  (setv lines [])
  (for [family (sorted families)]
    (setv f (get families family))
    (.append lines (.format "# TYPE {} {}" family (get f "type")))
    (for [#(sample labels value) (get f "samples")]
      (.append lines (.format "{}{} {}" sample (labels-text labels) (number-text value)))))
  (+ (.join "\n" lines) (if lines "\n" "")))


(defn #^ dict add-service-metrics [#^ dict families #^ dict labels #^ dict metrics]
  (for [#(name v) (sorted (.items (.get metrics "counters" {})))]
    (add-sample families (+ name "_total") "counter" (+ name "_total") labels (float v)))
  (for [#(name v) (sorted (.items (.get metrics "gauges" {})))]
    (add-sample families name "gauge" name labels (float v)))
  (for [#(name row) (sorted (.items (.get metrics "durations" {})))]
    (add-sample families (+ name "_seconds") "summary" (+ name "_seconds_sum") labels (float (get row "sum")))
    (add-sample families (+ name "_seconds") "summary" (+ name "_seconds_count") labels (int (get row "count"))))
  families)


(defn #^ list current-metrics [#^ ClusterState state #^ int now #^ ClusterTiming timing]
  "Service ごとの #(名 worker 報告): 今動いている process の最新の報告で、METRICS-STALE-MS より新しい物だけ。"
  (setv out [])
  (for [job (sorted state.jobs :key (fn [j] j.spec.name))]
    (setv name job.spec.name proc (running-process state name now timing))
    (when (get proc "ok")
      (setv report (current-report (.get state.metrics name) proc))
      (when (and report (<= (- now (get report "at")) METRICS-STALE-MS))
        (.append out #(name (get proc "worker") report)))))
  out)


(defn #^ (| float None) last-metrics-age [#^ ClusterState state #^ str name #^ int now]
  "Service の process(今の・前の・退いた — 残している直近の世代)のうち、最も新しい計器の報告の古さ(秒)。報告が 1 つも無ければ None。
   今の process が報告をやめると、この値が伸び続ける(GET /metrics の本番の名の系列は METRICS-STALE-MS で消えるので、途絶えの
   alert はこちらを読む)。"
  (setv reports (.get state.metrics name))
  (if reports (/ (- now (max (gfor r reports (get r "at")))) 1000.0) None))


(defn #^ str metrics-text [#^ ClusterState state #^ int now #^ ClusterTiming timing]
  (setv families {} unplaced (unplaced-jobs now state timing))
  (for [job (sorted state.jobs :key (fn [j] j.spec.name))]
    (setv name job.spec.name labels {"service" name}
          verdict (service-readiness state name now timing)
          ready (= (get verdict "state") "Ready")
          standby (and ready (= (.get verdict "role") "standby")))
    (when (> job.replicas 0)
      (add-sample families "doeff_worker_service_ready" "gauge" "doeff_worker_service_ready" labels (if ready 1.0 0.0)))
    ;; k8s の kube_deployment_spec_replicas / kube_deployment_status_ready_replicas と同じ意味の対(本番の Deployment の alert
    ;; KubeDeploymentReplicasMismatch と同じ式を書ける)。ready_replicas は「仕事をしている」Ready だけ(lease を待つ待機は数えない)。
    (add-sample families "doeff_worker_service_spec_replicas" "gauge" "doeff_worker_service_spec_replicas" labels (float job.replicas))
    (add-sample families "doeff_worker_service_ready_replicas" "gauge" "doeff_worker_service_ready_replicas" labels
                (if (and ready (not standby) (> job.replicas 0)) 1.0 0.0))
    (add-sample families "doeff_worker_service_standby" "gauge" "doeff_worker_service_standby" labels (if standby 1.0 0.0))
    ;; 置き先の無い Service(k8s の Pending の Pod に当たる)。
    (add-sample families "doeff_worker_service_unplaced" "gauge" "doeff_worker_service_unplaced" labels
                (if (and (> job.replicas 0) (in name unplaced)) 1.0 0.0))
    (setv age (last-metrics-age state name now))
    (when (and (> job.replicas 0) (is-not age None))
      (add-sample families "doeff_worker_service_last_metrics_age_seconds" "gauge"
                  "doeff_worker_service_last_metrics_age_seconds" labels age)))
  ;; worker ごとの最後の heartbeat の古さ(2026-09-24・coordinator 自身の alert の材料)。heartbeat は worker から coordinator への唯一の
  ;; 連絡なので、「coordinator がこの worker の heartbeat を受けていない」=「worker から見て coordinator に届かない」。label kind は
  ;; worker の label の kind(k3s / mac)。忘れた worker(DELETE /resources/Worker)は出さない。
  (for [w (sorted (.values state.workers) :key (fn [w] w.name))]
    (add-sample families "doeff_worker_worker_heartbeat_age_seconds" "gauge" "doeff_worker_worker_heartbeat_age_seconds"
                {"worker" w.name "kind" (.get (dict w.labels) "kind" "")}
                (/ (max 0 (- now w.last-seen-ms)) 1000.0)))
  ;; 盤の容量(2026-09-25): 行の数・値の合計と、その上限(cluster_policy の BOARD-MAX-*)。alert は合計が上限の 8 割を越えた時。
  (setv usage (board-usage state))
  (for [#(metric field) #(#("doeff_worker_board_rows" "rows") #("doeff_worker_board_max_rows" "maxRows")
                          #("doeff_worker_board_bytes" "bytes") #("doeff_worker_board_max_bytes" "maxBytes")
                          #("doeff_worker_board_expiring_rows" "expiring"))]
    (add-sample families metric "gauge" metric {} (float (get usage field))))
  (add-sample families "doeff_worker_open_tasks" "gauge" "doeff_worker_open_tasks" {}
              (float (len (lfor t (.values state.tasks) :if (or (= t.phase "queued") (in t.phase PLACED-PHASES)) t))))
  ;; 冷たい起動(2026-09-26): 実行環境の task を、その env を準備済みの worker が 1 つも無いまま置いた回数(置き先の worker が準備してから
  ;; 走る = 準備の時間が task の待ちに入った)。先読み(WarmRuntimeEnv)で 0 に近づける。profile などで 1 台に絞られる task は数で見る(U9)。
  (add-sample families "doeff_worker_env_cold_start_total" "counter" "doeff_worker_env_cold_start_total" {}
              state.env-cold-starts)
  ;; 戻し(RollingBack)が rollbackTimeoutSeconds を過ぎても終わらない Rollout(status.stuck・2026-09-25)。1 = 人が見る。
  (for [#(name r) (sorted (.items state.rollouts))]
    (when (not-in (.get (get r "status") "phase") #("Complete" "RolledBack"))
      (add-sample families "doeff_worker_rollout_stuck" "gauge" "doeff_worker_rollout_stuck" {"rollout" name}
                  (if (.get (get r "status") "stuck") 1.0 0.0))))
  (for [#(name worker report) (current-metrics state now timing)]
    (setv labels {"service" name "worker" worker})
    (add-sample families "doeff_worker_service_metrics_age_seconds" "gauge" "doeff_worker_service_metrics_age_seconds" labels
                (/ (- now (get report "at")) 1000.0))
    (add-service-metrics families labels (get report "metrics")))
  (render-families families))
