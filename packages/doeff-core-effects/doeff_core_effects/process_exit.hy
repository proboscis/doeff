;;; 子 process の終わりを待つ効果(process_effects.hy の AwaitProcessExit・warm_effects.hy の AwaitWarmChildExit)の答え手
;;; process-exit-handler(agora-redesign #3871 の単位 1)。
;;;
;;; 待ち方: その process が終わると読める fd を開き、await-handler の共用の asyncio の loop の add_reader に掛け、読めた時に答える —
;;; 周期で問わない。fd の開き方だけが機体ごと(EXIT-FD-OPENERS — Linux = pidfd_exit.hy の pidfd-of〔pidfd_open〕・macOS = kqueue_exit.hy
;;; の kqueue-fd-of〔kqueue の EVFILT_PROC / NOTE_EXIT〕)で、どちらも pid → fd | None(居ない pid は None・閉じるのは呼び手)の同じ形。
;;; 開き方を知らない機体は ExitWaitUnavailable で名指す(周期の問い直しへ黙って落ちない)。どちらの開き方も子でない process に開けるので、
;;; 待ちの子から分けた子(worker の子ではない)も同じ形で待てる。
;;;
;;;   AwaitProcessExit    この process が立てた子(os_process.hy の STARTED-CHILDREN)だけを待つ — 表に無い pid は ProcessNotChild。
;;;                       待ちは回収しない(終了 code は後の PollProcess)。fd を開いた後に、子がまだ回収されていない事を確かめる
;;;                       (回収されていなければ、開いた時の pid はこの子のまま — 使い回されていない)。
;;;   AwaitWarmChildExit  pid と start-ticks の組で待つ。fd を開いた後に、同じ start-ticks の process が終わらずに居る事を確かめる
;;;                       (os_warm_process.hy の same-child-running)。違えば(終わった・使い回された・居ない)その場で答える。
;;;
;;; 資源: fd を開く・確かめる・待つ・閉じるは全部、await された coroutine の中(loop の thread)で行う。Await が答え手に届く前に捨て
;;; られても開いた fd も待たれない coroutine も残らず、待っている task を取り消せば coroutine の finally が読み待ちを外して fd を閉じる。
;;;
;;; 外側に await-handler と scheduled が要る。
(require doeff-hy.macros [defhandler defk <- val])
(val MODULE-TAGS {:context "process" :role "foundation"})
(import asyncio)
(import os)
(import platform)
(import collections.abc [Callable Generator])
(import doeff [run])
(import doeff_core_effects.effects [Await])
(import doeff_core_effects.process_effects [AwaitProcessExit ProcessEnded ProcessNotChild])
(import doeff_core_effects.warm_effects [AwaitWarmChildExit])
(import doeff_core_effects.os_process [STARTED-CHILDREN])
(import doeff_core_effects.os_warm_process [same-child-running])
(import doeff_core_effects.pidfd_exit [pidfd-of])
(import doeff_core_effects.kqueue_exit [kqueue-fd-of])

;; 機体(platform.system の名)ごとの、終わると読める fd の開き方(頭の註)。
(val EXIT-FD-OPENERS {"Linux" pidfd-of "Darwin" kqueue-fd-of})


(defclass ExitWaitUnavailable [RuntimeError]
  "この機体の、process の終わりで読める fd の開き方を知らない(EXIT-FD-OPENERS に無い機体)。")


(defk exit-fd-opener-for [system]
  {:pre [(: system str)] :post [(: % Callable)] :tags {:context "process" :role "foundation"}}
  "機体の名(platform.system)から、その機体の終わると読める fd の開き方を選ぶため。知らない機体は名を持った ExitWaitUnavailable。"
  (val opener (.get EXIT-FD-OPENERS system))
  (when (is opener None)
    (raise (ExitWaitUnavailable (.format "process の終わりを待つ fd の開き方を知らない機体: {}(知っている機体 = {})"
                                         system (.join "・" (sorted EXIT-FD-OPENERS))))))
  opener)


(defk exit-fd-of [pid]
  {:pre [(: pid int)] :post [(: % (| int None))] :tags {:context "process" :role "foundation"}}
  "この機体の開き方で、pid の process が終わると読める fd を開くため。居ない pid は None(閉じるのは呼び手)。"
  (<- opener Callable (exit-fd-opener-for (platform.system)))
  (<- fd (| int None) (opener pid))
  fd)


(defk child-unreaped [pid]
  {:pre [(: pid int)] :post [(: % bool)] :tags {:context "process" :role "foundation"}}
  "pid が、この process が立ててまだ回収していない子かを確かめるため(回収前の子の pid は使い回されない)。"
  (val found (.find STARTED-CHILDREN pid))
  (and (is-not found None) (is (. (get found 0) returncode) None)))


(defn :async #^ None ended [#^ int pid #^ Callable still-ours]
  "pid の process が終わるまで待つ coroutine(loop の thread で走る)。終わると読める fd を開き、still-ours(開いた後に、まだ待つ相手の
   process か)が偽ならその場で、真なら fd が読めるまで待って返る。どの終わり方(取り消しを含む)でも読み待ちを外して fd を閉じる。"
  (setv fd (run (exit-fd-of pid)))
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


(defclass ExitEnd []
  "pid の process の終わりを待つ awaitable(Await に渡す)。coroutine は await された時に初めて作る — Await が答え手に届く前に捨てられても、
   開いた fd も待たれない coroutine も残さない(頭の註)。"
  (defn #^ None __init__ [self #^ int pid #^ Callable still-ours]
    (setv self.pid pid self.still-ours still-ours))

  (defn #^ (get Generator #(object None None)) __await__ [self]
    (.__await__ (ended self.pid self.still-ours))))


(defhandler process-exit-handler
  ;; 子 process の終わりを、終わると読める fd(機体ごとの開き方)で待つ(頭の註)。
  (AwaitProcessExit [pid]
    (if (is (.find STARTED-CHILDREN pid) None)
        (resume (ProcessNotChild :pid pid))
        (do (<- (Await (ExitEnd pid (fn [] (run (child-unreaped pid))))))
            (resume (ProcessEnded :pid pid)))))
  (AwaitWarmChildExit [pid start-ticks]
    (<- (Await (ExitEnd pid (fn [] (run (same-child-running pid start-ticks))))))
    (resume (ProcessEnded :pid pid))))
