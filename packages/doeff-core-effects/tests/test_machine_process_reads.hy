;;; 子 process の終わりの待ちと process の様子の読みの、機体ごとの仕組みの選び(macOS の機体で直接
;;; 動く worker が子を立てて待てるように)。
;;;
;;;   - 終わると読める fd の開き方: Linux = pidfd-of(pidfd_open)・macOS(Darwin)= kqueue-fd-of(kqueue の EVFILT_PROC / NOTE_EXIT)。
;;;     知らない機体は ExitWaitUnavailable で名指す — 黙って周期の問い直しにしない。
;;;   - macOS の process の様子は libproc の proc_pidinfo の答え(struct proc_bsdinfo・struct proc_taskinfo)から読む。bytes の並びの
;;;     読みは機体に依らない関数なので、Linux でも <sys/proc_info.h> の並びで作った bytes で確かめる(本物の proc_pidinfo を呼ぶ検は
;;;     macOS の上でだけ走る — test_process_exit_wait.hy の振る舞いの検が、その機体の開き方と読みを本物の process で測る)。
(require doeff-hy.macros [deftest val])
(import struct)
(import pytest)
(import doeff [run])
(import doeff_core_effects.process_exit [exit-fd-opener-for ExitWaitUnavailable])
(import doeff_core_effects.pidfd_exit [pidfd-of])
(import doeff_core_effects.kqueue_exit [kqueue-fd-of])
(import doeff_core_effects.darwin_proc [bsd-info-stat task-info-threads BSD-INFO-SIZE TASK-INFO-SIZE])
(import doeff_core_effects.os_warm_process [ProcStat])

;; <sys/proc_info.h> の struct proc_bsdinfo の並び(136 byte): uint32 × 5(flags status xstatus pid ppid)・uid / gid × 6・rfu_1・
;; comm[16]・name[32]・uint32 × 5(nfiles pgid pjobc e_tdev e_tpgid)・int32 nice・uint64 start_tvsec・uint64 start_tvusec。
(val BSD-INFO-LAYOUT "=5I6II16s32s5Ii2Q")
;; struct proc_taskinfo の並び(96 byte): uint64 × 6・int32 × 12(10 番目 = pti_threadnum)。
(val TASK-INFO-LAYOUT "=6Q12i")


(defn #^ bytes bsd-info [#^ int status #^ int seconds #^ int micros]
  "status と起動の時刻だけを持つ proc_bsdinfo の bytes を作るため(他の欄は 0・名は sleep)。"
  (struct.pack BSD-INFO-LAYOUT 0 status 0 4242 1 501 20 501 20 501 20 0 b"sleep" b"sleep" 0 4242 0 0 0 0 seconds micros))


(deftest test-the-exit-fd-opener-follows-the-machine
  ;; Linux は pidfd・macOS は kqueue。どちらの開き方も、居ない pid に None を返す同じ形(pid → fd | None)。
  (assert (is (exit-fd-opener-for "Linux") pidfd-of))
  (assert (is (exit-fd-opener-for "Darwin") kqueue-fd-of)))


(deftest test-a-machine-without-an-exit-wait-is-named
  ;; 失敗ケース: 開き方を知らない機体は、機体の名を持った ExitWaitUnavailable — 周期の問い直しへ黙って落ちない。
  (with [caught (pytest.raises ExitWaitUnavailable)]
    (exit-fd-opener-for "Windows"))
  (assert (in "Windows" (str caught.value)) caught.value))


(deftest test-the-layouts-are-the-sizes-libproc-answers
  ;; 並びの大きさは proc_pidinfo が書く byte 数と同じ(違えば読みの位置がずれる)。
  (assert (= (struct.calcsize BSD-INFO-LAYOUT) BSD-INFO-SIZE 136))
  (assert (= (struct.calcsize TASK-INFO-LAYOUT) TASK-INFO-SIZE 96)))


(deftest test-the-bsd-info-reads-the-state-and-the-start
  ;; 起動の時刻(秒と μ秒)を 1 つの整数(μ秒)にして start-ticks にし、status を /proc の state の 1 文字に写す(5 = SZOMB = Z)。
  (assert (= (bsd-info-stat (bsd-info 3 1760000000 123456)) (ProcStat :state "S" :start-ticks 1760000000123456)))
  (assert (= (bsd-info-stat (bsd-info 2 1 0)) (ProcStat :state "R" :start-ticks 1000000)))
  (assert (= (. (bsd-info-stat (bsd-info 5 7 8)) state) "Z")))


(deftest test-a-bsd-info-of-the-wrong-size-is-refused
  ;; 失敗ケース: 大きさの違う答え(proc_pidinfo が途中まで書いた・並びの違う版)は読まずに断る — ずれた位置の数を start-ticks にしない。
  (with [caught (pytest.raises ValueError)]
    (bsd-info-stat (cut (bsd-info 3 1 2) 0 120)))
  (assert (in "136" (str caught.value)) caught.value))


(deftest test-the-task-info-reads-the-thread-count
  ;; pti_threadnum(int32 の 10 番目)を thread の本数として読む。
  (val raw (struct.pack TASK-INFO-LAYOUT 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 3 1 31))
  (assert (= (task-info-threads raw) 3)))
