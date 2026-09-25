;;; 切り離した task の HTTP の口の純粋な判断(2026-09-25・effect は detached_model.hy)。I/O はしない。
;;;
;;;   PUT    /detached/<key>          送る(job id = key で冪等)。{env blob versions revision requires name leaseSeconds retainSeconds}
;;;                                   → {"key" "task" "created" "phase"}。同じ key が在れば何も作らず created = false
;;;   GET    /detached/<key>          読む(lease に触らない)→ {"key" "phase" "detail" "result" "worker"}。知らない key は phase = unknown
;;;   POST   /detached/<key>/cancel   取り消す → {"key" "cancelled" "phase"}(終わっていれば cancelled = false・結果は保持)
;;;   DELETE /detached/<key>          終わった task の保持を解く → {"key" "released"}。まだ終わっていなければ 409
;;;
;;; 寿命の規則(置く・lease を worker が延ばす・worker の死 = lost・保持の期限)は cluster_policy の task の節(settle-detached・
;;; renew-detached・absorb-detached-report)。ここは要求 1 件 → Reply(次の状態・status・本文)だけ。
(import dataclasses [replace])
(import typing [NamedTuple])
(import .cluster_model [ClusterState TaskRecord requirements-of component-versions-of])
(import .cluster_policy [DETACHED-TERMINAL TASK-MAX-OPEN end-detached])
(import .detached_model [DETACHED-DEFAULT-LEASE-SECONDS DETACHED-DEFAULT-RETAIN-SECONDS])

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


(defn #^ (| str None) seconds-refusal [#^ str label value #^ float limit]
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
  "PUT /detached/<key>: 同じ key の行が在ればそれを返す(created = false)。env・name・requires が違えば 409(同じ job id を別の
   仕事に使った呼び手の誤り)。無ければ待ちの行を作る。"
  (setv lease (.get body "leaseSeconds" DETACHED-DEFAULT-LEASE-SECONDS)
        retain (.get body "retainSeconds" DETACHED-DEFAULT-RETAIN-SECONDS)
        requires (requirements-of (.get body "requires" {}))
        refusal (or (key-refusal key)
                    (seconds-refusal "leaseSeconds" lease DETACHED-MAX-LEASE-SECONDS)
                    (seconds-refusal "retainSeconds" retain DETACHED-MAX-RETAIN-SECONDS)))
  (when refusal (return (Reply state 400 {"error" refusal})))
  (setv existing (task-by-key state key))
  (when (is-not existing None)
    (return
      (if (= #(existing.env existing.name existing.requires) #((get body "env") (.get body "name" "") requires))
          (Reply state 200 {"key" key "task" existing.id "created" False "phase" existing.phase})
          (Reply state 409 {"error" (.format "key {} は別の仕事(env {}・name {!r}・requires {})に使われている"
                                        key existing.env existing.name (dict existing.requires))}))))
  (setv open-count (len (lfor t (.values state.tasks) :if (in t.phase #("queued" "assigned")) t))
        detached-count (len (lfor t (.values state.tasks) :if t.detached t)))
  (when (>= open-count TASK-MAX-OPEN)
    (return (Reply state 429 {"error" (.format "終わっていない task が上限 {} 本に達している" TASK-MAX-OPEN) "open" open-count})))
  (when (>= detached-count DETACHED-MAX-RECORDS)
    (return (Reply state 429 {"error" (.format "切り離した task の行(保持中を含む)が上限 {} 本に達している — 終わった物を解放する"
                                          DETACHED-MAX-RECORDS)})))
  (setv id (.format "t{}" state.next-task)
        lease-ms (int (* 1000 lease))
        task (TaskRecord id (.get body "name" "") (get body "env") (get body "blob") (get body "revision")
                         (component-versions-of (.get body "versions" {}))
                         requires lease-ms (+ now lease-ms) now
                         :detached True :key key :retain-ms (int (* 1000 retain))))
  (Reply (replace state :tasks (| state.tasks {id task}) :next-task (+ state.next-task 1))
         200 {"key" key "task" id "created" True "phase" task.phase}))


(defn #^ dict detached-view [#^ ClusterState state #^ str key]
  "GET /detached/<key>: いまの phase と(終わっていれば)結果の blob。lease に触らない(呼び手の問い合わせは寿命と無関係)。"
  (setv task (task-by-key state key))
  (if (is task None)
      {"key" key "phase" "unknown"}
      {"key" key "task" task.id "phase" task.phase "detail" task.detail "result" task.result "worker" task.worker}))


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
