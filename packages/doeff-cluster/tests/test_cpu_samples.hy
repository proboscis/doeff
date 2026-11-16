;;; foundation/cpu_samples(主の thread の CPU を関数ごとに・GC の回収の回数と秒を、窓ごとに fd 2 へ書く口)の検。
;;; 口は daemon の thread と合図の受け手と GC の callback で書く本物の口なので(模擬の相手役は無い)、本物を回す。窓の行の中身を読む検は、
;;; sample-taker(と標本の thread の本体 sample-loop)を直に作り、見たい出来事を終えてから検が line を呼んで窓を閉じる: 標本の thread が
;;; 壁の時計で閉じる窓を読むと、混んだ machine では主の thread の出来事が読む窓の外へずれる(GC の callback は回収の終わりで回数を積むので、
;;; 回収が窓の終わりより後に終われば、その窓は 回数=0 になる)。with-cpu-samples の検は、置いた物を body の中で読み、終わった後に戻った事を読む。
(require doeff-hy.macros [defk deff deftest <- val var])
(import collections.abc [Callable])
(import gc)
(import os)
(import signal)
(import sys)
(import tempfile)
(import threading)
(import time)
(import doeff [Program EffectBase run with-handlers])
(import doeff_time [Delay sync-time-handler])
(import doeff_cluster._foundation.cpu_samples [SampleSettings SampleTaker SAMPLE-SIGNAL sample-loop sample-taker with-cpu-samples])


;; 持ち主の印は検の dir(この file の関数を持ち主として名指す)。0.01 秒ごと・0.5 秒の窓。
(val QUICK-SAMPLES (SampleSettings :every-seconds 0.01 :window-seconds 0.5 :top 5 :owner-mark "/tests/"))
;; 窓を標本の thread に閉じさせない設定(sample-loop を直に回す検 — 窓の行は検が line を呼んで閉じる)。0.01 秒ごと。
(val HELD-SAMPLES (SampleSettings :every-seconds 0.01 :window-seconds 3600.0 :top 5 :owner-mark "/tests/"))


