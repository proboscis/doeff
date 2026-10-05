;;; 待ちの子の効果(warm_effects.hy)の I/O なしの答え手 scripted-warm-process-handler(agora-redesign #3646 の A3)。
;;; 本物の socket と process を使わず、世界の値 WarmScript で答える。業務を知らない: 台本の中身は呼び手(検)が渡す。
;;;
;;;   ForkFromWarm     socket-path が WarmScript の sockets に無い = socket が無い断り。在る socket は answer の通りに答える:
;;;                    ACCEPTS = 仕事を受ける(entry の WarmRun が台本に無ければ台本の誤りとして例外)・REFUSES = refusal の文で断る・
;;;                    SILENT = 答えの期限切れの断り。受けた子には SCRIPTED-WARM-FIRST-PID からの pid と、SCRIPTED-WARM-FIRST-TICKS からの
;;;                    start-ticks を配る。
;;;   PollWarmChild    pid と start-ticks が受けた子と合い、終わっていなければ、WarmRun の polls の回数だけ WarmRunning を答え、その後に終わる。
;;;                    終わった子は writes-exit なら WarmExited(exit-code)・そうでなければ WarmLost(exit の file を書かずに居なくなった子)。
;;;                    start-ticks が合わない(pid の使い回し)・知らない pid は、終わって exit の file を書いた子なら WarmExited、それ以外は
;;;                    WarmLost — 本物が「居ないか使い回された時は exit の file を読む」のと同じ。
;;;   SignalWarmChild  pid と start-ticks が合い、終わっていない子だけ終わった形にして WarmSignaled。それ以外は WarmGone(他人の process
;;;                    には送らない)。TERM = 子 A は生き残り、入口の終わり(-15)を exit の file に書く・KILL = A も道連れに死に、exit の
;;;                    file は書かれない(後の PollWarmChild は WarmLost)— 本物の group の signal と同じ。
;;; 理由の文と exit の file の値の読みは本物と同じ関数(warm_effects.hy)。本物との契約は tests/test_warm_process_contract.hy。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord defenum])
(val MODULE-TAGS {:context "process" :role "foundation"})
(import dataclasses [dataclass replace])
(import enum [StrEnum])
(import doeff_core_effects.process_effects [ProcessSignal])
(import doeff_core_effects.warm_effects [ForkFromWarm PollWarmChild SignalWarmChild WarmForked WarmRefused WarmRunning WarmExited
                                         WarmLost WarmSignaled WarmGone warm-socket-missing warm-answer-late warm-lost warm-gone
                                         signal-number-of])

;; 台本の世界で配る pid と start-ticks の始まり(本物の値と混ざらない大きさ)。
(val SCRIPTED-WARM-FIRST-PID 60000)
(val SCRIPTED-WARM-FIRST-TICKS 900000)


;; 台本の socket の答え方(閉じた型): ACCEPTS = 仕事を受ける・REFUSES = 断りを答える・SILENT = 期限まで答えない。
(defenum WarmSocketAnswer ACCEPTS REFUSES SILENT)


