;;; foundation/kube_client の KubeClient.follow(Deployment 1 つを list の後の watch で見張る — #3868)と in-background の検。
;;;
;;; 本物の HTTP は使わない: httpx.MockTransport の後ろに模擬の k8s の API(FakeKube)を置く。FakeKube は受けた要求を順に記録し、用意した
;;; 答えを要求ごとに 1 つずつ順に返す(使い切った後は、閉じられるまで何も渡さない開いたままの watch の答え)。watch の答えの本文(Lines)は
;;; 1 行 = 1 つの JSON の出来事を渡す。開いたままの形(hold)では閉じられるまで次を渡さず、閉じられたら残りの出来事を渡してから
;;; httpx.ReadError を上げる(本物の socket を閉じた時に読み手が受ける形)。
;;; follow の答えは別の thread から来るので、受けた物(Received)は錠の下に記録し、数が揃うのを上限つきで待つ。
(require doeff-hy.macros [deftest defk <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import collections.abc [Callable Iterator])
(import json)
(import pathlib [Path])
(import threading)
(import time)
(import httpx)
(import doeff_hy.json_value [OpaqueJson])
(import doeff_cluster.foundation.kube_client [KubeClient])


(val NAMESPACE "prod")
(val NAME "old-beacon")
(val LIST-PATH "/apis/apps/v1/namespaces/prod/deployments")
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
    (with [self.lock]
      (setv index (len self.seen)
            self.seen (+ self.seen #(#((time.monotonic) request)))))
    (if (< index (len self.answers))
        (get self.answers index)
        (httpx.Response 200 :stream (Lines #() :hold True))))

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


(defclass ThenProbe []
  "in-background の then の検め: then が呼ばれた時に、in-background の答え(終わったかを答える関数)が何を答えたかを記録する。答えの関数は
   in-background が返った後に置かれるので、then はそれが置かれるのを待ってから呼ぶ。"
  (defn #^ None __init__ [self]
    (setv self.finished None self.placed (threading.Event) self.called (threading.Event) self.seen None))

  (defn #^ None place [self #^ (get Callable #([] bool)) finished]
    (setv self.finished finished)
    (.set self.placed))

  (defn #^ None then [self]
    (.wait self.placed WAIT-SECONDS)
    (setv self.seen (self.finished))
    (.set self.called)))


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


(deftest test-in-background-calls-then-after-the-finished-mark [tmp-path]
  (val kube (FakeKube #()))
  (<- client KubeClient (kube-client tmp-path kube RETRY-SECONDS))
  (val worked (threading.Event))
  (val probe (ThenProbe))
  (.place probe (.in-background client (fn [] (.set worked)) probe.then))
  (assert (.wait probe.called WAIT-SECONDS) "then が呼ばれない")
  (assert (.is-set worked) "work より前に then が呼ばれた")
  ;; then の中で、終わったかを答える関数がもう真を答える。
  (assert (is probe.seen True) probe.seen))
