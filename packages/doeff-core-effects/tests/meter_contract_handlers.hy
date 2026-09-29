;;; 計器の契約テストの解釈器(composition root)— 同じ契約の Program を、計器の答え手だけ替えて走らせる(agora-redesign #1440・#1107 の決め)。
;;;
;;;   process-meter  本物: process-meter-handler(process に 1 つの置き場 — 別の run・別の thread と同じ名前で共有する)
;;;   memory-meter   fake: memory-meter-handler(1 つの run の中の状態だけ — 時計・gc・thread を読まない)
;;;
;;; 本物は名前ごとに process の置き場を持つので、解釈器は走らせるたびに新しい名前を使う(前の検の数が混ざらない)。
;;; 桁の表は両方に同じ物(CONTRACT-SETTINGS)を渡す。
;;; 使い手は conftest.py の doeff_interpreter(deftest の :interpreters の名 → INTERPRETERS)。
(require doeff-hy.macros [defk <- val])
(import functools [partial])
(import itertools [count])
(import doeff [Program with_handlers])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.meter_effects [MeterBucket MeterSettings])
(import doeff_core_effects.process_meter [process-meter-handler])
(import doeff_core_effects.memory_meter [memory-meter-handler])

(val PROCESS-METER "process-meter")
(val MEMORY-METER "memory-meter")

;; 契約の検が読む桁の表: 1 秒以下・5 秒以下と、全部を数える inf。
(val CONTRACT-SETTINGS (MeterSettings :buckets #((MeterBucket :label "le_1s" :ceiling 1.0)
                                                 (MeterBucket :label "le_5s" :ceiling 5.0))
                                      :inf-label "le_inf"))

;; 本物の置き場の名前を走らせるたびに変える数え手(process の置き場は検の process の間ずっと残る)。
(val RUN-NUMBERS (count))


(defk under-meter [name program]
  {:pre [(: name str) (in name #{PROCESS-METER MEMORY-METER}) (: program Program)]
   :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "meter-test" :role "foundation"}}
  "name の計器の答え手の下で program を走らせる。本物は走らせるたびに新しい置き場の名前を使う。"
  (val handler (if (= name PROCESS-METER)
                   (process-meter-handler (+ "meter-contract-" (str (next RUN-NUMBERS))) CONTRACT-SETTINGS)
                   (memory-meter-handler CONTRACT-SETTINGS)))
  (<- answer (with_handlers [(state) handler] program))
  answer)


(val INTERPRETERS {PROCESS-METER (partial under-meter PROCESS-METER)
                   MEMORY-METER (partial under-meter MEMORY-METER)})
