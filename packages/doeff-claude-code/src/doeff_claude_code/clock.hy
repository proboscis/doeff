;;; 読み手の thread が行の時刻を刻む時計の関数を、doeff-time の時間の handler から作る。
;;;
;;; 読み手の thread は effect を撃てないので、composition root が選んだ時間の handler(本番 = sync-time-handler・模擬 = sim の
;;; handler)で GetTime を 1 度ずつ解く関数を handler に渡す(OS の時計を直に読まない)。doeff-agents の time_handler_clock と同じ形。
(import datetime [datetime])
(import doeff [do run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [GetTime])


(defn [do] read-time []
  (setv now (yield (GetTime)))
  now)


(defn clock-of [time-handler]
  "時間の handler → 引数なしで今の datetime を返す関数(呼ぶたびに GetTime をその handler で解く)。"
  (fn []
    (setv now (run (scheduled (with_handlers [time-handler] (read-time)))))
    (when (not (isinstance now datetime))
      (raise (TypeError (.format "GetTime の答えが datetime でない: {!r}" now))))
    now))
