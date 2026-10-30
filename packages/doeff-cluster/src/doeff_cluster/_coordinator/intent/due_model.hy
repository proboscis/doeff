;;; coordinator の調停ループが落ち着かない時の失敗(#3865)と、前の歩の刻み(止まりの検出の材料 — card ki-d033e5f969cf)。期限の答えの型
;;; (DueAt・DueNow・DueNever)は worker と共用なので shared/intent/due_model へ移した(#3871)— ここには coordinator だけの物が残る。
(require doeff-hy.macros [val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "coordinator" :role "intent"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す


(defclass CoordinatorUnsettled [Exception]
  "調停ループの歩が、今すぐ(DueNow)の答えのまま上限の数を越えて続いた — 状態が落ち着かずに回り続けている(#3865)。
   文に、最後の 2 つの状態で違う欄の名を書く(どの判断が変わり続けたかを名指す)。")


(defrecord StepPace
  "調停ループの前の歩の刻み(epoch ms — 止まりの検出 api_policy.stall-of の材料): judged-ms = 前の歩の判断の刻(歩の now)・
   waited-ms = 前の歩を終えて受付を待ち始めた刻・planned-ms = その時に決めた次に起きる刻(wake_policy.planned-wake-ms — 期限なしで
   待つなら None)。"
  (#^ int judged-ms)
  (#^ int waited-ms)
  (#^ (| int None) planned-ms))


(defrecord Stall
  "coordinator の process の中の止まり 1 つ(api_policy.stall-of の答え・ms): busy-ms = 前の歩の長さ(判断の刻から受付を待ち始める
   まで)・overslept-ms = 予定の起きる刻からの寝過ごし・gap-ms = 経過・沈黙に数えない長さ(前の歩の判断の刻から今まで)。busy-ms と
   overslept-ms は log の行に載り、保存の詰まり(busy)と機体の止まり(寝過ごし)を読み分ける材料になる。"
  (#^ int busy-ms)
  (#^ int overslept-ms)
  (#^ int gap-ms))
