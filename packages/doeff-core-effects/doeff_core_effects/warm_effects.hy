;;; 前もって読み込みを済ませて待っている子(待ちの子)に仕事を頼み、その終わりを読む効果(agora-redesign #3646 の A3)。
;;; 業務の語を持たない土台の語彙で、子 process の効果(process_effects.hy)の隣。答え手は仕組みごとに差し替える:
;;;   os-warm-process-handler        本物 — 待ちの子の unix socket に JSON 1 行を送り、/proc と exit の file で終わりを読む(os_warm_process.hy)
;;;   scripted-warm-process-handler  I/O なし — socket の在否と、仕事ごとの台本で答える(scripted_warm_process.hy)
;;;
;;; 待ちの子(呼び手の側 — この package の外)は、自分の作業 dir の unix socket で頼みを受け、fork した子 A(setsid して group の先頭・
;;; log へ dup2・env と cwd を整える)が、さらに fork した子 B で入口を走らせる。A は終わる前に終了 code を exit の file へ置き換えで書く
;;; (B が signal で終わった時は負の値)。A は頼んだ側の子ではないので、終わりは PollProcess では読めない — 下の PollWarmChild が /proc の
;;; 始まりの刻(start-ticks)と exit の file で読む。pid は使い回されるので、pid と start-ticks の組で子を名指す。
;;;
;;;   ForkFromWarm     待ちの子に 1 つ仕事を頼む。答え = WarmForked(A の pid と start-ticks)か WarmRefused(socket が無い・接続を断られた・
;;;                    待ちの子が断りを答えた・答えの期限 WARM-ANSWER-SECONDS が切れた・答えが読めない — 理由の文は下の関数で 1 度だけ作る)。
;;;                    env は子の環境変数の全部(置き換え)。値はどの答え・断りの文・log にも出さない。
;;;   PollWarmChild    頼んだ子の様子を 1 度だけ問う。答え = WarmRunning(同じ start-ticks の process が居て、終わっていない)・
;;;                    WarmExited(exit の file の終了 code)・WarmLost(居ないか使い回されたのに exit の file が無い)。
;;;   SignalWarmChild  頼んだ子の group へ signal を 1 度だけ送る。答え = WarmSignaled か WarmGone(居ない・終わっている・使い回された —
;;;                    他人の process には送らない)。終わりは PollWarmChild で読む: TERM は子 A が受けずに入口の終わり(-15)を exit の
;;;                    file に書く・KILL は A も道連れにするので exit の file は無く WarmLost。
;;;   AwaitWarmChildExit 頼んだ子が終わるまで待つ(#3871 — 消費者 = doeff-cluster の worker の周の間の待ち)。答え = ProcessEnded
;;;                    (process_effects.hy — 終わった・使い回された・居ない。終了 code は後の PollWarmChild が読む)。pid と start-ticks の組で
;;;                    子を名指す。答え手は process_exit.hy の process-exit-handler(終わると読める fd の開き方が機体ごと — Linux = pidfd・macOS = kqueue)。外側に await-handler と scheduled が要る。
;;;
;;; 断り・失った・送らない理由の文と、exit の file の中身の読みは、本物と I/O なしの答え手が同じ関数を呼ぶ(同じ形で答える — 契約テスト
;;; tests/test_warm_process_contract.hy)。
(require doeff-hy.macros [defk val])
(require doeff-hy.record [defrecord])
(import re)
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import doeff_core_effects.process_effects [EnvEntry ProcessSignal])

;; 待ちの子が答えるまでの期限の秒(接続・送り・答えの 1 行の受けの全部)。
(val WARM-ANSWER-SECONDS 5.0)
;; exit の file の中身の形(符号つきの整数 1 つ)。
(val EXIT-CODE-PATTERN "-?[0-9]+")


(defclass [(dataclass :frozen True :kw-only True)] ForkFromWarm [EffectBase]
  "待ちの子に仕事を 1 つ頼む(頭の註)。答え = WarmForked か WarmRefused。"
  #^ str socket-path
  #^ str entry
  #^ (get tuple #(str ...)) args
  (setv args #())
  #^ str cwd
  #^ (get tuple #(EnvEntry ...)) env
  (setv env #())
  #^ str log-path
  #^ str exit-path
  #^ float grace-seconds
  (setv grace-seconds 10.0))


(defclass [(dataclass :frozen True :kw-only True)] PollWarmChild [EffectBase]
  "頼んだ子の様子を、待たずに 1 度だけ問う(頭の註)。答え = WarmRunning か WarmExited か WarmLost。"
  #^ int pid
  #^ int start-ticks
  #^ str exit-path)


(defclass [(dataclass :frozen True :kw-only True)] AwaitWarmChildExit [EffectBase]
  "頼んだ子が終わるまで待つ(頭の註)。答え = ProcessEnded。"
  #^ int pid
  #^ int start-ticks)


(defclass [(dataclass :frozen True :kw-only True)] SignalWarmChild [EffectBase]
  "頼んだ子の group へ signal を 1 度だけ送り、待たずに返す(頭の註)。答え = WarmSignaled か WarmGone。"
  #^ int pid
  #^ int start-ticks
  #^ ProcessSignal signal)


(defrecord WarmForked
  "待ちの子が仕事を受け、子 A を立てた(pid と start-ticks の組 = PollWarmChild と SignalWarmChild で子を名指す印)。"
  (#^ int pid)
  (#^ int start-ticks))


(defrecord WarmRefused
  "仕事を頼めなかった(detail = 理由の文 — env の値は含まない)。"
  (#^ str detail))


(defrecord WarmRunning
  "子はまだ走っている。"
  (#^ int pid))


(defrecord WarmExited
  "子は終わった(exit-code = exit の file の値 — 負の値 = 入口が signal で終わった)。"
  (#^ int pid)
  (#^ int exit-code))


(defrecord WarmLost
  "子の終わりが読めない(detail = 理由 — 居ないか使い回されたのに exit の file が無い・exit の file が読めない)。"
  (#^ int pid)
  (#^ str detail))


(defrecord WarmSignaled
  "子の group へ signal を送った。"
  (#^ int pid))


(defrecord WarmGone
  "signal を送らなかった(detail = 理由 — 居ない・終わっている・使い回された)。"
  (#^ int pid)
  (#^ str detail))


(defk warm-socket-missing [socket-path]
  {:pre [(: socket-path str)] :post [(: % str)] :tags {:context "process" :role "judgment"}}
  "socket が無い時の断りの文を作るため。"
  (.format "待ちの子の socket が無い: {}" socket-path))


(defk warm-socket-refused [socket-path]
  {:pre [(: socket-path str)] :post [(: % str)] :tags {:context "process" :role "judgment"}}
  "socket は在るが接続を断られた時の断りの文を作るため(待ちの子が居ない)。"
  (.format "待ちの子が接続を受けない: {}" socket-path))


(defk warm-answer-late [socket-path]
  {:pre [(: socket-path str)] :post [(: % str)] :tags {:context "process" :role "judgment"}}
  "答えの期限が切れた時の断りの文を作るため。"
  (.format "待ちの子が {} 秒のうちに答えない: {}" WARM-ANSWER-SECONDS socket-path))


(defk warm-answer-unreadable [socket-path]
  {:pre [(: socket-path str)] :post [(: % str)] :tags {:context "process" :role "judgment"}}
  "待ちの子の答えが約束の形(JSON 1 行)でない時の断りの文を作るため。"
  (.format "待ちの子の答えが読めない: {}" socket-path))


(defk warm-lost [pid exit-path]
  {:pre [(: pid int) (: exit-path str)] :post [(: % WarmLost)] :tags {:context "process" :role "judgment"}}
  "居ないか使い回された子に、exit の file も無い時の答えを作るため。"
  (WarmLost :pid pid :detail (.format "pid {} の子は居ない(または別の process に使い回された)のに、exit の file が無い: {}" pid exit-path)))


(defk warm-gone [pid]
  {:pre [(: pid int)] :post [(: % WarmGone)] :tags {:context "process" :role "judgment"}}
  "居ない・終わっている・使い回された子に signal を送らない時の答えを作るため。"
  (WarmGone :pid pid :detail (.format "pid {} の子は居ないか終わっている(または別の process に使い回された)ので送らない" pid)))


(defk warm-exit-answer [pid exit-path text]
  {:pre [(: pid int) (: exit-path str) (: text str)] :post [(: % (| WarmExited WarmLost))] :tags {:context "process" :role "judgment"}}
  "exit の file の中身 text を読むため。整数 1 つ(前後の空白は読み飛ばす)なら WarmExited、それ以外は WarmLost。"
  (val word (.strip text))
  (if (re.fullmatch EXIT-CODE-PATTERN word)
      (WarmExited :pid pid :exit-code (int word))
      (WarmLost :pid pid :detail (.format "pid {} の子の exit の file が整数 1 つでない: {}" pid exit-path))))


(defk signal-number-of [signal]
  {:pre [(: signal ProcessSignal)] :post [(: % int)] :tags {:context "process" :role "judgment"}}
  "閉じた型の signal を番号へ(TERM = 15・KILL = 9 — 子が signal で終わった時の終了 code はこの番号の負)。"
  (match signal ProcessSignal.TERM 15 ProcessSignal.KILL 9))
