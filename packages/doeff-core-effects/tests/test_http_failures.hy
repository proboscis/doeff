(require doeff-hy.macros [deftest defk <- with-handler])

;;; HttpRequest の failures-as-values(agora-redesign #805 の構成レビュー 2-1 — 呼び手が transport の library の例外の型を知らずに届かない失敗を読むため):
;;;   * 立てれば、応答が 1 度も来なかった失敗は HttpFailed(url・detail = 例外の class と文・kind = 失敗の類)で答える
;;;   * 立てなければ今までどおり transport の例外が上がる(今の使い手は変わらない)
;;;   この 2 つは本物と fake(fixture の replay)の共通の契約として test_http_request_contract.hy が見る(agora-redesign #1159)。
;;; 失敗の類 kind(agora-redesign #850 — 消費者が detail の型名の文字列で期限切れを判じないため):
;;;   * 答え手が transport の例外の class の階層から写す: 期限切れ 4 種 = TIMED-OUT・接続できない = CONNECT-FAILED・残り = OTHER
;;;   * 反例: 名が Timeout で終わるだけの例外や、文に timed out を含む失敗は期限切れと読めない
;;;   * kind は既定値を持たない(作り手が書き忘れたら作る時に落ちる — 黙って「期限切れでない」にならない)
;;; 届かない相手は httpx 自身の MockTransport(library の持ち主の相手役)で作る。

(import datetime)
(import httpx)
(import doeff_core_effects.handlers [await-handler])
(import doeff_core_effects.http_effects [HttpFailed HttpFailureKind HttpRequest HttpResponse])
(import doeff_core_effects.http_handlers [http-production-handler])


(defclass GatewayTimeout [httpx.NetworkError]
  "名が Timeout で終わるが期限切れの類(httpx.TimeoutException)ではない transport の失敗 — 型の名を読むと期限切れと取り違える反例。")


(defk ask [url values]
  {:pre [(: url str) (: values bool)] :post [(: % (| HttpResponse HttpFailed))] :tags {:context "http" :role "program"}}
  "failures-as-values の旗だけを変えて同じ要求を 1 回出すため(撃ち直しなし)。"
  (<- answer (| HttpResponse HttpFailed) (HttpRequest "PATCH" url :max-retries 0 :failures-as-values values))
  answer)


(defk answer-from-silent-server [error-class text values]
  {:pre [(: error-class type) (: text str) (: values bool)] :post [(: % (| HttpResponse HttpFailed))]
   :tags {:context "http" :role "foundation"}}
  "応答しない相手(MockTransport が要求ごとに error-class の例外を上げる)へ本番の答え手で 1 回要求し、答え手の答えを読むため。"
  (<- answer (| HttpResponse HttpFailed)
      (with-handler [(await-handler)
                     (http-production-handler
                       :client-factory (fn [] (httpx.AsyncClient :transport (httpx.MockTransport (fn [request] (raise (error-class text :request request)))))))]
        (ask "https://api.test/x" values)))
  answer)


(deftest test-the-failure-kind-comes-from-the-transport-error-class
  (for [#(error-class kind) [#(httpx.ConnectTimeout HttpFailureKind.CONNECT-FAILED)
                            #(httpx.ReadTimeout HttpFailureKind.TIMED-OUT)
                            #(httpx.WriteTimeout HttpFailureKind.TIMED-OUT)
                            #(httpx.PoolTimeout HttpFailureKind.TIMED-OUT)
                            #(httpx.ConnectError HttpFailureKind.CONNECT-FAILED)
                            #(httpx.ReadError HttpFailureKind.OTHER)
                            #(httpx.RemoteProtocolError HttpFailureKind.OTHER)]]
    (<- failed (| HttpResponse HttpFailed) (answer-from-silent-server error-class "no answer" True))
    (assert (= failed.kind kind) #(error-class failed))))


(deftest test-a-failure-that-only-looks-like-a-timeout-is-not-read-as-one
  ;; detail は型の名と文を運ぶので、detail を読む暫定は 1 つ目(型の名が …Timeout)を期限切れと読み、文を読めば 2・3 つ目も
  ;; 期限切れと読んでしまう(#850 の反例)。kind は class の階層だけから決まる。
  (for [#(error-class text detail kind) [#(GatewayTimeout "upstream gave up" "GatewayTimeout: upstream gave up" HttpFailureKind.OTHER)
                                        #(httpx.ConnectError "timed out" "ConnectError: timed out" HttpFailureKind.CONNECT-FAILED)
                                        #(httpx.ReadError "ReadTimeout: peer went away" "ReadError: ReadTimeout: peer went away"
                                          HttpFailureKind.OTHER)]]
    (<- failed (| HttpResponse HttpFailed) (answer-from-silent-server error-class text True))
    (assert (= failed (HttpFailed :url "https://api.test/x" :detail detail :kind kind)) failed)))


(deftest test-a-failure-cannot-be-made-without-its-kind
  (try
    (HttpFailed :url "https://api.test/x" :detail "ReadTimeout: timed out")
    (assert False "kind の無い HttpFailed が作れた")
    (except [TypeError])))


(defclass TimeoutSpy []
  "要求ごとの timeout の引数を控えて、200 の返事を返す client(接続の段の上限の検 — 本物の transport は時間を数えない)。
   要求の口は答え手が呼ぶ client の口(http_handlers.pyi の HttpAsyncClient)と同じ引数を受ける。"
  (defn #^ None __init__ [self]
    "控えた timeout の列を空で始めるため。"
    (setv #^ (get tuple #((| float httpx.Timeout None) ...)) self.timeouts #()))
  (defn :async #^ httpx.Response request [self #^ str method #^ str url *
                                         #^ (| (get dict #(str (| str int float bool None))) None) [params None]
                                         #^ (| bytes None) [content None]
                                         #^ (| (get dict #(str str)) None) [headers None]
                                         #^ (| float httpx.Timeout None) [timeout None]
                                         #^ bool [follow-redirects True]]
    "要求の timeout を控えて、送らずに 200 の返事を返すため。"
    (setv self.timeouts (+ self.timeouts #(timeout)))
    (setv response (httpx.Response 200 :content b"ok" :request (httpx.Request method url)))
    (setattr response "elapsed" (datetime.timedelta 0))
    response)
  (defn :async #^ None aclose [self]
    "閉じる口(持つ資源が無いので何もしない)。"
    None))


(defk ask-with-connect-limit []
  {:pre [] :post [(: % tuple)] :tags {:context "http" :role "program"}}
  "接続の段の上限を付けた要求と付けない要求を 1 つずつ出すため。"
  (<- limited HttpResponse (HttpRequest "GET" "https://api.test/a" :timeout-seconds 15.0 :connect-timeout-seconds 2.0 :max-retries 0))
  (<- plain HttpResponse (HttpRequest "GET" "https://api.test/b" :timeout-seconds 15.0 :max-retries 0))
  #(limited plain))


(deftest test-a-connect-limit-is-passed-to-the-client-only-for-the-connection
  ;; 失敗ケース(#2337): connect-timeout-seconds は接続の段だけの上限として client に渡り(全体の上限は timeout-seconds のまま)、
  ;; 付けない要求は今までどおり全体の上限だけを渡す。
  (val spy (TimeoutSpy))
  (<- answers tuple (with-handler [(await-handler) (http-production-handler :client-factory (fn [] spy))] (ask-with-connect-limit)))
  (assert (= (tuple (gfor a answers a.status)) #(200 200)) answers)
  (val limited (get spy.timeouts 0))
  (val plain (get spy.timeouts 1))
  (assert (and (isinstance limited httpx.Timeout) (= limited.connect 2.0) (= limited.read 15.0)) limited)
  (assert (= plain 15.0) plain))


(defclass Made []
  "ClosingSpy を作った数と閉じた数(検の終わりに読む)。"
  (defn #^ None __init__ [self]
    "数えを 0 で始めるため。"
    (setv self.opened 0)
    (setv self.closed 0)))


(defclass ClosingSpy []
  "閉じた後の要求を断る client(範囲ごとの client の寿命の検 — httpx の client も閉じた後の要求を断る)。
   作られた数と閉じられた数を、作り手の数え(Made)に控える。"
  (defn #^ None __init__ [self #^ Made made]
    "作られた事を数えに足して、開いた client として始めるため。"
    (setv self.made made)
    (setv self.closed False)
    (setv made.opened (+ made.opened 1)))
  (defn :async #^ httpx.Response request [self #^ str method #^ str url *
                                         #^ (| (get dict #(str (| str int float bool None))) None) [params None]
                                         #^ (| bytes None) [content None]
                                         #^ (| (get dict #(str str)) None) [headers None]
                                         #^ (| float httpx.Timeout None) [timeout None]
                                         #^ bool [follow-redirects True]]
    "閉じた後なら httpx と同じく断り、開いていれば送らずに 200 の返事を返すため。"
    (when self.closed
      (raise (RuntimeError "Cannot send a request, as the client has been closed.")))
    (setv response (httpx.Response 200 :content b"ok" :request (httpx.Request method url)))
    (setattr response "elapsed" (datetime.timedelta 0))
    response)
  (defn :async #^ None aclose [self]
    "閉じた印を付け、閉じた数を数えに足すため。"
    (setv self.closed True)
    (setv self.made.closed (+ self.made.closed 1))))


(defk ask-once [url]
  {:pre [(: url str)] :post [(: % HttpResponse)] :tags {:context "http" :role "program"}}
  "撃ち直しなしで GET を 1 回出すため。"
  (<- answer HttpResponse (HttpRequest "GET" url :max-retries 0))
  answer)


(deftest test-one-handler-value-installed-around-two-scopes-answers-both
  ;; 失敗ケース(agora-redesign #3415): 1 つの答え手の値を作って、別々の 2 つの範囲に被せる(値を持って要求ごとに被せる使い手の形)。
  ;; 直す前は値を作った時の client 1 つを 2 つの範囲が使い、1 つ目の範囲の終わりで閉じたので、2 つ目の要求が「client が閉じた」で落ちた。
  ;; 直した後は範囲ごとに client を作って閉じる: 2 つとも答え、作った数 = 閉じた数 = 2。
  (val made (Made))
  (val handler (http-production-handler :client-factory (fn [] (ClosingSpy made))))
  (<- first HttpResponse (with-handler [(await-handler) handler] (ask-once "https://api.test/one")))
  (<- second HttpResponse (with-handler [(await-handler) handler] (ask-once "https://api.test/two")))
  (assert (= #(first.status second.status) #(200 200)) #(first second))
  (assert (= #(made.opened made.closed) #(2 2)) #(made.opened made.closed)))
