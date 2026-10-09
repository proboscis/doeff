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
;;;   - Service の一覧(ReadServices)= coordinator の Service の宣言の台数と、置き先の担い手が報告した落ちた事実(#3479)
;;;
;;; 2026-09-28: 同じ VM の scheduler の task で走らせる模擬(detached-local・置き場 DetachedLocalStore・模擬の担い手)を消した。呼び手の
;;; 外側の handler を継ぎ、Program に足りない handler を黙って補っていた(ADR-DOE-CLUSTER-001 R1・R2 に反する)。模擬の担い手の筋書き
;;; (担い手の死・drain・戻り・coordinator の途絶)は sim-cluster の検の effect(KillWorker・DrainWorker・StartWorker・StopCoordinator)が持つ。
(require doeff-hy.macros [defhandler defk deff <- val var])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import urllib.parse [quote :as url-quote])
(import json)
(import collections.abc [Mapping])
(import doeff_time [Delay])
(import doeff_core_effects.http_effects [HttpResponse HttpFailed])
(import doeff_cluster.shared.protocol.coordinator_route [RouteCell RouteOptions RoutedReply routed-request resent-request
                                                         answer-json])
(import doeff_cluster.shared.protocol.remote [program-put])
(import doeff_cluster.shared.intent.protocol [PROTOCOL-FORMAT])
(import doeff_cluster.shared.core.capabilities [env-mapping])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])
(import doeff_cluster.shared.core.runtime_env_rules [runtime-env->json])
(import doeff_cluster.shared.intent.warm_model [WarmRuntimeEnv ReadWarmState WarmState WarmUnreachable WarmAnswer AwaitWarm WarmReady WarmFailed
                                                WarmWaitExpired])
(import doeff_cluster.shared.core.warm_rules [warm-state-of-json warm-wait-answer])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.process_model [AwaitProcessEnded ProcessEnded ProcessWaitExpired])
(import doeff_cluster.shared.intent.detached_model [SubmitDetached AwaitDetached CancelDetached ReleaseDetached ReadRunners WARMING-PHASE
                         DetachedSubmitted DetachedPending DetachedRefused DetachedAwaited DetachedUnreachable DetachedSubmitAnswer
                         RunnerFact RunnersUnreachable OPEN-PHASES DetachedOutcome DetachedSucceeded DetachedFailed DetachedLost
                         DetachedCancelled DetachedVersionMismatch DetachedUnrunnable DetachedEnvUnavailable DetachedUnknown
                         AwaitRunnersChange RunnersChange RunnersWatchMissing RunnersChangeAnswer AwaitServiceReady ServiceReady
                         ServiceViewWire ReadServices ServiceFact ServicesUnreachable ServiceListWire])
(import doeff_hy.wire [Malformed parse])
(import doeff_cluster.shared.intent.remote_model [TaskSucceeded TaskFailed])
(import doeff_cluster.shared.protocol.program_codec [encode-program decode-outcome])

;; --- 子の結果・coordinator の答え → 答えの型(純粋な換算) -------------------------------------------

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


