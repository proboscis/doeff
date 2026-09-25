;; coordinator との HTTP: 接続を使い回すこと・読みは通信の途絶を越えて送り直すこと・宛先の切り替え(2026-09-23 の newmac の件)。
(require doeff-hy.macros [deftest])
(import threading)
(import httpx)
(import pytest)
(import doeff_cluster.coordinator [RequestInbox])
(import doeff_cluster.coordinator_http [CoordinatorEndpoint send-idempotent])
(import doeff_cluster.handlers [CoordinatorLink])
(import doeff_cluster.worker_model [DesiredJobs DesiredUnreadable])


(defn serve [inbox stop]
  (while (not (.is-set stop))
    (for [request (.take inbox 0.1 16)]
      (setv request.slot.status 200 request.slot.body {"path" request.path})
      (.set request.slot.done))))


(deftest test-coordinator-keeps-the-connection-between-requests
  ;; 以前(HTTP/1.0)は返事のたびに接続を閉じ、client は毎回 TCP を張り直していた。
  (setv inbox (RequestInbox 0))
  (.start inbox)
  (setv stop (threading.Event))
  (.start (threading.Thread :target serve :args #(inbox stop) :daemon True))
  (setv port (get inbox.server.server-address 1) opened [])
  (defn trace [name info]
    (when (= name "connection.connect_tcp.complete") (.append opened name)))
  (try
    (setv endpoint (CoordinatorEndpoint f"http://127.0.0.1:{port}" 5.0 0))
    (do
      (for [_ (range 3)]
        (setv response (.request endpoint "GET" "/board" :extensions {"trace" trace}))
        (assert (= response.status-code 200))
        (assert (= response.http-version "HTTP/1.1"))
        (assert (= (.json response) {"path" "/board"}))))
    (finally
      (.set stop)
      (.shutdown inbox.server)))
  (assert (= (len opened) 1)))


(deftest test-idempotent-read-is-resent-across-a-short-outage
  (setv calls [0])
  (defn send []
    (+= (get calls 0) 1)
    (when (< (get calls 0) 3) (raise (httpx.ConnectTimeout "timed out")))
    (httpx.Response 200 :json {}))
  (assert (= (. (send-idempotent send :deadline-seconds 5.0 :pause-seconds 0.01) status-code) 200))
  (assert (= (get calls 0) 3)))


(deftest test-idempotent-read-gives-up-after-the-deadline
  (defn send [] (raise (httpx.ReadTimeout "timed out")))
  (with [(pytest.raises httpx.ReadTimeout)]
    (send-idempotent send :deadline-seconds 0.05 :pause-seconds 0.01)))


;; --- 宛先の切り替え(fake の transport = httpx.MockTransport で、LAN の宛先が届く / 届かないを作る) ---------------------

(setv LAN "http://lan:30881" TS "http://tailnet:30881")

(defclass FakeNet []
  "宛先ごとに「届く / 接続できない / 読みの途中で切れる」を切り替える偽の網。届いた要求の宛先を記録する。"
  (defn __init__ [self]
    (setv self.down #{} self.read-fails #{} self.sent []))
  (defn handle [self request]
    (setv host request.url.host)
    (when (in host self.down) (raise (httpx.ConnectTimeout "timed out" :request request)))
    (when (in host self.read-fails) (raise (httpx.ReadTimeout "timed out" :request request)))
    (.append self.sent #(host request.url.path))
    (httpx.Response 200 :json {"jobs" [] "tasks" [] "host" host}))
  (defn transport [self] (httpx.MockTransport self.handle)))

(defclass FakeClock []
  (defn __init__ [self] (setv self.now 0.0))
  (defn __call__ [self] self.now))

(defn endpoint [net clock [retries 0]]
  (CoordinatorEndpoint f"{LAN},{TS}" 2.0 retries :transport (.transport net) :clock clock :pause (fn [s] None)))


(deftest test-endpoint-prefers-the-first-address
  (setv net (FakeNet) ep (endpoint net (FakeClock)))
  (assert (= (get (.json (.request ep "GET" "/board")) "host") "lan"))
  (assert (= ep.url LAN)))

(deftest test-endpoint-falls-back-when-the-first-address-cannot-connect-and-stays
  (setv net (FakeNet) clock (FakeClock) ep (endpoint net clock))
  (.add net.down "lan")
  (assert (= (get (.json (.request ep "GET" "/board")) "host") "tailnet"))
  (assert (= ep.url TS))
  ;; 回った後は tailnet を先に試す(毎回 LAN の接続の時間切れを払わない)
  (.discard net.down "lan")
  (setv clock.now 30.0)
  (.request ep "GET" "/board")
  (assert (= (get net.sent -1) #("tailnet" "/board")))
  ;; 試し直しの時間が過ぎたら先頭(LAN)を先に試し、届けば戻る
  (setv clock.now 61.0)
  (.request ep "GET" "/board")
  (assert (= (get net.sent -1) #("lan" "/board")))
  (assert (= ep.url LAN)))

(deftest test-endpoint-does-not-switch-on-a-failure-after-connecting
  ;; 接続した後の失敗(読みの時間切れ)は宛先の問題と限らないので、次の宛先へ回らずに投げる
  (setv net (FakeNet) ep (endpoint net (FakeClock)))
  (.add net.read-fails "lan")
  (with [(pytest.raises httpx.ReadTimeout)] (.request ep "GET" "/board"))
  (assert (= ep.url LAN))
  (assert (= net.sent [])))

(deftest test-endpoint-raises-the-connect-failure-when-no-address-is-reachable
  (setv net (FakeNet) ep (endpoint net (FakeClock) :retries 2))
  (.update net.down #{"lan" "tailnet"})
  (with [(pytest.raises httpx.ConnectTimeout)] (.request ep "GET" "/board")))

(deftest test-heartbeat-silence-is-counted-across-an-address-switch
  ;; 自己停止の数え方(最後に届いた時刻)は宛先と無関係。宛先を替えても続き、替えた先で届けば 0 に戻る。
  (import time)
  (setv net (FakeNet) link (CoordinatorLink f"{LAN},{TS}" "w" {} 1 20000 :transport (.transport net)))
  (.update net.down #{"lan" "tailnet"})
  (setv link.last-ok (- (time.monotonic) 5))
  (setv first (.poll link))
  (assert (isinstance first DesiredUnreadable))
  (.discard net.down "tailnet")
  (assert (= (.poll link) (DesiredJobs #())))
  (assert (= link.endpoint.url TS))
  (assert (< (- (time.monotonic) link.last-ok) 1))
  ;; heartbeat は今の宛先を名乗る(coordinator の /state に出る)
  (assert (= (get net.sent -1) #("tailnet" "/heartbeat"))))
