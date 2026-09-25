;;; WatchChanges の待ち(handler の組が共有する 1 つ)— 変更が来るか timeout 秒が過ぎるまで、poll-seconds ごとに読み直す。
;;; 時計は doeff-time(GetMonotonic で経過を測り、Delay で眠る・GetTime で保持の期限を刻む)。仮想の時計の下では一瞬で終わる。
(require doeff-hy.macros [defk <-])
(import collections.abc [Callable])
(import doeff_time [Delay GetMonotonic GetTime])
(import doeff_records.values [Changes])
(import doeff_records.admission [epoch-ms])


(defk wait-for-changes [#^ Callable scan #^ float poll-seconds #^ float timeout]
  {:pre [(: scan Callable) (: poll-seconds (| int float)) (: timeout (| int float))]
   :post [(: % "Changes | Reset | Unreachable")]}
  ;; scan = 今の刻(epoch ミリ秒)→ 1 回ぶんの答えを返す Program(待たない — 純粋な答えは doeff の Pure で包む)。
  ;; 空の Changes の間だけ待つ(Reset・Unreachable はすぐ返す)。
  (<- start (GetMonotonic))
  (<- now (GetTime))
  (<- answer (scan (epoch-ms now)))
  (<- at (GetMonotonic))
  (while (and (isinstance answer Changes) (not answer.items) (< (- at start) timeout))
    (<- (Delay (min poll-seconds (- timeout (- at start)))))
    (<- now (GetTime))
    (<- answer (scan (epoch-ms now)))
    (<- at (GetMonotonic)))
  answer)
