;;; process の主の thread が CPU をどの関数で使ったかを標本で数え、窓ごとに 1 行を process の log へ書く本物の口(層 foundation —
;;; coordinator の主の thread の CPU を何が使っているかを、debug の Pod を立てずに log から数で読む)。
;;;
;;; 何のためか: coordinator は調停ループを 1 本の主の thread で回す。/proc からは process の CPU の合計しか読めず、外からの ptrace は禁じられ、
;;; debug の Pod も立てられない。process が自分の主の thread の stack を標本にすれば、CPU を使い切っている関数を log から名指せる。
;;;
;;; 標本: 1 本の daemon の thread(cpu-samples)が settings.every-seconds ごとに主の thread の CPU 秒(pthread の CPU の時計)を読み、前の標本から
;;; 進んでいれば主の thread へ合図(SIGPROF — signal.pthread_kill)を送る。合図の受け手は主の thread の上で走り、前の受け手から進んだ主の
;;; thread の CPU 秒(time.thread_time)を、受けた刻の stack の 2 つの名へ積む:
;;;   持ち主  stack の内側から数えて最初の settings.owner-mark を path に含む frame(`_coordinator/core/cluster_policy:sweep_board` の形)
;;;   葉      一番内側の frame(`json/encoder:iterencode` の形 — 標準の library・doeff の部品の中を含む)
;;; doeff の VM は Rust で、Program の generator を VM が直に進めるので、stack に見えるのは今進めている Program の本体と、そこから呼んだ素の関数
;;; と、VM を呼んだ外側の frame(親の Program は止まっていて stack に無い)。CPU が進まなかった間(待ち)は合図を送らない。
;;;
;;; stack の読み方 — 主の thread が自分の frame を読む: Python の合図の受け手は主の thread の上で、bytecode の区切りで、その刻の frame を引数に
;;; 走る。自分の thread の frame を f_back で辿るので、free-threading の build でも安全(他の thread の frame の object は読まない — CPython の
;;; free-threading の HOWTO が sys._current_frames を「一般に安全でない」とする)。
;;; _remote_debugging.RemoteUnwinder を自分の pid に向ける形は使わない: 3.14 の写しは data stack の chunk の連なりを本数の上限なしに辿り
;;; (Modules/_remote_debugging_module.c の copy_stack_chunks)、写しの途中で主の thread が chunk を解くと輪に読んで、別の process で 1 回に
;;; 5〜6 GB を確保した。coordinator の Pod の memory の上限(1536Mi)では、それは process の終わり(OOMKilled)になる。
;;; 合図の受け手の限り: 受け手は次の bytecode の区切りで走るので、C や Rust の長い呼び(GC の回収を含む)で使った CPU は、呼びが返った後の
;;; frame に積む(呼んだ側の Python の frame が葉になる — 写しの形でも C の frame は見えない)。合図は、主の thread の CPU が前の受け手の
;;; 終わりから SIGNAL-FLOOR-SECONDS を越えて進んだ時だけ送る: 眠っている主の thread も合図で起きて受け手の分の CPU を使うので、進んだかだけで
;;; 送ると、受け手の CPU が次の合図を呼ぶ輪になり、待ちの frame を標本に数え続ける(床の下の CPU も次の受け手が積むので落ちない)。合図が届く前に次の標本が来ても、合図は 1 つに
;;; 畳まれ、CPU 秒は受け手が読む時計で数えるので落ちない。受け手が CPU 秒を積むので、窓の行の「主threadのCPU」と積んだ秒の和は、窓の終わりに
;;; まだ受けていない分だけ違う。受け手は lock を取らず、積む先は queue.SimpleQueue への put だけ(合図の受け手と GC の callback から呼んで
;;; よい reentrant な put — 標本の thread が窓の行を書く時に取り出す)。
;;;
;;; GC(ごみ集め)の回収: 回収の CPU は stack に出ない(上の限り)ので、gc.callbacks に繋いだ callback が回収 1 回ごとに世代と始めから終わりの秒を
;;; SimpleQueue へ積み、窓の行が窓の回数(世代ごと)・秒の和・最長を書く。callback も lock を取らない(回収は起こした thread の中で callback を
;;; 呼ぶので、lock を持った thread の中で回収が始まると、取り直す callback がその thread を永久に待たせる — doeff-core-effects の
;;; process_meter.hy と同じ)。始めの刻は thread ごとに持つ。
;;;
;;; 主の thread の外の CPU: 窓の process の CPU が主の thread の CPU を、窓の壁の時計の秒の OTHER-THREADS-CPU 倍越えたら、窓の行の尾に thread
;;; ごとの窓の CPU 秒を足す(窓の始めと終わりに /proc/self/task の全 thread の CPU の時計を読んだ差。他の thread の stack は読まない)。
;;;
;;; 窓ごと(settings.window-seconds)に 1 行を fd 2 へ書く:
;;;   cpu-samples: n=<窓の番号> 窓=<秒>秒 主threadのCPU=<%> processのCPU=<%> 標本=<受けた合図の数> | 持ち主: <名>=<%> … | 葉: <名>=<%> …
;;;     | GC: 回数=<n>(世代0=<n> 世代1=<n> 世代2=<n>) 秒=<秒> 最長=<秒>
;;; % は持ち主と葉では受け手が積んだ CPU 秒の和に占める割合(上位 settings.top まで — 行は 4096 byte の内に収め、pipe への 1 回の書きを割らない)。
;;; 窓の process の CPU が主の thread の CPU を OTHER-THREADS-CPU 本分越えた窓だけ、同じ行の尾に足す:
;;;   … | 主thread外: thread: <名>#<tid>=<CPU 秒>秒 …
;;;
;;; 標本を取らない時: Linux の外(thread の CPU の時計と /proc/self/task が無い)と、主の thread の外から包んだ時(合図の受け手は主の thread
;;; からだけ登録できる)は、標本の thread を立てず、訳の 1 行を書いて body だけを走らせる。
;;;
;;;   with-cpu-samples  標本の thread・合図の受け手・GC の callback を置いた下で body を走らせ、終わったら(落ちても)外す(1 つの process に
;;;                     2 度包まない)
;;;   sample-taker      合図の受け手・GC の callback・窓の行を書く関数の組を作る(検と thread が同じ 1 点を呼ぶ)
;;; 標本の thread・受け手・callback は VM の外から素の関数として呼ばれるので、内側の関数は sample-taker の中の fn にする。模擬と検の土台は
;;; 繋がない(標本は process の外の観測で、Program の答えを変えない)。
(require doeff-hy.macros [defk deff <- val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import collections [Counter])
(import collections.abc [Callable])
(import gc)
(import os)
(import queue [Empty SimpleQueue])
(import signal)
(import sys)
(import threading)
(import time)
(import traceback)
(import doeff [Program EffectBase])

;; 行に書く名の幅(長い path で 1 行が pipe の書きの上限 4096 byte を越えないように切る)。
(val NAME-WIDTH 64)
;; 主の thread の外の CPU の本数の閾(頭の註 — 窓で越えたら thread ごとの CPU 秒を書く)。
(val OTHER-THREADS-CPU 0.5)
;; 主の thread の外の欄に書く thread の数(行を 4096 byte の内に収める)。
(val THREAD-TOP 5)
;; thread の一覧を読む所(Linux)。
(val TASK-DIR "/proc/self/task")
;; 主の thread へ送る合図(coordinator の process は CPU の時計の合図を他に使わない)。
(val SAMPLE-SIGNAL signal.SIGPROF)
;; 合図を送る主の thread の CPU の進みの床(秒 — 頭の註: 受け手 1 回の CPU〔数十 µs〕より十分大きく、標本の間 0.1 秒の 1%)。
(val SIGNAL-FLOOR-SECONDS 0.001)


(defrecord SampleSettings
  "標本の設定: every-seconds = 標本の間の秒・window-seconds = 行を書く窓の秒・top = 持ち主と葉を書く数(多い順)・owner-mark = 持ち主の名を取る
   frame の path の印(名は印から後ろの path)。"
  {:check [(> every-seconds 0) (> window-seconds 0) (>= top 1) (> (len owner-mark) 0)] :tags {:context "doeff-cluster" :role "foundation"}}
  (#^ float every-seconds)
  (#^ float window-seconds)
  (#^ int top)
  (#^ str owner-mark))


;; coordinator の本番の設定(_coordinator/entry/main.hy が被せる)。0.1 秒ごと(1 分に最大 600 標本)・1 分ごとに 1 行・上位 12・持ち主は
;; doeff_cluster の下の関数。
(val COORDINATOR-SAMPLES (SampleSettings :every-seconds 0.1 :window-seconds 60.0 :top 12 :owner-mark "/doeff_cluster/"))


(defrecord SampleTaker
  "標本の関数の組: received = 合図の受け手(signum frame)・collected = GC の callback(phase info)・line = 窓の行を返して窓を始め直す(引数
   なし)・main-cpu = 主の thread の CPU 秒を読む(引数なし)・handled-cpu = 前の受け手が終わった刻の主の thread の CPU 秒(引数なし — 標本の
   thread が main-cpu と比べて合図を送るかを決める・頭の註)。"
  {:tags {:context "doeff-cluster" :role "foundation"}}
  (#^ Callable received)
  (#^ Callable collected)
  (#^ Callable line)
  (#^ Callable main-cpu)
  (#^ Callable handled-cpu))


(deff other-thread-clocks [native-id]  ; cannot be a defk: 標本の thread が窓の行を組む時に素の関数として呼ぶ
  {:pre [(: native-id int)] :post [(: % Counter)] :tags {:context "doeff-cluster" :role "foundation"}}
  "主の thread(kernel の番号 native-id)の外の全 thread の tid ごとの CPU 秒を読むため(頭の註 — 時計の番号は glibc の pthread_getcpuclockid と
   同じ形)。読む間に終わった thread は除き、一覧を読めなければ空。"
  (setv tids (try (os.listdir TASK-DIR) (except [OSError] [])))
  (setv read-clock
    (fn [tid]
      (try
        (time.clock-gettime (| (<< (bnot tid) 3) 6))
        (except [OSError] None))))
  (setv readings (gfor name tids :if (!= (int name) native-id) #((int name) (read-clock (int name)))))
  (Counter (dfor #(tid seconds) readings :if (is-not seconds None) tid seconds)))


(defk sample-taker [settings ident native-id]
  {:pre [(: settings SampleSettings) (: ident int) (: native-id int)] :post [(: % SampleTaker)] :tags {:context "doeff-cluster" :role "foundation"}}
  "主の thread(ident = threading の ident・native-id = kernel の thread の番号)の合図の受け手・GC の callback・窓の行を書く関数を作るため
   (頭の註)。受け手は主の thread の上で呼ばれる前提で、自分の thread の CPU 秒と frame だけを読む。"
  (val clock (time.pthread-getcpuclockid ident))
  (val main-cpu (fn [] (time.clock-gettime clock)))
  ;; 受け手が積む #(持ち主 葉 CPU 秒)・GC の callback が積む #(世代 秒)。どちらも標本の thread が窓の行で取り出す(頭の註 — lock を取らない)。
  (val spent (SimpleQueue))
  (val collections (SimpleQueue))
  (val received-cpu [(time.thread-time)])
  (val handled-at [(get received-cpu 0)])
  (val gc-started (threading.local))
  ;; 窓をまたいで持つ値(list は要素 0 に今の値を置く箱): 窓の番号・窓の始めの主の thread の CPU 秒・process の CPU 秒・単調時計の秒・主の
  ;; thread の外の tid ごとの CPU 秒。
  (val round [0])
  (val window-cpu [(main-cpu)])
  (val window-process [(time.process-time)])
  (val window-at [(time.monotonic)])
  (val window-threads [(other-thread-clocks native-id)])
  (val drained
    (fn [box]
      ;; 箱に今在る物を全部取り出す(取り出す間に足された物は次の窓へ回る)。
      (setv end (object))
      (tuple (iter (fn [] (try (.get-nowait box) (except [Empty] end))) end))))
  (val shown-name
    (fn [name]
      ;; 名を NAME-WIDTH で切る(長い名は頭を落として、関数の名の側を残す)。
      (if (> (len name) NAME-WIDTH) (+ "…" (cut name (- (- NAME-WIDTH 1)) None)) name)))
  (val frame-name
    (fn [frame owner]
      ;; frame の名: 持ち主は印から後ろの path(拡張子なし)・葉は親の dir と file の名(拡張子なし)。どちらも `:関数の名`。
      (setv path frame.f-code.co-filename)
      (setv place (if owner
                      (get (.partition path settings.owner-mark) 2)
                      (os.path.join (os.path.basename (os.path.dirname path)) (os.path.basename path))))
      (+ (get (os.path.splitext place) 0) ":" frame.f-code.co-name)))
  (val owner-of
    (fn [frame]
      ;; frame から外側へ辿って最初の印を含む frame の名(無ければ「(<印> の外)」)。
      (setv at frame)
      (while (is-not at None)
        (when (in settings.owner-mark at.f-code.co-filename)
          (return (frame-name at True)))
        (setv at at.f-back))
      (.format "({} の外)" (.strip settings.owner-mark "/"))))
  (val received
    (fn [signum frame]
      ;; 合図 1 つ: 前の受け手から進んだ自分(主の thread)の CPU 秒を、今の frame の持ち主と葉へ積む。受け手の例外は VM の中へ漏らさない
      ;; (落ちた訳を名に積む)。
      (setv now (time.thread-time))
      (setv used (- now (get received-cpu 0)))
      (setv (get received-cpu 0) now)
      (try
        (.put spent (if (is frame None)
                        #("(frame なし)" "(frame なし)" used)
                        #((owner-of frame) (frame-name frame False) used)))
        (except [e Exception]
          (setv unread (.format "(読めない: {})" (. (type e) __name__)))
          (.put spent #(unread unread used))))
      (setv (get handled-at 0) (time.thread-time))
      None))
  (val collected
    (fn [phase info]
      ;; GC の回収の始めと終わり(頭の註 — lock を取らない・始めの刻は thread ごと)。
      (if (= phase "start")
          (setv gc-started.at (time.monotonic))
          (do
            (setv began (getattr gc-started "at" None))
            (setv gc-started.at None)
            (when (is-not began None)
              (.put collections #((.get info "generation") (- (time.monotonic) began))))))
      None))
  (val seconds-by
    (fn [pairs]
      ;; #(名 CPU 秒) の列を名ごとの秒の和にする。
      (sum (gfor #(name seconds) pairs (Counter {name seconds})) (Counter))))
  (val shares
    (fn [counts total]
      ;; 名ごとの CPU 秒を、多い順に settings.top まで `名=割合%` で書く。
      (.join " " (gfor #(name seconds) (.most-common counts settings.top)
                   (.format "{}={:.0f}%" (shown-name name) (if (> total 0) (* 100 (/ seconds total)) 0))))))
  (val gc-part
    (fn []
      ;; 窓の GC の欄: 回数(世代ごと)・秒の和・最長。
      (setv taken (drained collections))
      (setv counts (Counter (gfor #(generation _) taken generation)))
      (setv total (sum (gfor #(_ seconds) taken seconds)))
      (setv longest (max (gfor #(_ seconds) taken seconds) :default 0.0))
      (.format " | GC: 回数={}(世代0={} 世代1={} 世代2={}) 秒={:.3f} 最長={:.3f}"
               (len taken) (get counts 0) (get counts 1) (get counts 2) total longest)))
  (val thread-name
    (fn [tid]
      ;; thread の名と tid(threading の名・無ければ kernel の名・読めなければ ?)。
      (setv name (next (gfor t (threading.enumerate) :if (= t.native-id tid) t.name) None))
      (when (is name None)
        (try
          (with [f (open (os.path.join TASK-DIR (str tid) "comm"))]
            (setv name (.strip (.read f))))
          (except [OSError] (setv name "?"))))
      (.format "{}#{}" (shown-name name) tid)))
  (val others-part
    (fn [clocks]
      ;; 窓の主の thread の外の欄(頭の註): clocks = 窓の終わりの tid ごとの CPU 秒。窓の中で終わった thread は引いた値が負になり、書かない。
      (setv used (- (+ (Counter) clocks) (get window-threads 0)))
      (setv ranked (.most-common used THREAD-TOP))
      (setv threads-text (or (.join " " (gfor #(tid seconds) ranked (.format "{}={:.2f}秒" (thread-name tid) seconds))) "(なし)"))
      (.format " | 主thread外: thread: {}" threads-text)))
  (val line
    (fn []
      ;; 窓の行を組み、窓を始め直す。
      (setv (get round 0) (+ (get round 0) 1))
      (setv at (time.monotonic))
      (setv cpu (main-cpu))
      (setv process (time.process-time))
      (setv span (max (- at (get window-at 0)) 1e-9))
      (setv main-used (- cpu (get window-cpu 0)))
      (setv process-used (- process (get window-process 0)))
      (setv taken (drained spent))
      (setv owners (seconds-by (gfor #(owner _ used) taken #(owner used))))
      (setv leaves (seconds-by (gfor #(_ leaf used) taken #(leaf used))))
      (setv attributed (sum (gfor #(_ _ used) taken used)))
      (setv clocks (other-thread-clocks native-id))
      (setv text (+ (.format "cpu-samples: n={} 窓={:.0f}秒 主threadのCPU={:.0f}% processのCPU={:.0f}% 標本={}"
                             (get round 0) span (* 100 (/ main-used span)) (* 100 (/ process-used span)) (len taken))
                    (.format " | 持ち主: {} | 葉: {}" (shares owners attributed) (shares leaves attributed))
                    (gc-part)
                    (if (> (- process-used main-used) (* OTHER-THREADS-CPU span))
                        (others-part clocks)
                        "")))
      (setv (get window-threads 0) clocks)
      (setv (get window-cpu 0) cpu)
      (setv (get window-process 0) process)
      (setv (get window-at 0) at)
      text))
  (SampleTaker :received received :collected collected :line line :main-cpu main-cpu :handled-cpu (fn [] (get handled-at 0))))


(defk sample-loop [settings taker ident stop]
  {:pre [(: settings SampleSettings) (: taker SampleTaker) (: ident int) (: stop threading.Event)] :post [(: % Callable)]
   :tags {:context "doeff-cluster" :role "foundation"}}
  "標本の thread の本体を作るため(頭の註): stop が立つまで every-seconds ごとに主の thread(ident)の CPU 秒を読み、前の合図か前の受け手の
   終わりから SIGNAL-FLOOR-SECONDS を越えて進んでいれば合図を送り、
   window-seconds ごとに窓の行を fd 2 へ書く。標本が落ちても thread は続ける(落ちた訳と起きた所を 1 行書く)。作った関数は VM の外の
   thread が素の関数として呼ぶ。"
  (val write-all
    (fn [text]
      ;; 1 行を fd 2 へ書き切る(pipe への書きが途中で返っても残りを書く)。
      (setv data (.encode (+ text "\n") "utf-8" "replace"))
      (while data
        (setv data (cut data (os.write 2 data) None)))))
  (val failure-place
    (fn [error]
      ;; 落ちた例外の一番内側の frame(file の名と行)。frame が無ければ ?。
      (setv frames (traceback.extract-tb error.__traceback__))
      (if frames
          (.format "{}:{}" (os.path.basename (. (get frames -1) filename)) (. (get frames -1) lineno))
          "?")))
  (fn []
    (setv window-end (+ (time.monotonic) settings.window-seconds))
    (setv last-cpu ((. taker main-cpu)))
    ;; 測りの周期 — 自分の process の主の thread の CPU を読み、合図と log の行を出すだけで、相手の物を読み直さない。
    (while (not (.wait stop settings.every-seconds))
      (try
        (setv now ((. taker main-cpu)))
        (when (> (- now (max last-cpu ((. taker handled-cpu)))) SIGNAL-FLOOR-SECONDS)
          (signal.pthread-kill ident SAMPLE-SIGNAL)
          (setv last-cpu now))
        (when (>= (time.monotonic) window-end)
          (write-all ((. taker line)))
          (setv window-end (+ (time.monotonic) settings.window-seconds)))
        (except [e Exception]
          (write-all (.format "cpu-samples: 標本が落ちた — {!r} 場所={}" e (failure-place e))))))))


(defk with-cpu-samples [settings body]
  {:pre [(: settings SampleSettings) (: body (| Program EffectBase))] :post [(: % "body の答え")] :tags {:context "doeff-cluster" :role "foundation"}}
  "主の thread が CPU をどの関数で使ったか・GC の回収の回数と秒が窓ごとに log に残るよう、合図の受け手・GC の callback・標本の daemon の thread を
   置いた下で body を走らせ、終わったら(落ちても)thread を止めて受け手と callback を外すため(頭の註)。主の thread = この Program を走らせて
   いる thread で、interpreter の主の thread でなければならない(本番は coordinator の process の主の流れ)。Linux の外と主の thread の外では
   標本を取らず、訳の 1 行を fd 2 へ書いて body だけを走らせる。"
  (val refusal (cond (!= sys.platform "linux") (.format "この OS({})では thread の CPU の時計を読めない" sys.platform)
                     (!= (threading.get-ident) (. (threading.main-thread) ident)) "主の thread の外では合図の受け手を置けない"
                     True None))
  (when (is-not refusal None)
    (os.write 2 (.encode (.format "cpu-samples: {} — 標本を取らない\n" refusal) "utf-8"))
    (<- bare body)
    (return bare))
  (val ident (threading.get-ident))
  (<- taker SampleTaker (sample-taker settings ident (threading.get-native-id)))
  (val before (signal.signal SAMPLE-SIGNAL (. taker received)))
  (.append gc.callbacks (. taker collected))
  (val stop (threading.Event))
  (<- target Callable (sample-loop settings taker ident stop))
  (val worker (threading.Thread :target target :name "cpu-samples" :daemon True))
  (.start worker)
  (try
    (<- answer body)
    answer
    (finally
      (.set stop)
      (.join worker :timeout 2.0)
      (.remove gc.callbacks (. taker collected))
      ;; 送った合図は届き終えている(合図を送る thread は止まり、主の thread はここを走っている)ので、前の受け手へ戻す。
      (signal.signal SAMPLE-SIGNAL before))))
