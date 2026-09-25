;;; 切り離した task の 2 つの handler(effect は detached_model.hy)。業務のコードは同じまま、composition root がどちらを被せるかで
;;; 「手元の 1 process で系全体を模擬する」と「本番でクラスタに分散する」を切り替える。どちらも同じ契約:
;;;   - key で冪等に送る(同じ key がまだ在れば created = False・env / name / requires が違えば DetachedRefused)
;;;   - 呼び手が消えても(await が取り消されても)task は続く・後から同じ key で待てる
;;;   - 終わった結果は解放か保持の期限まで持つ・終わった後の取り消しは False で結果はそのまま
;;;   - 担い手の死 = DetachedLost(走らせ直さない)・結果の後の担い手の死では結果は変わらない
;;;   - 版の不一致 = DetachedVersionMismatch
(require doeff-hy.macros [defhandler defk <-])
(import urllib.parse [quote :as url-quote])
(import doeff_core_effects.scheduler [Spawn Cancel Task TaskCancelledError])
(import doeff [Program])
(import doeff_time [Delay])
(import .coordinator_http [CoordinatorEndpoint send-idempotent REPLY-SECONDS])
(import .remote_model [encode-program current-versions version-mismatch failed-from])
(import .cluster_model [Requirement])
(import .detached_model [SubmitDetached AwaitDetached CancelDetached ReleaseDetached SimulateRunnerLoss
                         DetachedSubmitted DetachedSucceeded DetachedLost DetachedCancelled DetachedVersionMismatch
                         DetachedPending DetachedUnknown DetachedRefused DetachedOutcome DetachedAwaited
                         outcome-from-task-outcome outcome-of-view])


;; --- handler A: 同じ VM の scheduler の task として走らせる(fake・模擬環境) -------------------------

