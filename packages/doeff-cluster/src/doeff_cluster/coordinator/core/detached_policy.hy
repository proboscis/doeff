;;; 切り離した task の HTTP の口の純粋な判断(2026-09-25・effect は detached_model.hy)。I/O はしない。
;;;
;;;   PUT    /detached/<key>          送る(job id = key で冪等)。{program blob versions revision needs name leaseSeconds retainSeconds environ}
;;;                                   program = 詰めた Program の置き場のキー(sha)・blob と versions = 詰めた Program と送り手の版 — Program の
;;;                                   行と task の行を同じ拍で置く(#3741 の C'。blob の無い前の形は先に PUT /programs/<sha> で置いた Program を使う)
;;;                                   → {"key" "task" "created" "phase"}。同じ key が在れば何も作らず created = false
;;;   GET    /detached/<key>          読む(lease に触らない)→ {"key" "phase" "detail" "result" "worker"}。知らない key は phase = unknown
;;;                                   (coordinator が起きた直後の猶予の内は 503・phase = warming — detached-read)
;;;   POST   /detached/<key>/cancel   取り消す → {"key" "cancelled" "phase"}(終わっていれば cancelled = false・結果は保持)
;;;   DELETE /detached/<key>          終わった task の保持を解く → {"key" "released"}。まだ終わっていなければ 409
;;;
;;; 寿命の規則(置く・lease を worker が延ばす・worker の死 = lost・保持の期限)は cluster_policy の task の節(settle-detached・
;;; renew-detached・absorb-detached-report)。ここは要求 1 件 → Reply(次の状態・status・本文)だけ。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "coordinator" :role "judgment"})
(import dataclasses [replace])
(import typing [NamedTuple])
(import doeff [run])
(import doeff_cluster.coordinator.core.program_policy [carried-program])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.shared.core.capabilities [environ-pairs])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState TaskRecord ErrorReply DetachedSubmitted DetachedProgress DetachedUnknown DetachedWarming DetachedCancelled DetachedReleased])
(import doeff_cluster.coordinator.core.cluster_rules [format-version-refusal])
(import doeff_cluster.coordinator.intent.request_bodies [TaskBody])
(import doeff_cluster.coordinator.core.cluster_policy [DETACHED-TERMINAL TASK-MAX-OPEN end-detached runtime-env-value-refusal task-id task-body-refusal needs-named program-versions])
(import doeff_cluster.shared.intent.detached_model [DETACHED-DEFAULT-LEASE-SECONDS DETACHED-DEFAULT-RETAIN-SECONDS OPEN-PHASES WARMING-PHASE])

(setv DETACHED-MAX-LEASE-SECONDS 3600)
(setv DETACHED-MAX-RETAIN-SECONDS (* 30 24 3600))
(setv DETACHED-MAX-RECORDS 10000)                ; 切り離した task の行(終わって保持している物を含む)の上限
(setv DETACHED-MAX-KEY-LENGTH 200)


