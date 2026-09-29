;;; heap の凍結の本物の答え手 gc-freeze-handler(agora-redesign #1440・ADR-DOE-CORE-EFFECTS-004)— CollectAndFreeze に、process の GC で答える。
;;; gc に触るのはこの module だけ(fake の scripted_freeze.hy は触らない)。
(require doeff-hy.macros [defhandler])
(import gc)
(import doeff_core_effects.heap_effects [CollectAndFreeze])


(defhandler gc-freeze-handler
  "CollectAndFreeze に、回収(gc.collect)してから凍らせ(gc.freeze)、凍った object の数(gc.get_freeze_count)で答える。"
  (CollectAndFreeze []
    (gc.collect)
    (gc.freeze)
    (resume (gc.get-freeze-count))))
