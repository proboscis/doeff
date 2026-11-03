;; step を defk のまま書く(工程 1a・S1): defk が別の defk を <- で呼ぶ step は、素の関数の step と同じ trace を返し、
;; effect を出す defk の step は StepYieldedEffectError で止まる。
(require doeff-hy.macros [defk <-])
(import doeff [Ask Pure])
(import doeff_stream [Answered Finish Next Run StepYieldedEffectError replay])
(import pytest)


(defk counted [count item]
  {:pre [(: count int) (: item str)]
   :post [(: % Next)]}
  (Next (+ count 1) #((Run #("fetch" item) (Pure item)))))


(defk counting-step [count input]
  {:pre [(: count int) (: input (| str Answered))]
   :post [(: % Next)]}
  (match input
    (Answered :value value) (Next count (if (= count 2) #((Finish value)) #()))
    item (do (<- answer (counted count item))
             answer)))


(defn counting-plain [count input]
  (match input
    (Answered :value value) (Next count (if (= count 2) #((Finish value)) #()))
    item (Next (+ count 1) #((Run #("fetch" item) (Pure item))))))


(defn keys-of [effects]
  (lfor effect effects (if (isinstance effect Run) effect.key effect)))


(defn test-a-defk-step-replays-to-the-same-trace-as-the-plain-step []
  (setv inputs ["tea" "rice" (Answered #("fetch" "tea") "tea")])
  (setv written (replay counting-step 0 inputs))
  (setv plain (replay counting-plain 0 inputs))
  (assert (= written.states plain.states))
  (assert (= (keys-of written.effects) (keys-of plain.effects)))
  (assert (= written.finished (Finish "tea"))))


(defk asks-step [count input]
  {:pre [(: count int) (: input (| str Answered))]
   :post [(: % Next)]}
  (<- budget (Ask "budget"))
  (Next (+ count budget)))


(defn test-a-defk-step-that-asks-is-refused []
  (with [refused (pytest.raises StepYieldedEffectError)]
    (replay asks-step 0 ["tea"]))
  (assert (isinstance refused.value.effect Ask))
  (assert (in "asks" refused.value.step)))
