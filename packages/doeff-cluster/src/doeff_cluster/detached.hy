;;; 切り離した task の本番の handler(effect は detached_model.hy)。業務のコードは effect だけを知り、composition root が handler を
;;; 被せる。手元で系全体を確かめる時は、handler を被せずに手元の runner sim-cluster(local.hy)で走らせる — sim の宿が同じ要求の形
;;; (この module の detached-path・detached-submit-body・detached-refusal・awaited-answer・warm-request-body)で coordinator の口へ送る。
;;; 契約(本物の coordinator と worker が決める):
;;;   - key で冪等に送る(同じ key がまだ在れば created = False・name / needs / environ が違えば DetachedRefused)
;;;   - 呼び手が消えても(await が取り消されても)task は続く・後から同じ key で待てる
;;;   - 終わった結果は解放か保持の期限まで持つ・終わった後の取り消しは False で結果はそのまま
;;;   - 担い手の死 = DetachedLost(走らせ直さない)・結果の後の担い手の死では結果は変わらない
;;;   - 版の不一致 = DetachedVersionMismatch
;;;   - 置き先 = 生きていて drain でない、能力の合う担い手(needs ⊆ provides・専用の能力)。合う担い手が全部 drain 中なら待つ・合う担い手が居なければ
;;;     DetachedUnrunnable(coordinator の place-tasks)
;;;   - 担い手の名簿(ReadRunners)= coordinator の名簿の生存と drain
;;;
;;; 2026-09-28: 同じ VM の scheduler の task で走らせる模擬(detached-local・置き場 DetachedLocalStore・模擬の担い手)を消した。呼び手の
;;; 外側の handler を継ぎ、Program に足りない handler を黙って補っていた(ADR-DOE-CLUSTER-001 R1・R2 に反する)。模擬の担い手の筋書き
;;; (担い手の死・drain・戻り・coordinator の途絶)は sim-cluster の検の effect(KillWorker・DrainWorker・StartWorker・StopCoordinator)が持つ。
(require doeff-hy.macros [defhandler defk deff <- val var])
(import urllib.parse [quote :as url-quote])
(import httpx)
(import doeff [run :as run-program])
(import doeff_time [Delay])
(import .coordinator_http [CoordinatorEndpoint send-idempotent put-program REPLY-SECONDS IDEMPOTENT-DEADLINE-SECONDS])
(import .cluster_model [PROTOCOL-FORMAT])
(import .runtime_env_model [RuntimeEnv runtime-env->json])
(import .remote_model [encode-program current-versions])
(import .warm_model [WarmRuntimeEnv ReadWarmState WarmState warm-state-of-json])
(import .detached_model [SubmitDetached AwaitDetached CancelDetached ReleaseDetached ReadRunners WARMING-PHASE
                         DetachedSubmitted DetachedPending DetachedRefused DetachedAwaited DetachedUnreachable
                         RunnerFact RunnersUnreachable RunnersAnswer outcome-of-view])

