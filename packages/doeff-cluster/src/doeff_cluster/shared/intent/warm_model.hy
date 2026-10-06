;;; 実行環境の先読み(warm)の effect と型(2026-09-26・設計 worker-runtime-env.md 節 3.2・決定 D9)。
;;;
;;; 送り手(常駐の service 等)は、task を送る前に自分の実行環境を温めるよう頼む:
;;;
;;;   (<- state WarmAnswer (warm-runtime-env env (frozenset ["gpu"]) 600.0 "svc-a@<版>"))   ; 構築関数(core の warm_rules)が needs を検めて出す
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
;;; ここは型だけ。行のキー(warm-key)と通信の本文との往復(warm-state->json・warm-state-of-json)は doeff_cluster.shared.core.warm_rules。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import .runtime_env_model [RuntimeEnv])

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
   until-ms = 行の期限(coordinator の時計の epoch ミリ秒)・memory-unmeasured = 行の needs に合う worker のどれかが、この行の root の先の組みを
   memory を測らずに始めた(cgroup v1・memory の file が無い・上限が無い — 組みの前の memory の判じ〔no-memory-room〕が効いていない。
   本番だけ黙って読めない形を頼み手が見えるように・#3748)。"
  (#^ str key)
  (#^ tuple ready)
  (#^ tuple preparing)
  (#^ tuple failed)
  (#^ int until-ms)
  (setv #^ bool memory-unmeasured False))


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
  ;; needs の検め(空・旧い形)は型の外 — 作り手は構築関数 doeff_cluster.shared.core.warm_rules.warm-runtime-env を通す
  ;; (intent は core を読まない・#2564)。
  (#^ str holder))


(defclass [(dataclass :frozen True)] ReadWarmState [EffectBase]
  "温める表の行 key の今の姿を読む。答え = WarmAnswer(WarmState — 表に無い行は ready も preparing も空・until-ms = 0 — か、
   coordinator に届かなかった WarmUnreachable)。"
  (#^ str key))


;; --- 組みの完成を待つ(#3668 (b)・2026-10-06) ---------------------------------------------------------------
;; 送り手(回の Program など)は頼んだ行が組み上がるまで待つ: 準備済みが 1 台以上なら WarmReady・準備中の台が無く恒久の失敗だけが残れば
;; WarmFailed・期限で WarmWaitExpired。待ちは coordinator の版の変化(GET /watch の long-poll)で起き、間隔で起きて確かめない — worker の
;; 組みの進み(heartbeat が名乗る env-ready・env-preparing・env-failed)は Worker の資源の行の status の env に載り、版を進める。
;; memory の線(anon の量・memory 不足の止め)で組みを始めるか止めるかの判断は、この効果の外(WarmRuntimeEnv を頼むかを決める使い手の側)—
;; AwaitWarm は coordinator の見え方を待つだけ。

(defrecord WarmReady
  "待った行が組み上がった(行の needs に合う worker の 1 台以上で準備済み)。state = その時の行の姿。"
  (#^ WarmState state))


(defrecord WarmFailed
  "待った行の組みが落ちた: 準備済みも準備中も無く、準備の失敗(state.failed)が全部 retryable でない。state = その時の行の姿
   (failed の各行に worker・kind・detail)。"
  (#^ WarmState state))


(defrecord WarmWaitExpired
  "期限まで組み上がりも落ちもしなかった。last = 最後に読んだ行の姿(WarmState か、届かなかった WarmUnreachable)・waited-seconds = 待った秒。"
  (#^ str key)
  (#^ (| WarmState WarmUnreachable) last)
  (#^ float waited-seconds))

;; AwaitWarm の答え。
(val WarmWaitAnswer (| WarmReady WarmFailed WarmWaitExpired))


(defclass [(dataclass :frozen True)] AwaitWarm [EffectBase]
  "温める表の行 key が組み上がるか落ちるまで、timeout-seconds を上限に待つ。答え = WarmWaitAnswer(WarmReady・WarmFailed・WarmWaitExpired)。
   表に無い行・coordinator に届かない間・準備中・retryable の失敗は待ち続ける(期限で WarmWaitExpired)。"
  (#^ str key)
  (#^ float timeout-seconds))
