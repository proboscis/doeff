;;; 切り離した task の 2 つの handler(effect は detached_model.hy)。業務のコードは同じまま、composition root がどちらを被せるかで
;;; 「手元の 1 process で系全体を模擬する」と「本番でクラスタに分散する」を切り替える。どちらも同じ契約:
;;;   - key で冪等に送る(同じ key がまだ在れば created = False・env / name / requires が違えば DetachedRefused)
;;;   - 呼び手が消えても(await が取り消されても)task は続く・後から同じ key で待てる
;;;   - 終わった結果は解放か保持の期限まで持つ・終わった後の取り消しは False で結果はそのまま
;;;   - 担い手の死 = DetachedLost(走らせ直さない)・結果の後の担い手の死では結果は変わらない
;;;   - 版の不一致 = DetachedVersionMismatch
;;;   - 置き先 = 生きていて drain でない、label の合う担い手。合う担い手が全部 drain 中なら待つ・合う担い手が居なければ
;;;     DetachedUnrunnable(本物の coordinator の place-tasks と同じ規則 — fake は同じ述語 labels-satisfy / tolerates を使う)
;;;   - 担い手の名簿(ReadRunners)= coordinator の名簿の生存と drain
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import datetime [datetime])
(import urllib.parse [quote :as url-quote])
(import httpx)
(import doeff_core_effects.scheduler [Spawn Wait Cancel Task TaskCancelledError])
(import doeff [Program])
(import doeff_time [Delay GetTime])
(import .clock [epoch-ms-of])
(import .coordinator_http [CoordinatorEndpoint send-idempotent REPLY-SECONDS])
(import doeff [run :as run-program])
(import .cluster_model [PROTOCOL-FORMAT WorkerInfo])
(import .runtime_env_model [RuntimeEnv EnvFailure runtime-env->json env-key current-platform])
(import .env_prepare [PrepareRequest KnownRoot EnvReady prepare-env])
(import .cluster_policy [ENV-RETRIES labels-satisfy tolerates])
(import .remote_model [encode-program current-versions version-mismatch failed-from])
(import .cluster_model [Requirement])
(import .warm_model [WarmRuntimeEnv ReadWarmState WarmState WarmFailure warm-key warm-state-of-json])
(import doeff_time [GetMonotonic])
(import .detached_model [SubmitDetached AwaitDetached CancelDetached ReleaseDetached ReadRunners SimulateRunnerLoss
                         SimulateRunnerDrain SimulateRunnerReturn SimulateCoordinatorOutage
                         DetachedSubmitted DetachedSucceeded DetachedLost DetachedCancelled DetachedVersionMismatch
                         DetachedEnvUnavailable DetachedUnrunnable
                         DetachedPending DetachedUnknown DetachedRefused DetachedOutcome DetachedAwaited
                         RunnerFact RunnersUnreachable RunnersAnswer
                         outcome-from-task-outcome outcome-of-view])


;; --- handler A: 同じ VM の scheduler の task として走らせる(fake・模擬環境) -------------------------

;; 担い手を名指さずに作った置き場の、ただ 1 つの担い手(label を持たない — どの task も置ける)。
(val DEFAULT-RUNNER "local")

;; 真実の記録の op(fake の側が見た事実 — 呼び手の信念ではない)。
(val EVENT-SUBMITTED "submitted")     ; 送りを受けた(置き先はまだ)
(val EVENT-STARTED "started")         ; 担い手に置いて走らせ始めた
(val EVENT-SUCCEEDED "succeeded")     ; Program が値を返した
(val EVENT-FAILED "failed")          ; Program が例外で抜けた・env を準備できなかった・版が合わない
(val EVENT-LOST "lost")             ; 担い手ごと消えた
(val EVENT-CANCELLED "cancelled")     ; 取り消した
(val EVENT-UNRUNNABLE "unrunnable")  ; 置ける担い手が無い

