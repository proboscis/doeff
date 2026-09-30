;;; 切り離した task の HTTP の口の純粋な判断(2026-09-25・effect は detached_model.hy)。I/O はしない。
;;;
;;;   PUT    /detached/<key>          送る(job id = key で冪等)。{program revision needs name leaseSeconds retainSeconds environ}
;;;                                   program = 先に PUT /programs/<sha> で置いた詰めた Program の sha(版は置いた時の版 — service の宣言と同じ運び方)
;;;                                   → {"key" "task" "created" "phase"}。同じ key が在れば何も作らず created = false
;;;   GET    /detached/<key>          読む(lease に触らない)→ {"key" "phase" "detail" "result" "worker"}。知らない key は phase = unknown
;;;                                   (coordinator が起きた直後の猶予の内は 503・phase = warming — detached-read)
;;;   POST   /detached/<key>/cancel   取り消す → {"key" "cancelled" "phase"}(終わっていれば cancelled = false・結果は保持)
;;;   DELETE /detached/<key>          終わった task の保持を解く → {"key" "released"}。まだ終わっていなければ 409
;;;
;;; 寿命の規則(置く・lease を worker が延ばす・worker の死 = lost・保持の期限)は cluster_policy の task の節(settle-detached・
;;; renew-detached・absorb-detached-report)。ここは要求 1 件 → Reply(次の状態・status・本文)だけ。
(import dataclasses [replace])
(import typing [NamedTuple])
(import .cluster_model [ClusterState ClusterTiming TaskRecord format-refusal environ-pairs])
(import .cluster_policy [DETACHED-TERMINAL TASK-MAX-OPEN end-detached runtime-env-refusal task-id task-body-refusal request-needs
                         program-versions])
(import .detached_model [DETACHED-DEFAULT-LEASE-SECONDS DETACHED-DEFAULT-RETAIN-SECONDS OPEN-PHASES WARMING-PHASE])

(setv DETACHED-MAX-LEASE-SECONDS 3600)
(setv DETACHED-MAX-RETAIN-SECONDS (* 30 24 3600))
(setv DETACHED-MAX-RECORDS 10000)                ; 切り離した task の行(終わって保持している物を含む)の上限
(setv DETACHED-MAX-KEY-LENGTH 200)


