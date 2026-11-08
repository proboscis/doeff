;;; foundation/cpu_samples の with-cpu-samples(主の thread の CPU を関数ごとに・GC の回収の回数と秒を、窓ごとに fd 2 へ書く口)の検。
;;; 口は daemon の thread と合図の受け手と GC の callback で fd 2 へ書く本物の口なので(模擬の相手役は無い)、fd 2 を一時の file へ向けた下で
;;; 本物を回し、書かれた行を読む。
(require doeff-hy.macros [defk deff deftest <- val var])
(import gc)
(import os)
(import signal)
(import sys)
(import tempfile)
(import threading)
(import time)
(import doeff [Program EffectBase run with-handlers])
(import doeff_time [Delay sync-time-handler])
(import doeff_cluster._foundation.cpu_samples [SampleSettings SAMPLE-SIGNAL with-cpu-samples])


;; 持ち主の印は検の dir(この file の関数を持ち主として名指す)。0.01 秒ごと・0.5 秒の窓。
(val QUICK-SAMPLES (SampleSettings :every-seconds 0.01 :window-seconds 0.5 :top 5 :owner-mark "/tests/"))


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


(defk collected-for [seconds]
  {:pre [(: seconds float)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "全世代の回収を 1 度起こし、窓の行が書かれるまで眠るため(GC の欄の見本)。"
  (gc.collect)
  (<- (lived-for seconds))
  None)


(defn first-line [text n]
  "窓の番号 n の行を引くため(無ければ None)。"
  (next (gfor line (.splitlines text) :if (.startswith line (.format "cpu-samples: n={} " n)) line) None))


(deftest test-the-cpu-samples-name-the-function-that-burns-the-main-thread
  ;; 主の thread で CPU を使った関数(この検の burned-for)を持ち主と葉に名指し、本体が終われば標本の thread も止まり、合図の受け手を戻す。
  (val before (signal.getsignal SAMPLE-SIGNAL))
  (<- text str (stderr-text-of (with-cpu-samples QUICK-SAMPLES (burned-for 1.2))))
  (val line (first-line text 1))
  (assert (is-not line None) text)
  (assert (in " 主threadのCPU=" line) line)
  (assert (not-in " 標本=0 " line) line)
  (assert (in "持ち主: test_cpu_samples:burned_for=" line) line)
  (assert (in "葉: tests/test_cpu_samples:burned_for=" line) line)
  (assert (in " | GC: 回数=" line) line)
  (assert (not-in "cpu-samples" (lfor t (threading.enumerate) t.name)) (threading.enumerate))
  (assert (= (signal.getsignal SAMPLE-SIGNAL) before) (signal.getsignal SAMPLE-SIGNAL)))


(deftest test-the-cpu-samples-count-the-collections-of-the-window
  ;; 窓の中の全世代の回収(gc.collect)を、世代 2 の回数と秒で書く(回収の CPU は stack に出ない — 頭の註)。
  (<- text str (stderr-text-of (with-cpu-samples QUICK-SAMPLES (collected-for 0.7))))
  (val line (first-line text 1))
  (assert (is-not line None) text)
  (assert (not-in " | GC: 回数=0(" line) line)
  (assert (not-in "世代2=0 " line) line))


(deftest test-the-cpu-samples-count-no-sample-while-the-main-thread-waits
  ;; 主の thread が眠っている間(CPU が進まない)は合図を送らず、標本に数えない。2 つ目の窓は丸ごと眠りの中。
  (<- text str (stderr-text-of (with-cpu-samples QUICK-SAMPLES (lived-for 1.3))))
  (val line (first-line text 2))
  (assert (is-not line None) text)
  (assert (in " 標本=0 " line) line)
  (assert (not-in "主thread外:" line) line))


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
