;;; 機体の process の表の効果(proc_table_effects.hy)の I/O なしの答え手 scripted-proc-table-handler(card ki-7b934e2538f9)。
;;; 本物の /proc と signal を使わず、台本の表で答える。業務を知らない: 表の中身は呼び手(検・模擬)が渡す。
;;;
;;;   ReadProcTable  今の表(pid の昇順)を答える。
;;;   SignalPid      pid と start-ticks が表の行と合う時だけ、その行を表から除いて True(TERM も KILL も、台本の process はすぐ終わる)。
;;;                  合わない・居ない時は False(他人の process には送らない — 本物と同じ)。除いた行の子は表に残る(本物で親が死んだ子が
;;;                  残るのと同じ)。
;;; 表は session の値(この run の中だけ — 外側に state の handler が要る)。本物との契約は tests/test_proc_table_contract.hy。
(require doeff-hy.macros [defhandler <- val var])
(val MODULE-TAGS {:context "process" :role "foundation"})
(import doeff_core_effects._proc_table_effects [ReadProcTable SignalPid])


(defhandler scripted-proc-table-handler [#^ tuple rows]
  "ReadProcTable と SignalPid に、台本の表 rows(ProcRow の列)で答える(頭の註)。"
  ;; 引数に残す理由: 表は検と模擬ごとに決める台本の値で、Ask で読む設定ではない。
  (session var table (tuple (sorted rows :key (fn [row] row.pid))))
  (ReadProcTable []
    (resume table))
  (SignalPid [pid start-ticks signal]
    (val hit (any (gfor row table (and (= row.pid pid) (= row.start-ticks start-ticks)))))
    (when hit
      (:= table (tuple (gfor row table :if (!= row.pid pid) row))))
    (resume hit)))
