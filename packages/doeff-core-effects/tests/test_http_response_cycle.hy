;;; HTTP の答え手が答えた後に、httpx の応答が cyclic GC を待たずに消える事(agora card ki-83d403d6e421)。
;;; httpx の応答は本文の流れと互いを指す(Response.stream = BoundAsyncStream(..., response=self) — httpx 0.28.1 の _client.py)ので、
;;; そのままでは本文の bytes と str が参照の数で放されず、GC の回まで残る。free-threading の CPython は GC の間隔を生きている物の数に応じて
;;; 広げ(手元の実測で 4〜16 万個ごと)、GC で放した memory を機体へ返さないので、60 秒ごとに 96 KB の応答を受ける job
;;; (agora-controllers の node-boot-facts)の常駐量が数日で 380〜540 MiB まで上がった。
;;; 検は socket を使わない(httpx の MockTransport)— GC を止めたまま要求を 1 本出し、答えた後に httpx の応答の weakref が消えている事を見る。
(require doeff-hy.macros [deftest defk deff <- val with-handler])
(import collections.abc [Callable])
(import functools)
(import gc)
(import weakref)
(import httpx)
(import doeff_core_effects.handlers [await-handler])
(import doeff_core_effects.http_effects [HttpRequest HttpResponse])
(import doeff_core_effects.http_handlers [http-production-handler])

;; 答える本文(本番の Node の一覧の大きさに近い 96 KB)。
(val BODY (* b"x" (* 96 1024)))
(val URL "http://api.test/api/v1/nodes")


(deff answer-body [seen request]  ; defk にできない: httpx の MockTransport が呼ぶ callback(seen は functools.partial で束ねる)
  {:pre [(: seen dict) (: request httpx.Request)] :post [(: % httpx.Response)]}
  "検の相手役(MockTransport の答え)— どの要求にも 200 と BODY で答え、最初の応答の weakref を seen に控えるため(httpx は transport が返した
   応答の object に本文の流れを付け直して返すので、控えた物が client の返す応答そのもの)。"
  (setv response (httpx.Response 200 :stream (httpx.ByteStream BODY)))
  (.setdefault seen "first" (weakref.ref response))
  response)


(defk watching-factory [seen]
  {:pre [(: seen dict)] :post [(: % Callable)] :tags {:context "http" :role "foundation"}}
  "検の client の作り手 — socket を使わず、受けた httpx の応答の weakref を seen に控える client を作るため。"
  (fn [] (httpx.AsyncClient :transport (httpx.MockTransport (functools.partial answer-body seen)))))


;; 1 つの run の中で出す要求の数 — scheduler は終わった Await の答え(ここでは httpx の応答)を約束の表に持ち、id を 1024 個払い出すごとの
;; 掃除で、持ち手の死んだ行を消す(scheduler.py の HANDLE_SWEEP_INTERVAL)。その掃除を少なくとも 1 度通る数。run の終わりの後は scheduler の
;; 状態そのものが GC を待つので、最初の応答が消えたかは、掃除を通った後の run の中の値で見る。
(val REQUESTS 1100)


(defk fetch-many [n]
  {:pre [(: n int) (> n 0)] :post [(: % HttpResponse)] :tags {:context "http" :role "program"}}
  "要求を n 本出し、最後の答えを返すため。"
  (var answer None)
  (for [_ (range n)]
    (<- got HttpResponse (HttpRequest "GET" URL :max-retries 0))
    (:= answer got))
  answer)


(deftest test-the-httpx-response-is-freed-without-the-cyclic-gc
  ;; 失敗ケース: 直す前は httpx の応答が本文の流れとの循環で残り、GC を止めている間、scheduler の表の行が掃かれた後も最初の応答の weakref が
  ;; 消えない(本文の 96 KB も残る — 周ごとに大きい応答を受ける job の常駐量が上がり続けた形)。直した後は、答え手が答えた時点で循環が
  ;; 外れ、表の行が掃かれた時に参照の数で消える。答えの本文は欠けない。
  (val seen {})
  (<- factory Callable (watching-factory seen))
  (gc.collect)
  (gc.disable)
  (try
    (<- answer HttpResponse (with-handler [(await-handler) (http-production-handler :client-factory factory)]
                              (fetch-many REQUESTS)))
    (assert (= #(answer.status (len answer.content)) #(200 (len BODY))) answer.status)
    (assert (in "first" seen) seen)
    (assert (is ((get seen "first")) None) "最初の httpx の応答が GC を待って残っている(本文の流れとの循環)")
    (finally
      (gc.enable))))
