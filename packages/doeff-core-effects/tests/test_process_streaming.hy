;;; 子 process の本物の答え手だけの性質(agora-redesign #2184)— 台本の世界には時間の経過も並行も無いので、契約(test_process_contract.hy)
;;; には置けない性質を、本物の 2 つの答え手(subprocess-handler・offloaded-subprocess-handler)で確かめる。解釈器は
;;; process_contract_handlers.hy(conftest が scheduled を積む)。
;;;
;;;   * stream-output の output-path は、子が走っている間に読める(子自身が自分の log を読み返すと、もう書いた行が在る)
;;;   * offloaded-subprocess-handler の子は並んで走る(Spawn した 2 本の sleep が 1 本ぶんの時間で終わる)— subprocess-handler は順に走る
(require doeff-hy.macros [defk deftest <- val var])
(import time)
(import doeff_core_effects.scheduler [Spawn Gather])
(import doeff_core_effects.process_effects [EnvEntry EnvMode ProcessOutcome RunProcess])
(import process_contract_handlers [ContractRoot])

;; 子が自分の log の 1 行目を読み返す文(書いてから 0.3 秒待つ — 読み手の thread が行を書く間)。
(val READ-OWN-LOG "echo first; sleep 0.3; head -n 1 \"$LIVE_LOG\"")
;; 並べる子 1 本の眠りの秒と、並んで走ったと言える上限の秒(2 本ぶんより十分に短い)。
(val NAP "0.6")
(val SIDE-BY-SIDE-LIMIT 1.0)


(defk nap []
  {:pre [] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "program"}}
  "NAP 秒眠る子を 1 本走らせる(並べて Spawn する単位)。"
  (<- outcome ProcessOutcome (RunProcess :argv #("sleep" NAP)))
  outcome)


(deftest test-the-streamed-output-path-is-readable-while-the-child-runs
  {:interpreters ["subprocess" "offloaded-subprocess"]}
  (<- root str (ContractRoot))
  (val log (+ root "/live.log"))
  (<- read-back ProcessOutcome (RunProcess :argv #("/bin/sh" "-c" READ-OWN-LOG)
                                           :env #((EnvEntry :name "LIVE_LOG" :value log)) :env-mode EnvMode.EXTEND
                                           :output-path log :stream-output True))
  ;; 1 行目を書いた後の log を子が読むので、出力は 2 回目の first まで在る(子が終わってから足すと、読み返す時に file が無い)。
  (assert (= read-back (ProcessOutcome :exit-code 0 :stdout "first\nfirst\n" :stderr "")) (.format "子が読み返した log {}" read-back)))


(deftest test-offloaded-children-run-side-by-side
  {:interpreters ["offloaded-subprocess"]}
  (val started (time.monotonic))
  (<- one (Spawn (nap)))
  (<- two (Spawn (nap)))
  (<- outcomes list (Gather one two))
  (val elapsed (- (time.monotonic) started))
  (assert (= (lfor o outcomes o.exit-code) [0 0]) outcomes)
  (assert (< elapsed SIDE-BY-SIDE-LIMIT) (.format "{} 秒の子 2 本に {:.2f} 秒かかった(並んで走っていない)" NAP elapsed)))
