;;; coordinator の温める表の口の純粋な判断(2026-09-26 — 効果 WarmRuntimeEnv・ReadWarmState の本番の答え)。
;;;
;;;   POST /warm       {runtimeEnv requires ttlSeconds holder} → 行を書く(同じ宣言と requires の組は同じ行 — 期限だけ延びる)→ WarmState
;;;   GET  /warm/<キー>                                        → WarmState(表に無ければ 404)
;;;
;;; 行を worker に配るのは cluster_policy.heartbeat-reply(warms-for)、期限を過ぎた行を消すのは cluster_policy.reconcile(sweep-warms)。
(import dataclasses [replace])
(import doeff [run])
(import .cluster_model [ClusterState ClusterTiming WarmEntry requirements-of])
(import .cluster_policy [alive labels-satisfy tolerates tools-cover root-key-on draining-workers BOARD-MAX-TTL-SECONDS])
(import .runtime_env_model [runtime-env-of-json RuntimeEnvInvalid])
(import .warm_model [WarmState WarmFailure warm-key warm-state->json])


(defn #^ WarmState warm-view [#^ ClusterState state #^ WarmEntry entry #^ int now #^ ClusterTiming timing]
  "行 1 つの今の姿: 行の requires に合い、生きていて drain 中でない worker を、その worker の platform の root のキーで照らして分ける
   (送り手が「1 台以上で準備済み」を読んで Ready を決めるため)。"
  (setv draining (draining-workers state now)
        ready [] preparing [] failed [])
  (for [w (sorted (.values state.workers) :key (fn [w] w.name))]
    (when (and (alive now w timing.lease-ms) (not-in w.name draining) w.platform
               (labels-satisfy entry.requires w) (tolerates entry.requires w) (tools-cover entry.runtime-env w))
      (setv key (root-key-on entry.runtime-env w))
      (cond
        (in key w.env-ready) (.append ready w.name)
        (in key w.env-preparing) (.append preparing w.name)
        True (for [f w.env-failed]
               (when (= f.key key)
                 (.append failed (WarmFailure :worker w.name :kind f.kind :detail f.detail :retryable f.retryable)))))))
  (WarmState :key entry.key :ready (tuple ready) :preparing (tuple preparing) :failed (tuple failed) :until-ms entry.until-ms))


(defn #^ tuple warm-write [#^ ClusterState state #^ dict body #^ int now #^ str actor #^ ClusterTiming timing]
  "POST /warm: 行を書いて #(次の状態 status 本文) を返す。宣言の誤り・期限の範囲の外は 400。"
  (setv declared (.get body "runtimeEnv") ttl (.get body "ttlSeconds"))
  (when (not (isinstance declared dict))
    (return #(state 400 {"error" "runtimeEnv は JSON の object"})))
  (when (not (and (isinstance ttl #(int float)) (< 0 ttl (+ BOARD-MAX-TTL-SECONDS 1))))
    (return #(state 400 {"error" (.format "ttlSeconds は 0 より大きく {} 以下: {!r}" BOARD-MAX-TTL-SECONDS ttl)})))
  (try
    (setv env (run (runtime-env-of-json declared)))
    (except [error RuntimeEnvInvalid]
      (return #(state 400 {"error" (.format "runtimeEnv が誤っている: {}" error)}))))
  (setv requires (requirements-of (.get body "requires" {}))
        key (run (warm-key env requires))
        entry (WarmEntry key declared requires (+ now (int (* 1000 ttl))) (str (.get body "holder" actor)))
        after (replace state :warms (| state.warms {key entry})))
  #(after 200 (run (warm-state->json (warm-view after entry now timing)))))


(defn #^ tuple warm-read [#^ ClusterState state #^ str key #^ int now #^ ClusterTiming timing]
  "GET /warm/<キー>: 行の今の姿。表に無い(期限で消えた)行は 404。"
  (setv entry (.get state.warms key))
  (if (is entry None)
      #(state 404 {"error" (.format "温める表に行 {} が無い(期限で消えたか、書かれていない)" key)})
      #(state 200 (run (warm-state->json (warm-view state entry now timing))))))
