;;; 実行環境の先読み(warm)の純粋な判断: 温める表の行のキーと、行の今の姿(WarmState)と通信の本文との往復。
;;; 型と effect は doeff_cluster.shared.intent.warm_model。coordinator の表(warm_policy)と送り手の handler(detached.hy の warm-cluster)が同じ形を使う。
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import hashlib)
(import json)
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])
(import doeff_cluster.shared.intent.warm_model [WARM-KEY-LENGTH WarmFailure WarmState])
(import doeff_cluster.shared.core.runtime_env_rules [env-key])


(defk warm-key [env needs]
  {:pre [(: env RuntimeEnv) (: needs tuple)] :post [(: % str) (= (len %) WARM-KEY-LENGTH)]}
  "温める表の行のキー(定義点はここ 1 つ)= 宣言のキー(platform を含まない)と needs の組の sha256 の頭 24 桁。
   worker の root のキーは platform を含むので別の物(coordinator は worker の platform ごとに root のキーを計算して照らす)。"
  (<- declared str (env-key env ""))
  (val text (json.dumps {"env" declared "needs" (sorted needs)} :sort-keys True :separators #("," ":")))
  (cut (.hexdigest (hashlib.sha256 (.encode text "utf-8"))) 0 WARM-KEY-LENGTH))


(defk warm-state->json [state]
  {:pre [(: state WarmState)] :post [(: % dict)]}
  "WarmState → 通信の本文。"
  {"key" state.key "ready" (list state.ready) "preparing" (list state.preparing)
   "failed" (lfor f state.failed {"worker" f.worker "kind" f.kind "detail" f.detail "retryable" f.retryable})
   "untilMs" state.until-ms})


(defn #^ WarmState warm-state-of-json [#^ dict value]  ; defk にできない: coordinator の純粋な判断と HTTP の handler の境界で読む
  "通信の本文 → WarmState。"
  (WarmState :key (get value "key") :ready (tuple (get value "ready")) :preparing (tuple (get value "preparing"))
             :failed (tuple (gfor f (get value "failed")
                                  (WarmFailure :worker (get f "worker") :kind (get f "kind") :detail (get f "detail")
                                               :retryable (bool (get f "retryable")))))
             :until-ms (int (get value "untilMs"))))
