;;; 子 process の終わりを待つ効果(AwaitProcessExit・AwaitWarmChildExit)と、Linux の答え手 pidfd-exit-handler(pidfd_exit.hy・
;;; agora-redesign #3871 の単位 1)。本物の process(sleep・true・sh)を使う。
;;;
;;;   - 立てた子の終わりで答え、終わる前には答えない。待ちは子を回収しない(終了 code は後の PollProcess が答える)。
;;;   - 立てていない pid は ProcessNotChild。既に終わった(回収していない)子は、その場で答える。
;;;   - 子でない process(待ちの子から分けた子と同じ立場 — sh が背景に起こして自分は終わる)は、pid と start-ticks の組で待つ。
;;;     start-ticks が違う(pid が使い回された)なら、その場で答える(居る別の process の終わりを待たない)。
;;;   - 取り消した待ちは fd を残さず、待たれない coroutine の警告も出さない。
;;;   - pidfd を取る関数は、居ない pid に None を返す。
(require doeff-hy.macros [defk deftest <- val])
(import gc)
(import os)
(import subprocess)
(import time)
(import warnings)
(import asyncio)
(import doeff [run with-handlers])
(import doeff_core_effects.effects [Await])
(import doeff_core_effects.handlers [await-handler])
(import doeff_core_effects.scheduler [scheduled Spawn Race Cancel Wait TaskCancelledError])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.os_warm_process [os-warm-process-handler proc-stat-of])
(import doeff_core_effects.process_effects [StartProcess PollProcess AwaitProcessExit ProcessStarted ProcessExited ProcessEnded
                                            ProcessNotChild])
(import doeff_core_effects.warm_effects [AwaitWarmChildExit])
(import doeff_core_effects.pidfd_exit [pidfd-exit-handler pidfd-of])

;; 終わりから答えまでの遅れの上限(秒)と、終わる前に答えない事を見る下限の余裕(秒)。
(val PROMPT 0.3)


(defn #^ object handled [#^ object program]
  "本物の答え手の組(外側が先)の上で program を回すため。"
  (run (scheduled (with-handlers [(await-handler) subprocess-handler os-warm-process-handler pidfd-exit-handler] program))))


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


(defn #^ tuple timed [#^ object program]
  "program を回し、#(答え 秒) を返すため。"
  (setv started (time.monotonic))
  (setv answer (handled program))
  #(answer (- (time.monotonic) started)))


(defn #^ int stray-pid [#^ str seconds]
  "子でない process を 1 つ起こすため: sh が背景に sleep を起こして自分は終わる(sleep の親は init か subreaper — この process の子でない)。"
  (int (.strip (. (subprocess.run ["sh" "-c" (.format "sleep {} >/dev/null 2>&1 & echo $!" seconds)] :capture-output True :text True :check True)
                  stdout))))


(defn #^ int start-ticks-of [#^ int pid]
  "pid の process の start-ticks(/proc/<pid>/stat の 22 番目の欄)を読むため。"
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
  "この process の開いている fd の数を数えるため。"
  (len (os.listdir "/proc/self/fd")))


(deftest test-a-cancelled-wait-leaves-no-descriptor-and-no-warning
  ;; 競り負けて取り消された待ちは、pidfd を閉じ、待たれない coroutine を残さない。
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


(deftest test-the-pidfd-of-a-pid-that-is-gone-is-none
  ;; 居ない pid(回収し終えた子)には None。
  (setv child (subprocess.Popen ["true"]))
  (.wait child)
  (assert (is (run (pidfd-of child.pid)) None)))
