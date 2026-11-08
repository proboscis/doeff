(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "http" :role "program"})
(import doeff [do :as _doeff-do])
(import collections.abc [Mapping])
;; From the defining module: the package root declares the HTTP effects as `object` to type checkers (they live in Hy).
(import doeff-core-effects._http-effects [HttpFailed HttpRequest HttpResponse])


(defn #^ HttpResponse _response-of [#^ (| HttpResponse HttpFailed) answer]
  "Narrow the answer of a request sent without failures-as-values to HttpResponse (the handler raises instead of answering HttpFailed then)."
  (when (isinstance answer HttpFailed)
    (raise (TypeError (+ "HttpRequest without failures-as-values answered HttpFailed: " (repr answer)))))
  answer)


(defk http-get [url * [headers None] [params None] [timeout-seconds 30.0]
                [max-retries 3] [follow-redirects True]]
  {:pre [(: url str)
         (: headers (| (get dict #(str str)) None))
         (: params (| (get Mapping #(str object)) None))
         (: timeout-seconds (| int float))
         (: max-retries int)
         (: follow-redirects bool)]
   :post [(: % HttpResponse)]}
  (<- resp (HttpRequest :method "GET" :url url
                         :headers headers
                         :params params
                         :timeout-seconds timeout-seconds
                         :max-retries max-retries
                         :follow-redirects follow-redirects))
  (_response-of resp))


(defk http-post [url * [headers None] [params None] [body None] [timeout-seconds 30.0]
                 [max-retries 3] [follow-redirects True]]
  {:pre [(: url str)
         (: headers (| (get dict #(str str)) None))
         (: params (| (get Mapping #(str object)) None))
         (: body (| bytes str (get Mapping #(str object)) None))
         (: timeout-seconds (| int float))
         (: max-retries int)
         (: follow-redirects bool)]
   :post [(: % HttpResponse)]}
  (<- resp (HttpRequest :method "POST" :url url
                         :headers headers
                         :params params
                         :body body
                         :timeout-seconds timeout-seconds
                         :max-retries max-retries
                         :follow-redirects follow-redirects))
  (_response-of resp))


(defk http-put [url * [headers None] [params None] [body None] [timeout-seconds 30.0]
                [max-retries 3] [follow-redirects True]]
  {:pre [(: url str)
         (: headers (| (get dict #(str str)) None))
         (: params (| (get Mapping #(str object)) None))
         (: body (| bytes str (get Mapping #(str object)) None))
         (: timeout-seconds (| int float))
         (: max-retries int)
         (: follow-redirects bool)]
   :post [(: % HttpResponse)]}
  (<- resp (HttpRequest :method "PUT" :url url
                         :headers headers
                         :params params
                         :body body
                         :timeout-seconds timeout-seconds
                         :max-retries max-retries
                         :follow-redirects follow-redirects))
  (_response-of resp))


(defk http-delete [url * [headers None] [params None] [timeout-seconds 30.0]
                   [max-retries 3] [follow-redirects True]]
  {:pre [(: url str)
         (: headers (| (get dict #(str str)) None))
         (: params (| (get Mapping #(str object)) None))
         (: timeout-seconds (| int float))
         (: max-retries int)
         (: follow-redirects bool)]
   :post [(: % HttpResponse)]}
  (<- resp (HttpRequest :method "DELETE" :url url
                         :headers headers
                         :params params
                         :timeout-seconds timeout-seconds
                         :max-retries max-retries
                         :follow-redirects follow-redirects))
  (_response-of resp))


(defk http-head [url * [headers None] [params None] [timeout-seconds 30.0]
                 [max-retries 3] [follow-redirects True]]
  {:pre [(: url str)
         (: headers (| (get dict #(str str)) None))
         (: params (| (get Mapping #(str object)) None))
         (: timeout-seconds (| int float))
         (: max-retries int)
         (: follow-redirects bool)]
   :post [(: % HttpResponse)]}
  (<- resp (HttpRequest :method "HEAD" :url url
                         :headers headers
                         :params params
                         :timeout-seconds timeout-seconds
                         :max-retries max-retries
                         :follow-redirects follow-redirects))
  (_response-of resp))
