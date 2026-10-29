;;; 子 process の終わりを待つ効果(AwaitProcessExit・AwaitWarmChildExit)と、答え手 process-exit-handler(process_exit.hy・
;;; agora-redesign #3871 の単位 1)。本物の process(sleep・true・sh)を使う。終わると読める fd の開き方は機体ごと(Linux = pidfd・
;;; macOS = kqueue の EVFILT_PROC)で、同じ検がどちらの機体でもその機体の開き方を測る。
;;;
;;;   - 立てた子の終わりで答え、終わる前には答えない。待ちは子を回収しない(終了 code は後の PollProcess が答える)。
;;;   - 立てていない pid は ProcessNotChild。既に終わった(回収していない)子は、その場で答える。
;;;   - 子でない process(待ちの子から分けた子と同じ立場 — sh が背景に起こして自分は終わる)は、pid と start-ticks の組で待つ。
;;;     start-ticks が違う(pid が使い回された)なら、その場で答える(居る別の process の終わりを待たない)。
;;;   - 取り消した待ちは fd を残さず、待たれない coroutine の警告も出さない。
;;;   - 終わると読める fd を取る関数(機体の開き方)は、居ない pid に None を返す。
;;;   - 走っている子の終わりの待ちは、scheduler の止まりと数えない。子が終わったのに知らせの届かない待ちは止まりと数える
;;;     (条 S1 — packages/doeff-core-effects/architecture.hy・card ki-6222e22d4179)。
(require doeff-hy.macros [defk deff deftest <- val])
(import gc)
(import logging)
(import os)
(import platform)
(import select)
(import sys)
(import subprocess)
(import threading)
(import time)
(import warnings)
(import asyncio)
(import collections.abc [Callable])
(import doeff [run with-handlers])
(import doeff.program [Pure])
(import doeff_core_effects [Await])
(import doeff_core_effects [await-handler])
(import doeff_core_effects [scheduled Spawn Race Cancel Wait TaskCancelledError])
(import doeff_core_effects._scheduler :as scheduler-module)
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.os_warm_process [os-warm-process-handler proc-stat-of])
(import doeff_core_effects.process_effects [StartProcess PollProcess AwaitProcessExit ProcessStarted ProcessExited ProcessEnded
                                            ProcessNotChild])
(import doeff_core_effects.warm_effects [AwaitWarmChildExit])
(import doeff_core_effects.process_exit [process-exit-handler exit-fd-of])
(import doeff_core_effects.process_exit :as process-exit-module)
(import doeff_core_effects.pidfd_exit [pidfd-of])
(import doeff_core_effects.os_process [STARTED-CHILDREN])
(import pytest)

;; この file の待ちは実時間が対象(何が対象かは名簿 tests/realtime_tests.toml のこの file の行)— realtime を名乗る file の中の眠りを、
;; 検の眠りの規則(doeff-linter の DOEFF211)は当てない。
(val pytestmark pytest.mark.realtime)

;; 終わりから答えまでの遅れの上限(秒)と、終わる前に答えない事を見る下限の余裕(秒)。
(val PROMPT 0.3)
;; 止まりの検で縮める、scheduler の止まりの見張りの周期(秒 — 本番は 30 秒)。
(val STALL-INTERVAL 0.05)


(defn #^ object handled [#^ object program #^ (| str None) [implementation None]]
  "本物の答え手の組(外側が先)の上で program を回すため。implementation = scheduler の実装(None = 既定)。"
  (run (scheduled (with-handlers [await-handler subprocess-handler os-warm-process-handler process-exit-handler] program)
                  :implementation implementation)))


