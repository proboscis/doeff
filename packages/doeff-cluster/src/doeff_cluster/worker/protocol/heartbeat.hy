;;; worker が coordinator へ送る heartbeat の本文の形 — 実行環境の root の名乗り・生存と能力と版・状態の行と結果(本番の coordinator への口 と
;;; 手元の sim-cluster の宿 sim/local が同じ関数で作る — 本文を写さない)。handlers.hy から分けた(#2026)。判断は worker/core/heartbeat_rules。
(require doeff-hy.macros [deff val])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import doeff_cluster.shared.intent.protocol [PROTOCOL-FORMAT])
(import doeff_cluster.worker.intent.worker_model [CodeState JobStatus])
(import doeff_cluster.worker.core.worker_rules [ENV-KEY-PREFIX])
(import doeff_cluster.worker.core.heartbeat_rules [finished-task-id])


(deff env-report [#^ tuple views #^ str capacity]  ; defk にできない: worker の root の言い換え(env-host)と sim の宿が同じ形を作る純粋な判断
  {:pre [(: views tuple) (: capacity str)] :post [(: % dict)] :tags {:context "worker" :role "protocol"}}
  "実行環境の root の観測(CodeView — 鍵が env- で始まる物だけを読む)と disk の条件を、heartbeat で名乗る root の姿(準備済み・準備中・
   失敗のキーを env- を外して・disk の条件)にするため。"
  (let [roots (lfor v views :if (.startswith v.revision ENV-KEY-PREFIX) v)
        bare (fn [k] (cut k (len ENV-KEY-PREFIX) None))]
    {"ready" (sorted (gfor v roots :if (= v.state CodeState.READY) (bare v.revision)))
     "preparing" (sorted (gfor v roots :if (= v.state CodeState.PREPARING) (bare v.revision)))
     "failed" (lfor v roots :if (and (= v.state CodeState.FAILED) (is-not v.failure None))
                    {"key" (bare v.revision) "kind" v.failure.kind.value "detail" v.failure.detail
                     "retryable" v.failure.retryable})
     "capacity" capacity}))


(deff env-heartbeat-part [#^ dict report #^ str platform]  ; defk にできない: worker の coordinator への口(worker/protocol/coordinator_link)と sim の宿が同じ形を作る純粋な判断
  {:pre [(: report dict) (: platform str)] :post [(: % dict)] :tags {:context "worker" :role "protocol"}}
  "root の姿(env-report)を heartbeat の本文に足す欄(platform・envs・envCapacity)にするため。"
  {"platform" platform
   "envs" {"ready" (get report "ready") "preparing" (get report "preparing") "failed" (get report "failed")}
   "envCapacity" (get report "capacity")})


(deff heartbeat-body [* #^ str name #^ tuple provides #^ tuple exclusive #^ str node #^ int capacity #^ dict versions
                      #^ list statuses #^ str endpoint #^ str boot #^ int boot-at #^ dict tools]  ; defk にできない: worker の coordinator への口(worker/protocol/coordinator_link)と sim の宿が同じ形を作る純粋な判断
  {:pre [(: name str) (: provides tuple) (: exclusive tuple) (: node str) (: capacity int) (: versions dict) (: statuses list)
         (: endpoint str) (: boot str) (: boot-at int) (: tools dict)] :post [(: % dict)]
   :tags {:context "worker" :role "protocol"}}
  "POST /heartbeat の本文(生存・能力・版・状態の報告・世代)を作るため。実行環境の root の名乗り(env-body)は本番の worker だけが足す。"
  {"name" name "provides" (list provides) "exclusive" (list exclusive) "node" node "capacity" capacity "versions" versions
   "statuses" statuses "endpoint" endpoint "boot" boot "bootAt" boot-at
   "format" PROTOCOL-FORMAT
   "tools" tools})


(deff status-report [#^ tuple statuses #^ dict task-echo #^ dict results]  ; defk にできない: worker の coordinator への口(worker/protocol/coordinator_link)と sim の宿が同じ形を作る純粋な判断
  {:pre [(: statuses tuple) (: task-echo dict) (: results dict)] :post [(: % list)] :tags {:context "worker" :role "protocol" :spells "json"}}
  "状態の行の列を heartbeat の statuses にするため。終わった task には結果(results の task の id → 詰めた結果の文字列 か None =
   結果なし)を、切り離した task には置かれた時の返事の行(task-echo の id → 行 — 欄 task)を添える。"
  (lfor s statuses
    :setv row (status-row s)
    :setv echo (if (.startswith s.name "task/") (.get task-echo (cut s.name 5 None)) None)
    :setv row (if (is echo None) row (| row {"task" echo}))
    :setv done (finished-task-id s)
    (if (is done None) row (| row {"result" (.get results done)}))))


(defn #^ dict status-row [#^ JobStatus s]
  {"name" s.name "phase" s.phase.value "desiredRevision" s.desired-revision
   "runningRevision" s.running-revision "pid" s.pid "attempts" s.attempts "detail" s.detail
   ;; 動いている process の世代(coordinator の readiness と計器はこれと一致する報告だけを数える)。
   "instance" s.instance "specHash" s.spec-hash "placement" s.placement "retiredFrom" s.retired-from
   ;; 実行環境の準備の失敗(ENV-FAILED の行だけ): coordinator が置き直すか・答えの型を決める。
   #** (if (is s.failure None) {} {"failureKind" s.failure.kind.value "retryable" s.failure.retryable})
   ;; 入口の検めの姿(検めが通っていない間だけ — 2026-09-27): 状態・今の検めの経過の秒・回数・直前の失敗の理由。
   #** (if (is s.probe None) {} {"probe" {"state" s.probe.state "elapsedSeconds" s.probe.elapsed-seconds
                                          "attempts" s.probe.attempts "lastFailure" s.probe.last-failure}})})