(defk stderr-text-of [body]
  {:pre [(: body (| Program EffectBase))] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "fd 2 を一時の file へ向けた下で body を走らせ、その間に書かれた文を返すため(終われば戻す)。"
  (val sink (tempfile.TemporaryFile))
  (val saved (os.dup 2))
  (.flush sys.stderr)
  (os.dup2 (.fileno sink) 2)
  (try
    (<- body)
    (finally
      (os.dup2 saved 2)
      (os.close saved)))
  (.seek sink 0)
  (val text (.decode (.read sink) "utf-8" "replace"))
  (.close sink)
  text)


(defk sampled-while [taker body]
  {:pre [(: taker SampleTaker) (: body (| Program EffectBase))] :post [(: % "body の答え")] :tags {:context "doeff-cluster-test" :role "program"}}
  "taker の合図の受け手を置き、本物の標本の thread(sample-loop・HELD-SAMPLES)を回した下で body を走らせ、終わったら(落ちても)thread を
   止めて受け手を戻すため(窓は閉じない — 検が taker の line で閉じる)。"
  (val ident (threading.get-ident))
  (val before (signal.signal SAMPLE-SIGNAL (. taker received)))
  (val stop (threading.Event))
  (<- target Callable (sample-loop HELD-SAMPLES taker ident stop))
  (val worker (threading.Thread :target target :name "cpu-samples" :daemon True))
  (.start worker)
  (try
    (<- answer body)
    answer
    (finally
      (.set stop)
      (.join worker :timeout 2.0)
      (signal.signal SAMPLE-SIGNAL before))))


(defk burned-for [seconds]
  {:pre [(: seconds float)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "主の thread で seconds 秒 CPU を使い続けるため(標本が持ち主として名指す見本の関数)。"
  (val until (+ (time.thread-time) seconds))
  (var spins 0)
  (while (< (time.thread-time) until)
    (:= spins (+ spins 1)))
  spins)


(defk lived-for [seconds]
  {:pre [(: seconds float)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "主の thread を seconds 秒眠らせるため(CPU を使わない間の見本 — 本物の時計の答え手で待つ)。"
  (<- (with-handlers [(sync-time-handler)] (Delay seconds)))
  None)


(defk slept-window [taker seconds]
  {:pre [(: taker SampleTaker) (: seconds float)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "taker の窓を閉じてから seconds 秒眠り、眠りの間の窓を閉じた行を返すため。"
  ((. taker line))
  (<- (lived-for seconds))
  ((. taker line)))


(defk noted-wiring [seen]
  {:pre [(: seen list)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "body の間の合図の受け手と GC の callback の一覧を seen へ足すため(with-cpu-samples の body の答えは stderr-text-of の外へ出ないので箱に置く)。"
  (.append seen #((signal.getsignal SAMPLE-SIGNAL) (list gc.callbacks)))
  None)


(deftest test-the-cpu-samples-name-the-function-that-burns-the-main-thread
  ;; 標本の thread の合図が、主の thread で CPU を使った関数(この検の burned-for)を持ち主と葉に名指す。窓は焼き終えて thread を止めた後に
  ;; 検が閉じる(窓の時刻に依らない — 頭の註)。
  (<- taker SampleTaker (sample-taker HELD-SAMPLES (threading.get-ident) (threading.get-native-id)))
  (<- (sampled-while taker (burned-for 0.5)))
  (val line ((. taker line)))
  (assert (in " 主threadのCPU=" line) line)
  (assert (not-in " 標本=0 " line) line)
  (assert (in "持ち主: test_cpu_samples:burned_for=" line) line)
  (assert (in "葉: tests/test_cpu_samples:burned_for=" line) line))


(deftest test-the-cpu-samples-count-the-collections-of-the-window
  ;; 窓の中の全世代の回収(gc.collect)を、世代 2 の回数と秒で書く(回収の CPU は stack に出ない — 口の頭の註)。callback は回収の終わりで
  ;; 回数を積むので、回収を終えて callback を外してから窓を閉じる(窓の時刻に依らない — 頭の註)。
  (<- taker SampleTaker (sample-taker QUICK-SAMPLES (threading.get-ident) (threading.get-native-id)))
  (.append gc.callbacks (. taker collected))
  (try
    (gc.collect)
    (finally
      (.remove gc.callbacks (. taker collected))))
  (val line ((. taker line)))
  (assert (in " | GC: 回数=" line) line)
  (assert (not-in " | GC: 回数=0(" line) line)
  (assert (not-in "世代2=0 " line) line))


(deftest test-the-cpu-samples-count-no-sample-while-the-main-thread-waits
  ;; 主の thread が眠っている間(CPU が進まない)は合図を送らず、標本に数えない。窓は眠りの前と後に検が閉じる。眠りの前の CPU で送った合図が
  ;; 眠りの中で届く分と、眠りの後の CPU で送った合図が窓を閉じる前に届く分の 2 つまでを許す(待ちの frame を数え続ける輪なら 1 秒で 100 近い)。
  (<- taker SampleTaker (sample-taker HELD-SAMPLES (threading.get-ident) (threading.get-native-id)))
  (<- line str (sampled-while taker (slept-window taker 1.0)))
  (val samples (int (get (.split (get (.split line " 標本=" 1) 1) " " 1) 0)))
  (assert (<= samples 2) line)
  (assert (not-in "主thread外:" line) line))


(deftest test-the-cpu-samples-wire-the-receiver-and-the-callback-only-around-the-body
  ;; with-cpu-samples は body の間だけ合図の受け手と GC の callback を置き、本体が終われば標本の thread を止めて、受け手と callback を戻す
  ;; (置いた物を body の中で読む — 窓の行に依らない)。
  (val before-handler (signal.getsignal SAMPLE-SIGNAL))
  (val before-callbacks (list gc.callbacks))
  (val seen [])
  (<- (stderr-text-of (with-cpu-samples QUICK-SAMPLES (noted-wiring seen))))
  (assert (= (len seen) 1) seen)
  (val handler (get (get seen 0) 0))
  (val callbacks (get (get seen 0) 1))
  (assert (and (callable handler) (!= handler before-handler)) handler)
  (assert (= (len callbacks) (+ (len before-callbacks) 1)) callbacks)
  (assert (= (cut callbacks 0 (len before-callbacks)) before-callbacks) callbacks)
  (assert (not-in "cpu-samples" (lfor t (threading.enumerate) t.name)) (threading.enumerate))
  (assert (= (signal.getsignal SAMPLE-SIGNAL) before-handler) (signal.getsignal SAMPLE-SIGNAL))
  (assert (= (list gc.callbacks) before-callbacks) gc.callbacks))


(deftest test-the-cpu-samples-off-the-main-thread-run-the-body-without-samples
  ;; 合図の受け手は主の thread からだけ置けるので、別の thread で包めば標本を取らずに body を走らせ終え、訳の 1 行を書く。
  (val answers [])
  (val runner
    (threading.Thread
      :target (fn [] (.extend answers [(run (stderr-text-of (with-cpu-samples QUICK-SAMPLES (burned-for 0.2))))]))
      :name "not-main"))
  (.start runner)
  (.join runner :timeout 10.0)
  (assert (= (len answers) 1) answers)
  (val text (get answers 0))
  (assert (in "cpu-samples: 主の thread の外では合図の受け手を置けない — 標本を取らない" text) text)
  (assert (not-in "cpu-samples: n=" text) text))
