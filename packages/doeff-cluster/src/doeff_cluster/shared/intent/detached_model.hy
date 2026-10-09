;;; 切り離した task(呼び手と寿命を切り離した task)の effect と、答えの型(2026-09-25)。
;;;
;;;   (<- submitted (submit-detached-task (summarize foundation rows) :key job-id :needs (frozenset ["gpu"]) :environ #((EnvVar :name "ROWS_URL" :value url))))   ; 構築関数(core の detached_rules)が needs を検めて出す
;;;   ... 呼び手の process が消えてもよい ...
;;;   (<- outcome (AwaitDetached job-id))          ; 別の process からでも、同じ key で待てる
;;;
;;; RemoteJob(remote_model.hy)との違い: RemoteJob は呼び手の問い合わせが lease を延ばし、呼び手が抜けると task も落ちる。
;;; 切り離した task は呼び手の決めた job id(key)で冪等に送り、lease は担い手の worker が延ばし、呼び手が消えても続く。
;;; 結果は終わった後も(明示の解放か保持の期限まで)持っておくので、後から何度でも同じ答えを受け取れる。
;;;
;;; 失敗は例外ではなく値(DetachedOutcome)で返す。例外にするのは呼び手の誤り(送れない値 UnsendableProgram・同じ key の別の仕事・
;;; 上限越え DetachedRefused)だけ。
;;; ここは型と定数だけ。子の結果・coordinator の答えから答えの型への換算(outcome-from-task-outcome・decoded-result・
;;; outcome-of-view)は handler と同じ doeff_cluster.shared.protocol.detached。
;;;
;;; handler(detached.hy):
;;;   detached-cluster … coordinator の /detached の口へ出し、worker がその commit のコードを準備した子 process で走らせる
;;; 手元で確かめる時は handler を被せず、手元の runner sim-cluster(local.hy)の宿が同じ要求の形で本物の coordinator の口へ送る
;;; (2026-09-28 — 同じ VM で走らせる模擬 detached-local は、呼び手の外側の handler を継いで足りない handler を黙って補うので消した)。
(require doeff-hy.macros [val defeffect])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(require doeff-hy.record [defrecord defwire])
(import dataclasses [dataclass field])
(import doeff [EffectBase Program])
(import .runtime_env_model [EnvVar])

(setv DETACHED-DEFAULT-LEASE-SECONDS 60.0)       ; 担い手の worker が沈黙してから消失とみなすまで
(setv DETACHED-DEFAULT-RETAIN-SECONDS 86400.0)   ; 終わった後に結果を持っておく長さ


;; --- effect ----------------------------------------------------------------------

(defclass [(dataclass :frozen True)] SubmitDetached [EffectBase]
  "program = 未実行の Program(値 — handler は Program の中の with-handlers で並べる・ADR-DOE-CLUSTER-001 R1・R2)・key = 呼び手の決めた job id(冪等の単位)・
   needs = 要る能力の名の frozenset(置く worker は needs ⊆ provides — ADR-DOE-CLUSTER-001 R4b)・lease-seconds = 担い手の worker が沈黙してから消失とみなすまで・retain-seconds = 結果の保持。
   答え = DetachedSubmitted か DetachedUnreachable(coordinator に届かなかった — 送れたかは分からない。key で冪等なので送り直してよい)。
   同じ key がまだ在れば何も作らない(created = False)。"
  (#^ Program program)
  (#^ str key)
  (setv #^ frozenset needs (frozenset))
  (setv #^ str name "")
  (setv #^ float lease-seconds DETACHED-DEFAULT-LEASE-SECONDS)
  (setv #^ float retain-seconds DETACHED-DEFAULT-RETAIN-SECONDS)
  ;; 子の環境変数(EnvVar の tuple・既定は空 — 名と値の規則は service の :environ と同じ EnvVar 1 つ)。同じ key の送り直しで違えば
  ;; 別の仕事(409)。名 → 値の写像では受けない(呼び手は境目で env-vars-of を通す・coordinator への本文は handler が env-mapping で綴る・#2179)。
  (setv #^ (get tuple #(EnvVar ...)) environ #())
  (defn #^ None __post-init__ [self]
    "environ が EnvVar の重ならない tuple であることを作る時に検める(名 → 値の写像・名の重なりを黙って受けない。予約の名・秘密の名は
     EnvVar が作る時に断る)。needs の検め(能力の名の空でない frozenset — 旧い Requirement の tuple を断る)は型の外 — 作り手は構築関数
     doeff_cluster.shared.core.detached_rules.submit-detached-task を通す(intent は core を読まない・#2564)。"
    (when (not (and (isinstance self.environ tuple) (all (gfor v self.environ (isinstance v EnvVar)))))
      (raise (TypeError (.format "SubmitDetached.environ: EnvVar の tuple(名 → 文字列の写像ではない — env-vars-of で組む): {!r}"
                                 self.environ))))
    (setv names (lfor v self.environ v.name))
    (setv twice (sorted (sfor n names :if (> (.count names n) 1) n)))
    (when twice
      (raise (TypeError (+ "SubmitDetached.environ: 名が重なる(写像に戻すと片方が黙って消える): " (.join "・" twice)))))))


(defclass [(dataclass :frozen True)] AwaitDetached [EffectBase]
  "答え = DetachedOutcome(終わった)か DetachedPending(timeout-seconds を過ぎてもまだ終わらない)か DetachedUnreachable(timeout-seconds
   を決めた待ちで coordinator に届かなかった — task の生死は分からない。死んだとみなさない。timeout-seconds = None の待ちは届くまで待つ)。呼び手が抜けても task は続く。
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


(defclass [(dataclass :frozen True)] AwaitRunnersChange [EffectBase]
  "名簿を写す呼び手が、coordinator の版(資源の spec / status が変わるたびに進む数 — GET /watch)が after から変わるまで待つ(上限
   timeout-seconds — coordinator の拍の刻で返るので、最大で拍 1 つ分長い)。名簿を周回ごとに読み直さず、変化で起きるための待ち(#1934)。
   版に入らない変化(worker の生死の切り替わり)は上限で起きて読み直す。答え = RunnersChange(版と変わったか)・RunnersWatchMissing
   (待つ口の無い旧い coordinator — 呼び手は周回に戻る)・RunnersUnreachable。after は前の答えの revision(最初は 0)。"
  (#^ int after)
  (setv #^ float timeout-seconds 1.0))


(defclass [(dataclass :frozen True)] AwaitServiceReady [EffectBase]
  "名を挙げた Service が Ready になるまで待つ(#3470 — 依る service の短い停止を落ちずに越える呼び手が、戻りを出来事として知るため。
   使い手 = 依る service の変化を待つ合図の源・その service への追記の書き手)。coordinator の Service の status.ready を読み、Ready でなければ
   coordinator の版(GET /watch)が変わるまで待って読み直す — 時間で起きて確かめない。上限は持たない(待つ側が上限つきで待つ)。
   coordinator に届かない間も待ち続ける(間を置いて問い直すのは答え手の中だけ)。答え = ServiceReady。"
  (#^ str name)
  (defn #^ None __post_init__ [self]
    (when (or (not (isinstance self.name str)) (not self.name))
      (raise (ValueError (.format "AwaitServiceReady.name は空でない Service の名: {!r}" self.name))))))


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
  "実行環境(runtime env)を準備できなかった。kind = runtime_env_model.EnvFailureKind の値(repo-unreachable・commit-missing・
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
;; RunnerFact = 担い手 1 つ: name = worker の名・provides / exclusive = 提供する能力・専用の能力の名の tuple(名の順)・live = coordinator の名簿で
;;   生きている(heartbeat が lease の内)・draining = 新しい task を受けない・task-room = 今この担い手が受けられる task の数(coordinator が
;;   task を置くのと同じ数え task-room-of — 使い手の置き手が満杯の担い手を候補から外すため・#3488)・node = 担い手が置かれた k8s の
;;   node の名(coordinator の Worker の資源の spec.node の写し — worker の --node。空 = k8s の外か名乗らない。使い手が task の走った
;;   物理の機体を知るため・#3747)。足すだけの欄なので既定は空(作り手が名乗らない担い手と同じ値)。
;; RunnersUnreachable = coordinator に届かず名簿を読めなかった(担い手の生死は分からない — 死んだとみなさない)。

(defrecord RunnerFact
  #^ str name
  #^ (get tuple #(str ...)) provides
  #^ (get tuple #(str ...)) exclusive
  #^ bool live
  #^ bool draining
  #^ int task-room
  (setv #^ str node ""))

(defrecord RunnersUnreachable
  #^ str detail)

(val RunnersAnswer (| (get tuple #(RunnerFact ...)) RunnersUnreachable))

;; --- Service の一覧(#3479) ---------------------------------------------------------------
;; ServiceFact = Service 1 つ: name・replicas = 宣言の台数(宣言の行に無ければ None)・failures = 置き先の担い手が報告した続けて落ちた回数
;;   (status.process.failures — 今の process が安定して動いていれば 0)・last-exit-code / last-exit-at-ms = 最後の終わりの code と時刻
;;   (epoch ミリ秒)。担い手の報告が無い(置き先が無い・報告の行が無い・欄を載せない担い手)欄は None — 0 と黙って倒さない。
;;   last-exit-at-ms は最後に終わったと知れた刻(#3672)— 機体が死んで worker の世代が入れ替わった時は、新しい世代の起動の刻を上限として
;;   数える(実の終わりはそれ以前)。世代が重なる時(退いた世代の process がまだ走る)は、退いた世代が報告した終わりの刻も大きい方の
;;   候補に入れる。coordinator を作り直しても戻らない(worker の保存の行から運ぶ)。注記: Service が別の worker へ置き直されると
;;   status.process は新しい担い手の行で、前の担い手の刻は出ない・沈黙が 7 日続いた worker を coordinator が忘れるとその刻も消える・
;;   刻はその worker の node の時計。
;;   revision = その Service の宣言の版(行の spec の revision — #2718 の子 S2a: 別の Service を宣言し直す前に、どの Service がどの版で
;;   動くかを照らす。一覧の答えの頭の revision は coordinator の状態の版で、これではない)。行に版が無ければ None。手で組む値(検の答え手)
;;   で版を問わない物は書かなくてよい(既定 None = 版を知らない — 照らす側は None を「照らせない」と名指す)。
;; ServicesUnreachable = coordinator に届かず一覧を読めなかった(落ちているかは分からない — 直ったとみなさない)。

(defrecord ServiceFact
  #^ str name
  #^ (| int None) replicas
  #^ (| int None) failures
  #^ (| int None) last-exit-code
  #^ (| int None) last-exit-at-ms
  (setv #^ (| str None) revision None))

(defrecord ServicesUnreachable
  #^ str detail)

(val ServicesAnswer (| (get tuple #(ServiceFact ...)) ServicesUnreachable))

;; effect は答えの型 ServicesAnswer を宣言する(利用側の `(<- services (ReadServices))` が答えの型を得る — 素の EffectBase では Any)ので、
;; 答えの型の後に置く。
(defeffect ReadServices
  "coordinator が預かる Service(常駐の job)の一覧と、置き先の担い手が報告した落ちた事実を読む(GET /resources/Service — #3479)。
   呼び手が落ち続ける job を見つけるために読む。答え = ServiceFact の tuple(名の順)か ServicesUnreachable。"
  {:answer ServicesAnswer
   :tags {:context "doeff-cluster" :role "intent"}})

;; GET /resources/Service の返事の本文のうち、Service の一覧(ServiceFact の tuple)が読む欄の型。本番の client(detached.services-read)・
;; sim(local.read-services)・自分で GET を送る利用側の handler が同じ型で読み込み、同じ関数(detached.service-facts-of-view)で
;; ServiceFact の tuple にする。読まない欄は捨てる(:unknown :ignore)。行・欄が無い時は None で、0 などの値で埋めない(#3479)。

(defwire ServiceProcessWire
  "一覧の行の status.process(置き先の担い手が報告した process の行)のうち一覧が読む欄: failures = 続けて落ちた回数・
   last-exit-code / last-exit-at-ms = 最後の終わりの code と時刻(epoch ミリ秒)。担い手が載せない欄は None。ほかの欄(phase・pid など)は
   読まない。"
  {:tags {:context "doeff-cluster" :role "type"} :names :camel :unknown :ignore}
  (setv #^ (| int None) failures None)
  (setv #^ (| int None) last-exit-code None)
  (setv #^ (| int None) last-exit-at-ms None))


(defwire ServiceRowStatusWire
  "一覧の行の status のうち一覧が読む欄: process = 置き先の担い手の報告の行(置き先が無い・報告の行が無ければ None)。"
  {:tags {:context "doeff-cluster" :role "type"} :names :camel :unknown :ignore}
  (setv #^ (| ServiceProcessWire None) process None))


(defwire ServiceRowSpecWire
  "一覧の行の spec のうち一覧が読む欄: replicas = 宣言した replicas の数・revision = その Service の宣言の版(#2718 の子 S2a — 一覧の
   本文の頭の revision は coordinator の状態の版で、これではない)。"
  {:tags {:context "doeff-cluster" :role "type"} :names :camel :unknown :ignore}
  (setv #^ (| int None) replicas None)
  (setv #^ (| str None) revision None))


(defwire ServiceRowWire
  "一覧の行 1 つのうち一覧が読む欄: name・spec・status(行に無ければ None)。"
  {:tags {:context "doeff-cluster" :role "type"} :names :camel :unknown :ignore}
  (#^ str name)
  (setv #^ (| ServiceRowSpecWire None) spec None)
  (setv #^ (| ServiceRowStatusWire None) status None))


(defwire ServiceListWire
  "GET /resources/Service の返事の本文のうち一覧が読む欄: items = Service の行の列。本文の頭の revision(coordinator の状態の版)は読まない。"
  {:tags {:context "doeff-cluster" :role "type"} :names :camel :unknown :ignore}
  (#^ (get tuple #(ServiceRowWire ...)) items))

;; AwaitRunnersChange の答え(#1934): RunnersChange = 待ちが返った(revision = 今の版 — 次の after・changed = after から変わったか。偽は
;;   上限で返った)・RunnersWatchMissing = 待つ口の無い旧い coordinator(404 — detail = 理由)。
(defrecord RunnersChange
  #^ int revision
  #^ bool changed)

(defrecord RunnersWatchMissing
  #^ str detail)

(val RunnersChangeAnswer (| RunnersChange RunnersWatchMissing RunnersUnreachable))

;; AwaitServiceReady の答え(#3470): Service name が Ready と読めた時の coordinator の版 revision(最初の読みで Ready なら 0)。
(defrecord ServiceReady
  #^ str name
  #^ int revision)


(defwire ServiceStatusWire
  "coordinator の GET /resources/Service/<名> の返事の status の欄のうち、AwaitServiceReady が読む所: ready = Ready | NotReady | Unknown
   (coordinator の resource_policy.service-readiness の語)。"
  {:tags {:context "doeff-cluster" :role "type"} :names :camel :unknown :ignore}
  (#^ str ready))


(defwire ServiceViewWire
  "coordinator の GET /resources/Service/<名> の返事のうち、AwaitServiceReady が読む欄(status)。ほかの欄(spec・世代など)は読まない。"
  {:tags {:context "doeff-cluster" :role "type"} :names :camel :unknown :ignore}
  (#^ ServiceStatusWire status))


(setv DetachedOutcome (| DetachedSucceeded DetachedFailed DetachedLost DetachedCancelled DetachedVersionMismatch
                         DetachedUnrunnable DetachedEnvUnavailable DetachedUnknown))
;; DetachedUnreachable = coordinator に届かなかった(作り直しの最中・網の途絶)。送りも待ちも同じ値で答え、本番の client と sim の宿が同じ形を返す —
;; 呼び手は「届かない」を例外でなく値で受け、task の死と取り違えない(2026-09-26)。
(defrecord DetachedUnreachable
  #^ str detail)

(setv DetachedAwaited (| DetachedOutcome DetachedPending DetachedUnreachable))
(setv DetachedSubmitAnswer (| DetachedSubmitted DetachedUnreachable))


(defclass DetachedRefused [Exception]
  "coordinator が要求を断った(呼び手の誤り): 同じ key の別の仕事(409)・欄の誤り(400)・上限越え(429)・まだ終わっていない task の
   解放(409)。"
  (defn #^ None __init__ [self #^ int status #^ str message]
    (.__init__ (super) (.format "{}: {}" status message))
    (setv self.status status self.message message)))


;; --- 純粋な換算 ----------------------------------------------------------------------

(setv OPEN-PHASES #("queued" "preparing" "assigned"))
;; GET /detached/<key> の 503 の phase(2026-09-27 — detached_policy.detached-read): coordinator が起きた直後で、行の無い key を
;; 知らないと言えない。呼び手は届かないと同じに扱う(DetachedUnreachable・期限の無い待ちは待ち続ける)。
(setv WARMING-PHASE "warming")
