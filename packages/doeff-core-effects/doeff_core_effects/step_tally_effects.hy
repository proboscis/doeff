;;; 歩の積算の効果 — 名前つきの窓を開けている間に、Python の scheduler が task を走らせた歩(task へ入ってから scheduler の効果で
;;; 戻るまで)の数・壁の ns・CPU の ns と最長の歩 1 つを数える(agora-redesign #3855)。業務の語を持たない土台の語彙。
;;;
;;;   OpenStepTally   窓 key を開く。開いている key をもう 1 度開くと ValueError。答え = None。
;;;   CloseStepTally  窓 key を閉じ、開けていた間の積算(StepTally)を答える。開いていない key なら None。
;;;
;;; 数えるのは、窓を開けた scheduler の thread の歩のうち、始まりも終わりも窓を開けていた間にある物(開けた歩・閉じた歩の途中は
;;; 数えない)。窓が重なれば、同じ歩をどの窓にも足す。
;;;
;;; task ごとの積算(agora-redesign #4188 — #3855 の U6): 呼び手が区間の担当の task の分だけを数えられるように、窓の間に歩を進めた
;;; task ごとの行(TaskTally)を答える。行は (run, tid) で 1 つ(run = scheduler の run の番号 — tid は run ごとの番号なので組で持つ)。
;;; 親(parent)は窓の間に見た Spawn の出来事から引く — 窓を開ける前に生まれた task の親は None。子孫の和は呼び手が親の結びで足す。
;;; 窓の間に始まった歩だけを数えるのは歩の積算と同じ。thread では絞らない(run の番号が process で一意なので、別の thread の run も
;;; 別の行になる)。
;;;
;;;   OpenTaskTally   窓 key を開く。開いている key をもう 1 度開くと ValueError。答え = None。
;;;   ReadTaskTally   窓 key のここまでの表を答え、窓は開けたまま。開いていない key なら None。
;;;   CloseTaskTally  窓 key を閉じ、開けていた間の表を答える。開いていない key なら None。
;;;
;;; 答え手: step-tally-handler(scheduler_step_tally.hy — scheduler の測りの口 set_scheduler_trace に sink を据えて数える。
;;; 歩の積算の窓と task の積算の窓は同じ sink を分け合う)。
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


(defrecord TaskTally
  "task の積算の窓の 1 行(頭の註): run = scheduler の run の番号・tid = その run の中の task の番号(根の task は None)・
   parent = 窓の間に見た Spawn の親の tid(根か、窓を開ける前に生まれた task なら None)・steps = 歩の数・wall-ns / cpu-ns = 歩の
   壁の時間と thread の CPU の時間の和・vm-steps / handler-calls = 歩の doeff-vm の歩数と handler の呼びの和(機体の混みで揺れない)。"
  {:tags {:context "step-tally" :role "type"}
   :check [(>= steps 0) (>= wall-ns 0) (>= cpu-ns 0) (>= vm-steps 0) (>= handler-calls 0)]}
  (#^ int run)
  (#^ (| int None) tid)
  (#^ (| int None) parent)
  (#^ int steps)
  (#^ int wall-ns)
  (#^ int cpu-ns)
  (#^ int vm-steps)
  (#^ int handler-calls))


(defeffect OpenTaskTally
  "task の積算の窓 key を開く(頭の註)。"
  {:fields [(: key str)]
   :answer None
   :tags {:context "step-tally" :role "foundation"}})


(defeffect ReadTaskTally
  "task の積算の窓 key のここまでの表を (run, tid) の順で答え、窓は開けたまま。開いていなければ None(頭の註)。"
  {:fields [(: key str)]
   :answer (| (get tuple #(TaskTally ...)) None)
   :tags {:context "step-tally" :role "foundation"}})


(defeffect CloseTaskTally
  "task の積算の窓 key を閉じ、開けていた間の表を (run, tid) の順で答える。開いていなければ None(頭の註)。"
  {:fields [(: key str)]
   :answer (| (get tuple #(TaskTally ...)) None)
   :tags {:context "step-tally" :role "foundation"}})

