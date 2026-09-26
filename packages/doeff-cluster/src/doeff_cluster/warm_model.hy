;;; 実行環境の先読み(warm)の effect と型(2026-09-26・設計 worker-runtime-env.md 節 3.2・決定 D9)。
;;;
;;; 送り手(常駐の service 等)は、task を送る前に自分の実行環境を温めるよう頼む:
;;;
;;;   (<- state WarmState (WarmRuntimeEnv env requires 600.0 "svc-a@<版>"))
;;;   (<- again WarmState (ReadWarmState state.key))
;;;   (> (len again.ready) 0)    ; requires の合う・生きていて drain 中でない worker の 1 台以上で準備済み
;;;
;;; coordinator は温める表(行 = 宣言と requires の組)を持ち、label の合う worker の heartbeat の返事に載せる。worker は job の準備より
;;; 低い優先度で root を準備し、準備済みのキーを heartbeat で名乗る。準備の時間を task の待ちに入れないため(TI3 との関係は設計 節 3.2)。
;;; 使い方(いつ温め、いつ Ready を出すか)は送り手の方針で、ここは仕組みだけ。
;;;
;;; handler: 同じ VM の模擬 = detached.hy の detached-local・本番 = detached.hy の warm-cluster(POST /warm・GET /warm/<キー>)。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import hashlib)
(import json)
(import doeff [EffectBase])
(import .runtime_env_model [RuntimeEnv env-key])
(import .cluster_model [Requirement])

(val WARM-KEY-LENGTH 24)


(defrecord WarmFailure
  "温める準備の失敗 1 つ: worker の名・EnvFailureKind の値の綴り・理由・一時か。"
  (#^ str worker)
  (#^ str kind)
  (#^ str detail)
  (#^ bool retryable))


(defrecord WarmState
  "温める表の行 1 つの今の姿。key = 行のキー(宣言と requires の組)・ready / preparing = 行の requires(label・専用の印・宣言の道具)に
   合い、生きていて drain 中でない worker のうち準備済み / 準備中の worker の名・failed = 準備に失敗した worker(WarmFailure)・
   until-ms = 行の期限(coordinator の時計の epoch ミリ秒)。"
  (#^ str key)
  (#^ tuple ready)
  (#^ tuple preparing)
  (#^ tuple failed)
  (#^ int until-ms))


(defclass [(dataclass :frozen True)] WarmRuntimeEnv [EffectBase]
  "env を requires の合う worker で温めるよう頼む(同じ env と requires の組は同じ行 — 頼み直すと期限だけ延びる)。
   ttl-seconds = 行の期限(過ぎた行は配らず・掃除の固定からも外れる)・holder = 頼んだ主体(記録と表示だけ)。答え = WarmState。"
  (#^ RuntimeEnv env)
  (#^ tuple requires)
  (#^ float ttl-seconds)
  (#^ str holder))


(defclass [(dataclass :frozen True)] ReadWarmState [EffectBase]
  "温める表の行 key の今の姿を読む。答え = WarmState(表に無い行は ready も preparing も空・until-ms = 0)。"
  (#^ str key))


(defk warm-key [env requires]
  {:pre [(: env RuntimeEnv) (: requires tuple)] :post [(: % str) (= (len %) WARM-KEY-LENGTH)]}
  "温める表の行のキー(定義点はここ 1 つ)= 宣言のキー(platform を含まない)と requires の組の sha256 の頭 24 桁。
   worker の root のキーは platform を含むので別の物(coordinator は worker の platform ごとに root のキーを計算して照らす)。"
  (<- declared str (env-key env ""))
  (val labels (sorted (gfor r requires [r.label r.value])))
  (val text (json.dumps {"env" declared "requires" labels} :sort-keys True :separators #("," ":")))
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