;; 取り消しに当たる答えの status(本文の error を理由にした DetachedRefused にする)。413 = 詰めた Program が置き場の上限を越える
;; (PUT /programs — program_policy.PROGRAM-MAX-BYTES)。
(val REFUSED-STATUSES #(400 409 413 429))


;; --- 要求の形と答えの読み(本番の DetachedClient・WarmClient と sim の宿が同じ関数を使う — 本文を写さない)-----------------------

(deff detached-path [#^ str key #^ str suffix]  ; defk にできない: 本番の client(Program の外の I/O の道具)と sim の宿が同じ形を作る純粋な判断
  {:pre [(: key str) (: suffix str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol"}}
  "切り離した task の口の path(key は path の 1 節に収まるよう quote する)を作るため。"
  (+ "/detached/" (url-quote key :safe "") suffix))


(deff detached-submit-body [#^ str sha #^ str revision #^ frozenset needs #^ str name #^ float lease-seconds #^ float retain-seconds
                            #^ (| dict None) runtime-env #^ dict environ]  ; defk にできない: 本番の client と sim の宿が同じ形を作る純粋な判断
  {:pre [(: sha str) (: revision str) (: needs frozenset) (: name str) (: lease-seconds float) (: retain-seconds float)
         (: runtime-env (| dict None)) (: environ dict)]
   :post [(: % dict)] :tags {:context "doeff-cluster" :role "protocol"}}
  "PUT /detached/<key> の本文を作るため: 詰めた Program は置き場 /programs/<sha> に先に置き、本文は sha だけを運ぶ(ADR-DOE-CLUSTER-001
   R3b)。runtime-env = 実行環境の宣言の JSON(在れば worker は env の root を準備して、その中で走らせる)。environ = 子の環境変数
   (SubmitDetached.environ — 空なら欄を置かない・同じ key の送り直しの比べに入る)。"
  (| {"program" sha "revision" revision "needs" (sorted needs) "name" name "leaseSeconds" lease-seconds
      "retainSeconds" retain-seconds "format" PROTOCOL-FORMAT}
     (if (is runtime-env None) {} {"runtimeEnv" runtime-env})
     (if environ {"environ" (dict environ)} {})))


(deff detached-refusal [#^ (| int None) status #^ (| dict None) body]  ; defk にできない: 本番の client と sim の宿が同じ判断で返事を読む
  {:pre [(: status (| int None)) (: body (| dict None))] :post [(: % (| DetachedRefused None))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "返事が呼び手の誤り(400・409・413・429 — 形の誤り・同じ key の別の仕事・上限越え)なら、呼び手へ投げる DetachedRefused を作るため
   (それ以外は None)。"
  (if (in status REFUSED-STATUSES)
      (DetachedRefused status (str (.get (or body {}) "error" "")))
      None))


(deff submit-unreachable [#^ str reason]  ; defk にできない: 本番の client と sim の宿が同じ答えを作る純粋な判断
  {:pre [(: reason str)] :post [(: % DetachedUnreachable)] :tags {:context "doeff-cluster" :role "judgment"}}
  "送りが coordinator に届かなかった時の答えを作るため(送れたかは分からない — key で冪等なので呼び手が送り直してよい)。"
  (DetachedUnreachable :detail (.format "coordinator に届かない(送れたかは分からない — key で冪等): {}" reason)))


(deff awaited-answer [#^ (| dict None) view #^ str reason #^ str key #^ float waited #^ (| float int None) timeout-seconds]  ; defk にできない: 本番の client と sim の宿が同じ判断で待ちの 1 拍を読む
  {:pre [(: view (| dict None)) (: reason str) (: key str) (: waited float) (: timeout-seconds (| float int None))]
   :post [(: % (| DetachedAwaited None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "待ちの 1 拍の読み(view = GET /detached/<key> の本文・届かなければ None と理由 reason)から、答えるか(DetachedAwaited)・待ち続けるか
   (None)を決めるため。届かない読みと、起きた直後の coordinator の「まだ分からない」(phase warming)は、期限を決めた待ちなら
   DetachedUnreachable で返し、期限の無い待ちは届くまで待つ(task の死とみなさない・知らない key と読んで送り直さない)。"
  (cond
    (is view None)
      (if (is timeout-seconds None) None (DetachedUnreachable :detail (.format "coordinator に届かない: {}" reason)))
    (= (.get view "phase") WARMING-PHASE)
      (if (is timeout-seconds None) None (DetachedUnreachable :detail (.format "coordinator に届かない: {}" (.get view "error" ""))))
    True
      (let [outcome (outcome-of-view view)]
        (cond
          (is-not outcome None) outcome
          (and (is-not timeout-seconds None) (>= waited timeout-seconds))
            (DetachedPending key (get view "phase") :runner (or (.get view "worker") ""))
          True None))))


(deff runner-facts-of-view [#^ dict workers]  ; defk にできない: 本番の client と sim の宿が同じ読みを使う純粋な判断
  {:pre [(: workers dict)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "judgment"}}
  "coordinator の GET /state の workers(名 → {provides exclusive live draining …})を名簿の断面(RunnerFact の tuple・名の順)にするため。"
  (tuple (gfor #(name w) (sorted (.items workers))
               (RunnerFact :name name :provides (tuple (sorted (.get w "provides" []))) :exclusive (tuple (sorted (.get w "exclusive" [])))
                           :live (bool (get w "live")) :draining (bool (get w "draining"))))))


(deff runners-unreachable [#^ str reason]  ; defk にできない: 本番の client と sim の宿が同じ答えを作る純粋な判断
  {:pre [(: reason str)] :post [(: % RunnersUnreachable)] :tags {:context "doeff-cluster" :role "judgment"}}
  "名簿の読みが coordinator に届かなかった時の答えを作るため。"
  (RunnersUnreachable :detail (.format "coordinator に届かない: {}" reason)))


(deff warm-request-body [#^ dict runtime-env #^ frozenset needs #^ float ttl-seconds #^ str holder]  ; defk にできない: 本番の client と sim の宿が同じ形を作る純粋な判断
  {:pre [(: runtime-env dict) (: needs frozenset) (: ttl-seconds float) (: holder str)] :post [(: % dict)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "POST /warm の本文を作るため(runtime-env = 実行環境の宣言の JSON)。"
  {"runtimeEnv" runtime-env "needs" (sorted needs) "ttlSeconds" ttl-seconds "holder" holder "format" PROTOCOL-FORMAT})


(deff warm-path [#^ str key]  ; defk にできない: 本番の client と sim の宿が同じ形を作る純粋な判断
  {:pre [(: key str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol"}}
  "温める表の行 key の読みの path を作るため。"
  (+ "/warm/" (url-quote key :safe "")))


(deff absent-warm-state [#^ str key]  ; defk にできない: 本番の client と sim の宿が同じ答えを作る純粋な判断
  {:pre [(: key str)] :post [(: % WarmState)] :tags {:context "doeff-cluster" :role "judgment"}}
  "表に無い行(404 — 期限で消えたか、書かれていない)の答えを作るため: ready も preparing も空・期限 0。"
  (WarmState :key key :ready #() :preparing #() :failed #() :until-ms 0))


;; --- handler: coordinator の /detached の口へ出し、worker の子 process で走らせる ------------------------


(defclass DetachedClient []
  "coordinator の /detached との連絡(I/O)。revision = 送り手の commit(受け側はこの版のコードを準備してから復元する)。
   runtime-env = 実行環境の宣言(在れば worker は env の root を準備して、その中の子 process で走らせる — revision は使わない)。
   送る PUT は key で冪等なので、読みと同じく通信の失敗を越えて送り直す(送り直しで作られていれば created = False が返る)。"
  (defn __init__ [self #^ str url #^ str revision [timeout REPLY-SECONDS] [transport None]
                  #^ (| RuntimeEnv None) [runtime-env None] #^ float [deadline-seconds IDEMPOTENT-DEADLINE-SECONDS]]
    ;; deadline-seconds = 通信の失敗を越えて送り直す期限(過ぎたら「届かない」の答え — 検は短くする)。
    (setv self.revision revision self.runtime-env runtime-env self.deadline-seconds deadline-seconds
          self.endpoint (CoordinatorEndpoint url timeout 4 :transport transport)))

  (defn #^ httpx.Response resend [self send]
    "何度送っても同じ意味の要求を、期限まで送り直す(期限を過ぎた通信の失敗は httpx.TransportError のまま投げる)。"
    (send-idempotent send :deadline-seconds self.deadline-seconds))

  (defn #^ dict answer [self response]
    (setv refusal (detached-refusal response.status-code (if (in response.status-code REFUSED-STATUSES) (.json response) None)))
    (when refusal (raise refusal))
    (.raise-for-status response)
    (.json response))

  (defn #^ dict submit [self #^ str key #^ str blob #^ frozenset needs #^ str name
                        #^ float lease-seconds
                        #^ float retain-seconds #^ (| dict None) [environ None]]
    "切り離した task を 1 本出す: 詰めた Program を版と一緒に置き場 /programs/<sha> に先に置き、本文は sha だけを運ぶ(service の宣言と
     同じ運び方 — ADR-DOE-CLUSTER-001 R3b)。置きも送りも何度送っても同じ意味なので、通信の失敗を越えて送り直す。"
    (setv #(sha put) (put-program self.endpoint blob (current-versions) self.deadline-seconds))
    (.answer self put)
    (setv body (detached-submit-body sha self.revision needs name lease-seconds retain-seconds
                                     (if (is self.runtime-env None) None (run-program (runtime-env->json self.runtime-env)))
                                     (or environ {})))
    (.answer self (.resend self (fn [] (.request self.endpoint "PUT" (detached-path key "") :json body)))))

  (defn #^ dict read [self #^ str key]
    ;; 503 = coordinator が起きた直後で行の無い key を知らないと言えない(phase warming — detached_policy.detached-read)。本文を返し、
    ;; 待ちの側(awaited-answer)が届かないと同じに扱う。
    (setv response (.resend self (fn [] (.request self.endpoint "GET" (detached-path key "")))))
    (if (= response.status-code 503)
        (.json response)
        (.answer self response)))

  (defn #^ bool cancel [self #^ str key]
    ;; 取り消しは何度送っても同じ意味(終わりの phase は変わらない)。
    (get (.answer self (.resend self (fn [] (.request self.endpoint "POST" (detached-path key "/cancel"))))) "cancelled"))

  (defn #^ bool release [self #^ str key]
    (get (.answer self (.resend self (fn [] (.request self.endpoint "DELETE" (detached-path key ""))))) "released"))

  (defn #^ RunnersAnswer runners [self]
    "担い手の名簿(coordinator の GET /state の workers — live と draining は coordinator の判断)。届かなければ RunnersUnreachable。"
    (try
      (setv response (.resend self (fn [] (.request self.endpoint "GET" "/state"))))
      (except [error httpx.TransportError]
        (return (runners-unreachable (str error)))))
    (.raise-for-status response)
    (runner-facts-of-view (get (.json response) "workers"))))


(defk await-cluster [client key timeout-seconds poll-seconds]
  {:pre [(: client DetachedClient) (: key str) (: timeout-seconds (| float int None)) (: poll-seconds float)]
   :post [(: % DetachedAwaited)]}
  ;; 終わるまで問い合わせる。問い合わせは lease に触らず、抜けても(呼び手の Cancel・process の消失)何も落とさない。
  ;; 眠りは Delay(外側の doeff-time の handler)なので同じ VM の他の task を塞がない。1 拍の読みは awaited-answer(sim の宿と同じ判断)。
  (var waited 0.0)
  (var answer None)
  (while (is answer None)
    (val read (try (.read client key) (except [error httpx.TransportError] error)))
    (:= answer (if (isinstance read httpx.TransportError)
                   (awaited-answer None (str read) key waited timeout-seconds)
                   (awaited-answer read "" key waited timeout-seconds)))
    (when (is answer None)
      (<- (Delay poll-seconds))
      (:= waited (+ waited poll-seconds))))
  answer)

(defhandler detached-cluster [#^ DetachedClient client [poll-seconds 1.0]]
  (SubmitDetached [program key needs name lease-seconds retain-seconds environ]
    ;; 送れない値は送る前に断る(encode-program が UnsendableProgram を投げ、呼び手へ届く)。
    (setv blob (encode-program program))
    (resume (try (DetachedSubmitted key (get (.submit client key blob needs name (float lease-seconds) (float retain-seconds) environ) "created"))
                 (except [error httpx.TransportError]
                   (submit-unreachable (str error))))))
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

  (defn #^ WarmState write [self #^ RuntimeEnv env #^ frozenset needs #^ float ttl-seconds #^ str holder]
    "行を書いて今の姿を読む。"
    (setv body (warm-request-body (run-program (runtime-env->json env)) needs ttl-seconds holder)
          response (send-idempotent (fn [] (.request self.endpoint "POST" "/warm" :json body))))
    (when (= response.status-code 400)
      (raise (DetachedRefused 400 (.get (.json response) "error" ""))))
    (.raise-for-status response)
    (warm-state-of-json (.json response)))

  (defn #^ WarmState read [self #^ str key]
    "行の今の姿を読む(表に無い行は ready も preparing も空・期限 0)。"
    (setv response (send-idempotent (fn [] (.request self.endpoint "GET" (warm-path key)))))
    (if (= response.status-code 404)
        (absent-warm-state key)
        (do (.raise-for-status response)
            (warm-state-of-json (.json response))))))

(defhandler warm-cluster [#^ WarmClient client]
  ;; 引数に残す理由: client は coordinator への接続(I/O の資源)で、composition root が url から 1 つ作る。
  (WarmRuntimeEnv [env needs ttl-seconds holder]
    (resume (.write client env needs (float ttl-seconds) holder)))
  (ReadWarmState [key]
    (resume (.read client key))))
