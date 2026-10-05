;; coordinator との HTTP: coordinator が接続を使い回させること・heartbeat の途絶の数えが宛先の切り替えをまたぐこと(2026-09-23 の newmac の件)。
;; 宛先の切り替えと読みの送り直しの性質は宛先の部品の検(test_coordinator_route — #2427 で httpx の client を持つ口を退役させた)。
(require doeff-hy.macros [deftest deff val])
(import os)
(import signal)
(import threading)
(import httpx)
(import pytest)
(import doeff_cluster.foundation.coordinator_inbox [RequestInbox json-reply StopState stop-on-signals])
(import doeff_cluster.worker.protocol.stop [StopState :as WorkerStopState])
(import tests.link_rig [LinkRig])
(import doeff_cluster.worker.intent.worker_model [DesiredJobs DesiredUnreadable])


(deff serve [inbox stop]  ; defk にできない: threading.Thread が別の thread で呼ぶ target
  {:pre [(: inbox RequestInbox) (: stop threading.Event)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "箱に並んだ生の要求に、path を本文で返す返事を置き続けるため(stop が立つまで — 検の HTTP server の答え手)。"
  (while (not (.is-set stop))
    (for [request (.take inbox 0.1 16)]
      ;; 箱が並べるのは生の要求(RawRequest)— 返事は送る byte と content-type にして札に置く(#2563)。
      (setv #(data content-type) (json-reply {"path" request.path}))
      (setv request.slot.status 200 request.slot.data data request.slot.content-type content-type)
      (.set request.slot.done)))
  None)


(deftest test-coordinator-keeps-the-connection-between-requests
  ;; 以前(HTTP/1.0)は返事のたびに接続を閉じ、client は毎回 TCP を張り直していた。
  (setv inbox (RequestInbox 0))
  (.start inbox)
  (setv stop (threading.Event))
  (.start (threading.Thread :target serve :args #(inbox stop) :daemon True))
  (setv server inbox.server)
  (assert (is-not server None) "start の後は HTTP server が在る")
  (setv port (get server.server-address 1) opened [])
  (defn #^ None trace [#^ str name #^ dict info]
    (when (= name "connection.connect_tcp.complete") (.append opened name)))
  (try
    (with [client (httpx.Client :base-url f"http://127.0.0.1:{port}" :timeout 5.0)]
      (for [_ (range 3)]
        (setv response (.get client "/board" :extensions {"trace" trace}))
        (assert (= response.status-code 200))
        (assert (= response.http-version "HTTP/1.1"))
        (assert (= (.json response) {"path" "/board"}))))
    (finally
      (.set stop)
      (.shutdown server)))
  (assert (= (len opened) 1)))


;; --- 宛先の切り替えをまたぐ heartbeat(fake の transport = httpx.MockTransport で、LAN の宛先が届く / 届かないを作る) -------------

(setv LAN "http://lan:30881" TS "http://tailnet:30881")

(defclass FakeNet []
  "宛先ごとに「届く / 接続できない / 読みの途中で切れる」を切り替える偽の網。届いた要求の宛先を記録する。"
  (defn #^ None __init__ [self]
    (setv #^ (get set str) self.down (set))
    (setv #^ (get set str) self.read-fails (set))
    (setv self.sent []))
  (defn #^ httpx.Response handle [self #^ httpx.Request request]
    (setv host request.url.host)
    (when (in host self.down) (raise (httpx.ConnectTimeout "timed out" :request request)))
    (when (in host self.read-fails) (raise (httpx.ReadTimeout "timed out" :request request)))
    (.append self.sent #(host request.url.path))
    (httpx.Response 200 :json {"jobs" [] "tasks" [] "draining" False "host" host}))
  (defn #^ httpx.MockTransport transport [self] (httpx.MockTransport self.handle)))

(deftest test-heartbeat-silence-is-counted-across-an-address-switch
  ;; 自己停止の数え方(最後に届いた時刻)は宛先と無関係。宛先を替えても続き、替えた先で届けば 0 に戻る。
  (import time)
  (setv net (FakeNet) link (LinkRig f"{LAN},{TS}" "w" #() 1 0 20000 :transport (.transport net)))
  (.update net.down #{"lan" "tailnet"})
  (setv link.state.last-ok-ms (- (int (* 1000 (time.time))) (int (* 1000 5))))
  (setv first (.poll link))
  (assert (isinstance first DesiredUnreadable))
  (.discard net.down "tailnet")
  (assert (= (.poll link) (DesiredJobs #())))
  (assert (= (.endpoint link) TS))
  (assert (< (- (int (* 1000 (time.time))) link.state.last-ok-ms) 1000))
  ;; heartbeat は今の宛先を名乗る(coordinator の /state に出る)
  (assert (= (get net.sent -1) #("tailnet" "/heartbeat"))))


(defclass ScriptedCoordinator []
  "heartbeat への返事を順に返す偽の coordinator(返事が尽きたら最後の返事を繰り返す)。"
  (defn #^ None __init__ [self #^ list replies] (setv self.replies (list replies)))
  (defn #^ httpx.Response handle [self #^ httpx.Request request]
    (if (> (len self.replies) 1) (.pop self.replies 0) (get self.replies 0)))
  (defn #^ httpx.MockTransport transport [self] (httpx.MockTransport self.handle)))

(deftest test-heartbeat-refusal-and-registration-are-told-at-each-change [capsys]
  ;; 13 回目の本番の切り替え(#1005): coordinator が heartbeat を 400 で断り続けても、worker は「起動します」の後に
  ;; 32 分 log に何も出さなかった(断りを「届かない」と同じに数え、fence を越えると状態の file の note も空になる)。
  ;; 名乗れない理由(status と coordinator の返した本文)は変わり目ごとに 1 行、名乗れた時に 1 行出す。同じ理由の繰り返しは出さない。
  (setv refusal (httpx.Response 400 :json {"error" "TypeError: 'NoneType' object is not subscriptable"})
        accepted (httpx.Response 200 :json {"jobs" [] "tasks" [] "draining" False})
        coordinator (ScriptedCoordinator [refusal refusal accepted accepted])
        link (LinkRig LAN "w" #() 1 0 20000 :transport (.transport coordinator)))
  (setv first (.poll link))
  (assert (isinstance first DesiredUnreadable))
  (assert (in "400" first.reason) first.reason)
  (assert (in "TypeError: 'NoneType' object is not subscriptable" first.reason) first.reason)
  (.poll link)
  (assert (= (.poll link) (DesiredJobs #())))
  (.poll link)
  (setv lines (.splitlines (. (.readouterr capsys) err)))
  (assert (= (len lines) 2) lines)
  (assert (in "名乗れない" (get lines 0)) lines)
  (assert (in "TypeError: 'NoneType' object is not subscriptable" (get lines 0)) lines)
  (assert (in "名乗りました" (get lines 1)) lines)
  (assert (in LAN (get lines 1)) lines))


(deftest test-stop-on-signals-raises-both-stop-marks-and-refuses-other-values
  "3 つの main が信号の登録を foundation の 1 か所(stop-on-signals)に任せても、coordinator と記録の置き場の StopState と worker の
   StopState の両方が SIGTERM・SIGINT で立つ事(main の止まり方が変わらない)— requested を持たない値は登録の前に断る。"
  (val saved #((signal.getsignal signal.SIGTERM) (signal.getsignal signal.SIGINT)))
  (try
    (for [[kind sig] [[StopState signal.SIGTERM] [WorkerStopState signal.SIGINT]]]
      (setv mark (kind))
      (! (stop-on-signals mark))
      (assert (not mark.requested))
      (os.kill (os.getpid) sig)
      (assert mark.requested (.format "{} が {} で立たない" kind.__module__ sig)))
    (with [(pytest.raises Exception)]
      (! (stop-on-signals "印でない値")))
    (finally
      (signal.signal signal.SIGTERM (get saved 0))
      (signal.signal signal.SIGINT (get saved 1)))))
