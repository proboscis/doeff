(require doeff-hy.macros [defk <-])
(import doeff [do :as _doeff-do])
(import doeff-core-effects [HttpRequest HttpResponse])
(import numbers [Real])


(defk http-get [url * [headers None] [params None] [timeout-seconds 30.0]
                [max-retries 3] [follow-redirects True]]
  {:pre [(: url str)
         (: headers "dict[str, str] or None")
         (: params "dict[str, object] or None")
         (: timeout-seconds Real)
         (: max-retries int)
         (: follow-redirects bool)]
   :post [(: % HttpResponse)]}
  (<- resp (HttpRequest :method "GET" :url url
                         :headers headers
                         :params params
                         :timeout-seconds timeout-seconds
                         :max-retries max-retries
                         :follow-redirects follow-redirects))
  resp)


(defk http-post [url * [headers None] [params None] [body None] [timeout-seconds 30.0]
                 [max-retries 3] [follow-redirects True]]
  {:pre [(: url str)
         (: headers "dict[str, str] or None")
         (: params "dict[str, object] or None")
         (: body "bytes, str, dict, or None")
         (: timeout-seconds Real)
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
  resp)


(defk http-put [url * [headers None] [params None] [body None] [timeout-seconds 30.0]
                [max-retries 3] [follow-redirects True]]
  {:pre [(: url str)
         (: headers "dict[str, str] or None")
         (: params "dict[str, object] or None")
         (: body "bytes, str, dict, or None")
         (: timeout-seconds Real)
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
  resp)


(defk http-delete [url * [headers None] [params None] [timeout-seconds 30.0]
                   [max-retries 3] [follow-redirects True]]
  {:pre [(: url str)
         (: headers "dict[str, str] or None")
         (: params "dict[str, object] or None")
         (: timeout-seconds Real)
         (: max-retries int)
         (: follow-redirects bool)]
   :post [(: % HttpResponse)]}
  (<- resp (HttpRequest :method "DELETE" :url url
                         :headers headers
                         :params params
                         :timeout-seconds timeout-seconds
                         :max-retries max-retries
                         :follow-redirects follow-redirects))
  resp)


(defk http-head [url * [headers None] [params None] [timeout-seconds 30.0]
                 [max-retries 3] [follow-redirects True]]
  {:pre [(: url str)
         (: headers "dict[str, str] or None")
         (: params "dict[str, object] or None")
         (: timeout-seconds Real)
         (: max-retries int)
         (: follow-redirects bool)]
   :post [(: % HttpResponse)]}
  (<- resp (HttpRequest :method "HEAD" :url url
                         :headers headers
                         :params params
                         :timeout-seconds timeout-seconds
                         :max-retries max-retries
                         :follow-redirects follow-redirects))
  resp)