;; DetachedEvent = fake の真実の記録 1 行: at = 仮想の epoch ミリ秒・key・op(EVENT-*)・runner = 置いた担い手(置く前は空)。
(defrecord DetachedEvent
  #^ int at
  #^ str key
  #^ str op
  #^ str runner)


(defclass LocalRunner []
  "fake の担い手 1 つ(置き場の中の状態 — この handler だけが書き換える)。labels = label の (名 値) の組の tuple(名の順)。"
  (defn __init__ [self #^ str name #^ tuple labels #^ bool live #^ bool draining]
    (setv self.name name self.labels labels self.live live self.draining draining)))


(defclass LocalRecord []
  "fake の task 1 本。outcome = 終わりの答え(まだなら None)。handle = scheduler の task(走らせ始めるまで None)。
   runner = 置いた担い手の名(置く前 = queued は None)。
   runtime-env = 送った時の実行環境の宣言(None = 今の commit だけの task)・root = 走らせた env の root(準備の後に在る)。"
  (defn __init__ [self #^ str key #^ str env #^ str name #^ (get tuple #(Requirement ...)) requires #^ Program program
                  #^ (| RuntimeEnv None) [runtime-env None]]
    (setv self.key key self.env env self.name name self.requires requires self.program program self.runtime-env runtime-env)
    (setv #^ (| str None) self.runner None)
    (setv #^ (| str None) self.root None)
    ;; 通った phase の列(preparing = 走る前に env の root を準備した — 冷たい起動・running = Program が走り出した)。
    (setv #^ list self.phases [])
    (setv #^ (| Task None) self.handle None)
    (setv #^ (| DetachedOutcome None) self.outcome None)))


(defclass DetachedLocalStore []
  "fake の置き場(key → LocalRecord)。runner-versions = 模擬の担い手の版(None = 送り手と同じ。違えば版の不一致を返す)。
   runners = 担い手の名簿の初めの行(RunnerFact の tuple — None = label の無い担い手 DEFAULT-RUNNER が 1 つ)。
   runs = 走らせ始めた回数(冪等の検に使う)・events = 真実の記録(DetachedEvent の列 — 模擬の判定が読む)・
   cut-until = coordinator に届かない期限(epoch ミリ秒)。
   runtime-env = 送り手の実行環境の宣言(在れば、task を走らせる前に env の root を準備する — 準備の I/O は外側の handler、速い模擬
   では env_fake の fake-env)。envs = env のキー → 準備中の scheduler の task か答え(同じキーの準備は 1 本)・known = 完成した root・
   prepares = 準備を起こした回数。
   warms = 温める表(行のキー → #(宣言 期限の仮想の秒))・cold-starts = 準備の済んでいない env の task を走らせた回数(冷たい起動 —
   本物の coordinator の計器 doeff_worker_env_cold_start_total と同じ意味)。"
  (defn __init__ [self [runner-versions None] #^ (| RuntimeEnv None) [runtime-env None] #^ str [state-root "/state/roots"]
                  #^ int [min-free-bytes 0] #^ (| tuple None) [runners None]]
    (setv self.records {} self.runner-versions runner-versions self.runs 0 self.events [] self.cut-until 0
          self.runtime-env runtime-env self.state-root state-root self.min-free-bytes min-free-bytes
          self.envs {} self.known [] self.prepares 0 self.warms {} self.cold-starts 0)
    (setv self.runners (if (is runners None)
                           {DEFAULT-RUNNER (LocalRunner DEFAULT-RUNNER #() True False)}
                           (dfor fact runners fact.name (LocalRunner fact.name (tuple (sorted fact.labels)) fact.live fact.draining)))))

  (defn #^ list open-records [self]
    (lfor r (.values self.records) :if (is r.outcome None) r))

  (defn #^ list running-on [self #^ str runner]
    "その担い手に置いて、まだ終わっていない task。"
    (lfor r (.values self.records) :if (and (is r.outcome None) (= r.runner runner)) r)))


(defn #^ WorkerInfo worker-of [#^ LocalRunner runner]
  "担い手 → 本物の coordinator の判断が読む worker の形(label の照合を同じ述語で行うため)。"
  (WorkerInfo runner.name runner.labels 1 0))


(defn #^ (get tuple #(RunnerFact ...)) runner-facts [#^ DetachedLocalStore store]
  "名簿の断面(名の順)。"
  (tuple (gfor #(name r) (sorted (.items store.runners)) (RunnerFact :name name :labels r.labels :live r.live :draining r.draining))))


(defk note [store key op runner]
  {:pre [(: store DetachedLocalStore) (: key str) (: op str) (: runner (| str None))] :post [(: % None)]}
  "真実の記録に 1 行(時刻は外側の時計)。"
  (<- at datetime (GetTime))
  (.append store.events (DetachedEvent :at (epoch-ms-of at) :key key :op op :runner (or runner "")))
  None)


(defk finish-local [store key outcome]
  {:pre [(: store DetachedLocalStore) (: key str) (: outcome DetachedOutcome)] :post [(: % bool)]}
  "終わりの答えを置く。既に終わっていれば(取り消し・消失の後)何もしない — 終わりの答えは二度と変わらない。"
  (val record (.get store.records key))
  (when (or (is record None) (is-not record.outcome None))
    (return False))
  (setv record.outcome outcome)
  (<- (note store key (match outcome
                        (DetachedSucceeded) EVENT-SUCCEEDED
                        (DetachedLost) EVENT-LOST
                        (DetachedCancelled) EVENT-CANCELLED
                        (DetachedUnrunnable) EVENT-UNRUNNABLE
                        _ EVENT-FAILED)
            record.runner))
  True)


(defk prepared-env [store env]
  {:pre [(: store DetachedLocalStore) (: env RuntimeEnv)] :post [(: % (| EnvReady EnvFailure))]}
  "env の root を 1 度だけ準備する(同じキーの準備が走っていればそれを待つ・済んでいれば使い回す — worker の EnvStore と同じ規則)。"
  (<- env-id str (env-key env (current-platform)))
  (setv entry (.get store.envs env-id))
  (cond
    (isinstance entry EnvReady) entry
    (isinstance entry Task) (do (<- waited (Wait entry)) waited)
    True (do (+= store.prepares 1)
             (<- started (Spawn (prepare-env (PrepareRequest :env env :key env-id :platform (current-platform)
                                                             :root (.format "{}/{}" store.state-root env-id)
                                                             :known (tuple store.known)
                                                             :min-free-bytes store.min-free-bytes))))
             (setv (get store.envs env-id) started)
             (<- result (Wait started))
             (setv (get store.envs env-id) result)
             (when (isinstance result EnvReady)
               (.append store.known (KnownRoot :env env :root result.root)))
             result)))


(defk env-for-task [store env]
  {:pre [(: store DetachedLocalStore) (: env RuntimeEnv)] :post [(: % (| EnvReady EnvFailure))]}
  "task の env を準備する。一時の失敗は、本物の coordinator が別の worker へ置き直すのと同じ回数(ENV-RETRIES)だけ準備し直す。"
  (<- first (prepared-env store env))
  (var result first)
  (var tries 0)
  (while (and (isinstance result EnvFailure) result.retryable (< tries ENV-RETRIES))
    (:= tries (+ tries 1))
    (<- again (prepared-env store env))
    (:= result again))
  result)


(defk run-local [store key program]
  {:pre [(: store DetachedLocalStore) (: key str) (: program Program)] :post [(: % bool)]}
  ;; 模擬の担い手の上の 1 本。取り消し(Cancel)は投げ直す — 答えは取り消した側(CancelDetached・SimulateRunnerLoss)が置く。
  ;; 実行環境の task は、先に env の root を準備する(失敗は Program を走らせずに DetachedEnvUnavailable)。
  (val record (get store.records key))
  (when (is-not record.runtime-env None)
    ;; 準備の済んでいない env の task は、走る前に準備を待つ(冷たい起動 — 先読みで避ける)。
    (<- env-id str (env-key record.runtime-env (current-platform)))
    (when (not (isinstance (.get store.envs env-id) EnvReady))
      (.append record.phases "preparing")
      (+= store.cold-starts 1))
    (<- ready (env-for-task store record.runtime-env))
    (when (isinstance ready EnvFailure)
      (<- (finish-local store key (DetachedEnvUnavailable ready.kind.value ready.detail ready.retryable)))
      (return False))
    (setv record.root ready.root))
  (.append record.phases "running")
  (try
    (<- value program)
    (<- (finish-local store key (DetachedSucceeded value)))
    (except [error TaskCancelledError]
      (raise))
    (except [error Exception]
      (<- (finish-local store key (outcome-from-task-outcome (failed-from error))))))
  True)


(defk start-on [store record runner]
  {:pre [(: store DetachedLocalStore) (: record LocalRecord) (: runner LocalRunner)] :post [(: % None)]}
  "task を担い手に置いて走らせ始める。"
  (setv record.runner runner.name)
  (+= store.runs 1)
  (<- (note store record.key EVENT-STARTED runner.name))
  (<- handle (Spawn (run-local store record.key record.program) :daemon True))
  (setv record.handle handle)
  None)


(defk place-local [store record]
  {:pre [(: store DetachedLocalStore) (: record LocalRecord)] :post [(: % None)]}
  "待っている task 1 本の置き先を決める(本物の place-tasks の規則): 生きていて drain でない合う担い手のうち負荷の少ない方(同じなら名の順)
   に置く・合う担い手が全部 drain 中なら待つ・合う担い手が居なければ DetachedUnrunnable。"
  (val able (lfor r (.values store.runners)
                  :if (and r.live (labels-satisfy record.requires (worker-of r)) (tolerates record.requires (worker-of r)))
                  r))
  (val free (sorted (lfor r able :if (not r.draining) r) :key (fn [r] #((len (.running-on store r.name)) r.name))))
  (cond
    free (<- (start-on store record (get free 0)))
    (not able) (<- (finish-local store record.key
                                 (DetachedUnrunnable (.format "置ける担い手が無い(求める label {})" (dict record.requires))))))
  None)


(defk place-queued [store]
  {:pre [(: store DetachedLocalStore)] :post [(: % None)]}
  "名簿が変わった後に、待っている task を置き直す(置けるなら置き、合う担い手が消えたなら DetachedUnrunnable)。"
  (for [record (lfor r (.open-records store) :if (is r.runner None) r)]
    (<- (place-local store record)))
  None)


(defn #^ (| DetachedAwaited None) local-answer [#^ (| LocalRecord None) record #^ str key #^ bool reachable #^ bool timed-out]
  "純粋: 待ちの 1 拍の答え(まだ待つなら None)。coordinator に届かない間は終わりを読めない(まだ終わっていない答え)— 途絶を task の
   死とみなさない。"
  (cond
    (and reachable (is record None)) (DetachedUnknown key)
    (and reachable (is-not record None) (is-not record.outcome None)) record.outcome
    (not timed-out) None
    (or (not reachable) (is record None)) (DetachedPending key "assigned")
    (is record.runner None) (DetachedPending key "queued")
    True (DetachedPending key "assigned" :runner record.runner)))


(defk await-local [store key timeout-seconds poll-seconds]
  {:pre [(: store DetachedLocalStore) (: key str) (: timeout-seconds (| float int None)) (: poll-seconds float)]
   :post [(: % DetachedAwaited)]}
  (var waited 0.0)
  (while True
    (<- at datetime (GetTime))
    (val answer (local-answer (.get store.records key) key (>= (epoch-ms-of at) store.cut-until)
                              (and (is-not timeout-seconds None) (>= waited timeout-seconds))))
    (when (is-not answer None) (return answer))
    (<- (Delay poll-seconds))
    (:= waited (+ waited poll-seconds))))


(defk wait-reachable [store poll-seconds]
  {:pre [(: store DetachedLocalStore) (: poll-seconds float)] :post [(: % None)]}
  "coordinator に届くまで待つ(送りは key で冪等なので、本物の送り手も届くまで送り直す)。"
  (while True
    (<- at datetime (GetTime))
    (when (>= (epoch-ms-of at) store.cut-until)
      (return None))
    (<- (Delay (min poll-seconds (/ (- store.cut-until (epoch-ms-of at)) 1000.0))))))


(defn #^ None refuse-conflict [#^ LocalRecord record #^ str env #^ str name #^ (get tuple #(Requirement ...)) requires]
  (when (!= #(record.env record.name record.requires) #(env name (tuple (sorted requires))))
    (raise (DetachedRefused 409 (.format "key {} は別の仕事(env {}・name {!r})に使われている" record.key record.env record.name)))))


(defk submit-local [store program env key requires name]
  {:pre [(: store DetachedLocalStore) (: program Program) (: env str) (: key str) (: requires tuple) (: name str)] :post [(: % DetachedSubmitted)]}
  ;; 同じ key がまだ在れば何も作らない。送れない値は本物と同じく送り手で断る(UnsendableProgram)。
  (when (in key store.records) (return (DetachedSubmitted key False)))
  (encode-program program)
  (val record (LocalRecord key env name (tuple (sorted requires)) program :runtime-env store.runtime-env))
  (setv (get store.records key) record)
  (val mismatch (if (is store.runner-versions None) None (version-mismatch (current-versions) store.runner-versions)))
  (<- (note store key EVENT-SUBMITTED None))
  (if (is-not mismatch None)
      (<- (finish-local store key (DetachedVersionMismatch (+ "版と label が合う担い手が無い: " mismatch))))
      (<- (place-local store record)))
  (DetachedSubmitted key True))


(defk local-warm-state [store key]
  {:pre [(: store DetachedLocalStore) (: key str)] :post [(: % WarmState)]}
  "模擬の温める表の行の今の姿(担い手は 1 つ — 名は local)。本物の coordinator の warm-view と同じ形で答えるため。"
  (setv row (.get store.warms key))
  (if (is row None)
      (WarmState :key key :ready #() :preparing #() :failed #() :until-ms 0)
      (do (setv #(env until) row)
          (<- env-id str (env-key env (current-platform)))
          (setv entry (.get store.envs env-id) until-ms (int (* 1000 until)))
          (cond
            (isinstance entry EnvReady) (WarmState :key key :ready #("local") :preparing #() :failed #() :until-ms until-ms)
            (isinstance entry EnvFailure)
              (WarmState :key key :ready #() :preparing #() :until-ms until-ms
                         :failed #((WarmFailure :worker "local" :kind entry.kind.value :detail entry.detail
                                                :retryable entry.retryable)))
            True (WarmState :key key :ready #() :preparing #("local") :failed #() :until-ms until-ms)))))


(defk warm-local [store env requires ttl-seconds]
  {:pre [(: store DetachedLocalStore) (: env RuntimeEnv) (: requires tuple) (: ttl-seconds float)] :post [(: % WarmState)]}
  "模擬の先読み: 表に行を書き、env の root の準備を別の task で起こす(送り手を待たせない)。同じ行の頼み直しは期限だけ延ばす。"
  (<- key str (warm-key env requires))
  (<- now float (GetMonotonic))
  (setv (get store.warms key) #(env (+ now ttl-seconds)))
  (<- env-id str (env-key env (current-platform)))
  (when (not-in env-id store.envs)
    (<- (Spawn (prepared-env store env) :daemon True)))
  (<- answer WarmState (local-warm-state store key))
  answer)

(defk lose-runners [store runner]
  {:pre [(: store DetachedLocalStore) (: runner (| str None))] :post [(: % int)]}
  "担い手の死: 走っている task は消え(走らせ直さない)、終わった task の結果はそのまま。runner = None は全部の task(担い手の process の
   作り直し — 名簿の担い手は生きたまま)・名指した担い手は名簿から抜け(live = False)、その担い手の task だけが消える。"
  (val lost (if (is runner None) (.open-records store) (.running-on store runner)))
  (for [record lost]
    (<- (finish-local store record.key (DetachedLost "模擬の担い手が死んだ(task は走らせ直さない)")))
    (when (is-not record.handle None)
      (<- (Cancel record.handle))))
  (when (and (is-not runner None) (in runner store.runners))
    (setv (. (get store.runners runner) live) False)
    (<- (place-queued store)))
  (len lost))


(defhandler detached-local [#^ DetachedLocalStore store [poll-seconds 0.1]]
  ;; 引数に残す理由: store は模擬の担い手の置き場そのもの(検の筋書きが中を読む)で、設定ではない。
  (WarmRuntimeEnv [env requires ttl-seconds holder]
    (<- state (warm-local store env requires (float ttl-seconds)))
    (resume state))
  (ReadWarmState [key]
    (<- state (local-warm-state store key))
    (resume state))
  (SubmitDetached [program env key requires name lease-seconds retain-seconds]
    (<- (wait-reachable store poll-seconds))
    (val existing (.get store.records key))
    (when (is-not existing None)
      (refuse-conflict existing env name requires))
    (<- submitted (submit-local store program env key requires name))
    (resume submitted))
  (AwaitDetached [key timeout-seconds]
    (<- outcome (await-local store key timeout-seconds poll-seconds))
    (resume outcome))
  (CancelDetached [key]
    ;; 終わっていない task だけが取り消しの答えを受ける(知らない key・終わった task は False)。
    (val record (.get store.records key))
    (<- cancelled bool (finish-local store key (DetachedCancelled)))
    (when (and cancelled (is-not record None) (is-not record.handle None))
      (<- (Cancel record.handle)))
    (resume cancelled))
  (ReleaseDetached [key]
    (val record (.get store.records key))
    (cond
      (is record None) (resume False)
      (is record.outcome None) (raise (DetachedRefused 409 (.format "key {} はまだ終わっていない — 先に取り消す" key)))
      True (do (del (get store.records key))
               (resume True))))
  (ReadRunners []
    (<- at datetime (GetTime))
    (resume (if (< (epoch-ms-of at) store.cut-until)
                (RunnersUnreachable :detail "coordinator に届かない(模擬の途絶)")
                (runner-facts store))))
  (SimulateRunnerLoss [runner]
    (<- lost int (lose-runners store runner))
    (resume lost))
  (SimulateRunnerDrain [runner]
    ;; drain: 新しい task を置かない・走っている task は続く(抜けるのは担い手の process が止まった時 = SimulateRunnerLoss — 本物の
    ;; coordinator も drain した worker を heartbeat が止まるまで名簿に残す)。
    (setv (. (get store.runners runner) draining) True)
    (resume (len (.running-on store runner))))
  (SimulateRunnerReturn [runner]
    ;; 担い手が戻る(作り直した worker — 生きていて drain でない)。名簿に無い名は label の無い担い手として足す。
    (if (in runner store.runners)
        (setv (. (get store.runners runner) live) True (. (get store.runners runner) draining) False)
        (setv (get store.runners runner) (LocalRunner runner #() True False)))
    (<- (place-queued store))
    (resume None))
  (SimulateCoordinatorOutage [seconds]
    (<- at datetime (GetTime))
    (setv store.cut-until (max store.cut-until (+ (epoch-ms-of at) (int (* 1000 seconds)))))
    (resume None)))


;; --- handler B: coordinator の /detached の口へ出し、worker の子 process で走らせる ------------------------


(defclass DetachedClient []
  "coordinator の /detached との連絡(I/O)。revision = 送り手の commit(受け側はこの版のコードを準備してから復元する)。
   runtime-env = 実行環境の宣言(在れば worker は env の root を準備して、その中の子 process で走らせる — revision は使わない)。
   送る PUT は key で冪等なので、読みと同じく通信の失敗を越えて送り直す(送り直しで作られていれば created = False が返る)。"
  (defn __init__ [self #^ str url #^ str revision [timeout REPLY-SECONDS] [transport None]
                  #^ (| RuntimeEnv None) [runtime-env None]]
    (setv self.revision revision self.runtime-env runtime-env
          self.endpoint (CoordinatorEndpoint url timeout 4 :transport transport)))

  (defn #^ str path [self #^ str key #^ str [suffix ""]]
    (+ "/detached/" (url-quote key :safe "") suffix))

  (defn #^ dict answer [self response]
    (when (in response.status-code #(400 409 429))
      (raise (DetachedRefused response.status-code (.get (.json response) "error" ""))))
    (.raise-for-status response)
    (.json response))

  (defn #^ dict submit [self #^ str key #^ str blob #^ str env #^ (get tuple #(Requirement ...)) requires #^ str name
                        #^ float lease-seconds
                        #^ float retain-seconds]
    (setv body (| {"env" env "blob" blob "versions" (current-versions) "revision" self.revision "requires" (dict requires)
                   "name" name "leaseSeconds" lease-seconds "retainSeconds" retain-seconds "format" PROTOCOL-FORMAT}
                  (if (is self.runtime-env None) {} {"runtimeEnv" (run-program (runtime-env->json self.runtime-env))})))
    (.answer self (send-idempotent (fn [] (.request self.endpoint "PUT" (.path self key) :json body)))))

  (defn #^ dict read [self #^ str key]
    (.answer self (send-idempotent (fn [] (.request self.endpoint "GET" (.path self key))))))

  (defn #^ bool cancel [self #^ str key]
    ;; 取り消しは何度送っても同じ意味(終わりの phase は変わらない)。
    (get (.answer self (send-idempotent (fn [] (.request self.endpoint "POST" (.path self key "/cancel"))))) "cancelled"))

  (defn #^ bool release [self #^ str key]
    (get (.answer self (send-idempotent (fn [] (.request self.endpoint "DELETE" (.path self key))))) "released"))

  (defn #^ RunnersAnswer runners [self]
    "担い手の名簿(coordinator の GET /state の workers — live と draining は coordinator の判断)。届かなければ RunnersUnreachable。"
    (try
      (setv response (send-idempotent (fn [] (.request self.endpoint "GET" "/state"))))
      (except [error httpx.TransportError]
        (return (RunnersUnreachable :detail (.format "coordinator に届かない: {}" error)))))
    (.raise-for-status response)
    (runner-facts-of-view (get (.json response) "workers"))))


(defn #^ (get tuple #(RunnerFact ...)) runner-facts-of-view [#^ dict workers]
  "純粋: coordinator の GET /state の workers(名 → {labels live draining …})→ 名簿の断面(名の順)。"
  (tuple (gfor #(name w) (sorted (.items workers))
               (RunnerFact :name name :labels (tuple (sorted (.items (get w "labels")))) :live (bool (get w "live"))
                           :draining (bool (get w "draining"))))))


(defk await-cluster [client key timeout-seconds poll-seconds]
  {:pre [(: client DetachedClient) (: key str) (: timeout-seconds (| float int None)) (: poll-seconds float)]
   :post [(: % DetachedAwaited)]}
  ;; 終わるまで問い合わせる。問い合わせは lease に触らず、抜けても(呼び手の Cancel・process の消失)何も落とさない。
  ;; 眠りは Delay(外側の doeff-time の handler)なので同じ VM の他の task を塞がない。
  (var waited 0.0)
  (while True
    (setv view (.read client key)
          outcome (outcome-of-view view))
    (when (is-not outcome None) (return outcome))
    (when (and (is-not timeout-seconds None) (>= waited timeout-seconds)) (return (DetachedPending key (get view "phase") :runner (or (.get view "worker") ""))))
    (<- (Delay poll-seconds))
    (:= waited (+ waited poll-seconds))))


(defhandler detached-cluster [#^ DetachedClient client [poll-seconds 1.0]]
  (SubmitDetached [program env key requires name lease-seconds retain-seconds]
    ;; 送れない値は送る前に断る(encode-program が UnsendableProgram を投げ、呼び手へ届く)。
    (setv reply (.submit client key (encode-program program) env requires name (float lease-seconds) (float retain-seconds)))
    (resume (DetachedSubmitted key (get reply "created"))))
  (AwaitDetached [key timeout-seconds]
    (<- outcome (await-cluster client key timeout-seconds poll-seconds))
    (resume outcome))
  (CancelDetached [key] (resume (.cancel client key)))
  (ReleaseDetached [key] (resume (.release client key)))
  (ReadRunners [] (resume (.runners client))))


;; --- 温める表(2026-09-26): coordinator の /warm の口 -------------------------------------------------

(defclass WarmClient []
  "coordinator の /warm との連絡(I/O)。書きは同じ行への頼み直しが同じ意味なので、通信の失敗を越えて送り直す。"
  (defn __init__ [self #^ str url [timeout REPLY-SECONDS] [transport None] #^ str [actor ""]]
    (setv self.endpoint (CoordinatorEndpoint url timeout 4 :transport transport :actor (or actor None))))

  (defn #^ WarmState write [self #^ RuntimeEnv env #^ tuple requires #^ float ttl-seconds #^ str holder]
    "行を書いて今の姿を読む。"
    (setv body {"runtimeEnv" (run-program (runtime-env->json env)) "requires" (dict requires) "ttlSeconds" ttl-seconds
                "holder" holder "format" PROTOCOL-FORMAT}
          response (send-idempotent (fn [] (.request self.endpoint "POST" "/warm" :json body))))
    (when (= response.status-code 400)
      (raise (DetachedRefused 400 (.get (.json response) "error" ""))))
    (.raise-for-status response)
    (warm-state-of-json (.json response)))

  (defn #^ WarmState read [self #^ str key]
    "行の今の姿を読む(表に無い行は ready も preparing も空・期限 0)。"
    (setv response (send-idempotent (fn [] (.request self.endpoint "GET" (+ "/warm/" (url-quote key :safe ""))))))
    (if (= response.status-code 404)
        (WarmState :key key :ready #() :preparing #() :failed #() :until-ms 0)
        (do (.raise-for-status response)
            (warm-state-of-json (.json response))))))


(defhandler warm-cluster [#^ WarmClient client]
  ;; 引数に残す理由: client は coordinator への接続(I/O の資源)で、composition root が url から 1 つ作る。
  (WarmRuntimeEnv [env requires ttl-seconds holder]
    (resume (.write client env requires (float ttl-seconds) holder)))
  (ReadWarmState [key]
    (resume (.read client key))))