(defk started-then-awaited [argv]
  {:pre [(: argv tuple)] :post [(: % tuple)]}
  "子を立て、その終わりを待ち、待った後に PollProcess で問う: #(待ちの答え Poll の答え)。"
  (<- started ProcessStarted (StartProcess :argv argv))
  (<- ended (| ProcessEnded ProcessNotChild) (AwaitProcessExit started.pid))
  (<- polled (PollProcess started.pid))
  #(ended polled))


(defk awaited [wait]
  {:pre [(: wait (| AwaitProcessExit AwaitWarmChildExit))] :post [(: % (| ProcessEnded ProcessNotChild))]}
  "待ちの効果を 1 つ出し、答えを返す。"
  (<- answer (| ProcessEnded ProcessNotChild) wait)
  answer)


(defn #^ tuple timed [#^ object program #^ (| str None) [implementation None]]
  "program を回し、#(答え 秒) を返すため。"
  (setv started (time.monotonic))
  (setv answer (handled program implementation))
  #(answer (- (time.monotonic) started)))


;; 子の終わりの待ちがこの秒数を越えて続いた時に、証拠(子の state・waitid・pidfd・scheduler の待ちの列)を残し始める。
(val EVIDENCE-AFTER 10.0)
;; 証拠を取る間隔(秒)。
(val EVIDENCE-EVERY 5.0)


(defn #^ str child-evidence [#^ int pid]
  "子 pid の今の様子を 1 行にするため(待ちを乱さない読みだけ — 回収しない・fd は取って閉じる): /proc/<pid>/stat の state・
   waitid(WNOWAIT で回収しない)・pidfd の poll・Popen の returncode。"
  (try (setv stat (. (open (.format "/proc/{}/stat" pid)) (read)))
       (setv state (get (.split (get (.rsplit stat ")" 1) 1)) 0))
       (except [error OSError] (setv state (.format "gone({!r})" error))))
  (try (setv waited (os.waitid os.P_PID pid (| os.WEXITED os.WNOHANG os.WNOWAIT)))
       (except [error OSError] (setv waited (.format "error({!r})" error))))
  (try (setv fd (run (pidfd-of pid)))
       (if (is fd None)
           (setv polled "no-pidfd")
           (try (setv poller (select.poll))
                (.register poller fd select.POLLIN)
                (setv polled (.poll poller 0))
                (finally (os.close fd))))
       (except [error Exception] (setv polled (.format "error({!r})" error))))
  (setv found (.find STARTED-CHILDREN pid))
  (.format "pid={} stat-state={} waitid={} pidfd-poll={} popen-returncode={}" pid state waited polled
           (if (is found None) "not-in-table" (. (get found 0) returncode))))


(defclass ChildWaitEvidence []
  "走っている子の終わりの待ちが after 秒を越えた時に、子の様子と scheduler の待ちの列を lines に残すため(card ki-3a95051d3fb8 —
   日次の回で約 60 秒止まって落ちた時に、1 回の失敗の記録から原因を決める)。待ちの上限も検の厳しさも変えない。"
  (defn __init__ [self [after EVIDENCE-AFTER] [every EVIDENCE-EVERY]]
    (setv self.after after self.every every self.lines [] self.pid None self.began (time.monotonic)
          self.finished (threading.Event) self.lock (threading.Lock)))

  (defn note [self text]
    (setv line (.format "[+{:.1f}s] {}" (- (time.monotonic) self.began) text))
    (with [self.lock] (.append self.lines line))
    (.write sys.stderr (+ "child-wait-evidence " line "\n"))
    (.flush sys.stderr))

  (defn watch [self]
    "別 thread: after 秒を越えても終わらなければ、every 秒ごとに子の様子を残す(終わったら止まる)。"
    (when (not (.wait self.finished :timeout self.after))
      (while (not (.is-set self.finished))
        (.note self (if (is self.pid None) "pid not started yet" (child-evidence self.pid)))
        (.wait self.finished :timeout self.every))))

  (defn wrap-source-alive [self original]
    "止まりの判定が呼ぶ「子はまだ終わっていないか」の読み(process_exit の child-running)を包み、after 秒を越えた読みごとに、
     scheduler がその待ちを判じた時刻と読みの答えを残す(= scheduler の止まりの見張りが今見ている待ちの列。Python と Rust の
     scheduler のどちらも、この読みを経由する)。"
    (defn probe [pid]
      (setv alive (run (original pid)))
      (when (> (- (time.monotonic) self.began) self.after)
        (.note self (.format "scheduler probes pending watched wait: pid={} source_alive={}" pid alive)))
      (Pure alive))
    probe)

  (defn text [self]
    (.join "\n" self.lines)))


(defk started-then-awaited-watched [argv evidence]
  {:pre [(: argv tuple) (: evidence ChildWaitEvidence)] :post [(: % tuple)]}
  "started-then-awaited と同じ待ちで、立てた子の pid を evidence に教える。"
  (<- started ProcessStarted (StartProcess :argv argv))
  (setv evidence.pid started.pid)
  (<- ended (| ProcessEnded ProcessNotChild) (AwaitProcessExit started.pid))
  (<- polled (PollProcess started.pid))
  #(ended polled))


(defn #^ ChildWaitEvidence run-with-evidence [#^ object monkeypatch #^ tuple argv #^ str implementation #^ float after #^ float every]
  "argv の子の終わりの待ちを回し、待ちが after 秒を越えた間の証拠を残した ChildWaitEvidence を返す(待った答えは .answers)。"
  (setv evidence (ChildWaitEvidence after every))
  (.setattr monkeypatch process-exit-module "child_running" (.wrap-source-alive evidence process-exit-module.child_running))
  (setv watcher (threading.Thread :target evidence.watch :daemon True))
  (.start watcher)
  (try (setv evidence.answers (get (timed (started-then-awaited-watched argv evidence) implementation) 0))
       (finally (.set evidence.finished)
                (.join watcher)))
  evidence)


(defn #^ int stray-pid [#^ str seconds]
  "子でない process を 1 つ起こすため: sh が背景に sleep を起こして自分は終わる(sleep の親は init か subreaper — この process の子でない)。"
  (int (.strip (. (subprocess.run ["sh" "-c" (.format "sleep {} >/dev/null 2>&1 & echo $!" seconds)] :capture-output True :text True :check True)
                  stdout))))


(defn #^ int start-ticks-of [#^ int pid]
  "pid の process の start-ticks(Linux = /proc/<pid>/stat の 22 番目の欄・macOS = libproc の起動の時刻)を読むため。"
  (. (run (proc-stat-of pid)) start-ticks))


(deftest test-the-wait-ends-when-the-started-child-ends
  ;; 0.5 秒の子: 終わる前には答えず、終わってから PROMPT 秒の内に答える。待ちは回収しないので、後の PollProcess が終了 code 0 を答える。
  (setv #(answers seconds) (timed (started-then-awaited #("sleep" "0.5"))))
  (setv #(ended polled) answers)
  (assert (isinstance ended ProcessEnded) ended)
  (assert (<= 0.45 seconds (+ 0.5 PROMPT)) seconds)
  (assert (= polled (ProcessExited :pid ended.pid :exit-code 0)) polled))


(deftest test-a-pid-the-handler-did-not-start-is-not-a-child
  ;; 立てていない pid(自分の process)は ProcessNotChild — 待たない。
  (setv #(answer seconds) (timed (awaited (AwaitProcessExit (os.getpid)))))
  (assert (= answer (ProcessNotChild :pid (os.getpid))) answer)
  (assert (< seconds PROMPT) seconds))


(deftest test-a-child-that-already-ended-answers-at-once
  ;; 終わったがまだ回収していない子は、その場で答える。
  (setv started (handled (StartProcess :argv #("true"))))
  (time.sleep 0.3)
  (setv #(answer seconds) (timed (awaited (AwaitProcessExit started.pid))))
  (assert (= answer (ProcessEnded :pid started.pid)) answer)
  (assert (< seconds PROMPT) seconds)
  (handled (PollProcess started.pid)))


(deftest test-the-wait-ends-when-a-process-that-is-not-a-child-ends
  ;; 子でない process も、pid と start-ticks の組で、その終わりを待つ。
  (setv pid (stray-pid "0.6"))
  (setv ticks (start-ticks-of pid))
  (setv #(answer seconds) (timed (awaited (AwaitWarmChildExit :pid pid :start-ticks ticks))))
  (assert (= answer (ProcessEnded :pid pid)) answer)
  (assert (<= 0.3 seconds (+ 0.6 PROMPT)) seconds))


(deftest test-a-reused-pid-is-not-waited-on
  ;; start-ticks が違う(pid が別の process に使い回された)なら、居る process の終わりを待たずに、その場で答える。
  (setv pid (stray-pid "5"))
  (try
    (setv #(answer seconds) (timed (awaited (AwaitWarmChildExit :pid pid :start-ticks (+ (start-ticks-of pid) 1)))))
    (assert (= answer (ProcessEnded :pid pid)) answer)
    (assert (< seconds PROMPT) seconds)
    (finally (os.kill pid 9))))


(defk raced-away [pid]
  {:pre [(: pid int)] :post [(: % None)]}
  "pid の終わりの待ちを、0.2 秒の待ちと競わせて負けさせ、負けた待ちを取り消して、取り消しの済むまで待つ。"
  (<- waiting (Spawn (awaited (AwaitProcessExit pid))))
  (<- short (Spawn (Await (asyncio.sleep 0.2))))
  (<- (Race waiting short))
  (<- (Cancel waiting))
  (try (<- (Wait waiting))
       (except [TaskCancelledError] None))
  None)


(defn #^ int open-descriptors []
  "この process の開いている fd の数を数えるため(/dev/fd は Linux と macOS の両方に在る)。"
  (len (os.listdir "/dev/fd")))


(deftest test-a-cancelled-wait-leaves-no-descriptor-and-no-warning
  ;; 競り負けて取り消された待ちは、終わりの fd を閉じ、待たれない coroutine を残さない。
  (setv started (handled (StartProcess :argv #("sleep" "5"))))
  (setv before (open-descriptors))
  (with [_ (warnings.catch-warnings)]
    (warnings.simplefilter "error" RuntimeWarning)
    (handled (raced-away started.pid))
    (time.sleep 0.2)
    (gc.collect))
  (setv after (open-descriptors))
  (os.kill started.pid 9)
  (handled (PollProcess started.pid))
  (assert (= after before) #(before after)))


(deftest test-the-exit-fd-of-a-pid-that-is-gone-is-none
  ;; 居ない pid(回収し終えた子)には None。
  (setv child (subprocess.Popen ["true"]))
  (.wait child)
  (assert (is (run (exit-fd-of child.pid)) None)))


(defclass OnStallReport [logging.Handler]
  "scheduler が止まりの警告を書いた時に on-report を呼ぶ(どの thread からでも)。"
  (deff __init__ [self on-report]  ; defk にできない: logging.Handler の作り手(VM の外)
    {:pre [(: self OnStallReport) (: on-report Callable)] :post [(: % None)]}
    "止まりの警告を見た時の呼びを受けるため。"
    (.__init__ (super) :level logging.WARNING)
    (setv self.on-report on-report))

  (deff emit [self record]  ; defk にできない: logging が記録ごとに呼ぶ callback
    {:pre [(: self OnStallReport) (: record logging.LogRecord)] :post [(: % None)]}
    "記録が scheduler の止まりの警告なら on-report を呼ぶため。"
    (when (in "scheduler stalled" (.getMessage record))
      (self.on-report))))


(deftest test-waiting-on-a-running-child-is-not-a-stall [implementation monkeypatch caplog]
  {:params {"implementation" ["python" "rust"]}}
  ;; 条 S1: 走っている子の終わりの待ちは、終わりの知らせの出どころ(終わると読める fd)が登録され、子が生きている間は止まりでない。
  ;; 子の 0.5 秒は、縮めた止まりの見張りの周期の 10 倍。直す前は周期ごとに警告を書いた(courier の job で 30 秒ごと・card ki-6222e22d4179)。
  ;; 待ちが EVIDENCE-AFTER 秒を越えたら、子の様子と scheduler の待ちの列を残す(失敗の記録に証拠が出る — card ki-3a95051d3fb8)。
  (.setattr monkeypatch scheduler-module "EXTERNAL_STALL_LOG_INTERVAL_SECONDS" STALL-INTERVAL)
  (.set-level caplog logging.WARNING :logger scheduler-module.__name__)
  (val evidence (run-with-evidence monkeypatch #("sleep" "0.5") implementation EVIDENCE-AFTER EVIDENCE-EVERY))
  (val answers evidence.answers)
  (val reports (lfor record caplog.records :if (in "scheduler stalled" (.getMessage record)) (.getMessage record)))
  (assert (isinstance (get answers 0) ProcessEnded) #(answers (.text evidence)))
  (assert (= reports []) #(reports (.text evidence))))


(deftest test-a-long-wait-on-a-child-leaves-evidence [implementation monkeypatch]
  {:params {"implementation" ["python" "rust"]}}
  ;; 証拠を残す仕組みの検: 1 秒の子の待ちを 0.3 秒で証拠を取り始める設定にして、子の state・waitid・pidfd・scheduler が見ている待ちの列が
  ;; 残る事を見る(本番の閾は EVIDENCE-AFTER)。
  (.setattr monkeypatch scheduler-module "EXTERNAL_STALL_LOG_INTERVAL_SECONDS" STALL-INTERVAL)
  (val evidence (run-with-evidence monkeypatch #("sleep" "1") implementation 0.3 0.2))
  (val text (.text evidence))
  (assert (isinstance (get evidence.answers 0) ProcessEnded) text)
  (for [needle ["stat-state=S" "waitid=None" "pidfd-poll=[]" "popen-returncode=None" "scheduler probes pending watched wait" "source_alive=True"]]
    (assert (in needle text) #(needle text))))


(deftest test-a-child-that-ended-without-its-exit-notice-is-a-stall [implementation monkeypatch caplog]
  {:params {"implementation" ["python" "rust"]}}
  ;; 条 S1 の失敗ケース: 子は終わったのに終わりの知らせが届かない待ち(失われた起こし — #495b の見張りの対象)は止まり。
  ;; 終わると読める fd の開き方を、書かれるまで読めない pipe に差し替え、scheduler が止まりを報じた時に初めて書く。報じなければ
  ;; 40 周期の後に書かれ、検は赤。
  (.setattr monkeypatch scheduler-module "EXTERNAL_STALL_LOG_INTERVAL_SECONDS" STALL-INTERVAL)
  (.set-level caplog logging.WARNING :logger scheduler-module.__name__)
  (val pipe-ends (os.pipe))
  (val never-ready (get pipe-ends 0))
  (val notice (get pipe-ends 1))
  (.setattr monkeypatch process-exit-module "EXIT_FD_OPENERS" {(platform.system) (fn [pid] (Pure (os.dup never-ready)))})
  (val reported (threading.Event))
  (val watcher (OnStallReport (fn [] (.set reported))))
  (val logger (logging.getLogger scheduler-module.__name__))
  (deff notice-late []  ; defk にできない: threading.Thread が別の thread で呼ぶ口(VM の外)
    {:pre [] :post [(: % None)]}
    "止まりが報じられた時(報じられなければ 40 周期の後)に、終わりの知らせの pipe を読めるようにするため。"
    (.wait reported :timeout (* STALL-INTERVAL 40))
    (os.write notice b"x")
    None)
  (.addHandler logger watcher)
  (.start (threading.Thread :target notice-late :daemon True))
  (val answers (try (get (timed (started-then-awaited #("true")) implementation) 0)
                    (finally (.removeHandler logger watcher)
                             (os.close never-ready)
                             (os.close notice))))
  (assert (isinstance (get answers 0) ProcessEnded) answers)
  (assert (.is-set reported) "子が終わったのに知らせの届かない待ちは、止まりと報じる"))
