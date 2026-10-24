;;; CallJob(job 向けの effect — doeff_cluster.shared.intent.job_call_model)の handler 2 つと、両方が読む要求の綴りの 1 点。
;;;
;;;   job-target        API の根 root と要求の path・query → 要求の行の target(根の URL の後ろ — path は PATH-SAFE の外を %-encode・query は要求の順の
;;;                     まま、同じ名の 2 つ目も落とさない)。送る側の URL も、直に渡された相手が読む要求の綴りもこの 1 点
;;;   job-call-sender   cluster の handler: 宛先の表(JobDestination — API の名・根・宛先の URL を持つ環境変数の名)で、要求を <URL><target> へ HTTP の
;;;                     要求 1 本で送る(待ちは patience 秒・やり直しなし・redirect は要求の follow-redirects の時だけ辿る・届かない時は値で受ける)。答えは HttpResponse →
;;;                     JobAnswer・HttpFailed → JobUnreachable。環境変数の値が空なら送らずに JobUnreachable。表に無い API の CallJob は外側へ回す
;;;   job-call-handover 模擬の handler: 相手の表(Counterpart — API の名と、要求 1 つへの相手の job の答えの 1 点)で、要求を相手へ直に渡す
;;;                     (HTTP も宛先も通らない)。表に無い API の CallJob は外側へ回す
;;;
;;; 2 つが同じ要求に同じ答えを返す事(送る側の綴りと答えの写しが、直に渡す側と食い違わない事)は契約の検 tests/test_job_call_contract.hy が
;;; 見る(送る側の HttpRequest には、同じ process の中で HTTP の待ち受けと同じく要求の行を読んで相手の答えの 1 点へ渡す答え手が答える)。
;;; 宛先の環境変数の名・相手の答えの 1 点は使い手の組み立てが決めて渡す(この module は API の名を 1 つも知らない)。
(require doeff-hy.macros [defhandler defk <- val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(import dataclasses [dataclass])
(import collections.abc [Callable])
(import urllib.parse [quote :as url-quote urlencode])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.http_effects [HttpFailed HttpRequest HttpResponse])
(import doeff_cluster.shared.intent.job_call_model [CallJob JobAnswer JobField JobRequest JobUnreachable])

;; path の綴りで %-encode しない字(区切りの / と、鍵によく使う : — 鍵の綴りを読める形のまま送る)。
(val PATH-SAFE "/:")
;; 答えの見出しの名(content-type — 名の大小を問わずに引く)と、見出しが無い時の値。
(val CONTENT-TYPE "content-type")
(val NO-CONTENT-TYPE "")


(defrecord JobDestination
  "API 1 つの宛先(頭の註): api = API の名・root = API の口の根の path(空か `/` で始まり `/` で終わらない)・environ = 宛先の URL(scheme・host・
   port)を持つ環境変数の名。"
  {:tags {:context "doeff-cluster" :role "protocol"}
   :check [(.strip api) (.strip environ) (or (= root "") (and (.startswith root "/") (not (.endswith root "/"))))]}
  (#^ str api)
  (#^ str root)
  (#^ str environ))


(defrecord Counterpart
  "API 1 つの相手(頭の註): api = API の名・answer = 要求 1 つ(JobRequest)→ 答え(JobAnswer か JobUnreachable)の Program を作る関数 — 相手の
   job の答えの 1 点を、相手の job の答え手の下で回す物(使い手の組み立てが作る)。"
  {:tags {:context "doeff-cluster" :role "protocol"} :check [(.strip api)]}
  (#^ str api)
  (#^ (get Callable #(... object)) answer))


(defk job-target [root request]
  {:pre [(: root str) (: request JobRequest)] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol"}}
  "API の根 root と要求 request の path・query から、要求の行の target を綴るため(頭の註 — 送る側と直に渡す側の綴りの 1 点)。"
  (val path (+ root (url-quote request.path :safe PATH-SAFE)))
  (if request.query
      (+ path "?" (urlencode (lfor field request.query #(field.name field.value))))
      path))


(defk job-answer-of [response]
  {:pre [(: response (| HttpResponse HttpFailed))] :post [(: % (| JobAnswer JobUnreachable))] :tags {:context "doeff-cluster" :role "protocol"}}
  "宛先の口の答え response を API の答えにするため(頭の註): 届いた答えは status・content-type・本文・見出しのまま、届かなかった答えは訳と
   宛先の 1 行。"
  (if (isinstance response HttpFailed)
      (JobUnreachable :reason (.format "{}: {}({})" response.kind response.detail response.url))
      (JobAnswer :status response.status
                 :content-type (next (gfor #(name value) (.items response.headers) :if (= (.lower name) CONTENT-TYPE) value) NO-CONTENT-TYPE)
                 :body response.content
                 :headers (tuple (gfor #(name value) (.items response.headers) (JobField :name name :value value))))))


(defk sent-call [destination request]
  {:pre [(: destination JobDestination) (: request JobRequest)] :post [(: % (| JobAnswer JobUnreachable))]
   :tags {:context "doeff-cluster" :role "protocol" :spells "http"}}
  "要求 request を宛先 destination の口へ HTTP の要求 1 本で送り、答えを API の答えにするため(頭の註 — 環境変数の値が空なら送らない)。"
  (<- base str (Ask destination.environ))
  (when (not (.strip base))
    (return (JobUnreachable :reason (.format "API {} の宛先の環境変数 {} が空" destination.api destination.environ))))
  (<- target str (job-target destination.root request))
  (<- response (| HttpResponse HttpFailed)
      (HttpRequest request.method (+ (.rstrip (.strip base) "/") target)
                   :headers (if request.headers (dfor field request.headers field.name field.value) None)
                   :body request.body :timeout-seconds request.patience :max-retries 0 :follow-redirects request.follow-redirects
                   :failures-as-values True :log-each-request False))
  (<- answer (| JobAnswer JobUnreachable) (job-answer-of response))
  answer)


(defhandler job-call-sender [#^ (get tuple #(JobDestination ...)) destinations]
  ;; cluster で API の要求を宛先の口へ送るため(頭の註)。
  ;; 引数に残す理由: どの API をどの環境変数の宛先へ送るかは使い手の組み立てごとの値(業務の code にも Ask の設定にも置かない)。
  (CallJob [api request]
    (val found (next (gfor destination destinations :if (= destination.api api) destination) None))
    (if (is found None)
        (reperform effect)
        (do (<- answer (| JobAnswer JobUnreachable) (sent-call found request))
            (resume answer)))))


(defhandler job-call-handover [#^ (get tuple #(Counterpart ...)) counterparts]
  ;; 模擬で API の要求を相手の job の答えの 1 点へ直に渡すため(頭の註)。
  ;; 引数に残す理由: 相手の job の答え手(置き場・session)は模擬の世界ごとの値で、要求をまたいで持ち越す。
  (CallJob [api request]
    (val found (next (gfor counterpart counterparts :if (= counterpart.api api) counterpart) None))
    (if (is found None)
        (reperform effect)
        (do (<- answer (| JobAnswer JobUnreachable) (found.answer request))
            (resume answer)))))
