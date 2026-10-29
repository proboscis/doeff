;;; HTTP の答え手が同時に client へ渡す要求の数と、取り消し・時間切れの後の接続の枠(agora card ki-89809f7494aa — 画面の server が process の
;;; client 1 つへ数千の要求を上限なく撃ち、httpcore の待ちの列が 10,651 になって await の橋の thread を割り当ての照らしで使い切った・その後
;;; 橋が暇でも後の要求が全部 PoolTimeout のまま 20 分戻らなかった)。本物の socket で、検の中の最小の待ち受け(橋の loop の上の asyncio の
;;; server — 要求を数え、受けるたびに client の httpcore の列の長さを測る)へ当てる。
;;;   (1) 1,000 の要求を同時に出しても、httpcore の列(pool の要求の数)は宛先の上限 CLIENT-ORIGIN-MAX-IN-FLIGHT を越えない — 直す前は
;;;       全部が httpcore の列に並んだ
;;;   (2) 1 つの宛先が答えない要求で宛先の枠を埋めても、他の宛先の要求は答えを受ける — 直す前は答えない宛先が接続の上限 100 本を全部持ち、
;;;       他の宛先の要求は PoolTimeout になった
;;;   (3) 列で待つ要求に新しい接続が割り当てられた直後にその要求を取り消すと、httpcore 1.0.9 は つないでいない接続(CONNECTING)を pool に
;;;       残す(上流の不具合の形を素の client で示す — 上流が直せばこの断言が落ち、回り道を外す合図になる)。答え手の送り方(_sent-or-reaped)
;;;       は残さず、上限 1 本の client の次の要求が接続を得る
;;;   (4) 答え手を通した 600 の要求の取り消しと時間切れの後に、同じ client の次の要求は PoolTimeout にならず、pool に CONNECTING は残らない
(require doeff-hy.macros [deftest defk <- val with-handler])
(import asyncio)
(import random)
(import httpx)
(import pytest)
(import doeff_core_effects [Await])
(import doeff_core_effects [await-handler])
(import doeff_core_effects.http_effects [HttpFailed HttpRequest HttpResponse])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_core_effects._http_handlers_impl [CLIENT-MAX-CONNECTIONS CLIENT-ORIGIN-MAX-IN-FLIGHT http-client-factory _sent-or-reaped])
(import doeff_core_effects [Cancel Gather Spawn])

;; この file の待ちは実時間が対象(何が対象かは名簿 tests/realtime_tests.toml のこの file の行)。
(val pytestmark pytest.mark.realtime)

