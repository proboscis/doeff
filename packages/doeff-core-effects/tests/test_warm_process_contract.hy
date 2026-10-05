;;; 待ちの子の効果の契約テスト — 同じ効果(ForkFromWarm・PollWarmChild・SignalWarmChild)に答える本物(os-warm-process-handler)と fake
;;; (scripted-warm-process-handler)が、同じ deftest を通る。解釈器の組み立てと契約の世界は warm_contract_handlers.hy。
;;;
;;;   * 頼むと子 A の pid と start-ticks が返り、走っている間は WarmRunning、終わると exit の file の終了 code(WarmExited)
;;;   * signal は子の group へ送られ、入口が signal で終わった子は負の終了 code。終わった子には送らない(WarmGone)
;;;   * pid の使い回し(start-ticks が違う)は、exit の file が無ければ WarmLost で、signal は送らない(WarmGone)
;;;   * exit の file を書かずに消えた子は WarmLost
;;;   * 断り: socket が無い・待ちの子が断りを答える・答えの期限が切れる(本物は WARM-ANSWER-SECONDS の 5 秒かかる)
;;;   * env の値は、どの答えにも断りの文にも出ない
;;;   * 待ちの子との約束の 1 行は、頼みも答えも往復する(形の違う 1 行は読めない断り)
(require doeff-hy.macros [defk deftest <- val var])
(import doeff_hy.wire [Malformed])
(import doeff_core_effects.process_effects [EnvEntry ProcessSignal RunProcess])
(import doeff_core_effects.warm_effects [ForkFromWarm PollWarmChild SignalWarmChild WarmForked WarmRefused WarmRunning WarmExited
                                         WarmLost WarmSignaled WarmGone warm-socket-missing warm-answer-late warm-answer-unreadable
                                         warm-lost warm-gone])
(import doeff_core_effects.os_warm_process [WarmRequestWire warm-request-line warm-request-of warm-answer-line warm-answer-of])
(import warm_contract_handlers [WarmPaths WarmWorldPaths REFUSAL EXIT-3 SLEEPER VANISH])

(val SECRET "doeff-warm-secret-value")


