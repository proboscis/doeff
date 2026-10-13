;;; DOEFF210(Hy の program は壁の時計で眠らない)の検体 — program(defk・defp)の中の time.sleep / asyncio.sleep を鳴らし、
;;; handler の実装・時間の効果・0 秒の譲り・program でない定義は鳴らさない。
;;; 行末の「; 鳴る」が鳴るべき行の印: tests/program_sleep.rs が、印の行の集合と linter の当たりの行の集合を突き合わせる
;;; (規則を外す・除外を外す・除外を足すと、どれも集合が合わなくなって赤)。この file は実行しない(読むだけの検体)。
(require doeff-hy.macros [defk defp deff defhandler <- val var])
(import asyncio)
(import time)
(import time :as clock)
(import asyncio [sleep :as rest])
(import doeff [Await])
(import doeff_time [Delay DelayEffect])


;; ---------------------------------------------------------------------------
;; 鳴る: program が handler を迂回して壁の時計で待つ

(defk retry-after-a-pause [attempt]
  {:pre [(: attempt int)] :post [(: % None)] :tags {:context "sample" :role "program"}}
  "再試行の間を壁の時計で待つ program。"
  (time.sleep 1.0)  ; 鳴る
  None)


(defk poll-until-ready [probe]
  {:pre [(callable probe)] :post [(: % bool)] :tags {:context "sample" :role "program"}}
  "asyncio の眠りを Await に包んでも、待つのは壁の時計。"
  (var ready False)
  (while (not ready)
    (<- (Await (asyncio.sleep 0.5)))  ; 鳴る
    (:= ready (probe)))
  ready)


(defk waits-through-aliases []
  {:pre [] :post [(: % None)] :tags {:context "sample" :role "program"}}
  "import の別名と、名を並べた import で束ねた名。"
  (clock.sleep 0.2)  ; 鳴る
  (<- (Await (rest 0.2)))  ; 鳴る
  None)


(defp settle-then-go
  {:post [(: % None)] :tags {:context "sample" :role "program"}}
  (time.sleep 0.3)  ; 鳴る
  None)


(defk sleeps-inside-a-nested-callable [run-now]
  {:pre [(callable run-now)] :post [(: % None)] :tags {:context "sample" :role "program"}}
  "program の本体の中の fn は、字面で program の内側。"
  (run-now (fn [] (time.sleep 0.1)))  ; 鳴る
  None)


(defk program-without-tags [seconds]
  {:pre [(: seconds float)] :post [(: % None)]}
  "役を名乗らない定義は program(汎用の I/O の役は、名乗った定義だけ)。"
  (time.sleep seconds)  ; 鳴る
  None)


;; ---------------------------------------------------------------------------
;; 鳴らない: 時間の効果で待つ program

(defk waits-by-the-time-effect [seconds]
  {:pre [(: seconds float)] :post [(: % None)] :tags {:context "sample" :role "program"}}
  "1 秒が何であるかは、呼び手が置く時間の handler が決める。"
  (<- (Delay seconds))
  None)


(defk yields-once []
  {:pre [] :post [(: % None)] :tags {:context "sample" :role "program"}}
  "秒が literal の 0 の呼びは、時間を待たずに 1 度譲るだけ。"
  (<- (Await (asyncio.sleep 0)))
  (time.sleep 0)
  None)


;; ---------------------------------------------------------------------------
;; 鳴らない: handler の実装(壁の時計で待つのは handler の責務)

(defhandler wall-clock-time
  (DelayEffect [seconds]
    (time.sleep seconds)
    (resume None)))


(defk raw-time-handler [effect k]
  {:pre [] :post [(: % "handler の答え")] :tags {:context "sample" :role "protocol"}}
  "(effect k) を受ける定義は handler の実装。"
  (time.sleep 0.1)
  (<- answer (k None))
  answer)


(defk settle-the-terminal [pane]
  {:pre [(: pane str)] :post [(: % None)] :tags {:context "sample" :role "foundation"}}
  "handler の節からだけ呼ばれる helper — 本物の端末が貼り付けを受け終える実時間を待つ。汎用の I/O の役を自分の辞書で名乗る。"
  (time.sleep 1.0)
  None)


;; ---------------------------------------------------------------------------
;; 鳴らない: program でない定義・読まない form

(deff blocking-callback [seconds]  ; defk にできない: threading.Thread が別の thread で呼ぶ callback
  {:pre [(: seconds float)] :post [(: % None)] :tags {:context "sample" :role "entry"}}
  "素の callable は効果を出せない — 汎用の I/O の役を名乗らなくても program に数えない(deff の是非は DOEFF111・203 が持つ)。"
  (time.sleep seconds)
  None)


(defk mentions-a-sleep-without-calling-it []
  {:pre [] :post [(: % str)] :tags {:context "sample" :role "judgment"}}
  ;; (time.sleep 9) — 註の中
  #_(time.sleep 9)
  (val quoted '(time.sleep 9))
  "(time.sleep 9)")
