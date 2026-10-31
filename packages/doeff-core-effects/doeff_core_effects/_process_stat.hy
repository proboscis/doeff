;;; process の様子 1 つ(state と起動の刻)の型 ProcStat — 機体ごとの読み(Linux = os_warm_process.hy の /proc の読み・macOS =
;;; darwin_proc.hy の libproc の読み)が同じ形で答えるための置き場(2 つの読みが互いを読み込まずに同じ型を返す)。
;;;   state        = /proc/<pid>/stat の 3 番目の欄の 1 文字(R・S・D・T・Z …)。macOS の status は同じ文字へ写す(darwin_proc.hy)。
;;;   start-ticks  = pid の使い回しを見分ける起動の刻。値は機体ごと(Linux = starttime の clock tick・macOS = 起動の時刻の μ秒)で、
;;;                  同じ機体の同じ読みで照らす時だけ比べる(頼み手と待ちの子は同じ読み proc-stat-of を使う)。
(require doeff-hy.macros [val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "process" :role "type"})
(import dataclasses [dataclass])

;; 終わった process の state(どれも、もう走らない — 終わりを待つ相手でも signal を送る相手でもない): Z = 終わって親の回収を待つ・
;; X = 親が回収している最中(Linux の EXIT_DEAD — 回収が済んで /proc/<pid> が消える手前の短い間だけ見える。Z だけを見ると、この間の
;; 読みが「走っている」になる)。macOS の status は Z までしか写さない(darwin_proc.hy)。
(val ENDED-STATES (frozenset ["Z" "X"]))


(defrecord ProcStat
  "pid の process の state(1 文字)と start-ticks(起動の刻 — 頭の註)。"
  (#^ str state)
  (#^ int start-ticks))