(defk forked-at [paths socket-path entry name [env #()]]
  {:pre [(: paths WarmPaths) (: socket-path str) (: entry str) (: name str) (: env (of tuple EnvEntry ...))]
   :post [(: % (| WarmForked WarmRefused))] :tags {:context "warm-test" :role "program"}}
  "契約の世界で仕事を 1 つ頼むため(cwd = 根・log と exit の file は根の下の name の名)。"
  (<- answer (| WarmForked WarmRefused)
      (ForkFromWarm :socket-path socket-path :entry entry :cwd paths.root :env env
                    :log-path (+ paths.root "/" name ".log") :exit-path (+ paths.root "/" name ".exit")))
  answer)


(defk exit-path-of [paths name]
  {:pre [(: paths WarmPaths) (: name str)] :post [(: % str)] :tags {:context "warm-test" :role "judgment"}}
  "forked-at が name の仕事に渡した exit の file の path を引くため。"
  (+ paths.root "/" name ".exit"))


(defk done-soon [forked exit-path [ticks None]]
  {:pre [(: forked WarmForked) (: exit-path str) (: ticks (| int None))] :post [(: % (| WarmRunning WarmExited WarmLost))]
   :tags {:context "warm-test" :role "program"}}
  "頼んだ子を 5 秒の内に終わるまで 0.05 秒ずつ問う — 本物の子はまだ走っていることがあるので。答え = 最後の PollWarmChild の答え。"
  (val start-ticks (if (is ticks None) forked.start-ticks ticks))
  (var seen (WarmRunning :pid forked.pid))
  (var tries 0)
  (while (and (isinstance seen WarmRunning) (< tries 100))
    (<- polled (PollWarmChild :pid forked.pid :start-ticks start-ticks :exit-path exit-path))
    (:= seen polled)
    (when (isinstance seen WarmRunning)
      (<- (RunProcess :argv #("sleep" "0.05")))
      (:= tries (+ tries 1))))
  seen)


(deftest test-a-forked-child-runs-and-its-exit-code-is-read-from-the-exit-file
  {:interpreters ["os-warm" "scripted-warm"]}
  (<- paths WarmPaths (WarmWorldPaths))
  (<- forked (forked-at paths paths.accepting EXIT-3 "exit3"))
  (assert (isinstance forked WarmForked) forked)
  (<- exit-path str (exit-path-of paths "exit3"))
  (<- ended (done-soon forked exit-path))
  (assert (= ended (WarmExited :pid forked.pid :exit-code 3)) ended))


(deftest test-a-signal-ends-the-childs-group-and-an-ended-child-is-not-signalled
  {:interpreters ["os-warm" "scripted-warm"]}
  (<- paths WarmPaths (WarmWorldPaths))
  (<- forked (forked-at paths paths.accepting SLEEPER "sleeper"))
  (assert (isinstance forked WarmForked) forked)
  (<- exit-path str (exit-path-of paths "sleeper"))
  (<- running (PollWarmChild :pid forked.pid :start-ticks forked.start-ticks :exit-path exit-path))
  (assert (= running (WarmRunning :pid forked.pid)) running)
  (<- sent (SignalWarmChild :pid forked.pid :start-ticks forked.start-ticks :signal ProcessSignal.TERM))
  (assert (= sent (WarmSignaled :pid forked.pid)) sent)
  (<- ended (done-soon forked exit-path))
  (assert (= ended (WarmExited :pid forked.pid :exit-code -15)) (.format "TERM で終わった入口の終了 code は -15: {}" ended))
  (<- again (SignalWarmChild :pid forked.pid :start-ticks forked.start-ticks :signal ProcessSignal.KILL))
  (<- gone WarmGone (warm-gone forked.pid))
  (assert (= again gone) again))


(deftest test-a-reused-pid-is-lost-and-is-not-signalled
  {:interpreters ["os-warm" "scripted-warm"]}
  (<- paths WarmPaths (WarmWorldPaths))
  (<- forked (forked-at paths paths.accepting SLEEPER "reused"))
  (assert (isinstance forked WarmForked) forked)
  (<- exit-path str (exit-path-of paths "reused"))
  (val other-ticks (+ forked.start-ticks 1))
  (<- polled (PollWarmChild :pid forked.pid :start-ticks other-ticks :exit-path exit-path))
  (<- lost WarmLost (warm-lost forked.pid exit-path))
  (assert (= polled lost) (.format "start-ticks の違う pid は、exit の file が無ければ WarmLost: {}" polled))
  (<- refused-signal (SignalWarmChild :pid forked.pid :start-ticks other-ticks :signal ProcessSignal.KILL))
  (<- gone WarmGone (warm-gone forked.pid))
  (assert (= refused-signal gone) (.format "start-ticks の違う pid には送らない: {}" refused-signal))
  ;; 後片づけ: 本当の start-ticks で KILL を送って止める(本物の入口は 30 秒眠るので残さない)。KILL は group の先頭の子 A も道連れに
  ;; するので、exit の file は書かれず WarmLost で終わる。
  (<- sent (SignalWarmChild :pid forked.pid :start-ticks forked.start-ticks :signal ProcessSignal.KILL))
  (assert (= sent (WarmSignaled :pid forked.pid)) sent)
  (<- ended (done-soon forked exit-path))
  (assert (= ended lost) (.format "KILL で A ごと止まった子は exit の file が無く WarmLost: {}" ended)))


(deftest test-a-child-that-vanishes-without-an-exit-file-is-lost
  {:interpreters ["os-warm" "scripted-warm"]}
  (<- paths WarmPaths (WarmWorldPaths))
  (<- forked (forked-at paths paths.accepting VANISH "vanish"))
  (assert (isinstance forked WarmForked) forked)
  (<- exit-path str (exit-path-of paths "vanish"))
  (<- ended (done-soon forked exit-path))
  (<- lost WarmLost (warm-lost forked.pid exit-path))
  (assert (= ended lost) ended))


(deftest test-a-missing-socket-and-a-refusing-waiting-child-are-refused-by-name
  {:interpreters ["os-warm" "scripted-warm"]}
  (<- paths WarmPaths (WarmWorldPaths))
  (<- missing (forked-at paths paths.missing EXIT-3 "missing"))
  (<- missing-text str (warm-socket-missing paths.missing))
  (assert (= missing (WarmRefused :detail missing-text)) missing)
  (<- refused (forked-at paths paths.refusing EXIT-3 "refused"))
  (assert (= refused (WarmRefused :detail REFUSAL)) refused))


(deftest test-a-silent-waiting-child-is-refused-at-the-answer-deadline
  {:interpreters ["os-warm" "scripted-warm"]}
  (<- paths WarmPaths (WarmWorldPaths))
  (<- late (forked-at paths paths.silent EXIT-3 "late"))
  (<- late-text str (warm-answer-late paths.silent))
  (assert (= late (WarmRefused :detail late-text)) late))


(deftest test-env-values-appear-in-no-answer-and-no-refusal
  {:interpreters ["os-warm" "scripted-warm"]}
  (<- paths WarmPaths (WarmWorldPaths))
  (val env #((EnvEntry :name "DOEFF_WARM_SECRET" :value SECRET)))
  (<- missing (forked-at paths paths.missing EXIT-3 "env-missing" env))
  (<- refused (forked-at paths paths.refusing EXIT-3 "env-refused" env))
  (<- forked (forked-at paths paths.accepting EXIT-3 "env-forked" env))
  (for [answer #(missing refused forked)]
    (assert (not-in SECRET (repr answer)) (.format "env の値が答えに出た: {}" (type answer))))
  (assert (isinstance forked WarmForked) forked)
  (<- exit-path str (exit-path-of paths "env-forked"))
  (<- ended (done-soon forked exit-path))
  (assert (= ended (WarmExited :pid forked.pid :exit-code 3)) ended))


(deftest test-the-request-and-the-answer-lines-round-trip
  {:interpreters ["scripted-warm"]}
  (val request (ForkFromWarm :socket-path "/s.sock" :entry "e" :args #("a" "b") :cwd "/w" :env #((EnvEntry :name "N" :value "値"))
                             :log-path "/w/l" :exit-path "/w/x" :grace-seconds 2.5))
  (<- line bytes (warm-request-line request))
  (<- read-back (| WarmRequestWire Malformed) (warm-request-of line))
  (assert (isinstance read-back WarmRequestWire) read-back)
  (assert (= #(read-back.entry read-back.args read-back.cwd read-back.log-path read-back.exit-path read-back.grace-seconds)
             #("e" #("a" "b") "/w" "/w/l" "/w/x" 2.5))
          read-back)
  (assert (= (tuple (gfor e read-back.env #(e.name e.value))) #(#("N" "値"))) read-back.env)
  (<- broken (| WarmRequestWire Malformed) (warm-request-of b"{\"entry\": 1}"))
  (assert (isinstance broken Malformed) broken)
  (for [answer #((WarmForked :pid 12 :start-ticks 34) (WarmRefused :detail "断り"))]
    (<- answer-line bytes (warm-answer-line answer))
    (<- answer-back (| WarmForked WarmRefused) (warm-answer-of answer-line "/s.sock"))
    (assert (= answer-back answer) answer-back))
  (<- unreadable-answer (| WarmForked WarmRefused) (warm-answer-of b"{\"pid\": \"12\"}" "/s.sock"))
  (<- unreadable-text str (warm-answer-unreadable "/s.sock"))
  (assert (= unreadable-answer (WarmRefused :detail unreadable-text)) unreadable-answer))
