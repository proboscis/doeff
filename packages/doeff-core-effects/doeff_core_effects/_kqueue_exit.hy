;;; macOS(Darwin)で process の終わりで読める fd を開く — 子 process の終わりの待ち(process_exit.hy の process-exit-handler)の
;;; macOS の開き方(Linux の pidfd_exit.hy の pidfd-of と同じ形: pid → fd | None・閉じるのは呼び手)。
;;;
;;; 開き方: kqueue を 1 つ作り、pid に EVFILT_PROC / NOTE_EXIT を登録する。process が終わると kqueue に出来事が 1 つ溜まり、kqueue の
;;; fd は読める fd になる — pidfd と同じく asyncio の loop の add_reader に掛けられる。EVFILT_PROC は子でない process にも登録できる
;;; (同じ利用者の process)ので、待ちの子から分けた子も同じ形で待てる。終わった(zombie を含む)・居ない pid の登録は ESRCH
;;; (ProcessLookupError)で、None を答える(待つ物が無い)。
;;; fd の持ち主: select.kqueue の object は自分の fd を GC で閉じるので、登録の済んだ kqueue の fd を os.dup で写して object を閉じ、
;;; 写した fd だけを返す(kqueue と登録は、それを指す fd が 1 つでも在る間は残る)。呼び手は Linux の pidfd と同じく os.close で閉じる。
(require doeff-hy.macros [defk val])
(val MODULE-TAGS {:context "process" :role "foundation"})
(import os)
(import select)


(defk kqueue-fd-of [pid]
  {:pre [(: pid int)] :post [(: % (| int None))] :tags {:context "process" :role "foundation"}}
  "pid の process が終わると読める fd(EVFILT_PROC / NOTE_EXIT を登録した kqueue)を開くため(頭の註)。居ない pid は None。"
  (val queue (select.kqueue))
  (try
    (.control queue [(select.kevent pid :filter select.KQ-FILTER-PROC :flags (| select.KQ-EV-ADD select.KQ-EV-ONESHOT)
                                    :fflags select.KQ-NOTE-EXIT)]
              0 0)
    (os.dup (.fileno queue))
    (except [ProcessLookupError] None)
    (finally (.close queue))))
