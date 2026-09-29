;;; heap の凍結の契約テストの解釈器(composition root)— 同じ契約の Program を、凍結の答え手だけ替えて走らせる(agora-redesign #1440・
;;; #1107 の決め)。
;;;
;;;   gc-freeze        本物: gc-freeze-handler(回収してから凍らせる — process の GC の状態を変える。走った後に解いて戻す)
;;;   scripted-freeze  fake: scripted-freeze-handler(GC に触れず、決めた数を答える)
;;;
;;; 本物は process の GC の状態を変えるので、走った後に gc.unfreeze で凍らせた object を戻す(他の検の回収を変えない)。
;;; 使い手は conftest.py の doeff_interpreter(deftest の :interpreters の名 → INTERPRETERS)。
(require doeff-hy.macros [defk <- val])
(import functools [partial])
(import gc)
(import doeff [Program with_handlers])
(import doeff_core_effects.gc_freeze [gc-freeze-handler])
(import doeff_core_effects.scripted_freeze [scripted-freeze-handler])

(val GC-FREEZE "gc-freeze")
(val SCRIPTED-FREEZE "scripted-freeze")

;; fake が答える数(契約は「0 以上の int」だけを見る)。
(val SCRIPTED-COUNT 3)


(defk under-freeze [name program]
  {:pre [(: name str) (in name #{GC-FREEZE SCRIPTED-FREEZE}) (: program Program)]
   :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "heap-test" :role "foundation"}}
  "name の凍結の答え手の下で program を走らせ、本物なら走った後に凍らせた object を戻す。"
  (val handler (if (= name GC-FREEZE) gc-freeze-handler (scripted-freeze-handler SCRIPTED-COUNT)))
  (try
    (<- answer (with_handlers [handler] program))
    answer
    (finally
      (gc.unfreeze))))


(val INTERPRETERS {GC-FREEZE (partial under-freeze GC-FREEZE)
                   SCRIPTED-FREEZE (partial under-freeze SCRIPTED-FREEZE)})
