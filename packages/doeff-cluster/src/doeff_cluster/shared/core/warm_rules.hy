;;; 実行環境の先読み(warm)の純粋な判断: 温める表の行のキーと、行の今の姿(WarmState)と通信の本文との往復。
;;; 型と effect は doeff_cluster.shared.intent.warm_model。coordinator の表(warm_policy)と送り手の handler(detached.hy の warm-cluster)が同じ形を使う。
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import hashlib)
(import json)
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])
(import doeff_cluster.shared.intent.warm_model [WARM-KEY-LENGTH WarmFailure WarmState WarmRuntimeEnv WarmAnswer WarmUnreachable
                                                WarmReady WarmFailed WarmWaitExpired])
(import doeff_cluster.shared.core.runtime_env_rules [env-key])
(import doeff_cluster.shared.core.capabilities [effect-needs-problem])


(defk warm-runtime-env [env needs ttl-seconds holder]
  {:pre [(: env RuntimeEnv) (: needs (| frozenset tuple list set dict str None)) (: ttl-seconds float) (: holder str)]
   :post [(: % WarmAnswer)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "WarmRuntimeEnv の構築関数 — 作り手はここを通す。needs が能力の名の空でない frozenset でなければ(空・旧い形・label の形の名)
   頼む前に TypeError で断る。検めた WarmRuntimeEnv をその場で出し、答え(WarmState か WarmUnreachable)を返す(defk は作った effect を
   値として返せない — doeff-hy の _guard-performed)。needs の検めを型(intent)の外のここに置くのは、intent が core の判断を読まないため(#2564)。"
  (<- problem (effect-needs-problem needs))
  (when problem (raise (TypeError (+ "WarmRuntimeEnv.needs: " problem))))
  (<- answer WarmAnswer (WarmRuntimeEnv env needs ttl-seconds holder))
  answer)


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


(defk warm-wait-answer [read key waited timeout-seconds]
  {:pre [(: read WarmAnswer) (: key str) (: waited float) (: timeout-seconds float)] :post [(: % (| WarmReady WarmFailed WarmWaitExpired None))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "AwaitWarm の 1 回の読み(read)を答えにするため(本番の warm-cluster と sim の宿が同じ判断を呼ぶ — 定義点は 1 つ): 準備済みが 1 台以上 =
   WarmReady・準備済みも準備中も無く失敗が在り、全部 retryable でない = WarmFailed・待った秒が期限に届いた = WarmWaitExpired(最後の読み
   つき)・それ以外(準備中・retryable の失敗・表に無い行・届かない)= None(待ち続ける)。"
  (match read
    (WarmState) :if read.ready (WarmReady :state read)
    (WarmState) :if (and (not read.preparing) read.failed (not (any (gfor f read.failed f.retryable)))) (WarmFailed :state read)
    _ (if (>= waited timeout-seconds)
          (WarmWaitExpired :key key :last read :waited-seconds waited)
          None)))
