;;; Jev の呼び出しを覚える代理の流れ — 要求 1 つ(ProxyRequest)を答え 1 つ(ProxyReply)にする Program。I/O は effect で出すだけで、
;;; 置き場・本物の Jev・同時の問いのまとめ・身元の名簿は答え手(handlers.hy)が持つ。
;;;
;;; 口(path):
;;;   POST   /v1/systemone        Jev の問い(TypeSafe の System One と同じ本文と答え)。身元の token が要る。見出し Cache-Control の
;;;                               only-if-cached = 覚えている時だけ答える(無ければ 504・Jev を呼ばない)/ no-cache = 問い直して覚え直す。
;;;                               答えの見出し x-jev-proxy = hit / miss / coalesced / refreshed / absent / upstream-error、
;;;                               x-jev-proxy-key = 鍵(誤りとした答えを消す時に使う)
;;;   GET    /healthz             生きているか(身元は要らない)
;;;   GET    /metrics             計器(Prometheus の text・身元は要らない — 数だけで問いの中身を出さない)
;;;   GET    /v1/stats            計器(JSON・身元の token が要る)
;;;   GET    /v1/answers/<鍵>     覚えた答えを見る(管理者だけ)
;;;   DELETE /v1/answers/<鍵>     覚えた答えを消す(管理者だけ)
;;;   POST   /v1/answers/forget   本文(元の問いと同じ本文)の鍵の答えを消す(管理者だけ)
(require doeff-hy.macros [defk <- val var])
(import json)
(import doeff_jev_proxy.values [Directive Event Header ProxyRequest ProxyReply StoredAnswer UpstreamReply UpstreamUnreachable Fetched
                                Coalesced Counters Caller Stranger])
(import doeff_jev_proxy.effects [LookupAnswer RememberAnswer ForgetAnswer ReadAnswer AskJev Coalesce Count ReadCounters IdentifyCaller])
(import doeff_jev_proxy.key [NormalizedRequest BadRequest NotAnAnswer normalize-request header-value directive-of answer-model])