(defclass Reply [NamedTuple]
  "要求 1 件への答え: state = 次の状態(変えなければ受けた状態そのもの)・status = HTTP の status・body = 返す本文
   (JSON の object のまま — HTTP の境界で綴る)。"
  (#^ ClusterState state)
  (#^ int status)
  (#^ dict body))


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


(defn #^ Reply submit-detached [#^ ClusterState state #^ str key #^ dict body #^ int now]
  "PUT /detached/<key>: 同じ key の行が在ればそれを返す(created = false)。name・needs・実行環境・子の環境変数(environ)が違えば 409
   (同じ job id を別の仕事に使った呼び手の誤り — environ は Program の読む設定なので、違えば別の仕事)。無ければ待ちの行を作る。"
  (setv lease (.get body "leaseSeconds" DETACHED-DEFAULT-LEASE-SECONDS)
        retain (.get body "retainSeconds" DETACHED-DEFAULT-RETAIN-SECONDS)
        refusal (or (format-refusal body)
                    (task-body-refusal state body)
                    (runtime-env-refusal body)
                    (key-refusal key)
                    (seconds-refusal "leaseSeconds" lease DETACHED-MAX-LEASE-SECONDS)
                    (seconds-refusal "retainSeconds" retain DETACHED-MAX-RETAIN-SECONDS)))
  (when refusal (return (Reply state 400 {"error" refusal})))
  (setv needs (request-needs body "切り離した task の needs")
        environ (environ-pairs (.get body "environ" {}))
        existing (task-by-key state key))
  (when (is-not existing None)
    (return
      (if (= #(existing.name existing.needs existing.runtime-env existing.environ)
             #((.get body "name" "") needs (.get body "runtimeEnv") environ))
          (Reply state 200 {"key" key "task" existing.id "created" False "phase" existing.phase})
          (Reply state 409 {"error" (.format "key {} は別の仕事(name {!r}・needs {}・environ の名 {})に使われている"
                                        key existing.name (list existing.needs) (lfor #(k _) existing.environ k))}))))
  (setv open-count (len (lfor t (.values state.tasks) :if (in t.phase OPEN-PHASES) t))
        detached-count (len (lfor t (.values state.tasks) :if t.detached t)))
  (when (>= open-count TASK-MAX-OPEN)
    (return (Reply state 429 {"error" (.format "終わっていない task が上限 {} 本に達している" TASK-MAX-OPEN) "open" open-count})))
  (when (>= detached-count DETACHED-MAX-RECORDS)
    (return (Reply state 429 {"error" (.format "切り離した task の行(保持中を含む)が上限 {} 本に達している — 終わった物を解放する"
                                          DETACHED-MAX-RECORDS)})))
  (setv id (task-id state)
        lease-ms (int (* 1000 lease))
        task (TaskRecord id (.get body "name" "") (get body "program") (get body "revision")
                         (program-versions state (get body "program"))
                         needs lease-ms (+ now lease-ms) now
                         :detached True :key key :retain-ms (int (* 1000 retain))
                         :runtime-env (.get body "runtimeEnv") :environ environ))
  (Reply (replace state :tasks (| state.tasks {id task}) :next-task (+ state.next-task 1))
         200 {"key" key "task" id "created" True "phase" task.phase}))


(defn #^ Reply detached-read [#^ ClusterState state #^ str key #^ int now #^ ClusterTiming timing]
  "GET /detached/<key> の答え(2026-09-27 — #757)。coordinator が起きてから観測が揃うまで(lease-ms の猶予 — resource_policy の
   warming と同じ)は、行の無い key を unknown と言わない: 置き場を失った coordinator は、担い手の worker の最初の heartbeat で
   走っている task を引き取る(cluster_policy.adopt-running-detached)ので、その前の unknown は呼び手に送り直させ、引き取った
   旧い task と並走させる。猶予の内の行の無い key は 503・phase warming(client は届かないと同じに扱う — DetachedUnreachable)。"
  (setv view (detached-view state key))
  (if (and (= (get view "phase") "unknown") (< (- now state.started-ms) timing.lease-ms))
      (Reply state 503 {"key" key "phase" WARMING-PHASE
                        "error" "coordinator が起きた直後で、担い手の報告が揃っていない(行の無い key を知らないと言えない)"})
      (Reply state 200 view)))


(defn #^ dict detached-view [#^ ClusterState state #^ str key]
  "GET /detached/<key>: いまの phase と(終わっていれば)結果の blob。lease に触らない(呼び手の問い合わせは寿命と無関係)。"
  (setv task (task-by-key state key))
  (if (is task None)
      {"key" key "phase" "unknown"}
      {"key" key "task" task.id "phase" task.phase "detail" task.detail "result" task.result "worker" task.worker
       "failureKind" task.failure-kind "retryable" task.retryable}))


(defn #^ Reply cancel-detached [#^ ClusterState state #^ str key #^ int now]
  "POST /detached/<key>/cancel: 終わっていなければ cancelled にする(担い手は次の heartbeat の返事から外れた子 process を止める)。
   終わっていれば何もしない(結果は保持)。status は常に 200。"
  (setv task (task-by-key state key))
  (cond
    (is task None) (Reply state 200 {"key" key "cancelled" False "phase" "unknown"})
    (in task.phase DETACHED-TERMINAL) (Reply state 200 {"key" key "cancelled" False "phase" task.phase})
    True (Reply (replace state :tasks (| state.tasks {task.id (end-detached task "cancelled" now "取り消された")}))
                200 {"key" key "cancelled" True "phase" "cancelled"})))


(defn #^ Reply release-detached [#^ ClusterState state #^ str key]
  "DELETE /detached/<key>: 終わった task の行を消す(以後その key は unknown・同じ key で送り直せる)。まだ終わっていなければ 409。"
  (setv task (task-by-key state key))
  (cond
    (is task None) (Reply state 200 {"key" key "released" False})
    (not-in task.phase DETACHED-TERMINAL)
      (Reply state 409 {"error" (.format "key {} はまだ終わっていない({})— 先に取り消す" key task.phase)})
    True (Reply (replace state :tasks (dfor #(k v) (.items state.tasks) :if (!= k task.id) k v)) 200
                {"key" key "released" True})))
