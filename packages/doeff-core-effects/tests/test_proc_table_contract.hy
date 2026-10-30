;;; 機体の process の表の検(card ki-7b934e2538f9)。契約(本物 os-proc-table-handler と fake scripted-proc-table-handler が同じ deftest を
;;; 通る)と、stat の 1 行の読み。解釈器の組み立ては proc_table_contract_handlers.hy。
;;;
;;;   契約  表は pid の昇順で重なりが無く、眠る子の行は親の pid を持つ。起動の刻の違う SignalPid は送らずに False(使い回された pid の
;;;         他人の process へ送らない)・合う SignalPid は True で、その後の表から眠る子が消える。
;;;   読み  comm に空白と括弧を含む stat を読める・zombie は表に入れない・欄の足りない中身は名指しで断る。
(require doeff-hy.macros [deftest defk <- val var])
(import doeff_core_effects [ProcessSignal])
(import doeff_core_effects [ProcRow ReadProcTable SignalPid]
        doeff_core_effects._proc_table_effects [proc-row-of-stat])
(import proc_table_contract_handlers [ProcPids ProcWorld])

;; 眠る子が表から消えるまで表を読み直す回数の上限(本物の SIGKILL は届いてから終わるまでに少し間がある — 時間で待たずに読み直す)。
(val GONE-READS 10000)


(defk row-of [table pid]
  {:pre [(: table tuple) (: pid int)] :post [(: % (| ProcRow None))] :tags {:context "proc-table-test" :role "judgment"}}
  "表から pid の行を引くため(無ければ None)。"
  (next (gfor row table :if (= row.pid pid) row) None))


(defk read-until-gone [pid]
  {:pre [(: pid int)] :post [(: % bool)] :tags {:context "proc-table-test" :role "program"}}
  "signal を送った眠る子が表から消えたかを、上限の回数まで読み直して確かめるため。"
  (var gone False)
  (for [_ (range GONE-READS)]
    (<- table tuple (ReadProcTable))
    (<- seen (| ProcRow None) (row-of table pid))
    (when (is seen None)
      (:= gone True)
      (break)))
  gone)


(deftest test-the-proc-table-names-the-sleeper-and-signals-only-the-same-start
  {:interpreters ["os-proc-table" "scripted-proc-table"]}
  (<- pids ProcPids (ProcWorld))
  (<- table tuple (ReadProcTable))
  (val numbers (lfor row table row.pid))
  (assert (= numbers (sorted (set numbers))) "表が pid の昇順で重なりが無い形でない")
  (<- sleeper (| ProcRow None) (row-of table pids.sleeper))
  (assert (is-not sleeper None) (.format "眠る子 {} が表に無い" pids.sleeper))
  (assert (= sleeper.ppid pids.parent) sleeper)
  (assert (= sleeper.comm "sleep") sleeper)
  ;; 起動の刻が違う = pid が使い回された別の process — 送らない。眠る子は表に残る。
  (<- refused bool (SignalPid :pid pids.sleeper :start-ticks (+ sleeper.start-ticks 1) :signal ProcessSignal.KILL))
  (assert (not refused) "起動の刻の違う process へ送った")
  (<- still tuple (ReadProcTable))
  (<- kept (| ProcRow None) (row-of still pids.sleeper))
  (assert (is-not kept None) "送っていないのに眠る子が消えた")
  ;; 合う SignalPid は送り、眠る子は表から消える。
  (<- sent bool (SignalPid :pid pids.sleeper :start-ticks sleeper.start-ticks :signal ProcessSignal.KILL))
  (assert sent "同じ起動の刻の眠る子へ送らなかった")
  (<- gone bool (read-until-gone pids.sleeper))
  (assert gone "送った眠る子が表から消えない"))


(deftest test-a-stat-line-with-spaces-and-parens-in-comm-is-read
  (val text "4242 (a) b (c) S 7 4242 4242 0 -1 4194560 10 0 0 0 1 2 0 0 20 0 1 0 123456 1000000 300 18446744073709551615 0\n")
  (<- row (| ProcRow None) (proc-row-of-stat 4242 text 4096))
  (assert (= row (ProcRow :pid 4242 :ppid 7 :start-ticks 123456 :rss-bytes (* 300 4096) :comm "a) b (c")) row))


(deftest test-a-zombie-is-not-a-row-and-a-short-stat-is-refused
  (val zombie "9 (gone) Z 1 9 9 0 -1 4194560 0 0 0 0 0 0 0 0 20 0 1 0 77 0 0 18446744073709551615 0\n")
  (<- dead (| ProcRow None) (proc-row-of-stat 9 zombie 4096))
  (assert (is dead None) dead)
  ;; 親が回収している最中(state X — 回収が済んで /proc/<pid> が消える手前の短い間だけ見える)も、終わった process として表に入れない。
  (<- reaping (| ProcRow None) (proc-row-of-stat 9 (.replace zombie " Z " " X ") 4096))
  (assert (is reaping None) reaping)
  (var refused None)
  (try
    (<- (proc-row-of-stat 9 "9 (short) S 1 9\n" 4096))
    (except [error ValueError]
      (:= refused error)))
  (assert (is-not refused None) "欄の足りない stat を断らなかった"))
