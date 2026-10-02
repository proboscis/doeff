;; 本番の coordinator への口(worker/protocol/coordinator_link)の heartbeat の切り離し(beat_policy・名指しの待ちの背景の task — #1933・
;; #2427)。httpx の MockTransport の後ろの偽の coordinator へ本物の口で話す(実時間 — 生存の窓 400 ms・間隔 100 ms に縮める)。待ちの背景の
;; task は run をまたいで生きないので、筋書き(拍を何度か打つ Program)を 1 回の run で回す。
;;
;; - 待ちを使えるまで(返事に版が無い・待ちがまだ答えていない)は拍ごとに送る。
;; - 待ちが答えた後は、間隔の内の拍では送らず、間隔が過ぎれば送る。待ちが「変わった」と答えれば間隔の内でも次の拍で送る。
;; - /watch が 404 なら拍ごとに戻る(1 行出す)。背景の task が思わぬ例外で止まれば、1 行出して拍ごとに戻る(黙って待ちを失わない)。
;; - 止めの合図で背景の task が止まる。待ちを使わない口(既定)は背景の task を起こさない。
;; - 宣言の読みは拍の間の眠りを起こす呼び鈴を添え、呼び鈴は鳴るまで拍をまたいで同じ物・「変わった」で 1 度だけ鳴る(#2692)。
(require doeff-hy.macros [defhandler defk deftest <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import collections.abc [Callable])
(import threading)
(import time)
(import urllib.parse [parse-qsl])
(import pathlib [Path])
(import httpx)
(import doeff [Program run with-handlers])
(import doeff_core_effects.handlers [await-handler slog-handler])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_cluster.shared.core.promise_wait [promise-or-timeout])
(import doeff_time [Delay async-time-handler])
(import doeff_cluster.worker.intent.worker_model [ReadDesired DesiredJobs])
(import doeff_core_effects.http_effects [HttpRequest])
(import doeff_cluster.worker.protocol.coordinator_link [coordinator-link bell-rung])
(import tests.link_rig [LinkRig LINK-ROUTE])
(import tests.transport_http [transport-http])

(val TIMING {"lease_ms" 400 "fence_ms" 20000})   ; 間隔 = 400 / 4 = 100 ms


(defclass FakeCoordinator []
  "heartbeat に版つき(revision が None なら版の欄の無い旧い形)の返事をし、/watch に mode で答える偽の coordinator。
   mode = unchanged(少し待って「変わっていない」)・changed(最初の確かめの後に 1 度だけ「変わった」)・missing(404)・
   broken(待ちの答え手が思わぬ例外で落ちる)・gated(gate-open が立った後に 1 度だけ「変わった」— 変化の刻を検が決める)。
   beats = 受けた heartbeat の数・watches = 受けた待ちの数。"
  (defn #^ None __init__ [self #^ str mode #^ (| int None) [revision 3]]
    (setv self.mode mode self.revision revision self.beats 0 self.watches 0 self.changed-sent False self.gate-open False
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
              (= self.mode "broken") (raise (RuntimeError "壊れた待ち"))
              (= (get query "timeoutSeconds") "0.0") (httpx.Response 200 :json {"revision" self.revision "changed" False})
              (and (or (= self.mode "changed") (and (= self.mode "gated") self.gate-open)) (not self.changed-sent))
                (do (setv self.changed-sent True)
                    (httpx.Response 200 :json {"revision" (+ self.revision 1) "changed" True}))
              True (do (time.sleep 0.05)
                       (httpx.Response 200 :json {"revision" self.revision "changed" False}))))))

  (defn #^ int beat-count [self]
    (with [self.lock] (setv n self.beats))
    n))


