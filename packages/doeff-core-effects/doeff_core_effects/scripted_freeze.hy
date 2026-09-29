;;; heap の凍結の fake の答え手 scripted-freeze-handler(agora-redesign #1440・ADR-DOE-CORE-EFFECTS-004)— CollectAndFreeze に、GC に触れず
;;; 決めた数で答える。模擬と検で、凍結を求める境目だけを確かめ、process の GC の状態を変えないため。
(require doeff-hy.macros [defhandler])
(import doeff_core_effects.heap_effects [CollectAndFreeze])


(defhandler scripted-freeze-handler [#^ int count]
  "CollectAndFreeze に count で答える(GC に触れない)。"
  ;; 引数に残す理由: 答える数は検ごとに決める台本の値で、Ask で読む設定ではない。
  (CollectAndFreeze []
    (resume count)))
