;;; macOS(Darwin)の process の様子の読み — libproc の proc_pidinfo(<libproc.h>・<sys/proc_info.h>)が書く struct の bytes を読む。
;;; /proc の無い macOS で、待ちの子の終わりの読み(os_warm_process.hy の proc-stat-of)と、待ちの子の thread の本数(own-thread-count)に
;;; 答える(Linux の読みは os_warm_process.hy の /proc のまま)。
;;;
;;;   bsd-info-stat      struct proc_bsdinfo(PROC_PIDTBSDINFO・136 byte)から state と start-ticks を読む。status(SIDL 1・SRUN 2・
;;;                      SSLEEP 3・SSTOP 4・SZOMB 5)を /proc の state の文字(I・R・S・T・Z)へ写し、起動の時刻(秒と μ秒)を 1 つの
;;;                      整数(μ秒)にして start-ticks にする。
;;;   task-info-threads  struct proc_taskinfo(PROC_PIDTASKINFO・96 byte)の pti_threadnum を読む。
;;;   大きさの違う答え(途中まで書いた・並びの違う版)は ValueError で断る — ずれた位置の数を値にしない。bytes の読みは機体に依らない
;;;   ので Linux の検でも確かめ、proc_pidinfo を呼ぶ darwin-proc-stat・darwin-thread-count は macOS の上でだけ呼ばれる。
(require doeff-hy.macros [defk val])
(val MODULE-TAGS {:context "process" :role "foundation"})
(import ctypes)
(import errno)
(import os)
(import struct)
(import doeff_core_effects.process_stat [ProcStat])

;; proc_pidinfo の flavor の番号と、その答えの struct の大きさ・並び(<sys/proc_info.h>)。
(val PROC-PIDTBSDINFO 3)
(val PROC-PIDTASKINFO 4)
(val BSD-INFO-SIZE 136)
(val TASK-INFO-SIZE 96)
;; proc_bsdinfo のうち読む欄の位置: pbi_status(2 番目の uint32)と pbi_start_tvsec・pbi_start_tvusec(最後の uint64 × 2)。
(val BSD-STATUS-OFFSET 4)
(val BSD-START-OFFSET 120)
;; proc_taskinfo の pti_threadnum の位置(uint64 × 6 の後の int32 の 10 番目)。
(val TASK-THREADS-OFFSET 84)
;; macOS の status → /proc の state の文字。
(val STATUS-STATES {1 "I" 2 "R" 3 "S" 4 "T" 5 "Z"})
(val LIBPROC-PATH "/usr/lib/libproc.dylib")


(defk sized [raw size name]
  {:pre [(: raw bytes) (: size int) (: name str)] :post [(: % bytes)] :tags {:context "process" :role "foundation"}}
  "proc_pidinfo の答えが struct の大きさちょうどかを確かめるため。違えば大きさと struct の名を持った ValueError。"
  (when (!= (len raw) size)
    (raise (ValueError (.format "{} の大きさが {} byte でない: {} byte" name size (len raw)))))
  raw)


(defk bsd-info-stat [raw]
  {:pre [(: raw bytes)] :post [(: % ProcStat)] :tags {:context "process" :role "foundation"}}
  "struct proc_bsdinfo の bytes から state と start-ticks を読むため(頭の註)。"
  (<- whole bytes (sized raw BSD-INFO-SIZE "proc_bsdinfo"))
  (val status (get (struct.unpack-from "=I" whole BSD-STATUS-OFFSET) 0))
  (val start (struct.unpack-from "=2Q" whole BSD-START-OFFSET))
  (when (not-in status STATUS-STATES)
    (raise (ValueError (.format "proc_bsdinfo の status {} を知らない(SIDL 1〜SZOMB 5 の外)" status))))
  (ProcStat :state (get STATUS-STATES status) :start-ticks (+ (* (get start 0) 1000000) (get start 1))))


(defk task-info-threads [raw]
  {:pre [(: raw bytes)] :post [(: % int)] :tags {:context "process" :role "foundation"}}
  "struct proc_taskinfo の bytes から thread の本数を読むため(頭の註)。"
  (<- whole bytes (sized raw TASK-INFO-SIZE "proc_taskinfo"))
  (get (struct.unpack-from "=i" whole TASK-THREADS-OFFSET) 0))


(defk pid-info [pid flavor size]
  {:pre [(: pid int) (: flavor int) (: size int)] :post [(: % (| bytes None))] :tags {:context "process" :role "foundation"}}
  "proc_pidinfo(pid, flavor, 0, buffer, size)を呼んで書かれた bytes を返すため。居ない pid(ESRCH)は None・他の失敗は OSError。
   libproc を直に呼ぶのはこの 1 か所。"
  (val libproc (ctypes.CDLL LIBPROC-PATH :use-errno True))
  (val buffer (ctypes.create-string-buffer size))
  (val written (.proc-pidinfo libproc (ctypes.c-int pid) (ctypes.c-int flavor) (ctypes.c-uint64 0) buffer (ctypes.c-int size)))
  (val failure (if (<= written 0) (ctypes.get-errno) 0))
  (cond
    (> written 0) (bytes (cut buffer.raw 0 written))
    (= failure errno.ESRCH) None
    True (raise (OSError failure (.format "proc_pidinfo({}, {}) が断った: {}" pid flavor (os.strerror failure))))))


(defk darwin-proc-stat [pid]
  {:pre [(: pid int)] :post [(: % (| ProcStat None))] :tags {:context "process" :role "foundation"}}
  "macOS で pid の process の state と start-ticks を読むため。居ない = None。"
  (when (<= pid 0)
    (return None))
  (<- raw (| bytes None) (pid-info pid PROC-PIDTBSDINFO BSD-INFO-SIZE))
  (if (is raw None)
      None
      (do (<- seen ProcStat (bsd-info-stat raw))
          seen)))


(defk darwin-thread-count []
  {:pre [] :post [(: % int)] :tags {:context "process" :role "foundation"}}
  "macOS でこの process の OS の thread の本数を読むため。"
  (<- raw (| bytes None) (pid-info (os.getpid) PROC-PIDTASKINFO TASK-INFO-SIZE))
  (when (is raw None)
    (raise (ProcessLookupError "自分の process の proc_taskinfo を読めない")))
  (<- threads int (task-info-threads raw))
  threads)