(defrecord WarmSocket
  "台本の世界の待ちの子の socket 1 つ(path・答え方 answer・REFUSES の時の断りの文 refusal)。"
  (#^ str path)
  (setv #^ WarmSocketAnswer answer WarmSocketAnswer.ACCEPTS)
  (setv #^ str refusal ""))


(defrecord WarmRun
  "台本の仕事 1 つの終わり方(entry = 頼みの入口の名・polls = 終わる前に WarmRunning を答える回数・exit-code = 終わった時の exit の
   file の値・writes-exit = False なら exit の file を書かずに居なくなる)。"
  {:check [(>= polls 0)]}
  (#^ str entry)
  (setv #^ int polls 0)
  (setv #^ int exit-code 0)
  (setv #^ bool writes-exit True))


(defrecord WarmScript
  "scripted-warm-process-handler に渡す世界(sockets = 在る待ちの子の socket・runs = 入口の名ごとの終わり方)。"
  (setv #^ (get tuple #(WarmSocket ...)) sockets #())
  (setv #^ (get tuple #(WarmRun ...)) runs #()))


(defrecord WarmChildState
  "受けた子 1 つの今(start-ticks・残りの WarmRunning の回数・終わったか・終わった時の値と exit の file の在否)。"
  (#^ int start-ticks)
  (#^ int polls-left)
  (#^ bool ended)
  (#^ int exit-code)
  (#^ bool writes-exit))


(defk scripted-fork [script request pid ticks]
  {:pre [(: script WarmScript) (: request ForkFromWarm) (: pid int) (: ticks int)]
   :post [(: % (| WarmChildState WarmRefused))] :tags {:context "process" :role "judgment"}}
  "台本の世界で仕事を頼んだ答えを決めるため: 断り(WarmRefused)か、受けた子の始めの今(WarmChildState)。"
  (val found (lfor s script.sockets :if (= s.path request.socket-path) s))
  (<- missing str (warm-socket-missing request.socket-path))
  (<- late str (warm-answer-late request.socket-path))
  (when (not found)
    (return (WarmRefused :detail missing)))
  (val sock (get found 0))
  (match sock.answer
    WarmSocketAnswer.REFUSES (WarmRefused :detail sock.refusal)
    WarmSocketAnswer.SILENT (WarmRefused :detail late)
    WarmSocketAnswer.ACCEPTS
    (do (val runs (lfor r script.runs :if (= r.entry request.entry) r))
        (when (not runs)
          (raise (ValueError (.format "台本に入口 {} の WarmRun が無い(検の世界の誤り)" request.entry))))
        (val run (get runs 0))
        (WarmChildState :start-ticks ticks :polls-left run.polls :ended False :exit-code run.exit-code :writes-exit run.writes-exit))))


(defk ended-answer [pid exit-path state]
  {:pre [(: pid int) (: exit-path str) (: state WarmChildState)] :post [(: % (| WarmExited WarmLost))]
   :tags {:context "process" :role "judgment"}}
  "終わった子の答えを決めるため: exit の file を書いた子は WarmExited、書かずに居なくなった子は WarmLost。"
  (<- lost WarmLost (warm-lost pid exit-path))
  (if state.writes-exit (WarmExited :pid pid :exit-code state.exit-code) lost))


(defhandler scripted-warm-process-handler [#^ WarmScript script]
  ;; 引数に残す理由: socket と仕事の台本は筋書きごとに違う値(設定ではなく模擬の世界そのもの)。
  ;; 受けた子の表(pid → WarmChildState)と、次に配る番。
  (session val children ((get dict #(int WarmChildState))))
  (session var forks 0)
  (ForkFromWarm [socket-path entry args cwd env log-path exit-path grace-seconds]
    (val pid (+ SCRIPTED-WARM-FIRST-PID forks))
    (val ticks (+ SCRIPTED-WARM-FIRST-TICKS forks))
    (<- decided (| WarmChildState WarmRefused)
        (scripted-fork script (ForkFromWarm :socket-path socket-path :entry entry :args args :cwd cwd :env env
                                            :log-path log-path :exit-path exit-path :grace-seconds grace-seconds)
                       pid ticks))
    (if (isinstance decided WarmRefused)
        (resume decided)
        (do (:= forks (+ forks 1))
            (setv (get children pid) decided)
            (resume (WarmForked :pid pid :start-ticks ticks)))))
  (PollWarmChild [pid start-ticks exit-path]
    (val state (.get children pid))
    (<- lost WarmLost (warm-lost pid exit-path))
    (cond
      (is state None) (resume lost)
      ;; 使い回された pid(start-ticks が違う)は、本物と同じく exit の file だけで答える。
      (!= state.start-ticks start-ticks) (resume (if (and state.ended state.writes-exit)
                                                      (WarmExited :pid pid :exit-code state.exit-code)
                                                      lost))
      (and (not state.ended) (> state.polls-left 0))
      (do (setv (get children pid) (replace state :polls-left (- state.polls-left 1)))
          (resume (WarmRunning :pid pid)))
      True
      (do (val ended (replace state :ended True))
          (setv (get children pid) ended)
          (<- answer (| WarmExited WarmLost) (ended-answer pid exit-path ended))
          (resume answer))))
  (SignalWarmChild [pid start-ticks signal]
    (val state (.get children pid))
    (<- gone WarmGone (warm-gone pid))
    (<- number int (signal-number-of signal))
    (if (or (is state None) (!= state.start-ticks start-ticks) state.ended)
        (resume gone)
        (do (setv (get children pid) (replace state :ended True :exit-code (- number) :writes-exit (= signal ProcessSignal.TERM)))
            (resume (WarmSignaled :pid pid))))))
