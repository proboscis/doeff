;;; 実行環境の先読み(warm)の effect と型(2026-09-26・設計 worker-runtime-env.md 節 3.2・決定 D9)。
;;;
;;; 送り手(常駐の service 等)は、task を送る前に自分の実行環境を温めるよう頼む:
;;;
;;;   (<- state WarmAnswer (WarmRuntimeEnv env (frozenset ["gpu"]) 600.0 "svc-a@<版>"))
;;;   (<- again WarmAnswer (ReadWarmState state.key))
;;;   (> (len again.ready) 0)    ; needs の合う・生きていて drain 中でない worker の 1 台以上で準備済み
;;;
;;; 答えは WarmAnswer = WarmState か WarmUnreachable(coordinator の /warm に届かなかった — 接続の失敗・5xx。2026-09-28)。
;;; 届かないは「温まっていない」と同じに読む値で、呼び手は例外で落ちずに次の拍で頼み直す。
;;;
;;; coordinator は温める表(行 = 宣言と needs の組)を持ち、能力の合う worker の heartbeat の返事に載せる。worker は job の準備より
;;; 低い優先度で root を準備し、準備済みのキーを heartbeat で名乗る。準備の時間を task の待ちに入れないため(TI3 との関係は設計 節 3.2)。
;;; 使い方(いつ温め、いつ Ready を出すか)は送り手の方針で、ここは仕組みだけ。
;;;
;;; handler: 本番 = detached.hy の warm-cluster(POST /warm・GET /warm/<キー>)。手元では sim-cluster(local.hy)の宿が同じ要求の形で答える。
(require doeff-hy.macros [defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import hashlib)
(import json)
(import doeff [EffectBase])
(import .runtime_env_model [RuntimeEnv env-key])
(import doeff_cluster.shared.core.capabilities [effect-needs-problem])

(val WARM-KEY-LENGTH 24)


(defrecord WarmFailure
  "温める準備の失敗 1 つ: worker の名・EnvFailureKind の値の綴り・理由・一時か。"
  (#^ str worker)
  (#^ str kind)
  (#^ str detail)
  (#^ bool retryable))


(defrecord WarmState
  "温める表の行 1 つの今の姿。key = 行のキー(宣言と needs の組)・ready / preparing = 行の needs(能力・専用の能力・宣言の道具)に
   合い、生きていて drain 中でない worker のうち準備済み / 準備中の worker の名・failed = 準備に失敗した worker(WarmFailure)・
   until-ms = 行の期限(coordinator の時計の epoch ミリ秒)。"
  (#^ str key)
  (#^ tuple ready)
  (#^ tuple preparing)
  (#^ tuple failed)
  (#^ int until-ms))


(defrecord WarmUnreachable
  "coordinator の /warm に届かなかった(接続の失敗・coordinator の 5xx — 入れ替えの最中や網の途絶)。温まったかは分からないので、呼び手は
   温まっていないと同じに読み、次の拍で頼み直す(同じ組の頼み直しは同じ意味)。detail = 失敗の種類(接続の失敗か、返った HTTP の状態)と
   理由。前例 = 切り離した task の口の DetachedUnreachable(detached_model.hy — 送れなかったを値で返す形)。"
  (#^ str detail))

;; WarmRuntimeEnv と ReadWarmState の答え: 行の今の姿か、届かなかったか。
(val WarmAnswer (| WarmState WarmUnreachable))


(defclass [(dataclass :frozen True)] WarmRuntimeEnv [EffectBase]
  "env を needs の合う worker で温めるよう頼む(needs = 要る能力の名の frozenset・同じ env と needs の組は同じ行 — 頼み直すと期限だけ延びる)。
   ttl-seconds = 行の期限(過ぎた行は配らず・掃除の固定からも外れる)・holder = 頼んだ主体(記録と表示だけ)。
   答え = WarmAnswer(WarmState か、coordinator に届かなかった WarmUnreachable)。"
  (#^ RuntimeEnv env)
  (#^ frozenset needs)
  (#^ float ttl-seconds)
  (#^ str holder)
  (defn #^ None __post-init__ [self]
    "needs を作る時に検める(空・旧い形を断る — cluster_model.effect-needs-problem)。"
    (setv problem (effect-needs-problem self.needs))
    (when problem (raise (TypeError (+ "WarmRuntimeEnv.needs: " problem))))))


(defclass [(dataclass :frozen True)] ReadWarmState [EffectBase]
  "温める表の行 key の今の姿を読む。答え = WarmAnswer(WarmState — 表に無い行は ready も preparing も空・until-ms = 0 — か、
   coordinator に届かなかった WarmUnreachable)。"
  (#^ str key))


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
