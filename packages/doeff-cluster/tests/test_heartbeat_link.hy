;; 本番の CoordinatorLink の heartbeat の切り離し(beat_policy・背景の待ちの thread — #1933)。httpx の MockTransport の後ろの偽の
;; coordinator へ本物の link で話す(実時間 — 生存の窓 400 ms・間隔 100 ms に縮める)。
;;
;; - 待ちを使えるまで(返事に版が無い・待ちがまだ答えていない)は拍ごとに送る。
;; - 待ちが答えた後は、間隔の内の拍では送らず、間隔が過ぎれば送る。待ちが「変わった」と答えれば間隔の内でも次の拍で送る。
;; - /watch が 404 なら拍ごとに戻る(1 行出す)。thread が思わぬ例外で止まれば、1 行出して拍ごとに戻る(黙って待ちを失わない)。
;; - close で thread が止まる。待ちを使わない link(既定)は thread を起こさない。
(require doeff-hy.macros [deftest val var])
(import collections.abc [Callable])
(import threading)
(import time)
(import urllib.parse [parse-qsl])
(import httpx)
(import doeff_cluster.handlers [CoordinatorLink])

(val TIMING {"lease_ms" 400 "fence_ms" 20000})   ; 間隔 = 400 / 4 = 100 ms


(defclass FakeCoordinator []
  "heartbeat に版つき(revision が None なら版の欄の無い旧い形)の返事をし、/watch に mode で答える偽の coordinator。
   mode = unchanged(少し待って「変わっていない」)・changed(最初の確かめの後に 1 度だけ「変わった」)・missing(404)。
   beats = 受けた heartbeat の数・watches = 受けた待ちの数。"
  (defn #^ None __init__ [self #^ str mode #^ (| int None) [revision 3]]
    (setv self.mode mode self.revision revision self.beats 0 self.watches 0 self.changed-sent False
          self.lock (threading.Lock)))

  (defn #^ httpx.Response handle [self #^ httpx.Request request]
    (if (= request.url.path "/heartbeat")
        (do (with [self.lock] (+= self.beats 1))
            (httpx.Response 200 :json (| {"jobs" [] "tasks" [] "warm" [] "timing" TIMING "draining" False}
                                         (if (is self.revision None) {} {"revision" self.revision}))))
        (do (with [self.lock] (+= self.watches 1))
            (setv query (dict (parse-qsl (.decode request.url.query "ascii"))))
            (cond
              (= self.mode "missing") (httpx.Response 404 :json {"error" "知らない要求"})
              (= (get query "timeoutSeconds") "0.0") (httpx.Response 200 :json {"revision" self.revision "changed" False})
              (and (= self.mode "changed") (not self.changed-sent))
                (do (setv self.changed-sent True)
                    (httpx.Response 200 :json {"revision" (+ self.revision 1) "changed" True}))
              True (do (time.sleep 0.05)
                       (httpx.Response 200 :json {"revision" self.revision "changed" False}))))))

  (defn #^ int beat-count [self]
    (with [self.lock] (setv n self.beats))
    n))


(defn #^ CoordinatorLink watching-link [#^ FakeCoordinator coordinator #^ bool [watch True]]
  "待ちを使う(watch)本物の link を偽の coordinator へ向けて作る。"
  (CoordinatorLink "http://coord" "w" #() 1 20000 :transport (httpx.MockTransport coordinator.handle) :watch watch))


(defn #^ bool settle [#^ Callable condition #^ float [seconds 2.0]]
  "背景の thread の進みを実時間で待つ(condition が真になるまで・上限 seconds)。"
  (setv until (+ (time.monotonic) seconds))
  (while (and (not (condition)) (< (time.monotonic) until))
    (time.sleep 0.01))
  (condition))



(deftest test-a-reply-without-a-revision-keeps-a-heartbeat-every-tick
  ;; 版の欄の無い返事(待つ口の無い旧い coordinator)には thread を起こさず、拍ごとに送る。
  (val coordinator (FakeCoordinator "unchanged" :revision None))
  (val link (watching-link coordinator))
  (for [_ (range 5)] (.poll link))
  (assert (= (.beat-count coordinator) 5) coordinator.beats)
  (assert (is link.watcher None))
  (assert (= coordinator.watches 0)))


(deftest test-a-missing-watch-route-falls-back-to-a-heartbeat-every-tick [capsys]
  (val coordinator (FakeCoordinator "missing"))
  (val link (watching-link coordinator))
  (.poll link)
  (assert (settle (fn [] link.watch-state.unsupported)))
  (for [_ (range 4)] (.poll link))
  (assert (= (.beat-count coordinator) 5) coordinator.beats)
  (assert (in "待ちの口が無い" (. (.readouterr capsys) err)))
  (.close link))


(deftest test-a-confirmed-watch-skips-beats-inside-the-interval
  ;; 待ちが答えた後: 間隔(100 ms)の内の拍は送らず、間隔が過ぎた拍で送る。反例 — 待ちを使わない link は拍ごとに送る。
  (val coordinator (FakeCoordinator "unchanged"))
  (val link (watching-link coordinator))
  (.poll link)
  (assert (settle (fn [] (.watching link))))
  (val before (.beat-count coordinator))
  (.poll link)
  (.poll link)
  (assert (<= (- (.beat-count coordinator) before) 1) #(before coordinator.beats))
  (time.sleep 0.15)
  (.poll link)
  (assert (>= (- (.beat-count coordinator) before) 1) #(before coordinator.beats))
  (.close link)
  (assert (settle (fn [] (not (.is-alive link.watcher))) 1.0))
  (val plain (FakeCoordinator "unchanged"))
  (val every (watching-link plain :watch False))
  (for [_ (range 3)] (.poll every))
  (assert (= (.beat-count plain) 3) plain.beats)
  (assert (is every.watcher None)))


(deftest test-a-changed-watch-makes-the-next-tick-beat
  ;; 待ちが「変わった」と答えたら、間隔の内でも次の拍で送る(desired の変化に拍 1 つの内に起きる)。
  (val coordinator (FakeCoordinator "changed"))
  (val link (watching-link coordinator))
  (.poll link)
  (assert (settle (fn [] (.is-set link.watch-state.woken))))
  (val before (.beat-count coordinator))
  (.poll link)
  (assert (= (.beat-count coordinator) (+ before 1)) #(before coordinator.beats))
  (assert (not (.is-set link.watch-state.woken)))
  (.close link))


(deftest test-a-dead-watch-thread-is-told-and-falls-back [capsys]
  ;; thread が思わぬ例外で止まれば理由を出し、拍は watching で気づいて拍ごとの heartbeat に戻る(黙って待ちを失わない)。
  (val coordinator (FakeCoordinator "unchanged"))
  (val link (watching-link coordinator))
  (setv link.watch-once (fn [after confirmed] (raise (RuntimeError "壊れた待ち"))))
  (.poll link)
  (assert (settle (fn [] (and link.watcher (not (.is-alive link.watcher))))))
  (val before (.beat-count coordinator))
  (for [_ (range 3)] (.poll link))
  (assert (= (.beat-count coordinator) (+ before 3)) #(before coordinator.beats))
  (val err (. (.readouterr capsys) err))
  (assert (in "壊れた待ち" err) err)
  (assert (in "拍ごとの heartbeat に戻ります" err) err))
