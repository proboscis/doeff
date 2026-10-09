;;; Linux で process の終わりで読める fd(pidfd)を開く — 子 process の終わりの待ち(process_exit.hy の process-exit-handler)の Linux の
;;; 開き方(agora-redesign #3871 の単位 1)。macOS の開き方は kqueue_exit.hy の kqueue-fd-of で、どちらも pid → fd | None の同じ形。
;;;
;;; process の pidfd(Linux 5.3 からの pidfd_open)は、その process が終わると読める fd になる。pidfd は子でない process にも開けるので、
;;; 待ちの子から分けた子(worker の子ではない)も同じ形で待てる。
(require doeff-hy.macros [defk val])
(val MODULE-TAGS {:context "process" :role "foundation"})
(import ctypes)
(import errno)
(import os)
(import platform)

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
