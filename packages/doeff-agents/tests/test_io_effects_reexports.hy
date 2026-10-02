;;; driver 層の I/O 語彙(doeff_agents.io_effects)が、汎用の子 process の effect と答えの型を doeff-core-effects から re-export する
;;; (定義は 1 つ — agora-redesign #802 便 1)ことの検。io_effects の名は doeff-core-effects の object そのもの(写しではない)。
;;; doeff-core-effects の検(test_process_file_effects.hy)から移した(agora-redesign #2837): 確かめるのは doeff-agents の re-export で、
;;; 下の package の検が上の package を import すると層の向きが逆になる。
(require doeff-hy.macros [deftest])
(import doeff_core_effects.process_effects [ExecutableAt ProcessOutcome RunProcess])
(import doeff_agents.io_effects :as agents)


(deftest test-doeff-agents-re-exports-the-same-process-types
  (assert (is agents.RunProcess RunProcess))
  (assert (is agents.ProcessOutcome ProcessOutcome))
  (assert (is agents.ExecutableAt ExecutableAt)))