(defclass Reply [NamedTuple]
  "要求 1 件への答え: state = 次の状態(変えなければ受けた状態そのもの)・status = HTTP の status・body = 返す本文
   (答えの型の値 — JSON は coordinator/protocol/replies が綴る・#2614)。"
  (#^ ClusterState state)
  (#^ int status)
  (#^ (| DetachedSubmitted DetachedProgress DetachedUnknown DetachedWarming DetachedCancelled DetachedReleased ErrorReply) body))


(defn #^ (| TaskRecord None) task-by-key [#^ ClusterState state #^ str key]
  (for [t (.values state.tasks)]
    (when (and t.detached (= t.key key)) (return t)))
  None)


;; value は本文の欄の値そのもの(数かどうかを確かめる)。
(defn #^ (| str None) seconds-refusal [#^ str label #^ object value #^ float limit]
  "純粋: 秒の欄が 0 より大きく limit 以下の数でなければ理由の文。"
  (if (and (isinstance value #(int float)) (not (isinstance value bool)) (< 0 value (+ limit 1)))
      None
      (.format "{} は 0 より大きく {} 以下の数: {!r}" label limit value)))


(defn #^ (| str None) key-refusal [#^ str key]
  (cond
    (not key) "key(job id)が空"
    (> (len key) DETACHED-MAX-KEY-LENGTH) (.format "key(job id)は {} 文字まで" DETACHED-MAX-KEY-LENGTH)
    True None))


(defn #^ Reply submit-detached [#^ ClusterState state #^ str key #^ TaskBody body #^ int now]
  "PUT /detached/<key>: 同じ key の行が在ればそれを返す(created = false)。name・needs・実行環境・子の環境変数(environ)が違えば 409
   (同じ job id を別の仕事に使った呼び手の誤り — environ は Program の読む設定なので、違えば別の仕事)。無ければ待ちの行を作る。"
  (setv lease (if (is body.lease-seconds None) DETACHED-DEFAULT-LEASE-SECONDS body.lease-seconds)
        retain (if (is body.retain-seconds None) DETACHED-DEFAULT-RETAIN-SECONDS body.retain-seconds)
        refusal (or (format-version-refusal body.format)
                    (task-body-refusal state body)
                    (runtime-env-value-refusal body.runtime-env)
                    (key-refusal key)
                    (seconds-refusal "leaseSeconds" lease DETACHED-MAX-LEASE-SECONDS)
                    (seconds-refusal "retainSeconds" retain DETACHED-MAX-RETAIN-SECONDS)))
  (when refusal (return (Reply state 400 (ErrorReply :message refusal))))
  ;; 本文の詰めた Program を置いた状態(blob の無い前の形は受けた状態のまま)。新しい task の行はこの上に足す — Program の行と同じ
  ;; 状態の替え(同じ拍・WAL の 1 行)。同じ key の行が在る時と上限の断りは受けた状態のまま返す(Program を置かない)。
  (setv #(placed carried stored) (run (carried-program state body now)))
  (when (!= carried 200) (return (Reply state carried stored)))
  (setv needs (needs-named body.needs body.requires "切り離した task の needs")
        environ (environ-pairs (or body.environ {}))
        existing (task-by-key state key))
  (when (is-not existing None)
    (return
      (if (= #(existing.name existing.needs existing.runtime-env existing.environ)
             #(body.name needs body.runtime-env environ))
          (Reply state 200 (DetachedSubmitted :key key :id existing.id :created False :phase existing.phase))
          (Reply state 409 (ErrorReply :message (.format "key {} は別の仕事(name {!r}・needs {}・environ の名 {})に使われている"
                                        key existing.name (list existing.needs) (lfor #(k _) existing.environ k)))))))
  (setv open-count (len (lfor t (.values state.tasks) :if (in t.phase OPEN-PHASES) t))
        detached-count (len (lfor t (.values state.tasks) :if t.detached t)))
  (when (>= open-count TASK-MAX-OPEN)
    (return (Reply state 429 (ErrorReply :message (.format "終わっていない task が上限 {} 本に達している" TASK-MAX-OPEN) :open open-count))))
  (when (>= detached-count DETACHED-MAX-RECORDS)
    (return (Reply state 429 (ErrorReply :message (.format "切り離した task の行(保持中を含む)が上限 {} 本に達している — 終わった物を解放する"
                                          DETACHED-MAX-RECORDS)))))
  (setv id (task-id state)
        lease-ms (int (* 1000 lease))
        task (TaskRecord id body.name body.program body.revision
                         (program-versions placed body.program)
                         needs lease-ms (+ now lease-ms) now
                         :detached True :key key :retain-ms (int (* 1000 retain))
                         :runtime-env body.runtime-env :environ environ))
  (Reply (replace placed :tasks (| placed.tasks {id task}) :next-task (+ placed.next-task 1))
         200 (DetachedSubmitted :key key :id id :created True :phase task.phase)))


(defn #^ Reply detached-read [#^ ClusterState state #^ str key #^ int now #^ ClusterTiming timing]
  "GET /detached/<key> の答え(2026-09-27 — #757)。coordinator が起きてから観測が揃うまで(lease-ms の猶予 — resource_policy の
   warming と同じ)は、行の無い key を unknown と言わない: 置き場を失った coordinator は、担い手の worker の最初の heartbeat で
   走っている task を引き取る(cluster_policy.adopt-running-detached)ので、その前の unknown は呼び手に送り直させ、引き取った
   旧い task と並走させる。猶予の内の行の無い key は 503・phase warming(client は届かないと同じに扱う — DetachedUnreachable)。"
  (setv view (detached-view state key))
  (if (and (isinstance view DetachedUnknown) (< (- now state.started-ms) timing.lease-ms))
      (Reply state 503 (DetachedWarming :key key :phase WARMING-PHASE
                                        :reason "coordinator が起きた直後で、担い手の報告が揃っていない(行の無い key を知らないと言えない)"))
      (Reply state 200 view)))


(defn #^ (| DetachedProgress DetachedUnknown) detached-view [#^ ClusterState state #^ str key]
  "GET /detached/<key>: いまの phase と(終わっていれば)結果の blob。lease に触らない(呼び手の問い合わせは寿命と無関係)。"
  (setv task (task-by-key state key))
  (if (is task None)
      (DetachedUnknown :key key)
      (DetachedProgress :key key :id task.id :phase task.phase :detail task.detail :result task.result :worker task.worker
                        :failure-kind task.failure-kind :retryable task.retryable)))


(defn #^ Reply cancel-detached [#^ ClusterState state #^ str key #^ int now]
  "POST /detached/<key>/cancel: 終わっていなければ cancelled にする(担い手は次の heartbeat の返事から外れた子 process を止める)。
   終わっていれば何もしない(結果は保持)。status は常に 200。"
  (setv task (task-by-key state key))
  (cond
    (is task None) (Reply state 200 (DetachedCancelled :key key :cancelled False :phase "unknown"))
    (in task.phase DETACHED-TERMINAL) (Reply state 200 (DetachedCancelled :key key :cancelled False :phase task.phase))
    True (Reply (replace state :tasks (| state.tasks {task.id (end-detached task "cancelled" now "取り消された")}))
                200 (DetachedCancelled :key key :cancelled True :phase "cancelled"))))


(defn #^ Reply release-detached [#^ ClusterState state #^ str key]
  "DELETE /detached/<key>: 終わった task の行を消す(以後その key は unknown・同じ key で送り直せる)。まだ終わっていなければ 409。"
  (setv task (task-by-key state key))
  (cond
    (is task None) (Reply state 200 (DetachedReleased :key key :released False))
    (not-in task.phase DETACHED-TERMINAL)
      (Reply state 409 (ErrorReply :message (.format "key {} はまだ終わっていない({})— 先に取り消す" key task.phase)))
    True (Reply (replace state :tasks (dfor #(k v) (.items state.tasks) :if (!= k task.id) k v)) 200
                (DetachedReleased :key key :released True))))