;; 取り消しに当たる答えの status(本文の error を理由にした DetachedRefused にする)。413 = 詰めた Program が置き場の上限を越える
;; (PUT /programs — program_policy.PROGRAM-MAX-BYTES)。
(val REFUSED-STATUSES #(400 409 413 429))


;; --- 要求の形と答えの読み(本番の detached-cluster・warm-cluster と sim の宿が同じ関数を使う — 本文を写さない)-----------------------

(deff detached-path [#^ str key #^ str suffix]  ; defk にできない: 本番の client(Program の外の I/O の道具)と sim の宿が同じ形を作る純粋な判断
  {:pre [(: key str) (: suffix str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol"}}
  "切り離した task の口の path(key は path の 1 節に収まるよう quote する)を作るため。"
  (+ "/detached/" (url-quote key :safe "") suffix))


(defk detached-submit-body [sha revision needs name lease-seconds retain-seconds runtime-env environ]
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
   :tags {:context "doeff-cluster" :role "protocol" :reads "json"}}
  "返事が呼び手の誤り(400・409・413・429 — 形の誤り・同じ key の別の仕事・上限越え)なら、呼び手へ投げる DetachedRefused を作るため
   (それ以外は None)。"
  (if (in status REFUSED-STATUSES)
      (DetachedRefused status (str (.get (or body {}) "error" "")))
      None))


(deff submit-unreachable [#^ str reason]  ; defk にできない: 本番の client と sim の宿が同じ答えを作る純粋な判断
  {:pre [(: reason str)] :post [(: % DetachedUnreachable)] :tags {:context "doeff-cluster" :role "protocol"}}
  "送りが coordinator に届かなかった時の答えを作るため(送れたかは分からない — key で冪等なので呼び手が送り直してよい)。"
  (DetachedUnreachable :detail (.format "coordinator に届かない(送れたかは分からない — key で冪等): {}" reason)))


(deff awaited-answer [#^ (| dict None) view #^ str reason #^ str key #^ float waited #^ (| float int None) timeout-seconds]  ; defk にできない: 本番の client と sim の宿が同じ判断で待ちの 1 拍を読む
  {:pre [(: view (| dict None)) (: reason str) (: key str) (: waited float) (: timeout-seconds (| float int None))]
   :post [(: % (| DetachedAwaited None))] :tags {:context "doeff-cluster" :role "protocol"}}
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
  {:pre [(: workers dict)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "protocol"}}
  "coordinator の GET /state の workers(名 → {provides exclusive live draining taskRoom node …})を名簿の断面(RunnerFact の tuple・名の順)に
   するため。taskRoom(#3489)と node(Worker の資源の spec.node — 空 = k8s の外・#3747)は coordinator が必ず書く欄 — 無い
   答えは KeyError で落とす(既定の値で埋めない)。"
  (tuple (gfor #(name w) (sorted (.items workers))
               (RunnerFact :name name :provides (tuple (sorted (.get w "provides" []))) :exclusive (tuple (sorted (.get w "exclusive" [])))
                           :live (bool (get w "live")) :draining (bool (get w "draining")) :task-room (int (get w "taskRoom"))
                           :node (str (get w "node"))))))


(deff runners-change-of [#^ (| int None) status #^ (| dict list str int float bool None) body]  ; defk にできない: 本番の client と sim の宿が同じ読みを使う純粋な判断
  {:pre [(: status (| int None)) (: body (| dict list str int float bool None))] :post [(: % RunnersChangeAnswer)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "GET /watch の返事(status = None は届かない)を AwaitRunnersChange の答えにするため: 404 = 待つ口の無い旧い coordinator・
   200 の {revision changed} = 待ちの答え・それ以外は届かないと同じ(呼び手は間を置いて待ち直す)。"
  (cond
    (= status 404) (RunnersWatchMissing :detail "coordinator に GET /watch が無い(旧い版)")
    (and (= status 200) (isinstance body dict) (isinstance (.get body "revision") int) (isinstance (.get body "changed") bool))
      (RunnersChange :revision (get body "revision") :changed (get body "changed"))
    True (runners-unreachable (.format "{}: {}" status (cut (str body) 0 200)))))


(deff watch-query [#^ int after #^ float timeout-seconds]  ; defk にできない: 本番の client と sim の宿が同じ問いを作る純粋な判断
  {:pre [(: after int) (: timeout-seconds float)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "protocol"}}
  "AwaitRunnersChange の GET /watch の問いを作るため(worker を名指さない — coordinator 全体の版)。"
  {"after" (str (max 0 after)) "timeoutSeconds" (str timeout-seconds)})


(defk service-ready-of [status body]
  {:pre [(: status (| int None)) (: body (| dict list str int float bool None))] :post [(: % (| bool None))]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "AwaitServiceReady が読む GET /resources/Service/<名> の返事を、Ready(True)・まだ Ready でない(False)・届かない(None)に分けるため
   (本番の client と sim の宿が同じ読み — #3470)。200 の本文は ServiceViewWire に解く(形が違えば Malformed で名指して落ちる)。宣言の
   無い 404 は False: 作り直しや置き場の移しの間も越えて待つ(待つ側の上限が切る)。それ以外の status(5xx など)は届かないと同じ。"
  (match status
    None None
    200 (do (<- view (| ServiceViewWire Malformed) (parse ServiceViewWire body))
            (match view
              (Malformed :fields fields)
                (raise (ValueError (.format "coordinator の Service の返事が ServiceViewWire の形でない: {}"
                                            (.join "・" (gfor f fields (+ f.field " " f.reason))))))
              _ (= view.status.ready "Ready")))
    404 False
    _ None))


(defk service-readiness [cell options sender name]
  {:pre [(: cell RouteCell) (: options RouteOptions) (: sender DetachedSender) (: name str)] :post [(: % (| bool None))]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "名を挙げた Service が今 Ready かを coordinator の GET /resources/Service/<名> で読むため(AwaitServiceReady の 1 回の読み — 届かなければ None)。"
  (<- read (resent-answer cell options "GET" (+ "/resources/Service/" (url-quote name :safe "")) None None sender.deadline-seconds))
  (match read
    (HttpResponse :status status)
      (do (<- ready (| bool None) (service-ready-of status (try (json.loads read.text) (except [ValueError] read.text))))
          ready)
    _ None))


(defk service-ready-awaited [cell options sender name poll-seconds]
  {:pre [(: cell RouteCell) (: options RouteOptions) (: sender DetachedSender) (: name str) (: poll-seconds float)]
   :post [(: % ServiceReady)] :tags {:context "doeff-cluster" :role "protocol"}}
  "名を挙げた Service が Ready になるまで待つため(AwaitServiceReady の本番の答え — #3470)。Ready でなければ coordinator の版が変わるまで
   GET /watch(long-poll・上限 options.watch-seconds)で待って読み直す — 時間で起きて確かめず、版の変化で起きる。coordinator に届かない間だけ
   poll-seconds の間を置いて問い直す(境界の答え手の中だけの待ち)。待つ口の無い旧い coordinator は名指して落とす(後方互換を持たない)。"
  (var after 0)
  (while True
    (<- ready (| bool None) (service-readiness cell options sender name))
    (when (is ready True)
      (return (ServiceReady :name name :revision after)))
    (<- change (runners-changed cell options after options.watch-seconds))
    (match change
      (RunnersChange :revision revision) (:= after revision)
      (RunnersWatchMissing :detail detail)
        (raise (RuntimeError (.format "Service {!r} の Ready を版の変化で待てない(coordinator に GET /watch が無い): {}" name detail)))
      _ (<- (Delay poll-seconds)))))


(deff runners-unreachable [#^ str reason]  ; defk にできない: 本番の client と sim の宿が同じ答えを作る純粋な判断
  {:pre [(: reason str)] :post [(: % RunnersUnreachable)] :tags {:context "doeff-cluster" :role "protocol"}}
  "名簿の読みが coordinator に届かなかった時の答えを作るため。"
  (RunnersUnreachable :detail (.format "coordinator に届かない: {}" reason)))


(defk service-facts-of-view [view]
  {:pre [(: view ServiceListWire)] :post [(: % (get tuple #(ServiceFact ...)))] :tags {:context "doeff-cluster" :role "protocol"}}
  "coordinator の GET /resources/Service の本文を型で読み込んだ値(ServiceListWire)を、Service の一覧(ServiceFact の tuple・名の順)に
   するため。本番の client・sim・自分で GET を送る利用側の handler が同じこの関数を通る。落ちた事実は置き先の担い手の報告の行
   (status.process)の欄 — 行が無い・欄が無い時は None(0 と黙って倒さない・#3479)。"
  (tuple (gfor row (sorted view.items :key (fn [row] row.name))
               :setv spec row.spec
               :setv process (if (is row.status None) None row.status.process)
               (ServiceFact :name row.name :replicas (if (is spec None) None spec.replicas)
                            :failures (if (is process None) None process.failures)
                            :last-exit-code (if (is process None) None process.last-exit-code)
                            :last-exit-at-ms (if (is process None) None process.last-exit-at-ms)
                            ;; 宣言の版(#2718 の子 S2a)— 行の spec の revision。一覧の本文の頭の revision(coordinator の状態の版)ではない。
                            :revision (if (is spec None) None spec.revision)))))


(defk service-facts-of-json [body]
  {:pre [(: body (get Mapping #(str object)))] :post [(: % (get tuple #(ServiceFact ...)))] :tags {:context "doeff-cluster" :role "protocol"}}
  "coordinator の GET /resources/Service の返事の本文(JSON の object)を ServiceListWire に読み込み、Service の一覧にするため(本番の
   client と sim が同じこの関数を通る)。本文が型の形でなければ、合わない欄を挙げて ValueError で落ちる(Service が無いとは読まない)。"
  (<- view (| ServiceListWire Malformed) (parse ServiceListWire body))
  (match view
    (Malformed :fields fields)
      (raise (ValueError (.format "coordinator の Service の一覧の返事が ServiceListWire の形でない: {}"
                                  (.join "・" (gfor f fields (+ f.field " " f.reason))))))
    _ (do (<- facts (get tuple #(ServiceFact ...)) (service-facts-of-view view))
          facts)))


(deff services-unreachable [#^ str reason]  ; defk にできない: 本番の client と sim の宿が同じ答えを作る純粋な判断
  {:pre [(: reason str)] :post [(: % ServicesUnreachable)] :tags {:context "doeff-cluster" :role "protocol"}}
  "Service の一覧の読みが coordinator に届かなかった時の答えを作るため。"
  (ServicesUnreachable :detail (.format "coordinator に届かない: {}" reason)))


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
  {:pre [(: key str)] :post [(: % WarmState)] :tags {:context "doeff-cluster" :role "protocol"}}
  "表に無い行(404 — 期限で消えたか、書かれていない)の答えを作るため: ready も preparing も空・期限 0。"
  (WarmState :key key :ready #() :preparing #() :failed #() :until-ms 0))


;; coordinator の 5xx の下限(これ以上の状態は coordinator の側の失敗 — 呼び手の誤りではないので「届かない」の値にする)。
(val SERVER-ERROR 500)


(deff warm-unconnected [#^ str reason]  ; defk にできない: 本番の client と sim の宿が同じ答えを作る純粋な判断
  {:pre [(: reason str)] :post [(: % WarmUnreachable)] :tags {:context "doeff-cluster" :role "protocol"}}
  "温める表の頼みか読みが送り直しの期限まで coordinator に届かなかった時の答えを作るため(温まったかは分からない — 呼び手は温まって
   いないと同じに読み、次の拍で頼み直す)。"
  (WarmUnreachable :detail (.format "coordinator の /warm に接続できない: {}" reason)))


(deff warm-server-failure [#^ int status #^ str text]  ; defk にできない: 本番の client と sim の宿が同じ答えを作る純粋な判断
  {:pre [(: status int) (: text str)] :post [(: % WarmUnreachable)] :tags {:context "doeff-cluster" :role "protocol"}}
  "coordinator が温める表の口で 5xx(SERVER-ERROR 以上)を返した時の答えを作るため(呼び手の誤りではない — 届かないと同じに読む)。"
  (WarmUnreachable :detail (.format "coordinator の /warm が {} を返した: {}" status text)))


;; --- handler: coordinator の /detached の口へ出し、worker の子 process で走らせる ------------------------


(defrecord DetachedSender
  "切り離した task の送り手: revision = 送り手の commit(受け側はこの版のコードを準備してから復元する)・versions = 送り手の版の識別
   (blob に添える — 組み立てが宿の契約の Ask versions-key で読んで渡す・この層は読まない #2345)・runtime-env = 実行環境の宣言(在れば
   worker は env の root を準備して、その中の子 process で走らせる — revision は使わない)・deadline-seconds = 何度送っても同じ意味の
   要求を、通信の失敗を越えて送り直す期限(過ぎたら「届かない」の答え — 検は短くする)。"
  {:tags {:context "doeff-cluster" :role "protocol"}}
  (#^ str revision)
  (#^ dict versions)
  (#^ (| RuntimeEnv None) runtime-env)
  (#^ float deadline-seconds))


(defk resent-answer [cell options method path params body deadline-seconds]
  {:pre [(: cell RouteCell) (: options RouteOptions) (: method str) (: path str) (: params (| dict None)) (: body (| dict None))
         (: deadline-seconds float)]
   :post [(: % (| HttpResponse HttpFailed None))] :tags {:context "doeff-cluster" :role "protocol"}}
  "何度送っても同じ意味の要求 1 つを、通信の失敗を越えて deadline-seconds まで送り直し、答え(返事か最後の失敗)を返すため。
   宛先の状態は cell に書き戻す(切り離した task の口は、置き・送り・読み・取り消し・解放がどれも key で冪等)。"
  (<- reply RoutedReply (resent-request cell.route method path options params body deadline-seconds options.resend-pause-seconds))
  (setv cell.route reply.route)
  reply.answer)


(defk detached-json [answer]
  {:pre [(: answer (| HttpResponse HttpFailed None))] :post [(: % (| dict list str int float bool None))]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "切り離した task の口の答えを本文にするため: 呼び手の誤り(REFUSED-STATUSES)は DetachedRefused・ほかの断りは RouteRefused・
   届かないは RouteUnreachable(answer-json と同じ)。"
  (match answer
    (HttpResponse :status status) :if (in status REFUSED-STATUSES)
      (raise (detached-refusal status (json.loads answer.text)))
    _ (do (<- body (answer-json answer))
          body)))


(defk detached-submitted [cell options sender key blob needs name lease-seconds retain-seconds environ]
  {:pre [(: cell RouteCell) (: options RouteOptions) (: sender DetachedSender) (: key str) (: blob str) (: needs frozenset) (: name str)
         (: lease-seconds float) (: retain-seconds float) (: environ dict)]
   :post [(: % DetachedSubmitAnswer)] :tags {:context "doeff-cluster" :role "protocol"}}
  "切り離した task を 1 本出すため: 詰めた Program を版と一緒に置き場 /programs/<sha> に先に置き、本文は sha だけを運ぶ(service の宣言と
   同じ運び方 — ADR-DOE-CLUSTER-001 R3b)。置きも送りも何度送っても同じ意味なので、通信の失敗を越えて送り直し、期限まで届かなければ
   DetachedUnreachable(送れたかは分からない — key で冪等)。呼び手の誤りは DetachedRefused。"
  (<- put tuple (program-put cell options blob sender.versions sender.deadline-seconds))
  (setv #(sha stored) put)
  (when (isinstance stored HttpFailed)
    (return (submit-unreachable stored.detail)))
  (<- _stored (detached-json stored))
  (var declared None)
  (when (is-not sender.runtime-env None)
    (<- env-json dict (runtime-env->json sender.runtime-env))
    (:= declared env-json))
  (<- body dict (detached-submit-body sha sender.revision needs name lease-seconds retain-seconds declared environ))
  (<- sent (resent-answer cell options "PUT" (detached-path key "") None body sender.deadline-seconds))
  (when (isinstance sent HttpFailed)
    (return (submit-unreachable sent.detail)))
  (<- answer dict (detached-json sent))
  (DetachedSubmitted key (get answer "created")))


(defk detached-view [cell options sender key]
  {:pre [(: cell RouteCell) (: options RouteOptions) (: sender DetachedSender) (: key str)] :post [(: % (| dict str))]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "切り離した task の今の行を読むため(GET /detached/<key>)。答え = 行の本文か、届かなかった理由の文。503 = coordinator が起きた直後で
   行の無い key を知らないと言えない(phase warming — detached_policy.detached-read)— 本文を返し、待ちの側(awaited-answer)が届かないと
   同じに扱う。"
  (<- read (resent-answer cell options "GET" (detached-path key "") None None sender.deadline-seconds))
  (match read
    (HttpFailed :detail detail) detail
    (HttpResponse :status 503) (json.loads read.text)
    _ (do (<- view dict (detached-json read))
          view)))


(defk detached-flag [cell options sender method suffix key field]
  {:pre [(: cell RouteCell) (: options RouteOptions) (: sender DetachedSender) (: method str) (: suffix str) (: key str) (: field str)]
   :post [(: % bool)] :tags {:context "doeff-cluster" :role "protocol"}}
  "取り消し(POST /detached/<key>/cancel — cancelled)・解放(DELETE /detached/<key> — released)を送り、答えの真偽の欄を読むため。
   どちらも何度送っても同じ意味(終わりの phase は変わらない)なので期限まで送り直す。届かなければ RouteUnreachable を投げる。"
  (<- answer (resent-answer cell options method (detached-path key suffix) None None sender.deadline-seconds))
  (<- body dict (detached-json answer))
  (get body field))


(defk runners-read [cell options sender]
  {:pre [(: cell RouteCell) (: options RouteOptions) (: sender DetachedSender)] :post [(: % (| tuple RunnersUnreachable))]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "担い手の名簿を読むため(coordinator の GET /state の workers — live と draining は coordinator の判断)。届かなければ RunnersUnreachable。"
  (<- read (resent-answer cell options "GET" "/state" None None sender.deadline-seconds))
  (when (isinstance read HttpFailed)
    (return (runners-unreachable read.detail)))
  (<- state dict (answer-json read))
  (runner-facts-of-view (get state "workers")))


(defk services-read [cell options sender]
  {:pre [(: cell RouteCell) (: options RouteOptions) (: sender DetachedSender)]
   :post [(: % (| (get tuple #(ServiceFact ...)) ServicesUnreachable))] :tags {:context "doeff-cluster" :role "protocol"}}
  "Service の一覧を読むため(coordinator の GET /resources/Service — #3479)。届かなければ ServicesUnreachable。"
  (<- read (resent-answer cell options "GET" "/resources/Service" None None sender.deadline-seconds))
  (when (isinstance read HttpFailed)
    (return (services-unreachable read.detail)))
  (<- body dict (answer-json read))
  (<- facts (get tuple #(ServiceFact ...)) (service-facts-of-json body))
  facts)


(defk runners-changed [cell options after timeout-seconds]
  {:pre [(: cell RouteCell) (: options RouteOptions) (: after int) (: timeout-seconds float)] :post [(: % RunnersChangeAnswer)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "coordinator の版が after から変わるまで待つため(GET /watch — AwaitRunnersChange・#1934)。1 回だけ送る(接続の段だけ送り直す —
   届かなければ呼び手が間を置いて待ち直す。待ちを送り直しの期限まで重ねない)。"
  (<- reply RoutedReply (routed-request cell.route "GET" "/watch" options (watch-query after timeout-seconds) None))
  (setv cell.route reply.route)
  (val answer reply.answer)
  (match answer
    (HttpFailed :detail detail) (runners-unreachable detail)
    (HttpResponse :status status)
      (runners-change-of status (try (json.loads answer.text) (except [ValueError] answer.text)))
    _ (runners-unreachable "coordinator の宛先が無い")))


(defk await-cluster [cell options sender key timeout-seconds poll-seconds]
  {:pre [(: cell RouteCell) (: options RouteOptions) (: sender DetachedSender) (: key str) (: timeout-seconds (| float int None))
         (: poll-seconds float)]
   :post [(: % DetachedAwaited)] :tags {:context "doeff-cluster" :role "protocol"}}
  "切り離した task が終わるまで問い合わせるため。問い合わせは lease に触らず、抜けても(呼び手の Cancel・process の消失)何も落とさない。
   眠りは Delay(外側の doeff-time の handler)なので同じ VM の他の task を塞がない。1 拍の読みは awaited-answer(sim の宿と同じ判断)。"
  (var waited 0.0)
  (var answer None)
  (while (is answer None)
    (<- read (detached-view cell options sender key))
    (:= answer (if (isinstance read str)
                   (awaited-answer None read key waited timeout-seconds)
                   (awaited-answer read "" key waited timeout-seconds)))
    (when (is answer None)
      (<- (Delay poll-seconds))
      (:= waited (+ waited poll-seconds))))
  answer)

;; --- job の process の終わりの待ち(AwaitProcessEnded — process_model.hy)の本番の答え -------------------------------

;; worker の状態の行の phase のうち、子 process が動いている物と、まだ起きていない(準備・起動待ち・入口の検め)物。どちらでもない行
;; (backoff・finished・stopped・各種の失敗)は、その job の最後の process が終わった姿。
(val LIVE-JOB-PHASES (frozenset #("running" "stopping" "stop-unconfirmed")))
(val UNSTARTED-JOB-PHASES (frozenset #("preparing" "starting" "probing")))


(defrecord ProcessWatch
  "AwaitProcessEnded の本番の待ちの 1 回の読みの結果(process-watch-step の答え)。watched = 見張っている動いている process(終わった時の
   答えの形 — まだ見ていなければ None)・ended = 終わっていればその答え(まだなら None)。"
  (#^ (| ProcessEnded None) watched)
  (#^ (| ProcessEnded None) ended))


(defk process-watch-step [statuses job watched]
  {:pre [(: statuses dict) (: job str) (: watched (| ProcessEnded None))] :post [(: % ProcessWatch)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "coordinator の GET /state の statuses(worker の名 → 最後の状態の報告 {jobs stale …})の 1 回の読みから、job の待つ相手の process が
   終わったかを決めるため。沈黙した worker(stale)の報告は数えない。見張っている process が動いている行から消えれば終わり・まだ
   見張っていなければ、動いている行を見張り始めるか、終わった姿の行(動いていない・起きる前でもない)ならすぐ終わり。"
  (val rows (lfor #(worker status) (sorted (.items statuses)) :if (not (.get status "stale" False))
                  row (.get status "jobs" []) :if (= (.get row "name") job)
                  #(worker row)))
  (val live (lfor #(worker row) rows :if (in (.get row "phase") LIVE-JOB-PHASES)
                  (ProcessEnded :job job :instance (str (or (.get row "instance") "")) :worker worker)))
  (val gone (lfor #(worker row) rows :if (not-in (.get row "phase") (| LIVE-JOB-PHASES UNSTARTED-JOB-PHASES))
                  (ProcessEnded :job job :instance (str (or (.get row "instance") "")) :worker worker)))
  (cond
    (is-not watched None) (ProcessWatch :watched watched :ended (if (in watched live) None watched))
    live (ProcessWatch :watched (get live 0) :ended None)
    gone (ProcessWatch :watched None :ended (get gone 0))
    True (ProcessWatch :watched None :ended None)))


(defk await-process-cluster [cell options sender job timeout-seconds poll-seconds]
  {:pre [(: cell RouteCell) (: options RouteOptions) (: sender DetachedSender) (: job str) (: timeout-seconds (| float int None))
         (: poll-seconds float)]
   :post [(: % (| ProcessEnded ProcessWaitExpired))] :tags {:context "doeff-cluster" :role "protocol" :reads "json"}}
  "AwaitProcessEnded の本番の答え: coordinator の GET /state を poll-seconds ごとに読み、process-watch-step で終わりを決める(本番の
   coordinator は長い待ちの読みを持たないので読み直す — 契約の答え)。届かない読みは次の拍で読み直す。timeout-seconds を過ぎたら
   ProcessWaitExpired。眠りは Delay(同じ VM の他の task を塞がない)。"
  (var waited 0.0)
  (var watched None)
  (var answer None)
  (while (is answer None)
    (<- read (resent-answer cell options "GET" "/state" None None sender.deadline-seconds))
    (when (and (isinstance read HttpResponse) (= read.status 200))
      (<- step ProcessWatch (process-watch-step (.get (json.loads read.text) "statuses" {}) job watched))
      (:= watched step.watched)
      (:= answer step.ended))
    (when (and (is answer None) (is-not timeout-seconds None) (>= waited timeout-seconds))
      (:= answer (ProcessWaitExpired :job job :waited-seconds waited)))
    (when (is answer None)
      (<- (Delay poll-seconds))
      (:= waited (+ waited poll-seconds))))
  answer)


;; 本物の切り離した task: coordinator の /programs・/detached・/state・/watch へ、汎用の HttpRequest で話す(#2337 の 4c — httpx を直に
;; 持っていた DetachedClient を替えた)。宛先の順・切り替え・送り直しは宛先の部品(coordinator_route.hy)— 宛先の状態は組み立てが渡す
;; 入れ物(RouteCell)。出す HttpRequest に答える本物の I/O の答え手は、process の組み立ての根が外側に積む。
(defhandler detached-cluster [#^ RouteCell cell #^ RouteOptions options #^ DetachedSender sender #^ float [poll-seconds 1.0]]
  (SubmitDetached [program key needs name lease-seconds retain-seconds environ]
    ;; 送れない値は送る前に断る(encode-program が UnsendableProgram を投げ、呼び手へ届く)。
    (setv blob (encode-program program))
    ;; effect の EnvVar の tuple を、coordinator への本文の形(名 → 値の object)へ綴る(#2179)。
    (<- environ-body dict (env-mapping environ))
    (<- submitted (detached-submitted cell options sender key blob needs name (float lease-seconds) (float retain-seconds) environ-body))
    (resume submitted))
  (AwaitDetached [key timeout-seconds]
    (<- outcome (await-cluster cell options sender key timeout-seconds poll-seconds))
    (resume outcome))
  (CancelDetached [key]
    (<- cancelled bool (detached-flag cell options sender "POST" "/cancel" key "cancelled"))
    (resume cancelled))
  (ReleaseDetached [key]
    (<- released bool (detached-flag cell options sender "DELETE" "" key "released"))
    (resume released))
  (ReadRunners []
    (<- runners (runners-read cell options sender))
    (resume runners))
  (ReadServices []
    (<- services (services-read cell options sender))
    (resume services))
  (AwaitRunnersChange [after timeout-seconds]
    (<- change (runners-changed cell options after (float timeout-seconds)))
    (resume change))
  (AwaitServiceReady [name]
    (<- ready ServiceReady (service-ready-awaited cell options sender name poll-seconds))
    (resume ready))
  (AwaitProcessEnded [job timeout-seconds]
    (<- ended (await-process-cluster cell options sender job timeout-seconds poll-seconds))
    (resume ended)))


;; --- 温める表(2026-09-26): coordinator の /warm の口 -------------------------------------------------


(defk warm-answer [answer key]
  {:pre [(: answer (| HttpResponse HttpFailed None)) (: key (| str None))] :post [(: % WarmAnswer)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "温める表の口の答えを WarmAnswer にするため: 送り直しの期限まで届かない = WarmUnreachable・coordinator の 5xx = WarmUnreachable
   (2026-09-28 — 拍ごとに温める送り手が coordinator の入れ替えの間に落ちないため)・400 = 呼び手の誤り(DetachedRefused を投げる)・
   読みの 404 = 表に無い行(key を渡した時だけ — 空の姿)・それ以外 = 行の姿。"
  (match answer
    (HttpFailed :detail detail) (warm-unconnected detail)
    (HttpResponse :status 400) (raise (DetachedRefused 400 (.get (json.loads answer.text) "error" "")))
    (HttpResponse :status 404) :if (is-not key None) (absent-warm-state key)
    (HttpResponse :status status) :if (>= status SERVER-ERROR) (warm-server-failure status answer.text)
    _ (do (<- row dict (answer-json answer))
          (warm-state-of-json row))))


(defk warm-written [cell options deadline-seconds env needs ttl-seconds holder]
  {:pre [(: cell RouteCell) (: options RouteOptions) (: deadline-seconds float) (: env RuntimeEnv) (: needs frozenset)
         (: ttl-seconds float) (: holder str)]
   :post [(: % WarmAnswer)] :tags {:context "doeff-cluster" :role "protocol"}}
  "温める表の行を書いて今の姿を読むため(POST /warm — 同じ行への頼み直しは同じ意味なので、通信の失敗を越えて送り直す)。"
  (<- declared dict (runtime-env->json env))
  (<- answer (resent-answer cell options "POST" "/warm" None (warm-request-body declared needs ttl-seconds holder) deadline-seconds))
  (<- state (warm-answer answer None))
  state)


(defk warm-read [cell options deadline-seconds key]
  {:pre [(: cell RouteCell) (: options RouteOptions) (: deadline-seconds float) (: key str)] :post [(: % WarmAnswer)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "温める表の行の今の姿を読むため(GET /warm/<key> — 表に無い行は ready も preparing も空・期限 0)。"
  (<- answer (resent-answer cell options "GET" (warm-path key) None None deadline-seconds))
  (<- state (warm-answer answer key))
  state)


;; 組みの待ち(AwaitWarm)が coordinator に届かない間に問い直す間(秒 — detached-cluster の poll-seconds の既定と同じ)。
(val WARM-UNREACHED-PAUSE-SECONDS 1.0)


(defk warm-awaited [cell options key timeout-seconds poll-seconds]
  {:pre [(: cell RouteCell) (: options RouteOptions) (: key str) (: timeout-seconds float) (: poll-seconds float)]
   :post [(: % (| WarmReady WarmFailed WarmWaitExpired))] :tags {:context "doeff-cluster" :role "protocol"}}
  "温める表の行 key が組み上がるか落ちるまで待つため(AwaitWarm の本番の答え — #3668 (b))。GET /warm/<key> で読み、答えの判断
   (warm_rules.warm-wait-answer — sim の宿と同じ物)が待ちを言えば、coordinator の版が変わるまで GET /watch(long-poll・残りの秒と
   options.watch-seconds の小さい方)で待って読み直す — 時間で起きて確かめず、版の変化で起きる(worker の組みの進みは Worker の行の
   status の env に載り版を進める)。coordinator に届かない間だけ poll-seconds の間を置いて問い直す。待つ口の無い旧い coordinator は
   名指して落とす(後方互換を持たない)。"
  (<- started int (now-epoch-ms))
  (var after 0)
  (var answer None)
  (while (is answer None)
    (<- read WarmAnswer (warm-read cell options options.resend-deadline-seconds key))
    (<- now int (now-epoch-ms))
    (val waited (/ (- now started) 1000.0))
    (<- step (| WarmReady WarmFailed WarmWaitExpired None) (warm-wait-answer read key waited timeout-seconds))
    (val left (max 0.0 (- timeout-seconds waited)))
    (if (is-not step None)
        (:= answer step)
        (do (<- change (runners-changed cell options after (min options.watch-seconds left)))
            (match change
              (RunnersChange :revision revision) (:= after revision)
              (RunnersWatchMissing :detail detail)
                (raise (RuntimeError (.format "温める表の行 {!r} の組みを版の変化で待てない(coordinator に GET /watch が無い): {}" key detail)))
              _ (<- (Delay (min poll-seconds left)))))))
  answer)


;; 本物の温める表: coordinator の /warm へ、汎用の HttpRequest で話す(#2337 の 4c — httpx を直に持っていた WarmClient を替えた)。
;; 答えは WarmAnswer(coordinator に届かなければ WarmUnreachable — 例外で呼び手を落とさない)。書きの送り手の名は options の actor。
(defhandler warm-cluster [#^ RouteCell cell #^ RouteOptions options]
  (WarmRuntimeEnv [env needs ttl-seconds holder]
    (<- state (warm-written cell options options.resend-deadline-seconds env needs (float ttl-seconds) holder))
    (resume state))
  (ReadWarmState [key]
    (<- state (warm-read cell options options.resend-deadline-seconds key))
    (resume state))
  (AwaitWarm [key timeout-seconds]
    (<- answer (warm-awaited cell options key (float timeout-seconds) WARM-UNREACHED-PAUSE-SECONDS))
    (resume answer)))
