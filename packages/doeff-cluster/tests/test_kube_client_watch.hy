;;; foundation/kube_client の KubeClient.follow(Deployment 1 つを list の後の watch で見張る — #3868)と follow-node(Node 1 つを同じ形で
;;; 見張る — #4070)の検。
;;;
;;; 本物の HTTP は使わない: httpx.MockTransport の後ろに模擬の k8s の API(FakeKube)を置く。FakeKube は受けた要求を順に記録し、用意した
;;; 答えを要求ごとに 1 つずつ順に返す(使い切った後は、閉じられるまで何も渡さない開いたままの watch の答え — 用意した答えが例外なら
;;; それを上げる)。watch の答えの本文(Lines)は 1 行 = 1 つの JSON の出来事を渡す。開いたままの形(hold)では閉じられるまで次を渡さず、
;;; 閉じられたら残りの出来事を渡してから httpx.ReadError を上げる(本物の socket を閉じた時に読み手が受ける形)。
;;; 例外は接続の pool の検 1 本だけ: 本物の transport(接続の pool)を通すため、手元の HTTP の server(LocalKube — 127.0.0.1)に繋ぐ。
;;; follow の答えは別の thread から来るので、受けた物(Received)は錠の下に記録し、数が揃うのを上限つきで待つ。
(require doeff-hy.macros [deftest defk deff <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import collections.abc [Iterator])
(import http.server [BaseHTTPRequestHandler ThreadingHTTPServer])
(import json)
(import pathlib [Path])
(import threading)
(import time)
(import urllib.parse [parse-qs urlsplit])
(import certifi)
(import httpx)
(import doeff_hy.json_value [OpaqueJson])
(import doeff_cluster.foundation.kube_client [KubeClient])


(val NAMESPACE "prod")
(val NAME "old-beacon")
(val LIST-PATH "/apis/apps/v1/namespaces/prod/deployments")
(val NODE "n1")
(val NODES-PATH "/api/v1/nodes")
;; watch の要求の timeoutSeconds(既定と違う値 — 要求に載るかを見る)。
(val WATCH-SECONDS 120)
;; 1 つの検の中で thread の答えを待つ上限の秒(検 1 つは 30 秒以内)。
(val WAIT-SECONDS 10.0)
;; 開いたままの watch が閉じられずに待つ上限の秒(WAIT-SECONDS より長い — 閉じが届かなければ、thread の終わりの待ちが上限で切れて赤になる)。
(val HOLD-SECONDS 20.0)
;; 届かない・断られた後に list し直すまでの秒(検の短い値)と、その待ちがあったと見なす最小の間隔。
(val RETRY-SECONDS 0.2)
(val RETRY-AT-LEAST 0.15)

(val DEP-A {"metadata" {"name" NAME "namespace" NAMESPACE "resourceVersion" "11" "generation" 1} "spec" {"replicas" 1}})
(val DEP-B {"metadata" {"name" NAME "namespace" NAMESPACE "resourceVersion" "12" "generation" 2} "spec" {"replicas" 2}})
(val DEP-C {"metadata" {"name" NAME "namespace" NAMESPACE "resourceVersion" "13" "generation" 3} "spec" {"replicas" 3}})
(val NODE-A {"metadata" {"name" NODE "resourceVersion" "31" "labels" {"doeff.dev/company-machine" "true"}}})
(val NODE-B {"metadata" {"name" NODE "resourceVersion" "32" "labels" {}}})


(defclass Lines [httpx.SyncByteStream]
  "watch の答えの本文。events を 1 行ずつ渡す。hold でなければ渡し終えて終わる(server が timeoutSeconds で閉じた形)。hold なら、その後
   閉じられるまで(長くて HOLD-SECONDS)渡さず、閉じられたら after-close の出来事を渡してから httpx.ReadError を上げる。"
  (defn #^ None __init__ [self #^ tuple events #^ bool [hold False] #^ tuple [after-close #()]]
    (setv self.events events self.hold hold self.after-close after-close self.closed (threading.Event)))

  (defn [staticmethod] #^ bytes encoded [#^ dict event]
    (.encode (+ (json.dumps event) "\n") "utf-8"))

  (defn #^ (get Iterator bytes) __iter__ [self]
    (for [event self.events]
      (yield (Lines.encoded event)))
    (when (and self.hold (.wait self.closed HOLD-SECONDS))
      (for [event self.after-close]
        (yield (Lines.encoded event)))
      (raise (httpx.ReadError "閉じた stream を読んだ"))))

  (defn #^ None close [self]
    (.set self.closed)))


(defclass FakeKube []
  "模擬の k8s の API: 受けた要求を順に記録し(seen — 受けた刻と要求の組)、用意した答え(answers)を要求ごとに 1 つずつ順に返す。
   使い切った後の要求には、閉じられるまで何も渡さない開いたままの watch の答えを返す。"
  (defn #^ None __init__ [self #^ tuple answers]
    (setv self.answers answers self.seen #() self.lock (threading.Lock)))

  (defn #^ httpx.Response handle [self #^ httpx.Request request]
    "要求を記録し、用意した次の答えを返す(用意した答えが例外なら上げる — transport の中で思わぬ例外が上がった形)。"
    (with [self.lock]
      (setv index (len self.seen)
            self.seen (+ self.seen #(#((time.monotonic) request)))))
    (setv answer (if (< index (len self.answers))
                     (get self.answers index)
                     (httpx.Response 200 :stream (Lines #() :hold True))))
    (when (isinstance answer Exception)
      (raise answer))
    answer)

  (defn #^ tuple requests [self]
    "受けた要求の列(path と query の dict の組)。"
    (with [self.lock]
      (tuple (gfor #(_ request) self.seen #(request.url.path (dict request.url.params))))))

  (defn #^ tuple lists [self]
    "受けた要求のうち list(watch の印の無い物)の、受けた刻の列。"
    (with [self.lock]
      (tuple (gfor #(at request) self.seen :if (not-in "watch" request.url.params) at)))))


(defclass Received []
  "on-body と on-error が受けた物を順に記録する(別の thread から呼ばれる — 錠の下)。数が揃うのを上限つきで待てる。"
  (defn #^ None __init__ [self]
    (setv self.changed (threading.Condition) self.bodies #() self.errors #()))

  (defn #^ None on-body [self #^ OpaqueJson body]
    (with [self.changed]
      (setv self.bodies (+ self.bodies #(body)))
      (.notify-all self.changed)))

  (defn #^ None on-error [self #^ str reason]
    (with [self.changed]
      (setv self.errors (+ self.errors #(reason)))
      (.notify-all self.changed)))

  (defn #^ bool wait-for [self #^ int bodies #^ int errors]
    "on-body が bodies 回以上・on-error が errors 回以上来るまで待つ(上限 WAIT-SECONDS)。揃ったら真。"
    (with [self.changed]
      (.wait-for self.changed (fn [] (and (>= (len self.bodies) bodies) (>= (len self.errors) errors))) WAIT-SECONDS))))


(defclass LocalKubeHandler [BaseHTTPRequestHandler]
  "LocalKube の要求 1 つの答え手(http.server が接続ごとの thread で呼ぶ)。答えは 1 回の書きで送る(頭と本文を分けて書くと、相手の
   遅れた ACK を待って 1 往復 40 ms 遅れる)。"
  (setv protocol-version "HTTP/1.1")

  (deff log-message [self format #* args]  ; defk にできない: http.server が呼ぶ素の callback
    {:pre [(: self BaseHTTPRequestHandler) (: format str) (: args tuple)] :post [(: % (type None))] :tags {:context "doeff-cluster-test" :role "foundation"}}
    None)

  (deff do-GET [self]  ; defk にできない: http.server が呼ぶ素の callback
    {:pre [(: self BaseHTTPRequestHandler)] :post [(: % (type None))] :tags {:context "doeff-cluster-test" :role "foundation"}}
    (let [parts (urlsplit self.path)
          params (parse-qs parts.query)]
      (.note self.server.kube (get self.client-address 1) "GET" parts.path params)
      (if (in "watch" params)
          ;; 開いたままの watch: 頭だけ送り、release まで(長くて HOLD-SECONDS)何も渡さず、空の chunk で閉じる。
          (try
            (.write self.wfile b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n")
            (.wait self.server.kube.release HOLD-SECONDS)
            (.write self.wfile b"0\r\n\r\n")
            (except [OSError]
              None))
          (.write self.wfile (LocalKube.answered {"kind" "List" "metadata" {"resourceVersion" "10"}
                                                  "items" [(if (.endswith parts.path "/nodes") NODE-A DEP-A)]})))
      None))

  (deff do-PATCH [self]  ; defk にできない: http.server が呼ぶ素の callback
    {:pre [(: self BaseHTTPRequestHandler)] :post [(: % (type None))] :tags {:context "doeff-cluster-test" :role "foundation"}}
    (.read self.rfile (int (.get self.headers "Content-Length" "0")))
    (.note self.server.kube (get self.client-address 1) "PATCH" (. (urlsplit self.path) path) {})
    (.write self.wfile (LocalKube.answered {"spec" {"replicas" 1}}))
    None))


(defclass LocalKube []
  "手元の HTTP の server(127.0.0.1)で答える模擬の k8s の API — 本物の transport(接続の pool)を通す検だけが使う。受けた要求ごとに
   相手の TCP の port(= どの接続か)・method・path・query を記録する(seen)。list は 1 件の一覧、watch は開いたままの答え、scale の
   PATCH は台数を返す。release = 開いたままの watch を閉じる合図(close が立てる)。"
  (defn #^ None __init__ [self]
    (setv self.seen #() self.lock (threading.Lock) self.release (threading.Event)
          self.server (ThreadingHTTPServer #("127.0.0.1" 0) LocalKubeHandler))
    (setv self.server.daemon-threads True self.server.kube self)
    (.start (threading.Thread :target self.server.serve-forever :daemon True))
    None)

  (defn [staticmethod] #^ bytes answered [#^ dict body]
    "200 の答えの頭と JSON の本文を 1 つの bytes にする。"
    (setv text (.encode (json.dumps body) "utf-8"))
    (+ (.encode (.format "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n" (len text)) "ascii")
       text))

  (defn #^ str url [self]
    (.format "http://127.0.0.1:{}" (get self.server.server-address 1)))

  (defn #^ None note [self #^ int port #^ str method #^ str path #^ dict params]
    (with [self.lock]
      (setv self.seen (+ self.seen #(#(port method path params)))))
    None)

  (defn #^ tuple ports [self #^ str method]
    "method の要求を受けた接続の port の列(受けた順)。"
    (with [self.lock]
      (tuple (gfor #(port seen-method _ _) self.seen :if (= seen-method method) port))))

  (defn #^ None close [self]
    (.set self.release)
    (.shutdown self.server)
    (.server-close self.server)
    None))


(defk kube-client [sa-dir kube retry-seconds]
  {:pre [(: sa-dir Path) (: kube FakeKube) (: retry-seconds float)] :post [(: % KubeClient)] :tags {:context "doeff-cluster-test" :role "program"}}
  "模擬の k8s の API(kube)へ繋いだ client を作るため。ServiceAccount の dir は検の一時の dir で、token だけを置く(transport を渡すので
   httpx は ca.crt を読まない)。"
  (.write-text (/ sa-dir "token") "test-token" :encoding "utf-8")
  (KubeClient RuntimeError :sa-dir (str sa-dir) :transport (httpx.MockTransport kube.handle)
              :retry-seconds retry-seconds :watch-seconds WATCH-SECONDS))


(defk listed [version items]
  {:pre [(: version str) (: items tuple)] :post [(: % httpx.Response)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "list の答え(一覧の resourceVersion と items)。"
  (httpx.Response 200 :json {"kind" "DeploymentList" "metadata" {"resourceVersion" version} "items" (list items)}))


(defk watched [events hold]
  {:pre [(: events tuple) (: hold bool)] :post [(: % httpx.Response)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "watch の答え(出来事の列を 1 行ずつ渡す本文)。"
  (httpx.Response 200 :stream (Lines events :hold hold)))


(deftest test-the-listed-item-and-then-a-modified-event-reach-on-body [tmp-path]
  (<- first-list httpx.Response (listed "10" #(DEP-A)))
  (<- first-watch httpx.Response (watched #({"type" "MODIFIED" "object" DEP-B}) True))
  (val kube (FakeKube #(first-list first-watch)))
  (<- client KubeClient (kube-client tmp-path kube RETRY-SECONDS))
  (val received (Received))
  (val stop (.follow client NAMESPACE NAME received.on-body received.on-error))
  (try
    (assert (.wait-for received 2 0) #(received.bodies received.errors))
    (assert (= received.bodies #((OpaqueJson.of DEP-A) (OpaqueJson.of DEP-B))) received.bodies)
    (assert (= received.errors #()) received.errors)
    ;; list は名で絞った一覧、watch はその一覧の版から続きを受ける。
    (val requests (.requests kube))
    (assert (= (get requests 0) #(LIST-PATH {"fieldSelector" "metadata.name=old-beacon"})) requests)
    (assert (= (get requests 1) #(LIST-PATH {"fieldSelector" "metadata.name=old-beacon" "watch" "true" "resourceVersion" "10"
                                             "allowWatchBookmarks" "true" "timeoutSeconds" (str WATCH-SECONDS)}))
            requests)
    (finally
      (stop))))


(deftest test-a-deleted-event-reaches-on-error [tmp-path]
  (<- first-list httpx.Response (listed "10" #(DEP-A)))
  (<- first-watch httpx.Response (watched #({"type" "DELETED" "object" DEP-B}) True))
  (val kube (FakeKube #(first-list first-watch)))
  (<- client KubeClient (kube-client tmp-path kube RETRY-SECONDS))
  (val received (Received))
  (val stop (.follow client NAMESPACE NAME received.on-body received.on-error))
  (try
    (assert (.wait-for received 1 1) #(received.bodies received.errors))
    (assert (= received.bodies #((OpaqueJson.of DEP-A))) received.bodies)
    (assert (= (len received.errors) 1) received.errors)
    (assert (in "消された" (get received.errors 0)) received.errors)
    (assert (in "prod/old-beacon" (get received.errors 0)) received.errors)
    (finally
      (stop))))


(deftest test-a-missing-deployment-reaches-on-error-and-the-watch-still-follows [tmp-path]
  ;; 一覧に無い Deployment は on-error に名指しで来る。watch はその一覧の版から続き、後で作られた(ADDED)Deployment は on-body に来る。
  (<- first-list httpx.Response (listed "10" #()))
  (<- first-watch httpx.Response (watched #({"type" "ADDED" "object" DEP-A}) True))
  (val kube (FakeKube #(first-list first-watch)))
  (<- client KubeClient (kube-client tmp-path kube RETRY-SECONDS))
  (val received (Received))
  (val stop (.follow client NAMESPACE NAME received.on-body received.on-error))
  (try
    (assert (.wait-for received 1 1) #(received.bodies received.errors))
    (assert (= (len received.errors) 1) received.errors)
    (assert (in "無い Deployment: prod/old-beacon" (get received.errors 0)) received.errors)
    (assert (= received.bodies #((OpaqueJson.of DEP-A))) received.bodies)
    (finally
      (stop))))


(deftest test-a-gone-error-lists-again-without-on-error [tmp-path]
  ;; 覚えた版が古すぎる(ERROR の 410)時は on-error を呼ばずにすぐ list し直し、新しい一覧の版から watch し直す。
  (<- first-list httpx.Response (listed "10" #(DEP-A)))
  (<- first-watch httpx.Response
      (watched #({"type" "ERROR" "object" {"kind" "Status" "code" 410 "reason" "Expired" "message" "too old resource version: 10 (15)"}})
               False))
  (<- second-list httpx.Response (listed "20" #(DEP-B)))
  (val kube (FakeKube #(first-list first-watch second-list)))
  (<- client KubeClient (kube-client tmp-path kube RETRY-SECONDS))
  (val received (Received))
  (val stop (.follow client NAMESPACE NAME received.on-body received.on-error))
  (try
    (assert (.wait-for received 2 0) #(received.bodies received.errors))
    (assert (= received.bodies #((OpaqueJson.of DEP-A) (OpaqueJson.of DEP-B))) received.bodies)
    (assert (= received.errors #()) received.errors)
    (val lists (.lists kube))
    (assert (= (len lists) 2) (.requests kube))
    ;; 410 の後の list は待たずに出る。
    (assert (< (- (get lists 1) (get lists 0)) RETRY-AT-LEAST) lists)
    (finally
      (stop))))


(deftest test-another-watch-error-reaches-on-error-and-lists-again-after-the-retry-seconds [tmp-path]
  (<- first-list httpx.Response (listed "10" #(DEP-A)))
  (<- first-watch httpx.Response
      (watched #({"type" "ERROR" "object" {"kind" "Status" "code" 500 "message" "etcd が答えない"}}) True))
  (<- second-list httpx.Response (listed "20" #(DEP-B)))
  (val kube (FakeKube #(first-list first-watch second-list)))
  (<- client KubeClient (kube-client tmp-path kube RETRY-SECONDS))
  (val received (Received))
  (val stop (.follow client NAMESPACE NAME received.on-body received.on-error))
  (try
    (assert (.wait-for received 2 1) #(received.bodies received.errors))
    (assert (= received.errors #("etcd が答えない")) received.errors)
    (val lists (.lists kube))
    (assert (= (len lists) 2) (.requests kube))
    (assert (>= (- (get lists 1) (get lists 0)) RETRY-AT-LEAST) lists)
    (finally
      (stop))))


(deftest test-a-forbidden-answer-reaches-on-error-and-lists-again-after-the-retry-seconds [tmp-path]
  (val forbidden (httpx.Response 403 :json {"kind" "Status" "code" 403 "message" "deployments.apps is forbidden"}))
  (<- second-list httpx.Response (listed "10" #(DEP-A)))
  (val kube (FakeKube #(forbidden second-list)))
  (<- client KubeClient (kube-client tmp-path kube RETRY-SECONDS))
  (val received (Received))
  (val stop (.follow client NAMESPACE NAME received.on-body received.on-error))
  (try
    (assert (.wait-for received 1 1) #(received.bodies received.errors))
    (assert (= (len received.errors) 1) received.errors)
    (assert (in "403" (get received.errors 0)) received.errors)
    (assert (in "forbidden" (get received.errors 0)) received.errors)
    (assert (= received.bodies #((OpaqueJson.of DEP-A))) received.bodies)
    (val lists (.lists kube))
    (assert (= (len lists) 2) (.requests kube))
    (assert (>= (- (get lists 1) (get lists 0)) RETRY-AT-LEAST) lists)
    (finally
      (stop))))


(deftest test-a-watch-that-ends-normally-watches-again-from-the-remembered-version [tmp-path]
  ;; server が timeoutSeconds で閉じた watch は、list し直さず、最後に覚えた版(BOOKMARK の版を含む)から watch し直す。
  (<- first-list httpx.Response (listed "10" #(DEP-A)))
  (<- first-watch httpx.Response
      (watched #({"type" "MODIFIED" "object" DEP-B}
                 {"type" "BOOKMARK" "object" {"kind" "Deployment" "metadata" {"resourceVersion" "15"}}})
               False))
  (<- second-watch httpx.Response (watched #({"type" "MODIFIED" "object" DEP-C}) True))
  (val kube (FakeKube #(first-list first-watch second-watch)))
  (<- client KubeClient (kube-client tmp-path kube RETRY-SECONDS))
  (val received (Received))
  (val stop (.follow client NAMESPACE NAME received.on-body received.on-error))
  (try
    (assert (.wait-for received 3 0) #(received.bodies received.errors))
    (assert (= received.bodies #((OpaqueJson.of DEP-A) (OpaqueJson.of DEP-B) (OpaqueJson.of DEP-C))) received.bodies)
    (assert (= received.errors #()) received.errors)
    (val requests (.requests kube))
    (assert (= (len (.lists kube)) 1) requests)
    (assert (= (.get (get (get requests 2) 1) "resourceVersion") "15") requests)
    (finally
      (stop))))


(deftest test-nothing-reaches-the-callbacks-after-stop [tmp-path]
  ;; 止めた後に stream が出来事を渡しても、閉じた stream の読みが誤りを上げても、on-body / on-error は呼ばれない。止める関数は今の
  ;; stream を閉じ、読みの途中の thread を抜けさせる(閉じが届かなければ stream は HOLD-SECONDS 渡さず、thread の終わりの待ちが切れる)。
  (<- first-list httpx.Response (listed "10" #(DEP-A)))
  (val first-watch (httpx.Response 200 :stream (Lines #({"type" "MODIFIED" "object" DEP-B}) :hold True
                                                      :after-close #({"type" "MODIFIED" "object" DEP-C}
                                                                     {"type" "DELETED" "object" DEP-C}))))
  (val kube (FakeKube #(first-list first-watch)))
  (<- client KubeClient (kube-client tmp-path kube RETRY-SECONDS))
  (val received (Received))
  (val before (frozenset (threading.enumerate)))
  (val stop (.follow client NAMESPACE NAME received.on-body received.on-error))
  (val started (tuple (gfor t (threading.enumerate) :if (and (= t.name "kube-watch") (not-in t before)) t)))
  (assert (= (len started) 1) started)
  (assert (.wait-for received 2 0) #(received.bodies received.errors))
  (stop)
  (.join (get started 0) WAIT-SECONDS)
  (assert (not (.is-alive (get started 0))) "止めた後も watch の thread が終わらない")
  (assert (= received.bodies #((OpaqueJson.of DEP-A) (OpaqueJson.of DEP-B))) received.bodies)
  (assert (= received.errors #()) received.errors))


(deftest test-a-node-is-listed-by-name-and-a-label-change-reaches-on-body [tmp-path]
  ;; Node は名前空間を持たない一覧(/api/v1/nodes)を名で絞って list し、その一覧の版から watch する。label の変化(MODIFIED)は on-body に
  ;; 来る(#4070)。
  (<- first-list httpx.Response (listed "30" #(NODE-A)))
  (<- first-watch httpx.Response (watched #({"type" "MODIFIED" "object" NODE-B}) True))
  (val kube (FakeKube #(first-list first-watch)))
  (<- client KubeClient (kube-client tmp-path kube RETRY-SECONDS))
  (val received (Received))
  (val stop (.follow-node client NODE received.on-body received.on-error))
  (try
    (assert (.wait-for received 2 0) #(received.bodies received.errors))
    (assert (= received.bodies #((OpaqueJson.of NODE-A) (OpaqueJson.of NODE-B))) received.bodies)
    (assert (= received.errors #()) received.errors)
    (val requests (.requests kube))
    (assert (= (get requests 0) #(NODES-PATH {"fieldSelector" "metadata.name=n1"})) requests)
    (assert (= (get requests 1) #(NODES-PATH {"fieldSelector" "metadata.name=n1" "watch" "true" "resourceVersion" "30"
                                              "allowWatchBookmarks" "true" "timeoutSeconds" (str WATCH-SECONDS)}))
            requests)
    (finally
      (stop))))


(deftest test-a-missing-node-reaches-on-error-by-name [tmp-path]
  ;; 一覧に無い Node は on-error に「無い Node」の理由で来る。watch はその一覧の版から続き、後で加わった(ADDED)Node は on-body に来る。
  (<- first-list httpx.Response (listed "30" #()))
  (<- first-watch httpx.Response (watched #({"type" "ADDED" "object" NODE-A}) True))
  (val kube (FakeKube #(first-list first-watch)))
  (<- client KubeClient (kube-client tmp-path kube RETRY-SECONDS))
  (val received (Received))
  (val stop (.follow-node client NODE received.on-body received.on-error))
  (try
    (assert (.wait-for received 1 1) #(received.bodies received.errors))
    (assert (= received.errors #("無い Node: n1")) received.errors)
    (assert (= received.bodies #((OpaqueJson.of NODE-A))) received.bodies)
    (finally
      (stop))))


(deftest test-a-deleted-node-reaches-on-error-by-name [tmp-path]
  (<- first-list httpx.Response (listed "30" #(NODE-A)))
  (<- first-watch httpx.Response (watched #({"type" "DELETED" "object" NODE-B}) True))
  (val kube (FakeKube #(first-list first-watch)))
  (<- client KubeClient (kube-client tmp-path kube RETRY-SECONDS))
  (val received (Received))
  (val stop (.follow-node client NODE received.on-body received.on-error))
  (try
    (assert (.wait-for received 1 1) #(received.bodies received.errors))
    (assert (= received.errors #("Node が消された: n1")) received.errors)
    (finally
      (stop))))


(deftest test-the-live-transport-finds-a-silent-peer-by-tcp-keepalive [tmp-path]
  ;; transport を渡さない(本番の)client は、TCP の keepalive を付けた transport で繋ぐ — 繋がったまま黙った相手(FIN も RST も来ない)の
  ;; watch の stream を、読みの打ち切り(watch-seconds + 余白)より早く切れた接続として見つけ、見張りが理由を伝える(#3868 のレビュー)。
  (.write-text (/ tmp-path "token") "test-token" :encoding "utf-8")
  (.write-bytes (/ tmp-path "ca.crt") (.read-bytes (Path (certifi.where))))
  (val client (KubeClient RuntimeError :sa-dir (str tmp-path)))
  ;; httpx は transport の socket の設定を公開の API で見せないので、接続の pool の欄を読む。
  (assert (= client.client._transport._pool._socket_options (list (KubeClient.keepalive-options)))
          client.client._transport._pool._socket_options))


(deftest test-an-unexpected-error-in-the-watch-thread-reaches-on-error-and-lists-again [tmp-path]
  ;; 失敗ケース(本番 2026-10-09 12:40:48 JST — coordinator の kube-watch の thread が httpcore の has_expired の TypeError で抜け、以後
  ;; 見張りが何も伝えず、観測の表は最後の値のまま残った): 見張りの thread の中で思わぬ例外(httpx の誤りでも JSON の誤りでもない)が
  ;; 上がっても、thread は抜けない。理由(例外の型の名を含む)を on-error に伝え、retry-seconds の後に list し直し、その後の今を on-body に
  ;; 伝える。
  (<- first-list httpx.Response (listed "10" #(DEP-A)))
  (val broken (TypeError "'>' not supported between instances of 'float' and 'NoneType'"))
  (<- second-list httpx.Response (listed "20" #(DEP-B)))
  (val kube (FakeKube #(first-list broken second-list)))
  (<- client KubeClient (kube-client tmp-path kube RETRY-SECONDS))
  (val received (Received))
  (val stop (.follow client NAMESPACE NAME received.on-body received.on-error))
  (try
    (assert (.wait-for received 2 1) #(received.bodies received.errors))
    (assert (= received.bodies #((OpaqueJson.of DEP-A) (OpaqueJson.of DEP-B))) received.bodies)
    (assert (= (len received.errors) 1) received.errors)
    (assert (in "TypeError" (get received.errors 0)) received.errors)
    (val lists (.lists kube))
    (assert (= (len lists) 2) (.requests kube))
    (assert (>= (- (get lists 1) (get lists 0)) RETRY-AT-LEAST) lists)
    (finally
      (stop))))


(deftest test-each-watch-uses-its-own-connections-apart-from-the-calls [tmp-path]
  ;; 失敗ケース(本番 2026-10-09 12:40:48 JST の TypeError の元): 見張りの thread 2 本(Deployment・Node)と本体の thread(台数の変更)が
  ;; 1 つの接続の pool を使うと、free-threaded の Python では pool の錠の外で同じ接続の期限を読む thread(httpcore の has_expired)と
  ;; 書く thread(handle_request)が重なり、float と None を比べる TypeError になる(手元の 3.14t で 1 つの client を 6 thread で 30 秒
  ;; 使うと 142409 回のうち 314 回)。見張りは自分の接続を持ち、台数の変更が使った TCP 接続(相手の port)を見張りの要求が使う数は 0。
  ;; 1 つの pool なら、台数の変更の後に空いた接続を次の見張りの list がそのまま使う。
  (.write-text (/ tmp-path "token") "test-token" :encoding "utf-8")
  (.write-bytes (/ tmp-path "ca.crt") (.read-bytes (Path (certifi.where))))
  (val kube (LocalKube))
  (val client (KubeClient RuntimeError :base (.url kube) :sa-dir (str tmp-path) :retry-seconds RETRY-SECONDS
                          :watch-seconds WATCH-SECONDS))
  (val deployment (Received))
  (val node (Received))
  (var stops #())
  (try
    (.scale client NAMESPACE NAME 1 False)
    (:= stops (+ stops #((.follow client NAMESPACE NAME deployment.on-body deployment.on-error))))
    (assert (.wait-for deployment 1 0) #(deployment.bodies deployment.errors))
    (.scale client NAMESPACE NAME 1 False)
    (:= stops (+ stops #((.follow-node client NODE node.on-body node.on-error))))
    (assert (.wait-for node 1 0) #(node.bodies node.errors))
    (val scaled (.ports kube "PATCH"))
    (val watched (.ports kube "GET"))
    (assert (= (len scaled) 2) kube.seen)
    (assert (= (len (& (set scaled) (set watched))) 0) kube.seen)
    (finally
      (for [stop stops]
        (stop))
      (.close kube))))