;; 同時に出す要求の数((1))と、取り消し・時間切れを混ぜて出す要求の数((4))。
(val BURST 1000)
(val STORM 600)
;; (2) で答えない宛先へ出す要求の数 — 宛先の上限を越え、接続の上限 100 も越える。
(val HELD-REQUESTS 150)
;; 答えを待つ上限(秒 — 接続の枠を待つ pool の上限も同じ)。
(val ANSWER-SECONDS 5.0)
;; (4) の時間切れの要求の上限(秒)と、待ち受けが答える前に置く間の上限(秒)。
(val STORM-TIMEOUT-SECONDS 0.003)
(val STORM-DELAY-SECONDS 0.01)
(val OK-RESPONSE b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")


(defclass Lane []
  "検の中の最小の HTTP/1.1 の待ち受け 1 つ(橋の loop の上で開く)。受けた要求を数え(arrivals)、受けるたびに見張る client の httpcore の
   列の長さ(pool の要求の数)の最大を控える(peak)。held = 対象(request-target)が /held の要求は released が立つまで答えない。
   filled = 答えない要求を fill-at 本受けた時に立つ。delay = 答える前に置く間の上限(秒・乱数)。"
  (defn #^ None __init__ [self * #^ int [fill-at 0] #^ float [delay 0.0]]
    "数えを 0 で始め、見張る client を持たずに始めるため。"
    (setv self.arrivals 0 self.held 0 self.peak 0 self.fill-at fill-at self.delay delay self.client None
          self.released (asyncio.Event) self.filled (asyncio.Event) self.server None)
    None)
  (defn #^ httpx.AsyncClient watched [self]
    "答え手が範囲の初めに作る client を見張るため(http-production-handler の client-factory に渡す)。"
    (setv self.client (http-client-factory))
    self.client)
  (defn :async #^ str open [self]
    "127.0.0.1 の空きの port で開き、URL の頭を答えるため。"
    (setv self.server (await (asyncio.start-server self.serve "127.0.0.1" 0)))
    (.format "http://127.0.0.1:{}" (get (.getsockname (get self.server.sockets 0)) 1)))
  (defn :async #^ None release [self]
    "答えない要求に答えさせるため。"
    (.set self.released)
    None)
  (defn :async #^ None close [self]
    "待ち受けを閉じるため(答えない要求も先に放す)。"
    (.set self.released)
    (.close self.server)
    None)
  (defn :async #^ None serve [self reader writer]
    "接続 1 本の要求を順に受けて答えるため(接続を使い回す)。"
    (try
      (while True
        (setv head (await (.readuntil reader b"\r\n\r\n")))
        (+= self.arrivals 1)
        (when (is-not self.client None)
          (setv self.peak (max self.peak (len self.client._transport._pool._requests))))
        (when (.startswith (get (.split head b" ") 1) b"/held")
          (+= self.held 1)
          (when (= self.held self.fill-at)
            (.set self.filled))
          (await (.wait self.released)))
        (when (> self.delay 0.0)
          (await (asyncio.sleep (* (random.random) self.delay))))
        (.write writer OK-RESPONSE)
        (await (.drain writer)))
      (except [Exception]
        (.close writer)))
    None))


(defk opened [lane]
  {:pre [(: lane Lane)] :post [(: % str)] :tags {:context "http" :role "foundation"}}
  "待ち受けを橋の loop の上で開き、URL の頭を答えるため。"
  (<- base str (Await (.open lane)))
  base)


(defk asked [url timeout values]
  {:pre [(: url str) (: timeout float) (: values bool)] :post [(: % (| HttpResponse HttpFailed))] :tags {:context "http" :role "program"}}
  "GET を 1 回出すため(撃ち直しなし — 答え手の送り直しで枠の待ちを隠さない)。"
  (<- answer (| HttpResponse HttpFailed) (HttpRequest "GET" url :max-retries 0 :timeout-seconds timeout :failures-as-values values))
  answer)


(defk all-ok [answers]
  {:pre [(: answers tuple)] :post [(: % bool)] :tags {:context "http" :role "program"}}
  "答えが全部 200 の返事かを判じるため。"
  (all (gfor answer answers (and (isinstance answer HttpResponse) (= answer.status 200)))))


(defk spawned [requests]
  {:pre [(: requests list)] :post [(: % tuple)] :tags {:context "http" :role "program"}}
  "要求の Program の列を task 1 つに 1 本ずつ同時に走らせ、task の組を答えるため。"
  (var tasks #())
  (for [request requests]
    (<- task (Spawn request))
    (:= tasks (+ tasks #(task))))
  tasks)


(defk burst [url count]
  {:pre [(: url str) (: count int)] :post [(: % tuple)] :tags {:context "http" :role "program"}}
  "count 本の GET を同時に出し(task 1 つに 1 本)、全部の答えを待つため。"
  (<- tasks tuple (spawned (lfor _ (range count) (asked url ANSWER-SECONDS False))))
  (<- answers (Gather #* tasks))
  (tuple answers))


(deftest test-a-burst-waits-in-the-scheduler-not-in-the-httpcore-queue
  ;; (1): 1,000 本を同時に出す。直す前は全部が httpcore の列に並んだ(列の最大 ≒ 1,000)。直した後は宛先の上限の内。
  (val lane (Lane))
  (<- answers tuple
      (with-handler [await-handler (http-production-handler :client-factory lane.watched)]
        (do-burst lane)))
  (assert (! (all-ok answers)))
  (assert (= lane.arrivals BURST) lane.arrivals)
  (assert (<= lane.peak CLIENT-ORIGIN-MAX-IN-FLIGHT) lane.peak)
  (assert (< CLIENT-ORIGIN-MAX-IN-FLIGHT CLIENT-MAX-CONNECTIONS)))


(defk do-burst [lane]
  {:pre [(: lane Lane)] :post [(: % tuple)] :tags {:context "http" :role "program"}}
  "待ち受けを開いて BURST 本を同時に出し、待ち受けを閉じるため。"
  (<- base str (opened lane))
  (try
    (<- answers tuple (burst (+ base "/burst") BURST))
    answers
    (finally (<- (Await (.close lane))))))


(defk held-then-other [held other]
  {:pre [(: held Lane) (: other Lane)] :post [(: % tuple)] :tags {:context "http" :role "program"}}
  "held の宛先へ答えない要求を HELD-REQUESTS 本出し、宛先の枠が埋まってから other の宛先へ 1 本出し、その答えと、held が放されるまでに受けた
   要求の数と、放した後の held の答えを答えるため。"
  (<- held-base str (opened held))
  (<- other-base str (opened other))
  (try
    (<- tasks tuple (spawned (lfor _ (range HELD-REQUESTS) (asked (+ held-base "/held") 30.0 False))))
    (<- (Await (.wait held.filled)))
    (<- answer (| HttpResponse HttpFailed) (asked (+ other-base "/other") ANSWER-SECONDS True))
    (val arrived-while-held held.held)
    (<- (Await (.release held)))
    (<- held-answers (Gather #* tasks))
    #(answer arrived-while-held (tuple held-answers))
    (finally
      (<- (Await (.close held)))
      (<- (Await (.close other))))))


(deftest test-a-held-origin-leaves-room-for-another-origin
  ;; (2): 答えない宛先へ 150 本(接続の上限 100 を越える)出し、宛先の枠が埋まってから別の宛先へ 1 本出す。直す前は答えない宛先が 100 本の
  ;; 接続を全部持ち、別の宛先の要求は PoolTimeout(HttpFailed)になった。直した後は宛先の枠 64 本だけが埋まり、別の宛先は答えを受ける。
  (val held (Lane :fill-at CLIENT-ORIGIN-MAX-IN-FLIGHT))
  (val other (Lane))
  (<- seen tuple
      (with-handler [await-handler (http-production-handler)]
        (held-then-other held other)))
  (val answer (get seen 0))
  (val arrived-while-held (get seen 1))
  (val held-answers (get seen 2))
  (assert (and (isinstance answer HttpResponse) (= answer.status 200)) answer)
  (assert (= arrived-while-held CLIENT-ORIGIN-MAX-IN-FLIGHT) arrived-while-held)
  (assert (! (all-ok held-answers))))


(defn :async #^ tuple leak-scenario [#^ bool reaped]
  "上限 1 本の client で、答えない要求 A を出し、要求 B を列に並べ、A を取り消して B に新しい接続が割り当てられた拍に B を取り消し、
   pool に残った接続の info の列と、次の要求の結末(状態の数か PoolTimeout)を答えるため(頭の註 (3))。reaped = 答え手の送り方
   (_sent-or-reaped)で送るか、素の client.request で送るか。"
  (setv lane (Lane))
  (setv base (await (.open lane)))
  (setv client (httpx.AsyncClient :limits (httpx.Limits :max-connections 1)))
  (setv pool client._transport._pool)
  (defn send [path]
    (if reaped
        (_sent-or-reaped client :method "GET" :url (+ base path))
        (.request client "GET" (+ base path))))
  (try
    (setv held (asyncio.ensure-future (send "/held")))
    (setv queued (asyncio.ensure-future (send "/queued")))
    ;; B が pool の列に並ぶまで回す(A は接続を得て答えを待つ)。
    (while (not (any (gfor request pool._requests (= request.request.url.target b"/queued"))))
      (await (asyncio.sleep 0)))
    (setv waiting (next (gfor request pool._requests :if (= request.request.url.target b"/queued") request)))
    (.cancel held)
    ;; A の取り消しが接続を閉じ、B に新しい接続を割り当てた拍に、B を取り消す(B はまだ目を覚ましていない)。
    (while (is waiting.connection None)
      (await (asyncio.sleep 0)))
    (.cancel queued)
    (await (asyncio.gather held queued :return-exceptions True))
    (setv left (tuple (gfor connection pool._connections (.info connection))))
    (setv outcome
          (try
            (. (await (.request client "GET" (+ base "/next") :timeout (httpx.Timeout ANSWER-SECONDS :pool 0.5))) status-code)
            (except [httpx.PoolTimeout]
              "PoolTimeout")))
    #(left outcome)
    (finally
      (await (.aclose client))
      (await (.close lane)))))


(deftest test-a-request-cancelled-as-it-is-given-a-new-connection-leaves-no-slot-behind
  ;; (3): 素の client は つないでいない接続を残し、上限 1 本の client の次の要求が PoolTimeout になる(上流 httpcore 1.0.9 の形)。
  ;; 答え手の送り方は残さず、次の要求は答えを受ける。
  (<- plain tuple (with-handler [await-handler] (Await (leak-scenario False))))
  (assert (= plain #(#("CONNECTING") "PoolTimeout")) plain)
  (<- reaped tuple (with-handler [await-handler] (Await (leak-scenario True))))
  (assert (= reaped #(#() 200)) reaped))


(defk storm [lane]
  {:pre [(: lane Lane)] :post [(: % tuple)] :tags {:context "http" :role "program"}}
  "STORM 本の GET を同時に出し(半分は時間切れになる短い上限)、3 本に 1 本を出した直後から取り消し、残りの答えを待ってから次の 1 本を出し、
   その答えと、その時 pool に残る CONNECTING の数を答えるため。"
  (<- base str (opened lane))
  (try
    (<- tasks tuple (spawned (lfor index (range STORM)
                                   (asked (+ base "/storm") (if (% index 2) STORM-TIMEOUT-SECONDS ANSWER-SECONDS) True))))
    (val cancelled (cut tasks 0 None 3))
    (for [task cancelled]
      (<- (Await (asyncio.sleep 0)))
      (<- (Cancel task)))
    (<- (Gather #* (lfor #(index task) (enumerate tasks) :if (% index 3) task)))
    (<- answer (| HttpResponse HttpFailed) (asked (+ base "/next") ANSWER-SECONDS True))
    (val connecting (len (lfor connection lane.client._transport._pool._connections :if (= (.info connection) "CONNECTING") connection)))
    #(answer connecting)
    (finally (<- (Await (.close lane))))))


(deftest test-the-next-request-after-a-storm-of-cancels-and-timeouts-gets-an-answer
  ;; (4): 答え手を通した 600 本の取り消しと時間切れの後に、同じ client の次の要求が答えを受け、pool に つないでいない接続が残らない。
  (val lane (Lane :delay STORM-DELAY-SECONDS))
  (<- seen tuple
      (with-handler [await-handler (http-production-handler :client-factory lane.watched)]
        (storm lane)))
  (val answer (get seen 0))
  (val connecting (get seen 1))
  (assert (and (isinstance answer HttpResponse) (= answer.status 200)) answer)
  (assert (= connecting 0) connecting))
