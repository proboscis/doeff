;;; coordinator の調停ループが落ち着かない時の失敗(#3865)。期限の答えの型(DueAt・DueNow・DueNever)は worker と共用なので
;;; shared/intent/due_model へ移した(#3871)— ここには coordinator だけの物が残る。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "coordinator" :role "intent"})


(defclass CoordinatorUnsettled [Exception]
  "調停ループの歩が、今すぐ(DueNow)の答えのまま上限の数を越えて続いた — 状態が落ち着かずに回り続けている(#3865)。
   文に、最後の 2 つの状態で違う欄の名を書く(どの判断が変わり続けたかを名指す)。")
