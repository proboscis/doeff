;;; 記録 ON の模擬の検 — 境目の記録係(record_handlers.boundary-recorder)が置き場へ送る記録を、HTTP の答え手の差し替えだけで受ける。
;;;
;;; 記録係は置き場への送りを HTTP の effect(HttpRequest)で出す(本番は土台の http-production-handler が答える)。ここでは同じ Program
;;; (recorded-inside — 記録係の組を選んで業務の本体を包む)を、外の世界の handler だけを模擬の物に替えて回す:
;;;
;;;   置き場     stand-in-store — OTLP/HTTP の log の口(POST …/v1/logs)の代役。届いた log record の本文(記録の行)を貯め、検の effect
;;;              ReceivedLines に貯めた行を答える。dropping で「受けたふりをして捨てる」送りを選べる(失敗ケース)。
;;;   時計       sim-time-handler(仮想の時計 — Delay は眠らない)
;;;   宿と設定   reader(記録の mode・置き場の URL・宿の契約の run-context・Program の置き場のキー・版・業務の設定)
;;;
;;; 記録した行を read-recording で読み、同じ Program を記録係の replay の枝(外の世界の handler なし)で再生して違いが無いことを見る。
;;; 失敗ケース: 置き場が送りを捨てると記録が欠け、読めない(run の行が無い)か、再生が分岐する(問いが記録と食い違う)。
;;;
;;; 走らせ方 = この dir の conftest.py(`uv run pytest packages/doeff-cluster/src/doeff_cluster/sim/test_recording_on_sim.hy`)。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defenum])
(import enum [StrEnum])
(import json)
(import doeff [with-handlers])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.handlers [reader state])
(import doeff_core_effects.http_effects [HttpRequest HttpResponse])
(import doeff_time [Delay GetTime SimClock sim-time-handler])
(import doeff_cluster.foundation.host_contract [HOST-CONTRACT])
(import doeff_cluster.job_context [RunContext])
(import doeff_cluster.foundation.record_log [read-recording ReplayDiverged])
(import doeff_cluster.foundation.record_handlers [boundary-recorder ReplayState replay-report
                                                  RECORD-MODE-KEY RECORD-OTLP-KEY REPLAY-STATE-KEY])

;; 置き場の代役の宛先(名前だけ — 誰も待ち受けない。送りは代役の handler が受ける)。
(val STORE-URL "http://records.sim")
;; 業務の本体が読む設定の数。置き場の口は 500 行ごとに送るので、1200 の読みで送りは 3 回(500・500・終わりの残り)。
(val ROWS 1200)


;; 置き場の代役が捨てる送り: KEEP-ALL = 全部受ける・DROP-ALL = 全部を受けたふりをして捨てる・DROP-SECOND = 2 回目の送りだけ捨てる。
(defenum Dropping KEEP-ALL DROP-ALL DROP-SECOND)


