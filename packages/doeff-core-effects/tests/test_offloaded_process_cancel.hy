;;; offloaded-subprocess-handler(os_process.hy)で走らせる子 process の要求を取り消す検(agora-redesign #2829 — #2792 の
;;; L1276 で RLock にした offloaded_call.hy の Handoff の錠の、子 process の側の使い手の検)。本物の子(/bin/sh・sleep・true)を使う。
;;;
;;;   (a) 仕事が始まる前の取り消し: 子を起こす仕事を thread に渡したが、その thread がまだ仕事を始めていない間に、待っている task を
;;;       取り消す。Handoff の give-up が錠を持ったまま Future.cancel を呼び、Future.cancel は同じ thread で完了の callback(deliver)を
;;;       呼ぶので、錠が入り直せないと scheduler の thread がそこで止まる。始まる前の間を確実に作るため、この検だけ os_process の
;;;       PROCESS-THREADS を GatedStart(gate が開くまで仕事を始めない Executor)に差し替える。取り消した仕事は gate が開いても始まらず、
;;;       子は 1 度も起きない。
;;;   (b) 子が走っている間の取り消し: 子が自分の pid を FIFO へ書いた後(走っている)に、待っている task を取り消す。子は時間の上限
;;;       (RunProcess の timeout)と猶予の内に居なくなる — subprocess.run と同じ待ち方の道(process-group なし)も run-watched の道
;;;       (process-group)も。
;;;   (c) 時間の上限の無い子が走っている間の取り消し(agora-redesign #2847): 上限が無いので、取り消しの手当てが子を止めなければ子は
;;;       自然に終わるまで残る(#2829 で分かった — 前は答えを捨てるだけだった)。取り消しの後、RunProcess の stop-grace の内に子が
;;;       居なくなる。process-group の道では、子が背景に起こした孫も居なくなる(group ごと止める)。
;;;   (d) (c) の子が SIGTERM を無視する時: stop-grace の後に SIGKILL で止まる(group なら孫も)。
;;;   (e) 止めた子の数え: 計器の答え手を渡した metered-offloaded-subprocess-handler の下で (c) と (d) を 1 回ずつ取り消すと、計器の
;;;       counter process_cancel_terminated_total と process_cancel_killed_total が 1 つずつ進む。数えるのは要求の run の外(別の thread の
;;;       新しい VM)なので、計器は run をまたいで同じ置き場を読み書きする process-meter-handler(検ごとの名の置き場)。
;;;
;;; 確かめる事: 取り消された task は TaskCancelledError で終わる・scheduler は固まらずに次の task(子 1 本の RunProcess)へ進む・
;;; 子は残らない((a) は起きない・(b)(c)(d) は猶予の後に pid が生きていない)。
;;; 筋書きは検の thread とは別の daemon の thread の新しい VM で回し、SCENARIO-LIMIT 秒で答えが無ければ名指して落とす — 錠が固まって
;;; も、その thread を置いたまま検は赤で終わり、検の束を止めない(pytest の上限 60 秒を待たない)。
(require doeff-hy.macros [defk deff deftest <- val])
(require doeff-hy.record [defrecord])
(import contextlib)
(import dataclasses [dataclass])
(import os)
(import signal)
(import threading)
(import time)
(import collections.abc [Callable])
(import concurrent.futures [Future])
(import pytest)
(import doeff [Program with_handlers])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.meter_effects [MeterSettings MeterSnapshot ReadMeter])
(import doeff_core_effects.process_meter [process-meter-handler])
(import doeff_core_effects.os_process :as process-module)
(import doeff_core_effects.os_process [offloaded-subprocess-handler metered-offloaded-subprocess-handler
                                       CANCEL-TERMINATED-METRIC CANCEL-KILLED-METRIC])
(import doeff_core_effects.offloaded_call [ThreadPerCall run-detached])
(import doeff_core_effects.process_effects [ProcessAlive ProcessOutcome RunProcess])
(import doeff_core_effects.scheduler [scheduled CreateExternalPromise ExternalPromise Task Wait Spawn Cancel TaskCancelledError])

;; 筋書き 1 つの上限(秒)。普段は 1 秒の内に終わる。過ぎたら scheduler の thread が固まったと見て落とす。
(val SCENARIO-LIMIT 5.0)
;; (b) の子の時間の上限(RunProcess の timeout・秒)と、上限の後に子が止まって回収されるまでの猶予(秒)・生死を問う間隔(秒)。
(val CHILD-LIMIT 0.5)
(val GONE-GRACE 1.0)
(val POLL 0.02)
;; 自分の pid を $1 へ書いてから眠る子(exec なので眠りも同じ pid)。眠りは上限と猶予より長い — 止める係が居なければ残る。
(val PID-THEN-SLEEP "echo $$ > \"$1\"; exec sleep 5")
;; 次の task の子(取り消しの後も答え手が子を走らせる)とその答え。
(val NEXT-ARGV #("true"))
(val NEXT-OUTCOME (ProcessOutcome :exit-code 0 :stdout "" :stderr ""))
;; (c)(d) の子の RunProcess の stop-grace(秒 — 取り消しで SIGTERM を送ってから SIGKILL までの猶予)。
(val CANCEL-GRACE 0.5)
;; (c)(d) の子: 時間の上限を付けずに走らせ、止める係が居なければ 30 秒残る。process-group の道では背景に孫を 1 つ起こし、子と孫の pid を
;; $1 へ書く(exec なので眠りも子と同じ pid)。(d) は頭で SIGTERM を無視する(無視は exec と孫へ継がれる)。
(val LONE-UNBOUNDED "echo $$ > \"$1\"; exec sleep 30")
(val GROUP-UNBOUNDED "sleep 30 & echo $$ $! > \"$1\"; exec sleep 30")
(val IGNORE-TERM "trap '' TERM; ")


(defrecord BeforeStart
  "(a) の筋書きで見た事: ended-by-cancel = 取り消した task が TaskCancelledError で終わったか・held-cancelled = thread に渡した仕事の
   Future が取り消されたか・started = gate が開いた後に仕事の thread が仕事を始めたか・pid-written = 子が起きて pid を書いたか・
   next-outcome = 取り消しの後に走らせた次の子の答え。"
  (#^ bool ended-by-cancel)
  (#^ bool held-cancelled)
  (#^ bool started)
  (#^ bool pid-written)
  (#^ ProcessOutcome next-outcome))


(defrecord WhileRunning
  "(b) の筋書きで見た事: pid = 走っていた子の pid・ended-by-cancel = 取り消した task が TaskCancelledError で終わったか・
   moved-on-seconds = 取り消しから次の子の答えまでの秒・next-outcome = 次の子の答え・alive-at-deadline = 子の上限と猶予を過ぎても
   pid が生きていたか。"
  (#^ int pid)
  (#^ bool ended-by-cancel)
  (#^ float moved-on-seconds)
  (#^ ProcessOutcome next-outcome)
  (#^ bool alive-at-deadline))


(defrecord Unbounded
  "(c)(d) の筋書きで見た事: pids = 子(process-group なら子と孫)の pid・ended-by-cancel = 取り消した task が TaskCancelledError で
   終わったか・moved-on-seconds = 取り消しから次の子の答えまでの秒・next-outcome = 次の子の答え・survivors = 猶予(CANCEL-GRACE)と
   GONE-GRACE を過ぎても生きていた pid。"
  (#^ (get tuple #(int ...)) pids)
  (#^ bool ended-by-cancel)
  (#^ float moved-on-seconds)
  (#^ ProcessOutcome next-outcome)
  (#^ (get tuple #(int ...)) survivors))


(defrecord CountedStops
  "(e) の筋書きで見た事: terminated・killed = 計器の counter(SIGTERM で止まった数・SIGKILL まで要った数)・survivors = 2 つの取り消しの
   猶予の後も生きていた pid。"
  (#^ float terminated)
  (#^ float killed)
  (#^ (get tuple #(int ...)) survivors))


(defclass GatedStart [ThreadPerCall]
  "呼び 1 つに daemon の thread 1 本を起こす点は ThreadPerCall と同じで、その thread が仕事を始める(Future を走りに変える)前に gate が
   開くのを待つ Executor(検の殻 — 資源なので値の型ではない)。submit した Future を submitted へ、gate の後に仕事を始めたか
   (set_running_or_notify_cancel の答え — 取り消された Future は False)を decided へ渡す。"

  (deff __init__ [self gate submitted decided]  ; defk にできない: 検の殻の資源の初期化
    {:pre [(: self GatedStart) (: gate threading.Event) (: submitted (get ExternalPromise Future)) (: decided (get ExternalPromise bool))]
     :post [(: % None)]}
    "gate と、知らせを受ける promise 2 つを持つため。"
    (setv self.gate gate
          self.submitted submitted
          self.decided decided))

  (deff submit [self call #* args]  ; defk にできない: Executor の口(concurrent.futures の約束 — VM の外)
    {:pre [(: self GatedStart) (: call Callable) (: args tuple)] :post [(: % Future)]}
    "call を新しい thread で gate が開いた後に回す Future を返し、その Future を submitted へ渡すため。"
    (setv future (Future))
    (.complete self.submitted future)
    (.start (threading.Thread :target self.start-after-gate :args #(future call args) :name "doeff-gated-call" :daemon True))
    future)

  (deff start-after-gate [self future call args]  ; defk にできない: 仕事の thread の本体(VM の外)
    {:pre [(: self GatedStart) (: future Future) (: call Callable) (: args tuple)] :post [(: % None)]}
    "gate が開く(SCENARIO-LIMIT 秒まで)のを待ち、Future が取り消されていなければ call を回して答えを Future へ置くため。"
    (.wait self.gate SCENARIO-LIMIT)
    (setv started (.set-running-or-notify-cancel future))
    (.complete self.decided started)
    (when started
      (try
        (.set-result future (call #* args))
        (except [error BaseException]
          (.set-exception future error))))))


(deff report-pid [fifo promise]  ; defk にできない: 検の殻の thread の本体(VM の外で FIFO の書き手を待つ)
  {:pre [(: fifo str) (: promise (get ExternalPromise int))] :post [(: % None)]}
  "子が FIFO へ書いた自分の pid を読んで promise へ渡すため(子は読み手が開くまで書きを待つので、走り出した子の pid を問い直さずに知る)。"
  (try
    (with [reader (open fifo :encoding "utf-8")]
      (.complete promise (int (.read reader))))
    (except [error Exception]
      (.fail promise error))))


(deff report-pids [fifo promise]  ; defk にできない: 検の殻の thread の本体(VM の外で FIFO の書き手を待つ)
  {:pre [(: fifo str) (: promise (get ExternalPromise tuple))] :post [(: % None)]}
  "子が FIFO へ書いた空白区切りの pid(子と、process-group なら孫)を読んで promise へ渡すため(report-pid の複数の形)。"
  (try
    (with [reader (open fifo :encoding "utf-8")]
      (.complete promise (tuple (gfor word (.split (.read reader)) (int word)))))
    (except [error Exception]
      (.fail promise error))))


(defk ended-by-cancel [task]
  {:pre [(: task (get Task ProcessOutcome))] :post [(: % bool)]
   :tags {:context "process-test" :role "program"}}
  "取り消した task を待ち、取り消しで終わったかを答えるため。"
  (try
    (<- (Wait task))
    False
    (except [TaskCancelledError] True)))


(defk alive-at-deadline [pid deadline]
  {:pre [(: pid int) (: deadline float)] :post [(: % bool)]
   :tags {:context "process-test" :role "program"}}
  "pid の子が居なくなるのを POLL 秒ごとに問い、deadline(monotonic の秒)を過ぎてもまだ生きていたかを答えるため。"
  (<- alive bool (ProcessAlive pid))
  (if (and alive (< (time.monotonic) deadline))
      (do (time.sleep POLL)
          (<- later bool (alive-at-deadline pid deadline))
          later)
      alive))


(defk survivors [pids deadline]
  {:pre [(: pids tuple) (: deadline float)] :post [(: % tuple)]
   :tags {:context "process-test" :role "program"}}
  "pids のうち deadline(monotonic の秒)を過ぎても生きていた pid を答えるため(1 つずつ alive-at-deadline で問う — 期限は共通)。"
  (var left #())
  (for [pid pids]
    (<- alive bool (alive-at-deadline pid deadline))
    (when alive
      (:= left (+ left #(pid)))))
  left)


(defk cancelled-before-the-call-starts [monkeypatch pid-file]
  {:pre [(: monkeypatch pytest.MonkeyPatch) (: pid-file str)] :post [(: % BeforeStart)]
   :tags {:context "process-test" :role "program"}}
  "(a) の筋書き(頭の註): 子を起こす仕事を thread に渡した後、その thread が仕事を始める前に、待っている task を取り消す。gate を開いた
   後で、本物の PROCESS-THREADS に戻して次の子を走らせる。"
  (<- submitted (CreateExternalPromise))
  (<- decided (CreateExternalPromise))
  (val gate (threading.Event))
  (.setattr monkeypatch process-module "PROCESS_THREADS" (GatedStart gate submitted decided))
  (<- task (Spawn (RunProcess :argv #("/bin/sh" "-c" PID-THEN-SLEEP "sh" pid-file))))
  (<- held Future (Wait submitted.future))
  ;; 錠を持った give-up の中の Future.cancel が、同じ thread で deliver を呼ぶ(入り直せない錠ならここで固まる)。
  (<- (Cancel task))
  (<- ended bool (ended-by-cancel task))
  (.set gate)
  (<- started bool (Wait decided.future))
  (.undo monkeypatch)
  (<- next (Spawn (RunProcess :argv NEXT-ARGV)))
  (<- after ProcessOutcome (Wait next))
  (BeforeStart :ended-by-cancel ended :held-cancelled (.cancelled held) :started started
               :pid-written (os.path.exists pid-file) :next-outcome after))


(defk cancelled-while-the-child-runs [fifo grouped]
  {:pre [(: fifo str) (: grouped bool)] :post [(: % WhileRunning)]
   :tags {:context "process-test" :role "program"}}
  "(b) の筋書き(頭の註): 子が pid を書いた後(走っている間)に、待っている task を取り消し、次の子を走らせ、子が上限と猶予の内に
   居なくなるかを見る。grouped = process-group で走らせるか(run-watched の道)。"
  (<- seen-pid (CreateExternalPromise))
  (os.mkfifo fifo)
  (.start (threading.Thread :target report-pid :args #(fifo seen-pid) :name "doeff-pid-reader" :daemon True))
  (<- task (Spawn (RunProcess :argv #("/bin/sh" "-c" PID-THEN-SLEEP "sh" fifo) :timeout CHILD-LIMIT :process-group grouped)))
  (<- pid int (Wait seen-pid.future))
  (val cancelled-at (time.monotonic))
  (<- (Cancel task))
  (<- ended bool (ended-by-cancel task))
  (<- next (Spawn (RunProcess :argv NEXT-ARGV)))
  (<- after ProcessOutcome (Wait next))
  (val moved-on (- (time.monotonic) cancelled-at))
  (<- left bool (alive-at-deadline pid (+ cancelled-at CHILD-LIMIT GONE-GRACE)))
  (WhileRunning :pid pid :ended-by-cancel ended :moved-on-seconds moved-on :next-outcome after :alive-at-deadline left))


(defk cancelled-without-a-time-limit [fifo grouped ignores-term]
  {:pre [(: fifo str) (: grouped bool) (: ignores-term bool)] :post [(: % Unbounded)]
   :tags {:context "process-test" :role "program"}}
  "(c)(d) の筋書き(頭の註): 時間の上限の無い子が pid を書いた後(走っている間)に、待っている task を取り消し、次の子を走らせ、子(と
   孫)が猶予の内に居なくなるかを見る。grouped = process-group で走らせるか(run-watched の道)・ignores-term = 子と孫が SIGTERM を
   無視するか。猶予の後に残った pid は答えに名指し、検の後始末として SIGKILL で止める(赤の時に眠りを 30 秒残さない)。"
  (<- seen-pids (CreateExternalPromise))
  (os.mkfifo fifo)
  (.start (threading.Thread :target report-pids :args #(fifo seen-pids) :name "doeff-pid-reader" :daemon True))
  (val script (+ (if ignores-term IGNORE-TERM "") (if grouped GROUP-UNBOUNDED LONE-UNBOUNDED)))
  (<- task (Spawn (RunProcess :argv #("/bin/sh" "-c" script "sh" fifo) :process-group grouped :stop-grace CANCEL-GRACE)))
  (<- pids tuple (Wait seen-pids.future))
  (val cancelled-at (time.monotonic))
  (<- (Cancel task))
  (<- ended bool (ended-by-cancel task))
  (<- next (Spawn (RunProcess :argv NEXT-ARGV)))
  (<- after ProcessOutcome (Wait next))
  (val moved-on (- (time.monotonic) cancelled-at))
  (<- left tuple (survivors pids (+ cancelled-at CANCEL-GRACE GONE-GRACE)))
  (for [pid left]
    (with [(contextlib.suppress ProcessLookupError PermissionError)]
      (os.kill pid signal.SIGKILL)))
  (Unbounded :pids pids :ended-by-cancel ended :moved-on-seconds moved-on :next-outcome after :survivors left))


(defk meter-by [place deadline]
  {:pre [(: place str) (: deadline float)] :post [(: % MeterSnapshot)]
   :tags {:context "process-test" :role "program"}}
  "計器の置き場 place の 2 つの counter(取り消しで止めた子の数)が共に 1 以上になるのを POLL 秒ごとに deadline まで読み、最後に読んだ
   断面を答えるため(数えるのは止めた thread なので、子が居なくなった少し後に出る)。"
  (<- snapshot MeterSnapshot (with_handlers [(state) (process-meter-handler place (MeterSettings))] (ReadMeter)))
  (val counters snapshot.counters)
  (if (and (< (min (.get counters CANCEL-TERMINATED-METRIC 0.0) (.get counters CANCEL-KILLED-METRIC 0.0)) 1.0)
           (< (time.monotonic) deadline))
      (do (time.sleep POLL)
          (<- later MeterSnapshot (meter-by place deadline))
          later)
      snapshot))


(defk counted-cancels [term-fifo kill-fifo grouped place]
  {:pre [(: term-fifo str) (: kill-fifo str) (: grouped bool) (: place str)] :post [(: % CountedStops)]
   :tags {:context "process-test" :role "program"}}
  "(e) の筋書き(頭の註): SIGTERM で止まる子と SIGTERM を無視する子を 1 つずつ取り消し((c)(d) の筋書き)、計器 place の 2 つの counter を
   読む。"
  (<- by-term Unbounded (cancelled-without-a-time-limit term-fifo grouped False))
  (<- by-kill Unbounded (cancelled-without-a-time-limit kill-fifo grouped True))
  (<- snapshot MeterSnapshot (meter-by place (+ (time.monotonic) GONE-GRACE)))
  (CountedStops :terminated (.get snapshot.counters CANCEL-TERMINATED-METRIC 0.0)
                :killed (.get snapshot.counters CANCEL-KILLED-METRIC 0.0)
                :survivors (+ by-term.survivors by-kill.survivors)))


(defk settled-within [scenario what [handler offloaded-subprocess-handler]]
  {:pre [(: scenario Program) (: what str) (: handler Callable)] :post [(: % "筋書きの答え(型は筋書きごと)")]
   :tags {:context "process-test" :role "program"}}
  "筋書きを、検の thread とは別の daemon の thread の新しい VM で handler(既定 offloaded-subprocess-handler)と scheduled の下に 1 回回し、
   SCENARIO-LIMIT 秒まで答えを待つため。過ぎたら what を名指して落とす(固まった thread は daemon のまま置く — 頭の註)。"
  (val running (.submit (ThreadPerCall) run-detached (scheduled (with_handlers [handler] scenario))))
  (try
    (.result running :timeout SCENARIO-LIMIT)
    (except [TimeoutError]
      (raise (AssertionError (.format "{} が {} 秒で終わらない(scheduler の thread が固まった)" what SCENARIO-LIMIT))))))


(deftest test-a-run-cancelled-before-its-call-starts-moves-on-and-starts-no-child [monkeypatch tmp-path]
  ;; (a)(頭の註): 入り直せない錠なら、取り消しの中で scheduler の thread が固まり、SCENARIO-LIMIT で赤になる。
  (val pid-file (str (/ tmp-path "pid")))
  (<- seen BeforeStart (settled-within (cancelled-before-the-call-starts monkeypatch pid-file) "仕事が始まる前の取り消し"))
  (assert seen.ended-by-cancel seen)
  (assert seen.held-cancelled seen)
  ;; 取り消した仕事は gate が開いても始まらず、子は起きない。
  (assert (not seen.started) seen)
  (assert (not seen.pid-written) seen)
  (assert (= seen.next-outcome NEXT-OUTCOME) seen))


(deftest test-a-run-cancelled-while-its-child-runs-moves-on-and-leaves-no-child [#^ bool grouped tmp-path]
  {:params {"grouped" [False True]}}
  ;; (b)(頭の註): 取り消しは子の終わりを待たずに次の task へ進み、子は上限と猶予の内に居なくなる。
  (val fifo (str (/ tmp-path "pid.fifo")))
  (<- seen WhileRunning (settled-within (cancelled-while-the-child-runs fifo grouped) "子が走っている間の取り消し"))
  (assert seen.ended-by-cancel seen)
  (assert (= seen.next-outcome NEXT-OUTCOME) seen)
  (assert (< seen.moved-on-seconds CHILD-LIMIT)
          (.format "取り消しから次の子の答えまで {:.2f} 秒(子の上限 {} 秒を待った)" seen.moved-on-seconds CHILD-LIMIT))
  (assert (not seen.alive-at-deadline)
          (.format "子 {} が上限 {} 秒と猶予 {} 秒の後も残っている" seen.pid CHILD-LIMIT GONE-GRACE)))


(deftest test-a-cancelled-child-without-a-time-limit-is-stopped-within-the-grace [#^ bool grouped tmp-path]
  {:params {"grouped" [False True]}}
  ;; (c)(頭の註): 取り消しの手当てが子を止めなければ、上限の無い子(と孫)は 30 秒残って赤になる。
  (val fifo (str (/ tmp-path "pid.fifo")))
  (<- seen Unbounded (settled-within (cancelled-without-a-time-limit fifo grouped False) "上限の無い子の取り消し"))
  (assert seen.ended-by-cancel seen)
  (assert (= seen.next-outcome NEXT-OUTCOME) seen)
  (assert (< seen.moved-on-seconds CANCEL-GRACE)
          (.format "取り消しから次の子の答えまで {:.2f} 秒(子の止まりを待った)" seen.moved-on-seconds))
  (assert (= seen.survivors #())
          (.format "子と孫 {} のうち {} が、取り消しから猶予 {} 秒と {} 秒の後も残っている" seen.pids seen.survivors CANCEL-GRACE GONE-GRACE)))


(deftest test-a-cancelled-child-that-ignores-term-is-killed-after-the-grace [#^ bool grouped tmp-path]
  {:params {"grouped" [False True]}}
  ;; (d)(頭の註): SIGTERM を無視する子(と孫)は、猶予の後に SIGKILL を送らなければ残って赤になる。
  (val fifo (str (/ tmp-path "pid.fifo")))
  (<- seen Unbounded (settled-within (cancelled-without-a-time-limit fifo grouped True) "SIGTERM を無視する子の取り消し"))
  (assert seen.ended-by-cancel seen)
  (assert (= seen.next-outcome NEXT-OUTCOME) seen)
  (assert (< seen.moved-on-seconds CANCEL-GRACE)
          (.format "取り消しから次の子の答えまで {:.2f} 秒(子の止まりを待った)" seen.moved-on-seconds))
  (assert (= seen.survivors #())
          (.format "子と孫 {} のうち {} が、取り消しから猶予 {} 秒と {} 秒の後も残っている" seen.pids seen.survivors CANCEL-GRACE GONE-GRACE)))


(deftest test-children-stopped-by-cancel-are-counted-by-how-they-stopped [#^ bool grouped tmp-path caplog]
  {:params {"grouped" [False True]}}
  ;; (e)(頭の註): 止めた子を数えなければ、counter は 0 のまま(または無いまま)で赤になる。置き場の名は検ごとに分ける(process に 1 つ)。
  ;; 止めた子 2 つは log にも 1 行ずつ、argv の頭(/bin/sh)を名指して出る。
  (val place (.format "test-offloaded-process-cancel:{}" tmp-path))
  (<- seen CountedStops (settled-within (counted-cancels (str (/ tmp-path "term.fifo")) (str (/ tmp-path "kill.fifo")) grouped place)
                                        "止めた子の数え"
                                        (metered-offloaded-subprocess-handler (process-meter-handler place (MeterSettings)))))
  (assert (= seen.survivors #()) seen)
  (assert (= seen.terminated 1.0) (.format "SIGTERM で止まった数が {}(1 のはず)" seen.terminated))
  (assert (= seen.killed 1.0) (.format "SIGKILL まで要った数が {}(1 のはず)" seen.killed))
  (val lines (lfor record caplog.records :if (= record.name process-module.__name__) (.getMessage record)))
  (assert (and (= (len lines) 2) (all (gfor line lines (in "'/bin/sh'" line)))) lines))
