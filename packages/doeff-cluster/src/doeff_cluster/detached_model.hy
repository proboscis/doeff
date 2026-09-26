;;; 切り離した task(呼び手と寿命を切り離した task)の effect と、答えの型(2026-09-25)。
;;;
;;;   (<- submitted (SubmitDetached (summarize rows) :env "myapp.envs:board_env" :key job-id
;;;                                 :requires #((Requirement "kind" "k3s"))))
;;;   ... 呼び手の process が消えてもよい ...
;;;   (<- outcome (AwaitDetached job-id))          ; 別の process からでも、同じ key で待てる
;;;
;;; RemoteJob(remote_model.hy)との違い: RemoteJob は呼び手の問い合わせが lease を延ばし、呼び手が抜けると task も落ちる。
;;; 切り離した task は呼び手の決めた job id(key)で冪等に送り、lease は担い手の worker が延ばし、呼び手が消えても続く。
;;; 結果は終わった後も(明示の解放か保持の期限まで)持っておくので、後から何度でも同じ答えを受け取れる。
;;;
;;; 失敗は例外ではなく値(DetachedOutcome)で返す。例外にするのは呼び手の誤り(送れない値 UnsendableProgram・同じ key の別の仕事・
;;; 上限越え DetachedRefused)だけ。
;;;
;;; 2 つの handler(detached.hy):
;;;   detached-local   … 同じ VM の scheduler の task として走らせる(fake・模擬環境。外側の handler をそのまま継承する)
;;;   detached-cluster … coordinator の /detached の口へ出し、worker がその commit のコードを準備した子 process で走らせる
(require doeff-hy.macros [val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import doeff [EffectBase Program])
(import .remote_model [TaskSucceeded TaskFailed decode-outcome])
(import .cluster_model [Requirement])

(setv DETACHED-DEFAULT-LEASE-SECONDS 60.0)       ; 担い手の worker が沈黙してから消失とみなすまで
(setv DETACHED-DEFAULT-RETAIN-SECONDS 86400.0)   ; 終わった後に結果を持っておく長さ


;; --- effect ----------------------------------------------------------------------

(defclass [(dataclass :frozen True)] SubmitDetached [EffectBase]
  "program = 未実行の Program(値)・env = 実行先で組む handler の組の import path・key = 呼び手の決めた job id(冪等の単位)・
   requires = 実行先の条件(Requirement の tuple — worker の label の名と値)・lease-seconds = 担い手の worker が沈黙してから消失とみなすまで・retain-seconds = 結果の保持。
   答え = DetachedSubmitted。同じ key がまだ在れば何も作らない(created = False)。"
  (#^ Program program)
  (#^ str env)
  (#^ str key)
  (setv #^ (get tuple #(Requirement ...)) requires #())
  (setv #^ str name "")
  (setv #^ float lease-seconds DETACHED-DEFAULT-LEASE-SECONDS)
  (setv #^ float retain-seconds DETACHED-DEFAULT-RETAIN-SECONDS)
  (defn __post-init__ [self]
    (when (not (and (isinstance self.requires tuple) (all (gfor item self.requires (isinstance item Requirement)))))
      (raise (TypeError (.format "SubmitDetached.requires は Requirement の tuple: {!r}" self.requires))))))


(defclass [(dataclass :frozen True)] AwaitDetached [EffectBase]
  "答え = DetachedOutcome(終わった)か DetachedPending(timeout-seconds を過ぎてもまだ終わらない)。呼び手が抜けても task は続く。
   timeout-seconds = None なら終わるまで待つ。timeout は問い合わせの間隔の積算で数える。"
  (#^ str key)
  (setv #^ (| float None) timeout-seconds None))


(defclass [(dataclass :frozen True)] CancelDetached [EffectBase]
  "答え = bool。終わっていなければ取り消して True(以後の await は DetachedCancelled)。終わっていれば何もせず False(結果は保持)。
   知らない key も False。"
  (#^ str key))


(defclass [(dataclass :frozen True)] ReleaseDetached [EffectBase]
  "答え = bool。終わった task の保持を解いて True(以後その key は DetachedUnknown・同じ key で送り直せる)。知らない key は False。
   まだ終わっていなければ DetachedRefused(先に取り消す)。"
  (#^ str key))


(defclass [(dataclass :frozen True)] ReadRunners [EffectBase]
  "task を受ける担い手(worker)の名簿を読む — 生存と drain の正本は coordinator の名簿(heartbeat)1 つ。呼び手が置き先を選ぶ・
   機体の戻りを待つための読み。答え = RunnerFact の tuple(名の順)か RunnersUnreachable。")


(defclass [(dataclass :frozen True)] SimulateRunnerLoss [EffectBase]
  "模擬の担い手(worker)を死なせる。runner = 死なせる担い手の名(None = 全部)。答え = 消えた task の数。fake(detached-local)と
   検の組だけが答える — 本番の handler の組には答える者が無い(本物の worker の死は外で起きる)。"
  (setv #^ (| str None) runner None))


(defclass [(dataclass :frozen True)] SimulateRunnerDrain [EffectBase]
  "模擬の担い手を drain にする(新しい task を置かない・走っている task は続く・名簿には live のまま残る — 抜けるのは担い手の process が止まった時 = SimulateRunnerLoss)。答え = その担い手で
   まだ走っている task の数。fake と検の組だけが答える(本番の drain は coordinator の POST /workers/<名>/drain)。"
  (#^ str runner))


(defclass [(dataclass :frozen True)] SimulateRunnerReturn [EffectBase]
  "模擬の担い手を戻す(生きていて drain でない — 死んだ・抜けた担い手の作り直し)。答え = None。fake と検の組だけが答える。"
  (#^ str runner))


(defclass [(dataclass :frozen True)] SimulateCoordinatorOutage [EffectBase]
  "模擬の coordinator に seconds 秒届かなくする(作り直しの最中)。その間の送りと名簿の読みは届かず、待ちはまだ終わっていない
   答えを返す。走っている task は止めない(担い手は coordinator の途絶で task を止めない)。答え = None。fake と検の組だけが答える。"
  (#^ float seconds))


;; --- 答え --------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] DetachedSubmitted []
  (#^ str key)
  (#^ bool created))


(defclass [(dataclass :frozen True)] DetachedSucceeded []
  "Program が値を返した。"
  (#^ object value))


(defclass [(dataclass :frozen True)] DetachedFailed []
  "Program が例外を投げた(業務の失敗)。kind / message / traceback は文字列。error は例外そのもの(復元できなければ None)。"
  (#^ str kind)
  (#^ str message)
  (#^ str traceback)
  (setv #^ object error None))


(defclass [(dataclass :frozen True)] DetachedLost []
  "task が消えた: 担い手の worker が死んだ(lease 切れ・worker の process の作り直し)か、子 process が結果を書かずに終わった。
   走らせ直さない。"
  (#^ str reason))


(defclass [(dataclass :frozen True)] DetachedCancelled []
  "取り消した。")


(defclass [(dataclass :frozen True)] DetachedVersionMismatch []
  "送り手と受け側の Python / cloudpickle / doeff の版が合わない(合う worker が無い・子 process が復元の前に断った)。
   diffs = 食い違った欄(remote_model.VersionDiff の tuple — 欄の名・送り手の値・env の値)・env-key = 実行した env のキー
   (子 process が断った時だけ在る)。"
  (#^ str detail)
  (setv #^ tuple diffs #())
  (setv #^ str env-key ""))


(defclass [(dataclass :frozen True)] DetachedUnrunnable []
  "版は合うが走らせられない(label の合う worker が無い・その commit のコードを準備できない・Program を復元できない)。
   実行環境(runtime env)を準備できない時は DetachedEnvUnavailable。"
  (#^ str detail))


(defclass [(dataclass :frozen True)] DetachedEnvUnavailable []
  "実行環境(runtime env)を準備できなかった。kind = runtime_env_model.EnvFailureKind の値(repo-denied・commit-missing・
   lock-mismatch 等)・retryable = 一時の失敗だった(coordinator は起動前の task を別の worker へ 2 回まで置き直した上での答え)。
   どれも子 process を起こす前に起きるので、Program は 1 度も走っていない。"
  (#^ str kind)
  (#^ str detail)
  (#^ bool retryable))


(defclass [(dataclass :frozen True)] DetachedUnknown []
  "その key を知らない(送っていない・解放した・保持の期限を過ぎた)。"
  (#^ str key))


(defclass [(dataclass :frozen True)] DetachedPending []
  "await の timeout を過ぎても終わっていない。phase = queued | assigned・runner = 置いた担い手の名(assigned の時だけ・届かない時は空)。"
  (#^ str key)
  (#^ str phase)
  (setv #^ str runner ""))


;; --- 担い手の名簿 --------------------------------------------------------------------------
;; RunnerFact = 担い手 1 つ: name = worker の名・labels = label の (名 値) の組の tuple(名の順)・live = coordinator の名簿で
;;   生きている(heartbeat が lease の内)・draining = 新しい task を受けない。
;; RunnersUnreachable = coordinator に届かず名簿を読めなかった(担い手の生死は分からない — 死んだとみなさない)。

(defrecord RunnerFact
  #^ str name
  #^ (get tuple #((get tuple #(str str)) ...)) labels
  #^ bool live
  #^ bool draining)

(defrecord RunnersUnreachable
  #^ str detail)

(val RunnersAnswer (| (get tuple #(RunnerFact ...)) RunnersUnreachable))


(setv DetachedOutcome (| DetachedSucceeded DetachedFailed DetachedLost DetachedCancelled DetachedVersionMismatch
                         DetachedUnrunnable DetachedEnvUnavailable DetachedUnknown))
(setv DetachedAwaited (| DetachedOutcome DetachedPending))


(defclass DetachedRefused [Exception]
  "coordinator が要求を断った(呼び手の誤り): 同じ key の別の仕事(409)・欄の誤り(400)・上限越え(429)・まだ終わっていない task の
   解放(409)。"
  (defn __init__ [self #^ int status #^ str message]
    (.__init__ (super) (.format "{}: {}" status message))
    (setv self.status status self.message message)))


;; --- 純粋な換算 ----------------------------------------------------------------------

(setv OPEN-PHASES #("queued" "preparing" "assigned"))


(defn #^ DetachedOutcome outcome-from-task-outcome [#^ (| TaskSucceeded TaskFailed) outcome]
  "子 process の結果(remote_model の TaskSucceeded / TaskFailed)→ 答えの型。子 process が版の違いで復元を断ったら版の不一致。"
  (cond
    (isinstance outcome TaskSucceeded) (DetachedSucceeded outcome.value)
    (= outcome.kind "VersionMismatch")
      (DetachedVersionMismatch outcome.message
                               :diffs (getattr outcome.error "diffs" #())
                               :env-key (getattr outcome.error "env_key" ""))
    True (DetachedFailed outcome.kind outcome.message outcome.traceback outcome.error)))


(defn #^ DetachedOutcome decoded-result [#^ str blob]
  "結果の blob → 答えの型。呼び手の側で復元できない結果(呼び手に無い例外の型など)は DetachedFailed(kind UndecodableResult)。"
  (try
    (setv outcome (decode-outcome blob))
    (except [error Exception]
      (return (DetachedFailed "UndecodableResult"
                              (.format "結果を呼び手の側で復元できない: {}: {}" (. (type error) __name__) error) "" None))))
  (outcome-from-task-outcome outcome))


(defn #^ (| DetachedOutcome None) outcome-of-view [#^ dict view]
  "純粋: coordinator の GET /detached/<key> の答え → 答えの型(まだ終わっていなければ None)。"
  (setv phase (get view "phase") detail (.get view "detail" ""))
  (cond
    (= phase "unknown") (DetachedUnknown (get view "key"))
    (in phase OPEN-PHASES) None
    (and (= phase "finished") (is-not (.get view "result") None)) (decoded-result (get view "result"))
    (= phase "finished") (DetachedLost (.format "結果が無い({})" detail))
    (= phase "lost") (DetachedLost detail)
    (= phase "cancelled") (DetachedCancelled)
    (= phase "version-mismatch") (DetachedVersionMismatch detail)
    (= phase "env-failed") (DetachedEnvUnavailable (.get view "failureKind" "") detail (bool (.get view "retryable" False)))
    (in phase #("failed" "code-failed")) (DetachedUnrunnable detail)
    True (raise (ValueError (.format "知らない phase: {!r}" phase)))))