(defeffect ReceivedLines
  "検の effect: 置き場の代役が受けて貯めた記録の行(dict の tuple — 記録の行は JSON)を答える。"
  {:fields []
   :answer tuple
   :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler stand-in-store [#^ Dropping dropping]
  ;; 記録の置き場の代役(OTLP/HTTP の log の口)。本物の口と同じく本文を JSON として受け、log record の本文(記録の行の JSON)を貯める。
  ;; dropping の送りは 200 を返して捨てる(送り手からは届いたように見える)。
  ;; 引数に残す理由: 捨て方は検ごとに決まる代役の作りで、業務の Program が Ask で読む設定ではない(読む Ask を足すと記録に載る)。
  (session var posts 0)
  (session var lines #())
  (HttpRequest [method url body]
    (:= posts (+ posts 1))
    (val wire (json.loads (json.dumps body)))
    (val received (tuple (gfor resource (get wire "resourceLogs") scope (get resource "scopeLogs") record (get scope "logRecords")
                               (json.loads (get record "body" "stringValue")))))
    (val dropped (match dropping
                   Dropping.KEEP-ALL False
                   Dropping.DROP-ALL True
                   Dropping.DROP-SECOND (= posts 2)))
    (when (and (= method "POST") (= url (+ STORE-URL "/v1/logs")) (not dropped))
      (:= lines (+ lines received)))
    (resume (HttpResponse 200 {} b"" "" url 0.0)))
  (ReceivedLines []
    (resume lines)))


(defk read-settings [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "業務の本体: 設定 row/0 … row/<n-1> を順に読んで足し、100 読むごとに仮想の時計で 1 秒待ち、最後に時刻を読む。答え = 読んだ値の合計。"
  (var total 0)
  (for [i (range n)]
    (<- value int (Ask (.format "row/{}" i)))
    (:= total (+ total value))
    (when (= (% i 100) 99)
      (<- (Delay 1.0))))
  (<- (GetTime))
  total)


(defk recorded-inside [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "記録の mode(Ask)で境目の記録係の組を選び、業務の本体を包んで走らせる — 記録でも再生でも同じ Program。"
  (<- recorder list (boundary-recorder HOST-CONTRACT))
  (<- total int (with-handlers recorder (read-settings n)))
  total)


(defk record-then-collect [n]
  {:pre [(: n int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "記録の mode で 1 run 回し、置き場の代役が受けた行を返す(置き場の代役の内側で呼ぶ)。宿と設定と仮想の時計は ここで被せる。"
  (val settings (dfor i (range n) (.format "row/{}" i) (% i 7)))
  (val ctx (RunContext "http://coordinator.sim" "sim-worker" "rev-1" "settings-job" :instance "i-1" :attempt "1"))
  (<- (with-handlers [(sim-time-handler :clock (SimClock))
                      (reader (| settings {RECORD-MODE-KEY "record" RECORD-OTLP-KEY STORE-URL
                                           HOST-CONTRACT.run-context-key ctx
                                           HOST-CONTRACT.program-key "/state/programs/sim.json"
                                           HOST-CONTRACT.versions-key {"doeff" "sim"}}))]
                     (recorded-inside n)))
  (<- lines tuple (ReceivedLines))
  lines)


(defk recorded-lines [dropping]
  {:pre [(: dropping Dropping)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "置き場の代役(dropping の捨て方 — 貯めた行は session の値なので外側に state)の下で記録の 1 run を回し、置き場に残った行を返す。"
  (<- lines tuple (with-handlers [(state) (stand-in-store dropping)] (record-then-collect ROWS)))
  lines)


(defk replayed-report [lines]
  {:pre [(: lines tuple)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "置き場に残った行を read-recording で読み、同じ Program を記録係の replay の枝で(外の世界の handler なしで)再生した報告。
   分岐で止まった再生も報告にする(end = diverged)。"
  (val state (ReplayState (read-recording (list lines))))
  (var end "program-returned")
  (try
    (<- (with-handlers [(reader {RECORD-MODE-KEY "replay" REPLAY-STATE-KEY state})] (recorded-inside ROWS)))
    (except [ReplayDiverged]
      (:= end "diverged")))
  (replay-report state end))


;; --- 記録 → 置き場の代役 → 再生 ------------------------------------------------------------------------------

(deftest test-a-recording-sent-through-the-http-effect-replays-without-difference
  ;; 置き場の代役が全部受ける: run の行と全部の出来事が届き、再生は分岐なしで出来事を使い切る(違い 0)。
  (<- lines tuple (recorded-lines Dropping.KEEP-ALL))
  (assert (= (sum (gfor line lines (= (get line "k") "run"))) 1) (cut lines 0 3))
  (<- report dict (replayed-report lines))
  (assert (= (get report "end") "program-returned") report)
  (assert (is (get report "divergence") None) report)
  (assert (get report "identical") report)
  (assert (= (get report "consumed") (get report "events")) report)
  ;; 読み 1213(設定 1200・時計 13 = 待ち 12 と時刻 1 — 時計の答えも記録から返す読み)— 置き場の代役が受けた出来事を全部突き合わせた。
  (assert (= (get report "matched") {"read" (+ ROWS 13) "live" 0 "decision" 0 "output" 0}) report))


;; --- 失敗ケース: 送りを捨てる置き場 -------------------------------------------------------------------------------

(deftest test-a-store-that-drops-every-post-leaves-an-unreadable-recording
  ;; 受けたふりをして全部捨てる: 記録係からは届いたように見えるが、置き場には 1 行も無く、記録として読めない。
  (<- lines tuple (recorded-lines Dropping.DROP-ALL))
  (assert (= lines #()) (len lines))
  (var refused None)
  (try
    (read-recording (list lines))
    (except [error ValueError]
      (:= refused (str error))))
  (assert (= refused "記録に run の行が無い") refused))


(deftest test-a-store-that-drops-one-post-makes-the-replay-diverge
  ;; 2 回目の送り(途中の出来事の塊)だけを捨てる: 記録は読めるが出来事が欠け、再生は欠けた所で記録と食い違って止まる。
  (<- lines tuple (recorded-lines Dropping.DROP-SECOND))
  (<- report dict (replayed-report lines))
  (assert (= (get report "end") "diverged") report)
  (assert (not (get report "identical")) report)
  (assert (= (get report "divergence" "reason") "問いが記録と食い違った") report)
  (assert (< (get report "consumed") (get report "events")) report))
