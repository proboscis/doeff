;;; heap の凍結の検(agora-redesign #1440)。契約(本物 gc-freeze-handler と fake scripted-freeze-handler が同じ deftest を通る)と、
;;; 本物だけの性質・fake だけの性質。解釈器の組み立ては heap_contract_handlers.hy。
;;;
;;;   契約     CollectAndFreeze の答えは 0 以上の int
;;;   本物     何かを凍らせる(検の process には生きた object が在る — 答えは 0 より大きく、呼んだ後に凍った object が在る。凍らせた後に
;;;            object は解放されうるので、後で読む gc.get_freeze_count は答えより小さくなりうる)
;;;   fake     GC に触れない(凍った数が変わらない)で、決めた数を答える
(require doeff-hy.macros [deftest <- val])
(import gc)
(import doeff [run with_handlers])
(import doeff_core_effects.heap_effects [CollectAndFreeze])
(import doeff_core_effects.gc_freeze [gc-freeze-handler])
(import doeff_core_effects.scripted_freeze [scripted-freeze-handler])


(deftest test-a-freeze-answers-a-count
  {:interpreters ["gc-freeze" "scripted-freeze"]}
  (<- frozen int (CollectAndFreeze))
  (assert (>= frozen 0) (.format "凍らせた数が {!r}" frozen)))


(deftest test-the-real-freeze-freezes-live-objects
  (try
    (val frozen (run (with_handlers [gc-freeze-handler] (CollectAndFreeze))))
    (assert (> frozen 0) (.format "生きた object が在るのに凍らせた数が {!r}" frozen))
    (assert (> (gc.get-freeze-count) 0) "CollectAndFreeze の後に凍った object が無い")
    (finally
      (gc.unfreeze))))


(deftest test-the-scripted-freeze-leaves-the-collector-alone
  (val before (gc.get-freeze-count))
  (val frozen (run (with_handlers [(scripted-freeze-handler 3)] (CollectAndFreeze))))
  (assert (= frozen 3) (.format "fake の答えが {!r}(決めた数 3)" frozen))
  (assert (= (gc.get-freeze-count) before) "fake が GC の凍結を変えた"))
