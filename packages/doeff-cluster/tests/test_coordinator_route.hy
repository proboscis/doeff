;;; coordinator への宛先の部品(shared/protocol/coordinator_route.hy・#2337 の 1 本目)の検。
;;; 宛先の相手は HttpRequest に筋書きで答える fake(宛先ごとの答えの列)・時計は doeff-time の仮想の時計(sim-time-handler)。
;;; 失敗ケース: 接続できない時だけ次の宛先へ回る(途中の時間切れでは回らない)・先頭の試し直し・全部に届かない時の一巡し直しの間・
;;; 何度送っても同じ要求の期限までの送り直し。
(require doeff-hy.macros [deftest defhandler <- val])
(import doeff [with-handlers])
(import doeff_core_effects.http_effects [HttpRequest HttpResponse HttpFailed HttpFailureKind])
(import doeff_time [sim-time-handler])
(import doeff_cluster.shared.protocol.coordinator_route [CoordinatorRoute RouteOptions RoutedReply route-of route-order route-used
                                                         routed-request resent-request])
(import tests.clock_fixtures [clock-at clock-ms count-delays])

(val LAN "http://lan:8080")
(val NET "http://tailnet:8080")
(val START-MS 1790000000000)
(val OPTIONS (RouteOptions :reply-seconds 15.0 :connect-seconds 2.0 :connect-retries 2 :recheck-ms 60000 :actor "job@w1/1"))


(defn #^ HttpResponse ok [#^ str url]
  "200 の返事(本文は空の JSON)。"
  (HttpResponse 200 {} b"{}" "{}" url 0.01))


(defn #^ HttpFailed refused [#^ str url]
  "接続できない失敗(要求はまだ相手に届いていない)。"
  (HttpFailed :url url :detail "ConnectError: refused" :kind HttpFailureKind.CONNECT-FAILED))


(defn #^ HttpFailed read-timed-out [#^ str url]
  "読みの時間切れ(要求は届いたかもしれない)。"
  (HttpFailed :url url :detail "ReadTimeout: timed out" :kind HttpFailureKind.TIMED-OUT))


(defhandler scripted-coordinator [#^ dict script #^ list calls]
  ;; 宛先(要求の URL の頭)ごとの答えの列を前から返す(最後の 1 つは繰り返す)。送った宛先と header を calls に控える。
  (HttpRequest [method url headers]
    (val base (next (gfor b script :if (.startswith url b) b)))
    (.append calls #(base (.get (or headers {}) "X-Actor")))
    (val queue (get script base))
    (resume (if (> (len queue) 1) (.pop queue 0) (get queue 0)))))


(deftest test-a-refused-connection-moves-to-the-next-address-and-stays-there
  (val calls [])
  (val clock (clock-at START-MS))
  (<- route CoordinatorRoute (route-of (+ LAN "," NET) START-MS))
  (<- reply RoutedReply (with-handlers [(sim-time-handler :clock clock)
                                        (scripted-coordinator {LAN [(refused LAN)] NET [(ok NET)]} calls)]
                                       (routed-request route "GET" "/board" OPTIONS {"prefix" "a/"} None)))
  (assert (isinstance reply.answer HttpResponse) reply)
  (assert (= reply.route.active 1) reply.route)
  (assert (= (lfor c calls (get c 0)) [LAN NET]) calls)
  (assert (= (get calls 0 1) "job@w1/1") calls)
  ;; 次の要求は回った先から試す(LAN を試し直さない — recheck-ms の前)。
  (<- again RoutedReply (with-handlers [(sim-time-handler :clock clock)
                                        (scripted-coordinator {LAN [(refused LAN)] NET [(ok NET)]} calls)]
                                       (routed-request reply.route "GET" "/board" OPTIONS None None)))
  (assert (= (lfor c (cut calls 2 None) (get c 0)) [NET]) calls)
  (assert (= again.route.active 1) again.route))


(deftest test-a-timeout-after-the-connection-does-not-move-to-the-next-address
  ;; 読みの時間切れは要求が届いたかもしれない — 次の宛先へ回して書きを 2 度届けない。答えは失敗のまま返る。
  (val calls [])
  (<- route CoordinatorRoute (route-of (+ LAN "," NET) START-MS))
  (<- reply RoutedReply (with-handlers [(sim-time-handler :clock (clock-at START-MS))
                                        (scripted-coordinator {LAN [(read-timed-out LAN)] NET [(ok NET)]} calls)]
                                       (routed-request route "PUT" "/board/k" OPTIONS None {"value" 1})))
  (assert (isinstance reply.answer HttpFailed) reply)
  (assert (= reply.answer.kind HttpFailureKind.TIMED-OUT) reply)
  (assert (= (lfor c calls (get c 0)) [LAN]) calls)
  (assert (= reply.route.active 0) reply.route))


(deftest test-the-first-address-is-tried-again-after-the-recheck-interval
  (<- moved CoordinatorRoute (route-of (+ LAN "," NET) START-MS))
  (<- on-net CoordinatorRoute (route-used moved 1 START-MS))
  (<- early (route-order on-net (+ START-MS 59999) 60000))
  (assert (= early.indices #(1 0)) early)
  (<- late (route-order on-net (+ START-MS 60000) 60000))
  (assert (= late.indices #(0 1)) late)
  (assert (= late.route.switched-at-ms (+ START-MS 60000)) late)
  ;; 先頭に戻れる時は戻る。
  (val calls [])
  (<- reply RoutedReply (with-handlers [(sim-time-handler :clock (clock-at (+ START-MS 61000)))
                                        (scripted-coordinator {LAN [(ok LAN)] NET [(ok NET)]} calls)]
                                       (routed-request on-net "GET" "/state" OPTIONS None None)))
  (assert (= reply.route.active 0) reply.route)
  (assert (= (lfor c calls (get c 0)) [LAN]) calls))


(deftest test-when-no-address-answers-it-pauses-and-goes-round-again-then-answers-the-last-refusal
  (val calls [])
  (val delays [])
  (<- route CoordinatorRoute (route-of (+ LAN "," NET) START-MS))
  (<- reply RoutedReply (with-handlers [(sim-time-handler :clock (clock-at START-MS))
                                        (count-delays delays)
                                        (scripted-coordinator {LAN [(refused LAN)] NET [(refused NET)]} calls)]
                                       (routed-request route "GET" "/board" OPTIONS None None)))
  (assert (= (len calls) 6) calls)
  (assert (= delays [0.25 0.5]) delays)
  (assert (and (isinstance reply.answer HttpFailed) (= reply.answer.kind HttpFailureKind.CONNECT-FAILED) (= reply.answer.url NET)) reply))


(deftest test-a-repeatable-request-is-resent-until-the-deadline-and-stops-at-the-first-answer
  ;; 2 回失敗してから返事 — 返事で止まる。ずっと失敗 — 期限を越える前に失敗のまま返す。
  (val calls [])
  (val delays [])
  (<- route CoordinatorRoute (route-of LAN START-MS))
  (<- answered RoutedReply (with-handlers [(sim-time-handler :clock (clock-at START-MS))
                                           (count-delays delays)
                                           (scripted-coordinator {LAN [(read-timed-out LAN) (read-timed-out LAN) (ok LAN)]} calls)]
                                          (resent-request route "GET" "/board" OPTIONS None None 25.0 0.5)))
  (assert (isinstance answered.answer HttpResponse) answered)
  (assert (= (len calls) 3) calls)
  (val clock (clock-at START-MS))
  (<- gave-up RoutedReply (with-handlers [(sim-time-handler :clock clock)
                                          (scripted-coordinator {LAN [(read-timed-out LAN)]} [])]
                                         (resent-request route "GET" "/board" OPTIONS None None 2.0 0.5)))
  (assert (isinstance gave-up.answer HttpFailed) gave-up)
  (assert (<= (- (clock-ms clock) START-MS) 2000) (clock-ms clock)))


(deftest test-a-spec-without-addresses-is-refused
  (try
    (<- got (route-of " , " START-MS))
    (assert False got)
    (except [refused ValueError]
      (assert (in "宛先が無い" (str refused)) (str refused)))))
