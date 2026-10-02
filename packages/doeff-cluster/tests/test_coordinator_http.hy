;; coordinator との HTTP: coordinator が接続を使い回させること・heartbeat の途絶の数えが宛先の切り替えをまたぐこと(2026-09-23 の newmac の件)。
;; 宛先の切り替えと読みの送り直しの性質は宛先の部品の検(test_coordinator_route — #2427 で httpx の client を持つ口を退役させた)。
(require doeff-hy.macros [defk deftest <- val var])
(import threading)
(import httpx)
(import doeff_cluster.foundation.coordinator_inbox [RequestInbox json-reply])
(import doeff_cluster.shared.intent.protocol [NextRequests Reply RequestDoor])
(import doeff_cluster.shared.protocol.inbox [http-requests])
(import tests.link_rig [LinkRig])
(import doeff_cluster.worker.intent.worker_model [DesiredJobs DesiredUnreadable])


(defn #^ None serve [#^ RequestInbox inbox #^ threading.Event stop]
  (while (not (.is-set stop))
    (for [request (.take inbox 0.1 16)]
      ;; 箱が並べるのは生の要求(RawRequest)— 返事は送る byte と content-type にして札に置く(#2563)。
      (setv #(data content-type) (json-reply {"path" request.path}))
      (setv request.slot.status 200 request.slot.data data request.slot.content-type content-type)
      (.set request.slot.done))))


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


(defk answer-with-the-door [count]
  {:pre [(: count int)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "program"}}
  "受付の列から要求を count 件取り、届いた口の名を 203 で答えるため(coordinator の調停ループの代わり)。答え = path → RequestDoor。"
  (var got [])
  (while (> count (len got))
    (<- batch list (NextRequests 5.0))
    (:= got (+ got batch)))
  (for [request got]
    (<- (Reply request 203 {"door" (str request.door)})))
  (dfor r got r.path r.door))


(deftest test-the-read-port-queues-requests-marked-as-read-and-does-not-answer-probes
  ;; 読みだけの口(#2742): 同じ列に並べ、受付の handler(http-requests)が Request の door を RequestDoor.READ にする。どの経路を許すかは
  ;; 箱でなく coordinator の表が決めるので、箱は probe(/readyz)にも直に答えずに並べる。全部の経路の口の probe は今までどおり直に答える。
  (val inbox (RequestInbox 0 :read-port 0))
  (.start inbox)
  (val main-port (get inbox.server.server-address 1))
  (val read-port (get inbox.read-server.server-address 1))
  (val answers {})
  (defn #^ None ask [#^ str key #^ int port #^ str path]  ; thread の target(threading が呼ぶ callback)
    (with [client (httpx.Client :base-url f"http://127.0.0.1:{port}" :timeout 5.0)]
      (setv (get answers key) (.get client path))))
  (assert (= (. (httpx.get f"http://127.0.0.1:{main-port}/livez" :timeout 5.0) status-code) 200) "全部の経路の口は probe に直に答える")
  (val askers [(threading.Thread :target ask :args #("read-probe" read-port "/readyz") :daemon True)
               (threading.Thread :target ask :args #("main" main-port "/resources/Service/a") :daemon True)])
  (for [t askers] (.start t))
  (<- doors dict ((http-requests inbox) (answer-with-the-door 2)))
  (for [t askers] (.join t 5.0))
  (.shutdown inbox.server)
  (.shutdown inbox.read-server)
  (assert (= doors {"/readyz" RequestDoor.READ "/resources/Service/a" RequestDoor.MAIN}) doors)
  (assert (= (. (get answers "read-probe") status-code) 203) "読みの口の probe は列を通って coordinator の答えが返る")
  (assert (= (.json (get answers "read-probe")) {"door" "read"}))
  (assert (= (.json (get answers "main")) {"door" "main"})))


(deftest test-the-inbox-opens-no-read-port-by-default
  (val inbox (RequestInbox 0))
  (.start inbox)
  (try
    (assert (is inbox.read-server None))
    (finally (.shutdown inbox.server))))


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
    (httpx.Response 200 :json {"jobs" [] "tasks" [] "host" host}))
  (defn #^ httpx.MockTransport transport [self] (httpx.MockTransport self.handle)))

(deftest test-heartbeat-silence-is-counted-across-an-address-switch
  ;; 自己停止の数え方(最後に届いた時刻)は宛先と無関係。宛先を替えても続き、替えた先で届けば 0 に戻る。
  (import time)
  (setv net (FakeNet) link (LinkRig f"{LAN},{TS}" "w" #() 1 20000 :transport (.transport net)))
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
        accepted (httpx.Response 200 :json {"jobs" [] "tasks" []})
        coordinator (ScriptedCoordinator [refusal refusal accepted accepted])
        link (LinkRig LAN "w" #() 1 20000 :transport (.transport coordinator)))
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
