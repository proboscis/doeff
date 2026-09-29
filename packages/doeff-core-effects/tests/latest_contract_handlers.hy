;;; 最新の値の契約テストの解釈器(composition root)— 同じ契約の Program を、最新の値の答え手だけ替えて走らせる(agora-redesign #1440・
;;; #1107 の決め)。
;;;
;;;   process-latest  本物: process-latest-handler(process に 1 つの置き場 — 別の run・別の thread と同じ名前で共有する)
;;;   memory-latest   fake: memory-latest-handler(1 つの run の中の状態だけ — thread を読まない)
;;;
;;; 本物は名前ごとに process の置き場を持つので、解釈器は走らせるたびに新しい名前を使う(前の検の値が混ざらない)。
;;; 使い手は conftest.py の doeff_interpreter(deftest の :interpreters の名 → INTERPRETERS)。
(require doeff-hy.macros [defk <- val])
(import functools [partial])
(import itertools [count])
(import doeff [Program with_handlers])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.process_latest [process-latest-handler])
(import doeff_core_effects.memory_latest [memory-latest-handler])

(val PROCESS-LATEST "process-latest")
(val MEMORY-LATEST "memory-latest")

;; 本物の置き場の名前を走らせるたびに変える数え手(process の置き場は検の process の間ずっと残る)。
(val RUN-NUMBERS (count))


(defk under-latest [name program]
  {:pre [(: name str) (in name #{PROCESS-LATEST MEMORY-LATEST}) (: program Program)]
   :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "latest-test" :role "foundation"}}
  "name の最新の値の答え手の下で program を走らせる。本物は走らせるたびに新しい置き場の名前を使う。"
  (val handler (if (= name PROCESS-LATEST)
                   (process-latest-handler (+ "latest-contract-" (str (next RUN-NUMBERS))))
                   memory-latest-handler))
  (<- answer (with_handlers [(state) handler] program))
  answer)


(val INTERPRETERS {PROCESS-LATEST (partial under-latest PROCESS-LATEST)
                   MEMORY-LATEST (partial under-latest MEMORY-LATEST)})