(val ASK-PATH "/v1/systemone")
(val ANSWERS-PREFIX "/v1/answers/")
(val JSON-TYPE "application/json")
(val PROXY-HEADER "x-jev-proxy")
(val KEY-HEADER "x-jev-proxy-key")
;; 本物の Jev を呼ばずに済んだ出来事(節約した呼び出しの数)と、問いの数に入る出来事。
(val SAVED-EVENTS #(Event.HIT Event.COALESCED))
(val ASK-EVENTS #(Event.HIT Event.MISS Event.COALESCED Event.REFRESHED Event.PEEK-HIT Event.PEEK-MISS))


(defk json-reply [status document extra]
  {:pre [(: status int) (: document dict) (: extra tuple)] :post [(: % ProxyReply)]}
  "JSON の答えを組むため。"
  (ProxyReply :status status
              :headers (+ #((Header :name "content-type" :value JSON-TYPE)) extra)
              :body (.encode (json.dumps document :ensure-ascii False :separators #("," ":")) "utf-8")))


(defk jev-reply [status body outcome key]
  {:pre [(: status int) (: body bytes) (: outcome str) (: key str)] :post [(: % ProxyReply)]}
  "Jev の答えの本文をそのまま返す答えを組むため(どこから来たかと鍵を見出しに載せる)。"
  (ProxyReply :status status
              :headers #((Header :name "content-type" :value JSON-TYPE)
                         (Header :name PROXY-HEADER :value outcome)
                         (Header :name KEY-HEADER :value key))
              :body body))


(defk refused [status error reason]
  {:pre [(: status int) (: error str) (: reason str)] :post [(: % ProxyReply)]}
  "断りの答えを組み、断りを数えるため。"
  (<- (Count Event.REFUSED))
  (<- reply (json-reply status {"error" error "reason" reason} #()))
  reply)


(defk fetch-and-remember [normalized body fresh]
  {:pre [(: normalized NormalizedRequest) (: body bytes) (: fresh bool)] :post [(: % Fetched)]}
  "鍵 1 つの答えを本物の Jev から取り、答えとして読めれば覚えるため(同時の問いのまとめの先頭だけが走らせる)。fresh でなければ
   先に覚えを見直す — 先の先頭が答えを置いた直後に来た問いが、もう一度 Jev を呼ばないため。"
  (when (not fresh)
    (<- again (LookupAnswer normalized.key))
    (when (is-not again None)
      (return (Fetched :reply again :remembered True))))
  (<- reply (AskJev body))
  (match reply
    (UpstreamReply :status 200 :body answer)
      (do
        (<- served (answer-model answer))
        (match served
          (NotAnAnswer) None
          _ (<- (RememberAnswer normalized.key normalized.model served normalized.canonical answer)))))
  (Fetched :reply reply :remembered False))


(defk reply-of-fetched [fetched outcome key]
  {:pre [(: fetched Fetched) (: outcome str) (: key str)] :post [(: % ProxyReply)]}
  "取りに行った結果を答えにするため(Jev の失敗は数えて、その status と本文をそのまま返す・届かなければ 502)。"
  (match fetched.reply
    (StoredAnswer :body body) (do (<- r (jev-reply 200 body "hit" key)) r)
    (UpstreamReply :status 200 :body body) (do (<- r (jev-reply 200 body outcome key)) r)
    (UpstreamReply :status status :body body)
      (do
        (<- (Count Event.UPSTREAM-FAILED))
        (<- r (jev-reply status body "upstream-error" key))
        r)
    (UpstreamUnreachable :detail detail)
      (do
        (<- (Count Event.UPSTREAM-FAILED))
        (<- r (json-reply 502 {"error" "upstream-unreachable" "reason" detail}
                          #((Header :name PROXY-HEADER :value "upstream-error") (Header :name KEY-HEADER :value key))))
        r)))


(defk answer-normally [normalized body]
  {:pre [(: normalized NormalizedRequest) (: body bytes)] :post [(: % ProxyReply)]}
  "覚えていれば答え、無ければ同じ鍵の同時の問いを 1 回にまとめて本物の Jev に問うため。"
  (<- stored (LookupAnswer normalized.key))
  (when (is-not stored None)
    (<- (Count Event.HIT))
    (<- hit (jev-reply 200 stored.body "hit" normalized.key))
    (return hit))
  (<- coalesced (Coalesce normalized.key (fetch-and-remember normalized body False)))
  (val event (cond
                coalesced.joined Event.COALESCED
                coalesced.value.remembered Event.HIT
                True Event.MISS))
  (<- (Count event))
  (<- reply (reply-of-fetched coalesced.value (str event) normalized.key))
  reply)


(defk answer-freshly [normalized body]
  {:pre [(: normalized NormalizedRequest) (: body bytes)] :post [(: % ProxyReply)]}
  "覚えを使わずに本物の Jev に問い直し、覚え直すため(同時の問い直しは 1 回にまとめる — 普通の問いのまとめとは別の鍵)。"
  (<- coalesced (Coalesce (+ normalized.key ":fresh") (fetch-and-remember normalized body True)))
  (<- (Count (if coalesced.joined Event.COALESCED Event.REFRESHED)))
  (<- reply (reply-of-fetched coalesced.value (if coalesced.joined "coalesced" "refreshed") normalized.key))
  reply)


(defk answer-if-remembered [normalized]
  {:pre [(: normalized NormalizedRequest)] :post [(: % ProxyReply)]}
  "覚えている時だけ答えるため(無ければ 504・本物の Jev を呼ばない)。"
  (<- stored (LookupAnswer normalized.key))
  (if (is stored None)
      (do
        (<- (Count Event.PEEK-MISS))
        (<- absent (json-reply 504 {"error" "not-cached" "reason" "覚えている答えが無い(only-if-cached なので Jev を呼ばない)"}
                               #((Header :name PROXY-HEADER :value "absent") (Header :name KEY-HEADER :value normalized.key))))
        absent)
      (do
        (<- (Count Event.PEEK-HIT))
        (<- hit (jev-reply 200 stored.body "hit" normalized.key))
        hit)))


(defk identified [request]
  {:pre [(: request ProxyRequest)] :post [(: % (| Caller ProxyReply))]}
  "要求の身元を引くため(引けなければ 401 の答え)。"
  (<- authorization (header-value request.headers "authorization"))
  (<- caller (IdentifyCaller authorization))
  (match caller
    (Stranger :reason reason) (do (<- r (refused 401 "unauthorized" reason)) r)
    _ caller))


(defk ask [request]
  {:pre [(: request ProxyRequest)] :post [(: % ProxyReply)]}
  "Jev の問い 1 つに答えるため。"
  (<- caller (identified request))
  (when (isinstance caller ProxyReply) (return caller))
  (<- normalized (normalize-request request.body))
  (when (isinstance normalized BadRequest)
    (<- bad (refused 400 "bad-request" normalized.reason))
    (return bad))
  (<- directive (directive-of request.headers))
  (<- reply (match directive
              Directive.ONLY-IF-CACHED (answer-if-remembered normalized)
              Directive.NO-CACHE (answer-freshly normalized request.body)
              Directive.NORMAL (answer-normally normalized request.body)))
  reply)


(defk counter-summary [counters]
  {:pre [(: counters Counters)] :post [(: % dict)]}
  "計器の数から、問いの数・節約した呼び出しの数・当たった率を出すため(当たった率 = 本物の Jev を呼ばずに済んだ問い ÷ 覚えを
   使えた問い — 覚えている時だけの問いと問い直しは率に入れない)。"
  (val counts counters.counts)
  (val saved (sum (gfor e SAVED-EVENTS (.get counts (str e) 0))))
  (val misses (.get counts (str Event.MISS) 0))
  {"requests" (sum (gfor e ASK-EVENTS (.get counts (str e) 0)))
   "saved_calls" saved
   "upstream_calls" (+ misses (.get counts (str Event.REFRESHED) 0))
   "hit_ratio" (if (> (+ saved misses) 0) (/ saved (+ saved misses)) 0.0)
   "answers" counters.answers
   "events" (dict (sorted (.items counts)))})


(defk metrics-text [summary]
  {:pre [(: summary dict)] :post [(: % str)]}
  "計器を Prometheus の text にするため。"
  (val lines ["# HELP jev_proxy_requests_total 代理が受けた Jev の問いの数"
              "# TYPE jev_proxy_requests_total counter"
              (.format "jev_proxy_requests_total {}" (get summary "requests"))
              "# HELP jev_proxy_saved_calls_total 本物の Jev を呼ばずに済んだ問いの数(覚えた答え + 同時の問いの相乗り)"
              "# TYPE jev_proxy_saved_calls_total counter"
              (.format "jev_proxy_saved_calls_total {}" (get summary "saved_calls"))
              "# HELP jev_proxy_upstream_calls_total 本物の Jev に問うた数"
              "# TYPE jev_proxy_upstream_calls_total counter"
              (.format "jev_proxy_upstream_calls_total {}" (get summary "upstream_calls"))
              "# HELP jev_proxy_hit_ratio 当たった率(節約 ÷ (節約 + 本物に問うた普通の問い))"
              "# TYPE jev_proxy_hit_ratio gauge"
              (.format "jev_proxy_hit_ratio {:.6f}" (get summary "hit_ratio"))
              "# HELP jev_proxy_answers 覚えている答えの数"
              "# TYPE jev_proxy_answers gauge"
              (.format "jev_proxy_answers {}" (get summary "answers"))
              "# HELP jev_proxy_events_total 出来事ごとの数"
              "# TYPE jev_proxy_events_total counter"])
  (.join "\n" (+ lines
                 (lfor e Event (.format "jev_proxy_events_total{{event=\"{}\"}} {}" (str e) (.get (get summary "events") (str e) 0)))
                 [""])))


(defk admin-only [request]
  {:pre [(: request ProxyRequest)] :post [(: % (| Caller ProxyReply))]}
  "管理者だけに開く口の身元を検めるため(引けなければ 401・管理者でなければ 403)。"
  (<- caller (identified request))
  (cond
    (isinstance caller ProxyReply) caller
    (not caller.admin) (do (<- r (refused 403 "forbidden" (.format "{} は管理者でない" caller.name))) r)
    True caller))


(defk forget-key [request key]
  {:pre [(: request ProxyRequest) (: key str)] :post [(: % ProxyReply)]}
  "管理者が誤りとした答えを鍵で消すため。"
  (<- caller (admin-only request))
  (when (isinstance caller ProxyReply) (return caller))
  (<- removed (ForgetAnswer key))
  (when removed (<- (Count Event.FORGOTTEN)))
  (<- reply (json-reply 200 {"key" key "forgotten" removed "by" caller.name} #()))
  reply)


(defk forget-question [request]
  {:pre [(: request ProxyRequest)] :post [(: % ProxyReply)]}
  "管理者が誤りとした答えを、元の問いと同じ本文から鍵を作って消すため。"
  (<- normalized (normalize-request request.body))
  (when (isinstance normalized BadRequest)
    (<- bad (refused 400 "bad-request" normalized.reason))
    (return bad))
  (<- reply (forget-key request normalized.key))
  reply)


(defk show-answer [request key]
  {:pre [(: request ProxyRequest) (: key str)] :post [(: % ProxyReply)]}
  "管理者が覚えた答えの中身を見るため(版を問わず)。"
  (<- caller (admin-only request))
  (when (isinstance caller ProxyReply) (return caller))
  (<- stored (ReadAnswer key))
  (<- reply (if (is stored None)
                (json-reply 404 {"error" "not-found" "key" key} #())
                (json-reply 200 {"key" stored.key "model" stored.model "served_model" stored.served-model "hits" stored.hits
                                 "answer" (.decode stored.body "utf-8" "replace")} #())))
  reply)


(defk stats [request]
  {:pre [(: request ProxyRequest)] :post [(: % ProxyReply)]}
  "計器を JSON で返すため(身元の token が要る)。"
  (<- caller (identified request))
  (when (isinstance caller ProxyReply) (return caller))
  (<- counters (ReadCounters))
  (<- summary (counter-summary counters))
  (<- reply (json-reply 200 summary #()))
  reply)


(defk metrics [request]
  {:pre [(: request ProxyRequest)] :post [(: % ProxyReply)]}
  "計器を Prometheus の text で返すため。"
  (<- counters (ReadCounters))
  (<- summary (counter-summary counters))
  (<- text (metrics-text summary))
  (ProxyReply :status 200 :headers #((Header :name "content-type" :value "text/plain; version=0.0.4; charset=utf-8"))
              :body (.encode text "utf-8")))


(defk respond [request]
  {:pre [(: request ProxyRequest)] :post [(: % ProxyReply)]}
  "要求 1 つを口(method と path)で振り分けて答えるため。"
  (<- reply (match #(request.method request.path)
              #("POST" "/v1/systemone") (ask request)
              #("GET" "/healthz") (json-reply 200 {"ok" True} #())
              #("GET" "/metrics") (metrics request)
              #("GET" "/v1/stats") (stats request)
              #("POST" "/v1/answers/forget") (forget-question request)
              #("DELETE" path) :if (.startswith path ANSWERS-PREFIX) (forget-key request (cut path (len ANSWERS-PREFIX) None))
              #("GET" path) :if (.startswith path ANSWERS-PREFIX) (show-answer request (cut path (len ANSWERS-PREFIX) None))
              _ (json-reply 404 {"error" "not-found" "reason" (.format "{} {} の口は無い" request.method request.path)} #())))
  reply)
