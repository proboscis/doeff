;;; ReachJob(届くかの検査 — doeff_cluster.shared.intent.job_reach_model)の handler 2 つ。
;;;
;;;   job-reach-sender   cluster の handler: 道ごとの宛先の表(ReachDestination — API の名・根・名で引く道の URL を持つ環境変数の名・番地の道の
;;;                      URL を持つ環境変数の名)で、要求を <URL><target> へ HTTP の要求 1 本で出す(待ちは patience 秒・繋ぐまでは connect-patience
;;;                      秒・やり直しなし・redirect は要求の follow-redirects の時だけ辿る・届かない時は値で受ける)。答えは HttpResponse → JobReached・
;;;                      HttpFailed → JobNotReached(HttpFailureKind を届かない種類へ写す — 訳の字では分けない)。道の環境変数の名か値が空なら
;;;                      出さずに JobNoWay。表に無い API の ReachJob は外側へ回す
;;;   job-reach-handover 模擬の handler: 相手の表(CallJob と同じ Counterpart — API の名と、要求 1 つへの相手の job の答えの 1 点)で、要求を
;;;                      相手へ直に渡す(HTTP も宛先も道も通らない — 道を問わず同じ相手)。JobAnswer → JobReached・JobUnreachable → JobNotReached
;;;                      (繋がらなかった)。表に無い API の ReachJob は外側へ回す
;;;
;;; 要求の行の target の綴りは CallJob の送る側と同じ 1 点 job-target。2 つが同じ要求に同じ答えを返す事は契約の検
;;; tests/test_job_reach_contract.hy が見る。宛先の環境変数の名・相手の答えの 1 点は使い手の組み立てが決めて渡す(この module は API の名を
;;; 1 つも知らない)。
(require doeff-hy.macros [defhandler defk <- val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.http_effects [HttpFailed HttpFailureKind HttpRequest HttpResponse])
(import doeff_cluster.shared.intent.job_call_model [JobAnswer JobRequest JobUnreachable])
(import doeff_cluster.shared.intent.job_reach_model [JobNoWay JobNotReached JobReached JobWay ReachFailure ReachJob])
(import doeff_cluster.shared.protocol.job_call_handlers [Counterpart job-target])


(defrecord ReachDestination
  "API 1 つの道ごとの宛先(頭の註): api = API の名・root = API の口の根の path(空か `/` で始まり `/` で終わらない)・environ = 名で引く道の
   宛先の URL(scheme・host・port)を持つ環境変数の名・address-environ = 番地の道の宛先の URL を持つ環境変数の名(空 = 番地の道を持たない)。"
  {:tags {:context "doeff-cluster" :role "protocol"}
   :check [(.strip api) (.strip environ) (or (= root "") (and (.startswith root "/") (not (.endswith root "/"))))]}
  (#^ str api)
  (#^ str root)
  (#^ str environ)
  (setv #^ str address-environ ""))


(defk reach-failure-of [kind]
  {:pre [(: kind HttpFailureKind)] :post [(: % ReachFailure)] :tags {:context "doeff-cluster" :role "protocol"}}
  "答えの来なかった HTTP の要求の失敗の種類を、届かない種類の閉じた語へ写すため(頭の註 — 写すだけで判じない。知らない種類は当てはまる枝が
   無く :post で落ちる — 既定に倒さない)。"
  (match kind
    HttpFailureKind.CONNECT-FAILED ReachFailure.NOT-CONNECTED
    HttpFailureKind.TIMED-OUT ReachFailure.TIMED-OUT
    HttpFailureKind.OTHER ReachFailure.OTHER))


(defk reach-answer-of [response]
  {:pre [(: response (| HttpResponse HttpFailed))] :post [(: % (| JobReached JobNotReached))] :tags {:context "doeff-cluster" :role "protocol"}}
  "宛先の口の答え response を届いた事実にするため(頭の註): 届いた答えは status だけ、届かなかった答えは種類と訳。"
  (match response
    (HttpFailed :kind kind :detail detail) (do (<- failure ReachFailure (reach-failure-of kind))
                                               (JobNotReached :failure failure :reason detail))
    (HttpResponse :status status) (JobReached :status status)))


(defk way-environ [destination way]
  {:pre [(: destination ReachDestination) (: way JobWay)] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol"}}
  "道 way の宛先の環境変数の名を引くため(番地の道を持たない宛先は空)。"
  (match way
    JobWay.BY-NAME destination.environ
    JobWay.BY-ADDRESS destination.address-environ))


(defk sent-reach [destination request way connect-patience]
  {:pre [(: destination ReachDestination) (: request JobRequest) (: way JobWay) (: connect-patience (| float None))]
   :post [(: % (| JobReached JobNotReached JobNoWay))] :tags {:context "doeff-cluster" :role "protocol" :spells "http"}}
  "要求 request を宛先 destination の道 way の口へ HTTP の要求 1 本で出し、届いた事実にするため(頭の註 — 道の名か値が空なら出さない)。"
  (<- environ str (way-environ destination way))
  (when (not (.strip environ))
    (return (JobNoWay :reason (.format "API {} は道 {} の宛先を持たない" destination.api way.value))))
  (<- base str (Ask environ))
  (when (not (.strip base))
    (return (JobNoWay :reason (.format "API {} の道 {} の宛先の環境変数 {} が空" destination.api way.value environ))))
  (<- target str (job-target destination.root request))
  (<- response (| HttpResponse HttpFailed)
      (HttpRequest request.method (+ (.rstrip (.strip base) "/") target)
                   :headers (if request.headers (dfor field request.headers field.name field.value) None)
                   :body request.body :timeout-seconds request.patience :connect-timeout-seconds connect-patience :max-retries 0
                   :follow-redirects request.follow-redirects :failures-as-values True :log-each-request False))
  (<- answer (| JobReached JobNotReached) (reach-answer-of response))
  answer)


(defk handed-reach [counterpart request]
  {:pre [(: counterpart Counterpart) (: request JobRequest)] :post [(: % (| JobReached JobNotReached))] :tags {:context "doeff-cluster" :role "protocol"}}
  "要求 request を相手の job の答えの 1 点へ直に渡し、届いた事実にするため(頭の註 — 答えなかった相手は繋がらなかったと読む)。"
  (<- answer (| JobAnswer JobUnreachable) (counterpart.answer request))
  (match answer
    (JobAnswer :status status) (JobReached :status status)
    (JobUnreachable :reason reason) (JobNotReached :failure ReachFailure.NOT-CONNECTED :reason reason)))


(defhandler job-reach-sender [#^ (get tuple #(ReachDestination ...)) destinations]
  ;; cluster で届くかの要求を道ごとの宛先の口へ出すため(頭の註)。
  ;; 引数に残す理由: どの API をどの環境変数の宛先へ出すかは使い手の組み立てごとの値(業務の code にも Ask の設定にも置かない)。
  (ReachJob [api request way connect-patience]
    (val found (next (gfor destination destinations :if (= destination.api api) destination) None))
    (if (is found None)
        (reperform effect)
        (do (<- answer (| JobReached JobNotReached JobNoWay) (sent-reach found request way connect-patience))
            (resume answer)))))


(defhandler job-reach-handover [#^ (get tuple #(Counterpart ...)) counterparts]
  ;; 模擬で届くかの要求を相手の job の答えの 1 点へ直に渡すため(頭の註)。
  ;; 引数に残す理由: 相手の job の答え手(置き場・session)は模擬の世界ごとの値で、要求をまたいで持ち越す。
  (ReachJob [api request]
    (val found (next (gfor counterpart counterparts :if (= counterpart.api api) counterpart) None))
    (if (is found None)
        (reperform effect)
        (do (<- answer (| JobReached JobNotReached) (handed-reach found request))
            (resume answer)))))
