;;; 機体の process の表の効果 — 走っている process の親子と memory を 1 度に読み、1 つの process へ signal を送る。
;;; 業務の語を持たない土台の語彙。
;;;
;;;   ReadProcTable  機体の process の表を 1 度読む。答え = ProcRow の列(pid の昇順)。読む間に終わった process は列に入れない。
;;;   SignalPid      pid の process が start-ticks の時のままで終わっていない時だけ、その process 1 つへ signal を 1 度送り、待たずに返す。
;;;                  答え = 送ったか(bool — 居ない・終わっている・pid が使い回された時は送らずに False。他人の process には送らない)。
;;;                  group へは送らない(group へ送るのは SignalProcess・SignalWarmChild)。
;;;
;;; 何のためか: 機体の memory が尽きかけた時に、どの job の木がどれだけ使っているかを数え、いちばん大きい木を先に止める見張り
;;; (doeff-cluster の worker の memory_watch)が、/proc を直に読まずに表を求めるため。模擬では台本の表に替える。
;;;
;;; 答え手: os-proc-table-handler(os_proc_table.hy — 本物・Linux の /proc)と scripted-proc-table-handler(scripted_proc_table.hy — 台本の表)。
;;; 本物との契約は tests/test_proc_table_contract.hy。
(require doeff-hy.macros [defeffect defk val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff_core_effects._process_effects [ProcessSignal])
(import doeff_core_effects._process_stat [ENDED-STATES])

;; /proc/<pid>/stat の欄の位置(最後の ')' の後ろを空白で割った列の番号 — 3 番目の欄 state が 0)。ppid = 4 番目・starttime = 22 番目・
;; rss = 24 番目(page の数)。
(val STAT-PPID 1)
(val STAT-START-TICKS 19)
(val STAT-RSS-PAGES 21)


(defrecord ProcRow
  "機体の process 1 つ: pid・ppid = 親の pid・start-ticks = 起動の刻(pid の使い回しを見分ける — Linux は /proc/<pid>/stat の starttime の
   clock tick)・rss-bytes = 今の resident set の byte・comm = 命令の名(/proc/<pid>/stat の 2 番目の欄 — 括弧の中・最長 15 字)。"
  {:check [(> pid 0) (>= ppid 0) (>= start-ticks 0) (>= rss-bytes 0)]}
  (#^ int pid)
  (#^ int ppid)
  (#^ int start-ticks)
  (#^ int rss-bytes)
  (#^ str comm))


(defk proc-row-of-stat [pid text page-bytes]
  {:pre [(: pid int) (> pid 0) (: text str) (: page-bytes int) (> page-bytes 0)] :post [(: % (| ProcRow None))]
   :tags {:context "process" :role "judgment"}}
  "/proc/<pid>/stat の中身 text を表の 1 行にするため(本物の答え手が使う)。comm(2 番目の欄)は空白や括弧を含みうるので、最後の ')' の
   後ろを空白で割る。終わって刈られる前・刈られている最中の process(state Z・X — process_stat.hy の ENDED-STATES)は None(表に
   入れない)。欄の足りない中身は機体の形を知らない読みなので ValueError で名指す(黙って捨てない)。"
  (val parts (.rsplit text ")" 1))
  (val head (get parts 0))
  (val comm (cut head (+ (.find head "(") 1) None))
  (val fields (if (= (len parts) 2) (.split (get parts 1)) []))
  (when (<= (len fields) STAT-RSS-PAGES)
    (raise (ValueError (.format "/proc/{}/stat の欄が足りない: {!r}" pid text))))
  (if (in (get fields 0) ENDED-STATES)
      None
      (ProcRow :pid pid :ppid (int (get fields STAT-PPID)) :start-ticks (int (get fields STAT-START-TICKS))
               :rss-bytes (* (max 0 (int (get fields STAT-RSS-PAGES))) page-bytes) :comm comm)))


(defeffect ReadProcTable
  "機体の process の表を 1 度読む(頭の註)。答え = ProcRow の列(pid の昇順)。"
  {:fields []
   :answer (get tuple #(ProcRow ...))
   :tags {:context "process" :role "foundation"}})


(defeffect SignalPid
  "pid の process(start-ticks の時のままの物)1 つへ signal を 1 度送る(頭の註)。答え = 送ったか。"
  {:fields [(: pid int) (: start-ticks int) (: signal ProcessSignal)]
   :answer bool
   :tags {:context "process" :role "foundation"}})
