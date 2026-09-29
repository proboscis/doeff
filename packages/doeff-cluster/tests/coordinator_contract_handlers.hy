;;; coordinator に話す 3 つの effect の族(共有の保存・計器の報告・readiness の報告)の契約テストの解釈器(composition root)—
;;; 同じ契約の Program を、handler だけ替えて走らせる。
;;;
;;;   shared-memory     fake: shared-memory(同じ process の dict)
;;;   shared-http       本物: shared-http(SharedClient → coordinator の /board・/leases)
;;;   metrics-memory    fake: metrics-memory(list に記録)
;;;   metrics-http      本物: metrics-http(ServiceReportClient → POST /resources/Service/<名>/metrics)
;;;   readiness-memory  fake: readiness-memory(list に記録)
;;;   readiness-http    本物: readiness-http(ServiceReportClient → POST /resources/Service/<名>/readiness)
;;;
;;; 本物の側の相手は実の coordinator の process ではなく、test_detached.hy の coordinator の組と同じ MemoryCoordinator(本物の
;;; api_policy.respond / tick を httpx の MockTransport の後ろに置く)。時刻は両方とも 1 つの仮想の時計(SimClock)で、fake の lease は
;;; その時計を GetTime で・MemoryCoordinator は同じ時計を直に読む(契約の Program の Delay が両方の時刻を進める)。
;;;
;;; 契約の Program が coordinator の側の真実を読む口は検の effect だけ(読む手段だけを解釈器ごとに替える):
;;;   BoardSeen          → 盤の行 {鍵: 値}(fake = dict・本物 = MemoryCoordinator の状態の盤)
;;;   ReportSeen kind    → 最後に残った報告(metrics = 計器の dict・readiness = {ready reason role})か None
;;;   SetReachable up    → coordinator へ届くか(本物 = transport が ConnectError を上げる・fake は網を持たないので何もしない)
;;; 使い手は conftest.py の doeff_interpreter(deftest の :interpreters の名 → INTERPRETERS)。
(require doeff-hy.macros [defk deff defhandler <- val])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import functools [partial])
(import httpx)
(import doeff [EffectBase Program with_handlers])
(import doeff_time [SimClock sim-time-handler])
(import doeff_cluster.shared_handlers [shared-memory shared-http SharedClient])
(import doeff_cluster.metrics_handlers [metrics-memory metrics-http])
(import doeff_cluster.readiness_handlers [readiness-memory readiness-http])
(import doeff_cluster.report_client [ServiceReportClient])
(import tests.detached_rig [MemoryCoordinator])
(import tests.program_rows [SAMPLE-RUN])

(val COORDINATOR "http://coordinator")
;; 報告の送り手が名乗る Service(本物の側では coordinator に宣言してある — 無い Service の報告は coordinator が 404 で断る)。
(val SERVICE "contract-service")
(val SERVICE-SPEC {"revision" "r1" "needs" ["net"] "run" SAMPLE-RUN})
(val REFUSED "[Errno 111] Connection refused")
(val METRICS "metrics")
(val READINESS "readiness")


(defclass [(dataclass :frozen True)] BoardSeen [EffectBase]
  "coordinator の側の盤の行 {鍵: 値}(検の effect — 契約の Program が真実を読む口)。")

