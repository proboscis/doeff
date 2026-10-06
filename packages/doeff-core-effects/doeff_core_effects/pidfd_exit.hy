;;; 子 process の終わりを待つ効果(process_effects.hy の AwaitProcessExit・warm_effects.hy の AwaitWarmChildExit)の Linux の答え手
;;; pidfd-exit-handler(agora-redesign #3871 の単位 1)。
;;;
;;; 待ち方: process の pidfd(Linux 5.3 からの pidfd_open)は、その process が終わると読める fd になる。await-handler の共用の asyncio の
;;; loop の add_reader に掛け、読めた時に答える — 周期で問わない。pidfd は子でない process にも開けるので、待ちの子から分けた子(worker の子
;;; ではない)も同じ形で待てる。
;;;
;;;   AwaitProcessExit    この process が立てた子(os_process.hy の STARTED-CHILDREN)だけを待つ — 表に無い pid は ProcessNotChild。
;;;                       待ちは回収しない(終了 code は後の PollProcess)。pidfd を開いた後に、子がまだ回収されていない事を確かめる
;;;                       (回収されていなければ、開いた時の pid はこの子のまま — 使い回されていない)。
;;;   AwaitWarmChildExit  pid と start-ticks の組で待つ。pidfd を開いた後に、同じ start-ticks の process が終わらずに居る事を確かめる
;;;                       (os_warm_process.hy の same-child-running)。違えば(終わった・使い回された・居ない)その場で答える。
;;;
;;; 資源: pidfd を開く・確かめる・待つ・閉じるは全部、await された coroutine の中(loop の thread)で行う。Await が答え手に届く前に捨て
;;; られても開いた fd も待たれない coroutine も残らず、待っている task を取り消せば coroutine の finally が読み待ちを外して fd を閉じる。
;;;
;;; 外側に await-handler と scheduled が要る。macOS の答え手(kqueue の EVFILT_PROC)は無い — macOS の worker を足す時に、その件で足す。
(require doeff-hy.macros [defhandler defk <- val])
(val MODULE-TAGS {:context "process" :role "foundation"})
(import asyncio)
(import ctypes)
(import errno)
(import os)
(import platform)
(import collections.abc [Callable Generator])
(import doeff [run])
(import doeff_core_effects.effects [Await])
(import doeff_core_effects.process_effects [AwaitProcessExit ProcessEnded ProcessNotChild])
(import doeff_core_effects.warm_effects [AwaitWarmChildExit])
(import doeff_core_effects.os_process [STARTED-CHILDREN])
(import doeff_core_effects.os_warm_process [same-child-running])

;; pidfd_open の syscall の番号(機体の種類ごと)。この版の Python(3.14 の free-threading の build)は os.pidfd_open を持たないので、
;; libc の syscall で直に呼ぶ。x86_64 と aarch64 は同じ番号(Linux の共通の表)。表に無い機体は PidfdUnavailable。
(val PIDFD-OPEN-SYSCALLS {"x86_64" 434 "aarch64" 434})


(defclass PidfdUnavailable [RuntimeError]
  "この機体では pidfd を開けない(pidfd_open の番号を知らない機体の種類・Linux 5.3 より古い kernel)。")


(defk pidfd-of [pid]
  {:pre [(: pid int)] :post [(: % (| int None))] :tags {:context "process" :role "foundation"}}
  "pid の process の pidfd を開くため(syscall を直に呼ぶのはこの 1 か所 — 頭の註)。答え = fd(閉じるのは呼び手)・居ない pid は None。
   fd の取り方: libc の syscall(SYS_pidfd_open, pid, 0)。flags 0 = 終わると読める fd(Linux 5.3 から)。kernel が知らない syscall(5.3 より
   古い)は ENOSYS で PidfdUnavailable。"
  (val number (.get PIDFD-OPEN-SYSCALLS (platform.machine)))
  (when (is number None)
    (raise (PidfdUnavailable (.format "pidfd_open の番号を知らない機体の種類: {}" (platform.machine)))))
  (val libc (ctypes.CDLL None :use-errno True))
  (val fd (.syscall libc number (ctypes.c-int pid) (ctypes.c-uint 0)))
  (val failure (if (< fd 0) (ctypes.get-errno) 0))
  (cond
    (>= fd 0) fd
    (= failure errno.ESRCH) None
    (= failure errno.ENOSYS) (raise (PidfdUnavailable "この kernel は pidfd_open を持たない(Linux 5.3 から)"))
    True (raise (OSError failure (os.strerror failure)))))


(defk child-unreaped [pid]
  {:pre [(: pid int)] :post [(: % bool)] :tags {:context "process" :role "foundation"}}
  "pid が、この process が立ててまだ回収していない子かを確かめるため(回収前の子の pid は使い回されない)。"
  (val found (.find STARTED-CHILDREN pid))
  (and (is-not found None) (is (. (get found 0) returncode) None)))


(defn :async #^ None ended [#^ int pid #^ Callable still-ours]
  "pid の process が終わるまで待つ coroutine(loop の thread で走る)。pidfd を開き、still-ours(開いた後に、まだ待つ相手の process か)が
   偽ならその場で、真なら pidfd が読めるまで待って返る。どの終わり方(取り消しを含む)でも読み待ちを外して fd を閉じる。"
  (setv fd (run (pidfd-of pid)))
  (when (is fd None)
    (return None))
  (try
    (when (still-ours)
      (setv loop (asyncio.get-running-loop) done (.create-future loop))
      (.add-reader loop fd (fn [] (when (not (.done done)) (.set-result done None))))
      (try (await done)
           (finally (.remove-reader loop fd))))
    (finally (os.close fd)))
  None)


(defclass PidfdEnd []
  "pid の process の終わりを待つ awaitable(Await に渡す)。coroutine は await された時に初めて作る — Await が答え手に届く前に捨てられても、
   開いた fd も待たれない coroutine も残さない(頭の註)。"
  (defn #^ None __init__ [self #^ int pid #^ Callable still-ours]
    (setv self.pid pid self.still-ours still-ours))

  (defn #^ (get Generator #(object None None)) __await__ [self]
    (.__await__ (ended self.pid self.still-ours))))


(defhandler pidfd-exit-handler
  ;; Linux の pidfd で子 process の終わりを待つ(頭の註)。
  (AwaitProcessExit [pid]
    (if (is (.find STARTED-CHILDREN pid) None)
        (resume (ProcessNotChild :pid pid))
        (do (<- (Await (PidfdEnd pid (fn [] (run (child-unreaped pid))))))
            (resume (ProcessEnded :pid pid)))))
  (AwaitWarmChildExit [pid start-ticks]
    (<- (Await (PidfdEnd pid (fn [] (run (same-child-running pid start-ticks))))))
    (resume (ProcessEnded :pid pid))))
