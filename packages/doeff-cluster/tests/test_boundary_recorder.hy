;; 境目の記録係(record_handlers.boundary-recorder)と再生の道具(replay_main)の検(ADR-DOE-CLUSTER-001 R5・R5b)。
;;
;;   1. boundary-recorder が Ask RECORD-MODE-KEY の答えで記録係を選ぶ: off = 空の組・record = 記録係 1 つ(置き場に届かなくても業務は
;;      止まらない)・replay = 外から渡した状態の effect-replayer・知らない mode = ValueError・mode(と replay の状態)の Ask に答えが無い
;;      (本番の宿に鍵が無い)= 答えの無い effect で落ちる(黙って off にしない)。
;;   2. recording-header が宿の契約の run-context と Program の置き場のキーと版を載せる。
;;   3. 記録 → 再生の通し: 本物の子の入口(job_entry service)が record の mode の Program を走らせ、置き場に届いた行と同じ Program の
;;      file で本物の再生の道具(replay_main)を走らせる。決定的な Program は分岐なしで出来事を使い切り、記録係より内側に非決定の handler を
;;      置いた Program は分岐として報告される(R5b)。
;;
;; 記録の検め方: 3 は record の枝そのもの(boundary-recorder → recording-handler → OtlpSink → OTLP/HTTP)を通す。置き場を memory の sink に
;; 差し替える口は boundary-recorder に無い(置き場は Ask RECORD-OTLP-KEY の URL だけ)ので、差し替えのために src に口を足さず、OTLP の
;; 受け口の fake をこの検の process の thread に立てて受ける。子の process で走らせるのは、置き場の口が残りの行を送るのが process の終わり
;; (EffectLog.close — atexit)だからで、本番の worker の子と同じ終わり方で記録が届くことも一緒に確かめる。1 の replay の枝の検は、
;; 渡す状態を作るためだけに test_effect_record と同じ形(EffectLog + MemorySink)で記録を作る(record の枝の検ではない)。
(require doeff-hy.macros [deftest defk deff <- val var])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import json)
(import os)
(import socket)
(import subprocess)
(import sys)
(import threading)
(import http.server [BaseHTTPRequestHandler ThreadingHTTPServer])
(import pathlib [Path])
(import doeff [Program with-handlers])
(import doeff_vm [UnhandledEffect])
(import doeff_core_effects.handlers [reader])
(import doeff_cluster.foundation.host_contract [HOST-CONTRACT environ-reader])
(import doeff_cluster.job_context [RunContext])
(import doeff_cluster.shared.protocol.program_codec [encode-program])
(import doeff_cluster.shared.core.remote_rules [program-sha])
(import doeff_cluster.foundation.process_versions [current-versions])
(import doeff_cluster.shared.intent.shared_model [ReadShared])
(import doeff_time [SimClock sim-time-handler])
(import tests.board_fake [board-handlers])
(import doeff_cluster.shared.core.record_model [read-recording])
(import doeff_cluster.shared.protocol.record_handlers [MemorySink EffectLog effect-recorder ReplayState replay-report
                                       boundary-recorder recording-header RECORD-MODE-KEY RECORD-OTLP-KEY REPLAY-STATE-KEY])
(import tests.fixtures.recorded_programs [ledger-program world-foundation ledger-translation-layer drifting-translation-layer])

