;;; 歩の積算の効果 — 名前つきの窓を開けている間に、Python の scheduler が task を走らせた歩(task へ入ってから scheduler の効果で
;;; 戻るまで)の数・壁の ns・CPU の ns と最長の歩 1 つを数える(agora-redesign #3855)。業務の語を持たない土台の語彙。
;;;
;;;   OpenStepTally   窓 key を開く。開いている key をもう 1 度開くと ValueError。答え = None。
;;;   CloseStepTally  窓 key を閉じ、開けていた間の積算(StepTally)を答える。開いていない key なら None。
;;;
;;; 数えるのは、窓を開けた scheduler の thread の歩のうち、始まりも終わりも窓を開けていた間にある物(開けた歩・閉じた歩の途中は
;;; 数えない)。窓が重なれば、同じ歩をどの窓にも足す。
;;; 答え手: step-tally-handler(scheduler_step_tally.hy — scheduler の測りの口 set_scheduler_trace に sink を据えて数える)。
(require doeff-hy.macros [defeffect val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])


(defrecord StepTally
  "窓 1 つの積算: steps = 歩の数・wall-ns / cpu-ns = 歩の壁の時間と thread の CPU の時間の和・longest-* = 壁の時間が最長の歩 1 つ
   (壁・CPU・task の spawn の場所・歩を終えた効果の型の名 — 歩が無ければ 0 と None)・ready = 最後の歩を終えた時に走れる entry の数。"
  {:tags {:context "step-tally" :role "type"}
   :check [(>= steps 0) (>= wall-ns 0) (>= cpu-ns 0) (>= ready 0)]}
  (#^ int steps)
  (#^ int wall-ns)
  (#^ int cpu-ns)
  (#^ int longest-wall-ns)
  (#^ int longest-cpu-ns)
  (#^ (| str None) longest-site)
  (#^ (| str None) longest-effect)
  (#^ int ready))


;; 歩を 1 つも数えていない積算。
(val EMPTY-STEP-TALLY (StepTally :steps 0 :wall-ns 0 :cpu-ns 0 :longest-wall-ns 0 :longest-cpu-ns 0
                                 :longest-site None :longest-effect None :ready 0))


(defeffect OpenStepTally
  "窓 key を開く(頭の註)。"
  {:fields [(: key str)]
   :answer None
   :tags {:context "step-tally" :role "foundation"}})


(defeffect CloseStepTally
  "窓 key を閉じ、開けていた間の積算を答える。開いていない key なら None(頭の註)。"
  {:fields [(: key str)]
   :answer (| StepTally None)
   :tags {:context "step-tally" :role "foundation"}})

