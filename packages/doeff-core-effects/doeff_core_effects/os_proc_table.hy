;;; 機体の process の表の効果(proc_table_effects.hy)の本物の答え手 os-proc-table-handler(card ki-7b934e2538f9)。
;;;
;;;   ReadProcTable  /proc の直下の数の dir ごとに stat を 1 度読み、ProcRow の列(pid の昇順)で答える。読む間に終わった process・
;;;                  zombie は入れない。rss は page の数 × この機体の page の byte(os.sysconf の SC_PAGE_SIZE)。
;;;   SignalPid      stat を読み直し、同じ start-ticks で zombie でない時だけ os.kill で 1 つの pid へ送る(group へは送らない)。
;;;   /proc の無い機体では ProcTableUnavailable を上げる — 黙って空の表にしない。
;;; /proc と os.kill に触るのはこの module だけ(台本の scripted_proc_table.hy は触らない)。本物との契約は tests/test_proc_table_contract.hy。
(require doeff-hy.macros [defhandler defk <- val])
(val MODULE-TAGS {:context "process" :role "foundation"})
(import os)
(import itertools [accumulate repeat takewhile])
(import doeff_core_effects.process_effects [ProcessSignal])
(import doeff_core_effects.proc_table_effects [ProcRow ReadProcTable SignalPid proc-row-of-stat])
(import doeff_core_effects.warm_effects [signal-number-of])

;; process の表の置き場の根(Linux の procfs)。
(val PROC-ROOT "/proc")


(defclass ProcTableUnavailable [RuntimeError]
  "この機体に /proc が無いので、process の表を読めない・signal を送る相手を確かめられない。")


(defk stat-row [pid page-bytes]
  {:pre [(: pid int) (> pid 0) (: page-bytes int)] :post [(: % (| ProcRow None))] :tags {:context "process" :role "foundation"}}
  "pid の process を表の 1 行にするため。読む間に終わった・zombie = None(表に入れない)。"
  (val text (try (with [f (open (os.path.join PROC-ROOT (str pid) "stat") :encoding "utf-8" :errors "surrogateescape")] (.read f))
                 (except [[FileNotFoundError ProcessLookupError]] None)))
  (if (is text None)
      None
      (do (<- row (| ProcRow None) (proc-row-of-stat pid text page-bytes))
          row)))


(defk proc-root-present []
  {:pre [] :post [(: % None)] :tags {:context "process" :role "foundation"}}
  "/proc の無い機体を名指しで断るため(黙って空の表・送らない答えにしない)。"
  (when (not (os.path.isdir PROC-ROOT))
    (raise (ProcTableUnavailable (.format "{} が無い機体では process の表を読めない・signal の相手を確かめられない" PROC-ROOT))))
  None)


(defk os-read-proc-table []
  {:pre [] :post [(: % tuple)] :tags {:context "process" :role "foundation"}}
  "機体の process の表を 1 度読むため(頭の註)。"
  (<- (proc-root-present))
  (val page-bytes (os.sysconf "SC_PAGE_SIZE"))
  (val pids (sorted (gfor name (os.listdir PROC-ROOT) :if (.isdigit name) (int name))))
  ;; 読んだ行は #(行 前の組) の組に繋ぎ、最後に 1 度だけ tuple にする(行ごとに今までの列を写し直すと行の数の 2 乗 — process が
  ;; 8 万本を越えた機体で、読みが main の scheduler を塞いだ)。
  (var chained #())
  (for [pid pids]
    (<- row (| ProcRow None) (stat-row pid page-bytes))
    (when (is-not row None)
      (:= chained #(row chained))))
  (val cells (takewhile bool (accumulate (repeat None) (fn [cell _] (get cell 1)) :initial chained)))
  (tuple (reversed (tuple (gfor cell cells (get cell 0))))))


(defk os-signal-pid [pid start-ticks sent]
  {:pre [(: pid int) (: start-ticks int) (: sent ProcessSignal)] :post [(: % bool)] :tags {:context "process" :role "foundation"}}
  "pid の process 1 つへ signal を送るため(頭の註)。同じ start-ticks で走っている時だけ送り、他人の process には送らない。"
  (<- (proc-root-present))
  (var row None)
  (when (> pid 0)
    (<- seen (| ProcRow None) (stat-row pid 1))
    (:= row seen))
  (<- number int (signal-number-of sent))
  (if (or (is row None) (!= row.start-ticks start-ticks))
      False
      (try
        (do (os.kill pid number) True)
        (except [ProcessLookupError] False))))


(defhandler os-proc-table-handler
  ;; 本物の /proc と os.kill(頭の註)。
  (ReadProcTable []
    (<- rows tuple (os-read-proc-table))
    (resume rows))
  (SignalPid [pid start-ticks signal]
    (<- sent bool (os-signal-pid pid start-ticks signal))
    (resume sent)))