(val ROOT (str (. (Path __file__) (resolve) parent parent)))   ; この package の根(見本の module は tests.* の名)
(val HY (str (/ (. (Path sys.executable) parent) "hy")))
(val SAMPLE-SHA (* "b" 64))
(val JOB "ledger")
(val NAMES #("a" "b"))
;; 子へ渡す環境から、この検の process の環境の同じ名を外す(記録の mode・業務の設定・宿の文脈は、検が渡す物だけにする)。
(val CHILD-OWN-KEYS #(RECORD-MODE-KEY RECORD-OTLP-KEY "LEDGER_ROUNDS" HOST-CONTRACT.program-env))


;; --- 補助 ---------------------------------------------------------------------------------------

(defk sample-context []
  {:pre [] :post [(: % RunContext)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "宿の契約の run-context の見本(世代の欄を全部埋める)。"
  (RunContext "http://coordinator" "w-1" "rev-9" "job-a" :instance "i-7" :attempt "2" :spec-hash "f00d" :placement "3"))


(defk read-rows [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "汎用の読み(ReadShared)を n 回出し、答えの行の数の合計を返す(row/0・row/1・row/2 を順に読む)。"
  (var total 0)
  (for [i (range n)]
    (<- rows dict (ReadShared (.format "row/{}" (% i 3))))
    (:= total (+ total (len rows))))
  total)


(defk closed-port-url []
  {:pre [] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "誰も待っていない番地の URL(port を 1 つ取ってすぐ閉じる — 送りは接続を断られる)。"
  (val probe (socket.socket))
  (.bind probe #("127.0.0.1" 0))
  (val port (get (.getsockname probe) 1))
  (.close probe)
  (.format "http://127.0.0.1:{}" port))


(defk unanswered [program]
  {:pre [(: program Program)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "program を走らせ、答えの無い effect で落ちたらその文を返す(落ちなければ AssertionError)。"
  (var message None)
  (try
    (<- program)
    (except [error UnhandledEffect]
      (:= message (str error))))
  (assert (is-not message None) "答えの無い effect で落ちるはず")
  message)


(defk open-inbox [lines]
  {:pre [(: lines list)] :post [(: % ThreadingHTTPServer)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "OpenTelemetry の collector の OTLP/HTTP の log の口(POST /v1/logs)の fake を thread で立てる。届いた log record の body(記録の行の
   JSON の文字列)を lines に届いた順で積む。"
  (defclass Inbox [BaseHTTPRequestHandler]
    (deff log-message [self format #* args]  ; defk にできない: http.server が呼ぶ素の callback
      {:pre [(: self BaseHTTPRequestHandler) (: format str) (: args tuple)] :post [(: % (type None))] :tags {:context "doeff-cluster-test" :role "foundation"}}
      None)
    (deff do-POST [self]  ; defk にできない: http.server が呼ぶ素の callback
      {:pre [(: self BaseHTTPRequestHandler)] :post [(: % (type None))] :tags {:context "doeff-cluster-test" :role "foundation"}}
      (let [body (json.loads (.read self.rfile (int (get self.headers "Content-Length"))))]
        (when (= self.path "/v1/logs")
          (.extend lines (lfor resource (get body "resourceLogs") scope (get resource "scopeLogs") record (get scope "logRecords")
                               (get record "body" "stringValue"))))
        (.send-response self (if (= self.path "/v1/logs") 200 404))
        (.send-header self "Content-Length" "0")
        (.end-headers self)
        None)))
  (val server (ThreadingHTTPServer #("127.0.0.1" 0) Inbox))
  (.start (threading.Thread :target server.serve-forever :daemon True))
  server)


(defk child [module environ #* args]
  {:pre [(: module str) (: environ dict) (: args tuple)] :post [(: % subprocess.CompletedProcess)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "hy -m <module> <args…> を、この検の process の環境から CHILD-OWN-KEYS と宿の文脈(DOEFF_WORKER_*)を外して environ を重ねた環境で起こす。"
  (val base (dfor #(k v) (.items os.environ) :if (not (or (in k CHILD-OWN-KEYS) (.startswith k "DOEFF_WORKER_"))) k v))
  (val words (lfor a args :if (isinstance a str) a))
  (assert (= (len words) (len args)) #("子 process の引数は文字列だけ" args))
  (subprocess.run [HY "-m" module #* words] :cwd ROOT :env (| base {"PYTHONPATH" ROOT} environ)
                  :capture-output True :text True :timeout 120))


(defrecord Cycle
  "記録と再生の 1 巡の結果。recorded / replayed = 子の process の終わり・lines = 置き場に届いた記録の行・sha = Program の置き場のキー・
   report = 再生の道具が書いた報告(JSON — 書かれなければ空)。"
  (#^ subprocess.CompletedProcess recorded)
  (#^ list lines)
  (#^ str sha)
  (#^ subprocess.CompletedProcess replayed)
  (#^ dict report))


(defk record-and-replay [tmp-path translation]
  {:pre [(: tmp-path Path) (: translation Callable)] :post [(: % Cycle)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "ledger-program(土台 world-foundation・翻訳の組 translation)を worker の cache と同じ形の file(<sha>.json)に詰め、本物の子の入口で
   record の mode で走らせ(宣言の :environ と宿の文脈を環境変数で渡す)、置き場の fake に届いた行と同じ file で本物の再生の道具を走らせる。
   再生の子には記録の mode も業務の設定も渡さない(境目の記録係が記録から答える)。"
  (val texts [])
  (<- inbox ThreadingHTTPServer (open-inbox texts))
  (val blob (encode-program (ledger-program world-foundation translation NAMES)))
  (val sha (program-sha blob))
  (val programs (/ tmp-path "programs"))
  (.mkdir programs)
  (val path (/ programs (+ sha ".json")))
  (.write-text path (json.dumps {"blob" blob "versions" (current-versions)}) :encoding "utf-8")
  (<- recorded subprocess.CompletedProcess
      (child "doeff_cluster.job_entry"
             {RECORD-MODE-KEY "record"
              RECORD-OTLP-KEY (.format "http://127.0.0.1:{}" (get inbox.server-address 1))
              "LEDGER_ROUNDS" "2"
              "DOEFF_WORKER_JOB" JOB
              "DOEFF_WORKER_NAME" "w-rec"
              "DOEFF_WORKER_INSTANCE" "i-1"
              HOST-CONTRACT.program-env (str path)}
             "service" "--identity" (* "0" 16) "--program" (str path)))
  (.shutdown inbox)
  (val recording (/ tmp-path "recording.jsonl"))
  (.write-text recording (.join "" (gfor text texts (+ text "\n"))) :encoding "utf-8")
  (val out (/ tmp-path "report.json"))
  (<- replayed subprocess.CompletedProcess
      (child "doeff_cluster.shared.entry.replay_main" {} "--recording" (str recording) "--program" (str path) "--out" (str out)))
  (Cycle :recorded recorded
         :lines (lfor text texts (json.loads text))
         :sha sha
         :replayed replayed
         :report (if (.exists out) (json.loads (.read-text out :encoding "utf-8")) {})))


;; --- 1. mode ごとの記録係 ---------------------------------------------------------------------------

(deftest test-off-mode-places-no-recorder
  (<- handlers list (with-handlers [(reader {RECORD-MODE-KEY "off"})] (boundary-recorder HOST-CONTRACT)))
  (assert (= handlers []) handlers))


(deftest test-record-mode-places-one-recorder-and-an-unreachable-store-does-not-stop-the-business [capsys]
  ;; 置き場の URL・run-context・Program の path は宿と environ が Ask で答える物(ここでは reader)。URL は誰も待っていない番地。
  (<- url str (closed-port-url))
  (<- ctx RunContext (sample-context))
  (<- handlers list (with-handlers [(reader {RECORD-MODE-KEY "record" RECORD-OTLP-KEY url
                                             HOST-CONTRACT.run-context-key ctx
                                             HOST-CONTRACT.program-key (+ "/state/programs/" SAMPLE-SHA ".json")
                                             HOST-CONTRACT.versions-key {"doeff" "9.9.9"}})]
                                   (boundary-recorder HOST-CONTRACT)))
  (assert (= (len handlers) 1) handlers)
  ;; 置き場の口は 500 行たまると送る — 600 の読みで送りを 1 度は試みて断られ、業務の答えはそのまま返る(記録は貯め続ける)。
  (<- total int (with-handlers [(sim-time-handler :clock (SimClock)) #* (board-handlers {"row/0" 1 "row/1" 2}) #* handlers] (read-rows 600)))
  (assert (= total 400) total)
  (val err (. (.readouterr capsys) err))
  (assert (in "recorder: job-a の effect を記録します" err) err)
  (assert (in url err) err)
  (assert (in "記録の置き場に送れない" err) err))


(deftest test-replay-mode-places-the-replayer-of-the-state-given-from-outside
  ;; 渡す状態を作るための記録(record の枝の検ではない — 頭の註)。
  (val sink (MemorySink))
  (val log (EffectLog sink {"service" "rows" "run" "r1"} :strict True))
  (<- recorded int (with-handlers [(sim-time-handler :clock (SimClock)) #* (board-handlers {"row/0" 1 "row/1" 2}) (effect-recorder log)] (read-rows 4)))
  (val state (ReplayState (read-recording sink.lines)))
  (<- handlers list (with-handlers [(reader {RECORD-MODE-KEY "replay" REPLAY-STATE-KEY state})] (boundary-recorder HOST-CONTRACT)))
  (assert (= (len handlers) 1) handlers)
  ;; 外の世界(fake の盤)を置かずに、記録の答えだけで同じ答えになり、渡した状態の出来事を使い切る。
  (<- replayed int (with-handlers handlers (read-rows 4)))
  (assert (= replayed recorded) #(replayed recorded))
  (assert state.finished)
  (val report (replay-report state "program-returned"))
  (assert (= (get report "consumed") (get report "events") 8) report))


(deftest test-an-unknown-mode-is-refused-with-the-known-modes
  (var refused None)
  (try
    (<- (with-handlers [(reader {RECORD-MODE-KEY "sideways"})] (boundary-recorder HOST-CONTRACT)))
    (except [error ValueError]
      (:= refused (str error))))
  (assert (is-not refused None) "知らない mode は ValueError")
  (for [word [RECORD-MODE-KEY "off" "record" "replay" "sideways"]]
    (assert (in word refused) refused)))


(deftest test-without-an-answer-the-recorder-choice-is-an-unanswered-effect [monkeypatch]
  ;; 本番の土台が environ を読む handler((environ-reader) — 環境に無い鍵は外へ通す)で答える形。宣言の :environ に RECORD-MODE-KEY が
  ;; 無ければ、黙って off にせず答えの無い effect で落ちる。
  (.delenv monkeypatch RECORD-MODE-KEY :raising False)
  (<- no-mode str (unanswered (with-handlers [(environ-reader)] (boundary-recorder HOST-CONTRACT))))
  (assert (in RECORD-MODE-KEY no-mode) no-mode)
  ;; replay を選んでも、状態(REPLAY-STATE-KEY)に答えるのは再生の道具だけ — 本番の宿で replay を選ぶと落ちる。
  (.setenv monkeypatch RECORD-MODE-KEY "replay")
  (<- no-state str (unanswered (with-handlers [(environ-reader)] (boundary-recorder HOST-CONTRACT))))
  (assert (in REPLAY-STATE-KEY no-state) no-state))


;; --- 2. 記録の header -----------------------------------------------------------------------------

(deftest test-the-recording-header-carries-the-run-context-and-the-program-key-and-versions
  (<- ctx RunContext (sample-context))
  ;; 版は引数で受ける(宿の契約の Ask versions-key の答えを記録係が渡す — 判断の関数は process の版を自分で読まない #2345)。
  (val versions {"doeff" "9.9.9" "envKey" "k-1"})
  (<- header dict (recording-header ctx (+ "/state/programs/" SAMPLE-SHA ".json") versions))
  (assert (= header {"worker" "w-1" "instance" "i-7" "attempt" "2" "specHash" "f00d" "placement" "3" "revision" "rev-9"
                     "program" SAMPLE-SHA "versions" versions})
          header)
  ;; 宿が Program の path を渡していない(空)なら program も空(記録は Program の中身を持たない — キーだけ)。
  (<- bare dict (recording-header ctx "" versions))
  (assert (= (get bare "program") "") bare))


;; --- 3. 記録 → 再生の通し ---------------------------------------------------------------------------

(deftest test-a-deterministic-program-recorded-by-the-child-entry-replays-without-divergence [tmp-path]
  (<- cycle Cycle (record-and-replay tmp-path ledger-translation-layer))
  ;; 記録: 子の入口は Program を解いて走らせるだけ(記録係は Program の中の境目)。process の終わりに置き場へ run の行と出来事が届く。
  (assert (= cycle.recorded.returncode 0) cycle.recorded.stderr)
  (assert (in "が終わった: {'a': 2, 'b': 2, 'ticket': 1}" cycle.recorded.stderr) cycle.recorded.stderr)
  (val header (next (gfor line cycle.lines :if (= (get line "k") "run") line) None))
  (assert (is-not header None) cycle.lines)
  (assert (= #((get header "program") (get header "service") (get header "worker") (get header "instance"))
             #(cycle.sha JOB "w-rec" "i-1"))
          header)
  (assert (= (get header "versions") (current-versions)) header)
  ;; 記録係は翻訳の後の汎用の effect だけを見る: 業務の effect(CountVisit・DrawTicket)も、記録係を選ぶ前の mode の Ask も載らない。
  (val types (sfor line cycle.lines :if (in "ty" line) (get line "ty")))
  (assert (= types #{"doeff_core_effects.effects:Ask" "doeff_cluster.shared.intent.shared_model:ReadShared" "doeff_cluster.shared.intent.shared_model:WriteShared"})
          types)
  (val asked (lfor line cycle.lines :if (= (.get line "ty") "doeff_core_effects.effects:Ask") (get line "a" "key")))
  (assert (= asked ["LEDGER_ROUNDS"]) asked)
  ;; 再生: 同じ Program の file を再生の道具が解き、境目の記録係が記録から答える。再生の子には LEDGER_ROUNDS も記録の mode も無い
  ;; (記録係が答えなければ業務の Ask は答えが無く落ちる)。分岐なしで出来事を使い切る。
  (assert (= cycle.replayed.returncode 0) cycle.replayed.stderr)
  (val report cycle.report)
  (assert (in (get report "end") #("program-returned" "finished")) report)
  (assert (is (get report "divergence") None) report)
  (assert (get report "identical") report)
  (assert (= (get report "consumed") (get report "events") 22) report)
  (assert (= (get report "matched") {"read" 6 "live" 0 "decision" 0 "output" 5}) report)
  (assert (= (get report "program") cycle.sha) report))


(deftest test-a-nondeterministic-handler-inside-the-recorder-is-reported-as-a-divergence [tmp-path]
  ;; R5b の反例: 札を乱数で作る handler を記録係より内側(翻訳の組)に置く。札は記録に載らず、再生では違う札の行を読みに行く。
  (<- cycle Cycle (record-and-replay tmp-path drifting-translation-layer))
  (assert (= cycle.recorded.returncode 0) cycle.recorded.stderr)
  (assert (= cycle.replayed.returncode 0) cycle.replayed.stderr)
  (val report cycle.report)
  (assert (= (get report "end") "diverged") report)
  (assert (not (get report "identical")) report)
  (val divergence (get report "divergence"))
  (assert (= (get divergence "reason") "問いが記録と食い違った") divergence)
  (val recorded-prefix (get divergence "expected" "args" "prefix"))
  (val replayed-prefix (get divergence "actual" "args" "prefix"))
  (assert (and (.startswith recorded-prefix "visits/ticket-") (.startswith replayed-prefix "visits/ticket-")) divergence)
  (assert (!= recorded-prefix replayed-prefix) divergence)
  ;; 札を引く前の決定的な部分(周回の読み書き)は一致し、分岐はその後の地点で止まる(残りの出来事は使わない)。
  (assert (= (get report "matched") {"read" 5 "live" 0 "decision" 0 "output" 4}) report)
  (assert (< (get report "consumed") (get report "events")) report))
