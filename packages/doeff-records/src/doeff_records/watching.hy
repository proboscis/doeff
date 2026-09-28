;;; WatchChanges と WatchEvents の待ち(本物の外とつながる handler — PostgreSQL の置き場と HTTP の口 — が共有する 1 つ)— 答えが来るか
;;; timeout 秒が過ぎるまで、poll-seconds ごとに読み直す。memory の置き場はこれを使わない(書きが待ち手を起こす呼び鈴 — memory.hy の
;;; memory-watch)。WatchEvents は ReadEvents(limit 1)の読み直しで答える(記録の service には待ちの口を足さない — 出自の issue は #1019)。
;;; 時計は doeff-time(GetMonotonic で経過を測り、Delay で眠る・GetTime で保持の期限を刻む)。仮想の時計の下では一瞬で終わる。
(require doeff-hy.macros [defk <- var])
(import collections.abc [Callable])
(import doeff_time [Delay GetMonotonic GetTime])
(import doeff_records.values [Changes Events EventsMoved EventsQuiet Reset Unreachable])
(import doeff_records.admission [epoch-ms])


(defk moved-of [answer]
  {:pre [(: answer (| Events Unreachable))] :post [(: % (| EventsMoved EventsQuiet Unreachable))]
   :tags {:context "records" :role "foundation"}}
  "ReadEvents(limit 1)の答え → WatchEvents の 1 回ぶんの答え(PostgreSQL の置き場と HTTP の口の client が、列の待ちを読み直しで答えるため)。"
  (match answer
    (Events :items items) (if items (EventsMoved) (EventsQuiet))
    (Unreachable) answer))


(defk wait-for-changes [scan poll-seconds timeout]
  {:pre [(: scan Callable) (: poll-seconds (| int float)) (: timeout (| int float))]
   :post [(: % (| Changes Reset EventsMoved EventsQuiet Unreachable))]}
  ;; scan = 今の刻(epoch ミリ秒)→ 1 回ぶんの答えを返す Program(待たない — 純粋な答えは doeff の Pure で包む)。
  ;; 待ち続ける答え(空の Changes・EventsQuiet)の間だけ待つ(Reset・Unreachable・EventsMoved はすぐ返す)。
  (<- start (GetMonotonic))
  (<- started-at (GetTime))
  (<- first-answer (scan (epoch-ms started-at)))
  (<- first-at (GetMonotonic))
  (var answer first-answer)
  (var at first-at)
  (while (and (or (and (isinstance answer Changes) (not answer.items)) (isinstance answer EventsQuiet)) (< (- at start) timeout))
    (<- (Delay (min poll-seconds (- timeout (- at start)))))
    (<- now (GetTime))
    (<- next-answer (scan (epoch-ms now)))
    (:= answer next-answer)
    (<- next-at (GetMonotonic))
    (:= at next-at))
  answer)