(defn #^ LinkRig watching-link [#^ FakeCoordinator coordinator #^ Path tmp #^ bool [watch True]]
  "待ちを使う(watch)本物の口を偽の coordinator へ向けて作る。"
  (LinkRig "http://coord" "w" #() 1 20000 :task-dir (str (/ tmp "tasks")) :transport (httpx.MockTransport coordinator.handle) :watch watch))


(defhandler yielding-http
  ;; 検の HTTP の答え手(transport-http)は同期で答えるので、要求の前に scheduler へ譲る(本番の http-production-handler は Await で譲る —
  ;; 譲らないと「変わっていない」が続く待ちの背景の task が筋書きの拍を止める)。
  (HttpRequest [method url headers params body]
    (<- (Delay 0.01))
    (<- answer (HttpRequest method url :headers headers :params params :body body :failures-as-values True))
    (resume answer)))


(defn #^ object on-link [#^ LinkRig link #^ object scenario]  ; defk にできない: 検が Program の外から 1 回走らせる入口
  "筋書きを、口の handler と検の答え手と非同期の時計の下で 1 回の run で回す(待ちの背景の task が筋書きの Delay の間に進む)。"
  (run (scheduled (with-handlers [(await-handler) (async-time-handler) (transport-http link.transport) yielding-http os-file-handler slog-handler
                                  (coordinator-link link.state link.cell LINK-ROUTE link.watch-cell)]
                                 scenario))))


(defk polls [times]
  {:pre [(: times int)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "拍を times 回打つため。"
  (for [n (range times)]
    (<- (ReadDesired)))
  None)


(defk settle [condition [seconds 2.0]]
  {:pre [(: condition Callable) (: seconds float)] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "program"}}
  "背景の task の進みを待つため(condition が真になるまで・上限 seconds — 待つ間は Delay で背景の task に譲る)。"
  (var waited 0.0)
  (while (and (not (condition)) (< waited seconds))
    (<- (Delay 0.01))
    (:= waited (+ waited 0.01)))
  (condition))


(defk counted [scenario coordinator]
  {:pre [(: scenario Program) (: coordinator FakeCoordinator)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きを走らせ、その間に偽の coordinator が受けた heartbeat の数を返すため。"
  (val before (.beat-count coordinator))
  (<- scenario)
  (- (.beat-count coordinator) before))


(deftest test-a-reply-without-a-revision-keeps-a-heartbeat-every-tick [tmp-path]
  ;; 版の欄の無い返事(待つ口の無い旧い coordinator)には背景の task を起こさず、拍ごとに送る。
  (val coordinator (FakeCoordinator "unchanged" :revision None))
  (val link (watching-link coordinator tmp-path))
  (on-link link (polls 5))
  (assert (= (.beat-count coordinator) 5) coordinator.beats)
  (assert (not link.state.watch.running))
  (assert (= coordinator.watches 0)))


(defk missing-route [link]
  {:pre [(: link LinkRig)] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "program"}}
  "1 拍で待ちを起こし、口が無いと分かるのを待ってから 4 拍打つため。答え = 口が無いと分かったか。"
  (<- (ReadDesired))
  (<- known bool (settle (fn [] link.state.watch.unsupported)))
  (<- (polls 4))
  known)


(deftest test-a-missing-watch-route-falls-back-to-a-heartbeat-every-tick [tmp-path capsys]
  (val coordinator (FakeCoordinator "missing"))
  (val link (watching-link coordinator tmp-path))
  (assert (on-link link (missing-route link)))
  (assert (= (.beat-count coordinator) 5) coordinator.beats)
  (assert (in "待ちの口が無い" (. (.readouterr capsys) err))))


(defk confirmed-skips [link coordinator]
  {:pre [(: link LinkRig) (: coordinator FakeCoordinator)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "待ちが答えた後の拍の数え: #(待ちを使えたか 間隔の内の 2 拍で送った数 間隔の後の拍までに送った数 止めの合図で背景の task が止まったか)。"
  (<- (ReadDesired))
  (<- watching bool (settle (fn [] (and link.state.watch.running link.state.watch.confirmed))))
  (<- inside int (counted (polls 2) coordinator))
  (<- (Delay 0.15))
  (<- after int (counted (polls 1) coordinator))
  (setv link.state.watch.closing True)
  (<- stopped bool (settle (fn [] (not link.state.watch.running)) 1.0))
  #(watching inside (+ inside after) stopped))


(deftest test-a-confirmed-watch-skips-beats-inside-the-interval [tmp-path]
  ;; 待ちが答えた後: 間隔(100 ms)の内の拍は送らず、間隔が過ぎた拍で送る。反例 — 待ちを使わない口は拍ごとに送る。
  (val coordinator (FakeCoordinator "unchanged"))
  (val link (watching-link coordinator tmp-path))
  (val got (on-link link (confirmed-skips link coordinator)))
  (val watching (get got 0))
  (val inside (get got 1))
  (val total (get got 2))
  (val stopped (get got 3))
  (assert watching)
  (assert (<= inside 1) inside)
  (assert (>= total 1) total)
  (assert stopped)
  (val plain (FakeCoordinator "unchanged"))
  (val every (watching-link plain (/ tmp-path "plain") :watch False))
  (on-link every (polls 3))
  (assert (= (.beat-count plain) 3) plain.beats)
  (assert (not every.state.watch.running)))


(defk changed-beats [link coordinator]
  {:pre [(: link LinkRig) (: coordinator FakeCoordinator)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "待ちが「変わった」と答えた後の 1 拍: #(起こしの印が立ったか その拍で送った数 送った後に印が下りたか)。"
  (<- (ReadDesired))
  (<- woken bool (settle (fn [] link.state.watch.woken)))
  (<- sent int (counted (polls 1) coordinator))
  (val lowered (not link.state.watch.woken))
  (setv link.state.watch.closing True)
  #(woken sent lowered))


(deftest test-a-changed-watch-makes-the-next-tick-beat [tmp-path]
  ;; 待ちが「変わった」と答えたら、間隔の内でも次の拍で送る(desired の変化に拍 1 つの内に起きる)。
  (val coordinator (FakeCoordinator "changed"))
  (val link (watching-link coordinator tmp-path))
  (val got (on-link link (changed-beats link coordinator)))
  (val woken (get got 0))
  (val sent (get got 1))
  (val lowered (get got 2))
  (assert woken)
  (assert (= sent 1) sent)
  (assert lowered))


(defk dead-watch [link coordinator]
  {:pre [(: link LinkRig) (: coordinator FakeCoordinator)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "背景の task が落ちた後の 3 拍: #(落ちたと分かったか 3 拍で送った数)。"
  (<- (ReadDesired))
  (<- dead bool (settle (fn [] (and link.state.watch.failure (not link.state.watch.running)))))
  (<- sent int (counted (polls 3) coordinator))
  #(dead sent))


(deftest test-a-dead-watch-task-is-told-and-falls-back [tmp-path capsys]
  ;; 背景の task が思わぬ例外で止まれば理由を出し、拍は気づいて拍ごとの heartbeat に戻る(黙って待ちを失わない)。
  (val coordinator (FakeCoordinator "broken"))
  (val link (watching-link coordinator tmp-path))
  (val got (on-link link (dead-watch link coordinator)))
  (val dead (get got 0))
  (val sent (get got 1))
  (assert dead)
  (assert (= sent 3) sent)
  (val err (. (.readouterr capsys) err))
  (assert (in "壊れた待ち" err) err)
  (assert (in "拍ごとの heartbeat に戻ります" err) err))


(defk bell-of [read]
  {:pre [(: read DesiredJobs)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "宣言の読みが添えた呼び鈴の #(promise の番号 もう鳴ったか) — 番号が同じなら同じ呼び鈴(Future は読むたびに作り直される)。"
  (val changed read.changed)
  (if (is changed None)
      #(None False)
      (do (<- rung (promise-or-timeout changed 0.0))
          #(changed.promise-id (is rung True)))))


(defk coalesced-bells [link coordinator]
  {:pre [(: link LinkRig) (: coordinator FakeCoordinator)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "待ちが答えた後の読み 2 つ → 変化 → 読み 1 つの呼び鈴: #(1 つ目 2 つ目 変化の後の 1 つ目の呼び鈴 変化の後の読みの呼び鈴 重ねて鳴らした時の
   例外の有無)。"
  (<- (ReadDesired))
  (<- (settle (fn [] (and link.state.watch.running link.state.watch.confirmed))))
  (<- first DesiredJobs (ReadDesired))
  (<- second DesiredJobs (ReadDesired))
  (setv coordinator.gate-open True)
  (<- (settle (fn [] link.state.watch.woken)))
  (<- after-first tuple (bell-of first))
  ;; 同じ眠りの間に「変わった」がもう 1 度来た(鳴らす呼び鈴はもう手放している — 2 度鳴らして例外にならない)。
  (var repeated "")
  (try
    (<- (bell-rung link.state.watch))
    (except [error Exception]
      (:= repeated (repr error))))
  (<- third DesiredJobs (ReadDesired))
  (<- after-third tuple (bell-of third))
  (<- before-first tuple (bell-of second))
  (setv link.state.watch.closing True)
  #(before-first after-first after-third repeated))


(deftest test-a-read-carries-one-bell-until-a-change-rings-it-once [tmp-path]
  ;; 待ちを使える口の宣言の読みは、拍の間の眠りを起こす呼び鈴を添える。呼び鈴は鳴るまで拍をまたいで同じ物(拍ごとに作らない)で、
  ;; 待ちの「変わった」で 1 度だけ鳴り、次の読みは新しい呼び鈴を添える。同じ眠りの間に変化が重なっても鳴らすのは 1 度(#2692)。
  ;; 反例: 呼び鈴を拍ごとに作る形は 1 つ目と 2 つ目の番号が違う・鳴らした呼び鈴を手放さない形は 2 度目で例外。
  (val coordinator (FakeCoordinator "gated"))
  (val link (watching-link coordinator tmp-path))
  (val got (on-link link (coalesced-bells link coordinator)))
  (val second (get got 0))
  (val first (get got 1))
  (val third (get got 2))
  (val repeated (get got 3))
  (assert (is-not (get first 0) None) got)
  (assert (= (get first 0) (get second 0)) got)
  (assert (get first 1) got)
  (assert (!= (get third 0) (get first 0)) got)
  (assert (not (get third 1)) got)
  (assert (= repeated "") got))