(defclass LocalRecord []
  "fake の task 1 本。outcome = 終わりの答え(まだなら None)。handle = scheduler の task(走らせ始めるまで None)。"
  (defn __init__ [self #^ str key #^ str env #^ str name #^ (get tuple #(Requirement ...)) requires]
    (setv self.key key self.env env self.name name self.requires requires)
    (setv #^ (| Task None) self.handle None)
    (setv #^ (| DetachedOutcome None) self.outcome None)))


(defclass DetachedLocalStore []
  "fake の置き場(key → LocalRecord)。runner-versions = 模擬の担い手の版(None = 送り手と同じ。違えば版の不一致を返す)。
   runs = 走らせ始めた回数(冪等の検に使う)。"
  (defn __init__ [self [runner-versions None]]
    (setv self.records {} self.runner-versions runner-versions self.runs 0))

  (defn #^ bool finish [self #^ str key outcome]
    "終わりの答えを置く。既に終わっていれば(取り消し・消失の後)何もしない — 終わりの答えは二度と変わらない。"
    (setv record (.get self.records key))
    (when (or (is record None) (is-not record.outcome None)) (return False))
    (setv record.outcome outcome)
    True)

  (defn #^ list open-records [self]
    (lfor r (.values self.records) :if (is r.outcome None) r)))


(defk run-local [store key program]
  {:pre [(: store DetachedLocalStore) (: key str) (: program Program)] :post [(: % bool)]}
  ;; 模擬の担い手の上の 1 本。取り消し(Cancel)は投げ直す — 答えは取り消した側(CancelDetached・SimulateRunnerLoss)が置く。
  (try
    (<- value program)
    (.finish store key (DetachedSucceeded value))
    (except [error TaskCancelledError]
      (raise))
    (except [error Exception]
      (.finish store key (outcome-from-task-outcome (failed-from error))))))


(defk await-local [store key timeout-seconds poll-seconds]
  {:pre [(: store DetachedLocalStore) (: key str) (: timeout-seconds (| float int None)) (: poll-seconds float)]
   :post [(: % DetachedAwaited)]}
  (setv waited 0.0)
  (while True
    (setv record (.get store.records key))
    (when (is record None) (return (DetachedUnknown key)))
    (when (is-not record.outcome None) (return record.outcome))
    (when (and (is-not timeout-seconds None) (>= waited timeout-seconds)) (return (DetachedPending key "assigned")))
    (<- (Delay poll-seconds))
    (+= waited poll-seconds)))


(defn #^ None refuse-conflict [#^ LocalRecord record #^ str env #^ str name #^ (get tuple #(Requirement ...)) requires]
  (when (!= #(record.env record.name record.requires) #(env name (tuple (sorted requires))))
    (raise (DetachedRefused 409 (.format "key {} は別の仕事(env {}・name {!r})に使われている" record.key record.env record.name)))))


(defk submit-local [store program env key requires name]
  {:pre [(: store DetachedLocalStore) (: program Program) (: env str) (: key str) (: requires tuple) (: name str)] :post [(: % DetachedSubmitted)]}
  ;; 同じ key がまだ在れば何も作らない。送れない値は本物と同じく送り手で断る(UnsendableProgram)。
  (when (in key store.records) (return (DetachedSubmitted key False)))
  (encode-program program)
  (setv record (LocalRecord key env name (tuple (sorted requires)))
        (get store.records key) record
        mismatch (if (is store.runner-versions None) None (version-mismatch (current-versions) store.runner-versions)))
  (if (is-not mismatch None)
      (setv record.outcome (DetachedVersionMismatch (+ "版と label が合う担い手が無い: " mismatch)))
      (do (+= store.runs 1)
          (<- handle (Spawn (run-local store key program) :daemon True))
          (setv record.handle handle)))
  (DetachedSubmitted key True))


(defhandler detached-local [#^ DetachedLocalStore store [poll-seconds 0.1]]
  (SubmitDetached [program env key requires name lease-seconds retain-seconds]
    (setv existing (.get store.records key))
    (when (is-not existing None)
      (refuse-conflict existing env name requires))
    (<- submitted (submit-local store program env key requires name))
    (resume submitted))
  (AwaitDetached [key timeout-seconds]
    (<- outcome (await-local store key timeout-seconds poll-seconds))
    (resume outcome))
  (CancelDetached [key]
    (setv record (.get store.records key))
    (if (and (is-not record None) (.finish store key (DetachedCancelled)))
        (do (<- (Cancel record.handle))
            (resume True))
        (resume False)))
  (ReleaseDetached [key]
    (setv record (.get store.records key))
    (cond
      (is record None) (resume False)
      (is record.outcome None) (raise (DetachedRefused 409 (.format "key {} はまだ終わっていない — 先に取り消す" key)))
      True (do (del (get store.records key))
               (resume True))))
  (SimulateRunnerLoss []
    ;; 担い手の死: 走っている task は消え(走らせ直さない)、終わった task の結果はそのまま。
    (setv lost (.open-records store))
    (for [record lost]
      (.finish store record.key (DetachedLost "模擬の担い手が死んだ(task は走らせ直さない)"))
      (when (is-not record.handle None)
        (<- (Cancel record.handle))))
    (resume (len lost))))


;; --- handler B: coordinator の /detached の口へ出し、worker の子 process で走らせる ------------------------

(defclass DetachedClient []
  "coordinator の /detached との連絡(I/O)。revision = 送り手の commit(受け側はこの版のコードを準備してから復元する)。
   送る PUT は key で冪等なので、読みと同じく通信の失敗を越えて送り直す(送り直しで作られていれば created = False が返る)。"
  (defn __init__ [self #^ str url #^ str revision [timeout REPLY-SECONDS] [transport None]]
    (setv self.revision revision self.endpoint (CoordinatorEndpoint url timeout 4 :transport transport)))

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
    (setv body {"env" env "blob" blob "versions" (current-versions) "revision" self.revision "requires" (dict requires)
                "name" name "leaseSeconds" lease-seconds "retainSeconds" retain-seconds})
    (.answer self (send-idempotent (fn [] (.request self.endpoint "PUT" (.path self key) :json body)))))

  (defn #^ dict read [self #^ str key]
    (.answer self (send-idempotent (fn [] (.request self.endpoint "GET" (.path self key))))))

  (defn #^ bool cancel [self #^ str key]
    ;; 取り消しは何度送っても同じ意味(終わりの phase は変わらない)。
    (get (.answer self (send-idempotent (fn [] (.request self.endpoint "POST" (.path self key "/cancel"))))) "cancelled"))

  (defn #^ bool release [self #^ str key]
    (get (.answer self (send-idempotent (fn [] (.request self.endpoint "DELETE" (.path self key))))) "released")))


(defk await-cluster [client key timeout-seconds poll-seconds]
  {:pre [(: client DetachedClient) (: key str) (: timeout-seconds (| float int None)) (: poll-seconds float)]
   :post [(: % DetachedAwaited)]}
  ;; 終わるまで問い合わせる。問い合わせは lease に触らず、抜けても(呼び手の Cancel・process の消失)何も落とさない。
  ;; 眠りは Delay(外側の doeff-time の handler)なので同じ VM の他の task を塞がない。
  (setv waited 0.0)
  (while True
    (setv view (.read client key)
          outcome (outcome-of-view view))
    (when (is-not outcome None) (return outcome))
    (when (and (is-not timeout-seconds None) (>= waited timeout-seconds)) (return (DetachedPending key (get view "phase"))))
    (<- (Delay poll-seconds))
    (+= waited poll-seconds)))


(defhandler detached-cluster [#^ DetachedClient client [poll-seconds 1.0]]
  (SubmitDetached [program env key requires name lease-seconds retain-seconds]
    ;; 送れない値は送る前に断る(encode-program が UnsendableProgram を投げ、呼び手へ届く)。
    (setv reply (.submit client key (encode-program program) env requires name (float lease-seconds) (float retain-seconds)))
    (resume (DetachedSubmitted key (get reply "created"))))
  (AwaitDetached [key timeout-seconds]
    (<- outcome (await-cluster client key timeout-seconds poll-seconds))
    (resume outcome))
  (CancelDetached [key] (resume (.cancel client key)))
  (ReleaseDetached [key] (resume (.release client key))))
