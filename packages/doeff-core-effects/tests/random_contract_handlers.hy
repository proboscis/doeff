;;; 乱数の契約テストの解釈器(composition root)— 同じ契約の Program を、乱数の答え手だけ替えて走らせる(agora-redesign #1544・
;;; #1107 の決め)。
;;;
;;;   os-random      本物: os-random-handler(os.urandom)
;;;   seeded-random  fake: seeded-random-handler(種と呼びの順で決まった値 — 呼びの数えを持つので外側に state が要る)
;;;
;;; 使い手は conftest.py の doeff_interpreter(deftest の :interpreters の名 → INTERPRETERS)。
(require doeff-hy.macros [defk <- val])
(import functools [partial])
(import doeff [Program with_handlers])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.os_random [os-random-handler])
(import doeff_core_effects.seeded_random [seeded-random-handler])

(val OS-RANDOM "os-random")
(val SEEDED-RANDOM "seeded-random")

;; fake の種(契約は長さと呼びごとの違いだけを見る)。
(val SEED 7)


(defk under-random [name program]
  {:pre [(: name str) (in name #{OS-RANDOM SEEDED-RANDOM}) (: program Program)]
   :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "random-test" :role "foundation"}}
  "name の乱数の答え手の下で program を走らせるため。"
  (val handlers (if (= name OS-RANDOM) [os-random-handler] [(state) (seeded-random-handler SEED)]))
  (<- answer (with_handlers handlers program))
  answer)


(val INTERPRETERS {OS-RANDOM (partial under-random OS-RANDOM)
                   SEEDED-RANDOM (partial under-random SEEDED-RANDOM)})