(defclass [(dataclass :frozen True)] ReportSeen [EffectBase]
  "coordinator の側に最後に残った kind(metrics | readiness)の報告(検の effect)。"
  (#^ str kind))

(defclass [(dataclass :frozen True)] SetReachable [EffectBase]
  "coordinator へ届くかを切り替える(検の effect)。"
  (#^ bool up))


(defhandler memory-side [#^ dict store #^ list reports]
  ;; 引数に残す理由: 真実は fake の handler と同じ dict / list そのもの(組み立てが 1 つ作って両方へ渡す — Ask で運ぶ設定ではない)。
  ;; fake の側の真実: 共有の保存の dict と、報告の handler が積む list(1 つの解釈器の報告の族は 1 つ)。fake は網を持たない。
  (BoardSeen [] (resume (dict store)))
  (ReportSeen [kind] (resume (if reports (get reports -1) None)))
  (SetReachable [up] (resume None)))


(deff latest-report [#^ MemoryCoordinator coordinator #^ str kind]  ; defk にできない: handler の節が Program の外の状態(MemoryCoordinator)から組む純粋な読み
  {:pre [(: coordinator MemoryCoordinator) (in kind #(METRICS READINESS))] :post [(: % (| dict None))]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "coordinator の状態に最後に残った kind の報告を、effect の側の形(metrics = 計器の dict・readiness = {ready reason role})にする。"
  (let [reports (.get (getattr coordinator.state kind) SERVICE #())]
    (cond
      (not reports) None
      (= kind METRICS) (get reports -1 "metrics")
      True (dfor field #("ready" "reason" "role") field (get reports -1 field)))))


(defhandler coordinator-side [#^ MemoryCoordinator coordinator #^ dict line]
  ;; 引数に残す理由: 真実は transport の後ろの MemoryCoordinator と線そのもの(組み立てが 1 つ作って transport と共有する)。
  ;; 本物の側の真実: MemoryCoordinator の状態。line = {"up": bool}(transport が読む)。
  (BoardSeen [] (resume (dict coordinator.state.board)))
  (ReportSeen [kind] (resume (latest-report coordinator kind)))
  (SetReachable [up] (.update line {"up" up}) (resume None)))


(deff line-answer [#^ MemoryCoordinator coordinator #^ dict line request]  ; defk にできない: httpx の MockTransport が要求ごとに同期で呼ぶ callback
  {:pre [(: coordinator MemoryCoordinator) (: line dict) (: request httpx.Request)] :post [(: % httpx.Response)]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "coordinator への線: 届く間は MemoryCoordinator が答え、切れている間は接続できない(httpx.ConnectError)。"
  (if (get line "up")
      (.handle coordinator request)
      (raise (httpx.ConnectError REFUSED :request request))))


(deff declared-coordinator [#^ SimClock clock]  ; defk にできない: 組み立て(Program を走らせる前)が呼ぶ Program の外の準備
  {:pre [(: clock SimClock)] :post [(: % MemoryCoordinator)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "Service SERVICE を宣言した MemoryCoordinator(時計 = clock)。宣言は運用者と同じ口(POST /resources/Service)で送る。"
  (let [coordinator (MemoryCoordinator clock)
        response (.handle coordinator (httpx.Request "POST" (+ COORDINATOR "/resources/Service")
                                                     :json {"name" SERVICE "spec" SERVICE-SPEC}
                                                     :headers {"x-actor" "c-contract"}))]
    (assert (= response.status-code 201) response.text)
    coordinator))


(defk under-memory [make-handler program]
  {:pre [(: make-handler Callable) (: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "fake の下で program を走らせる。make-handler = (fn [store reports] fake の handler)。外から順に: 仮想の時計・真実の口・fake。"
  (val store {})
  (val reports [])
  (<- answer (with_handlers [(sim-time-handler :clock (SimClock)) (memory-side store reports) (make-handler store reports)] program))
  answer)


(defk under-coordinator [make-handler program]
  {:pre [(: make-handler Callable) (: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "本物の http の handler の下で program を走らせる。make-handler = (fn [transport] 本物の handler)。相手は Service を宣言した
   MemoryCoordinator(仮想の時計を契約の Program と共有する)。"
  (val clock (SimClock))
  (val coordinator (declared-coordinator clock))
  (val line {"up" True})
  (val transport (httpx.MockTransport (partial line-answer coordinator line)))
  (<- answer (with_handlers [(sim-time-handler :clock clock) (coordinator-side coordinator line) (make-handler transport)] program))
  answer)


(deff report-client [transport]  ; defk にできない: 組み立ての表(INTERPRETERS)が本物の handler を作る時に呼ぶ Program の外の準備
  {:pre [(: transport httpx.MockTransport)] :post [(: % ServiceReportClient)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "SERVICE の報告の口(送り手の worker と版は固定)。"
  (ServiceReportClient COORDINATOR SERVICE "w1" "r1" :transport transport))


(val INTERPRETERS
  {"shared-memory" (partial under-memory (fn [store reports] (shared-memory store)))
   "shared-http" (partial under-coordinator (fn [transport] (shared-http (SharedClient COORDINATOR :transport transport))))
   "metrics-memory" (partial under-memory (fn [store reports] (metrics-memory reports)))
   "metrics-http" (partial under-coordinator (fn [transport] (metrics-http (report-client transport))))
   "readiness-memory" (partial under-memory (fn [store reports] (readiness-memory reports)))
   "readiness-http" (partial under-coordinator (fn [transport] (readiness-http (report-client transport))))})
