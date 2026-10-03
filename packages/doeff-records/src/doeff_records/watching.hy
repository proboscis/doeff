;;; WatchChanges と WatchEvents の待ち(本物の外とつながる handler — PostgreSQL の置き場と HTTP の口 — が共有する 1 つ)— 答えが来るか
;;; timeout 秒が過ぎるまで、poll-seconds ごとに読み直す。memory の置き場はこれを使わない(書きが待ち手を起こす呼び鈴 — memory.hy の
;;; memory-watch)。WatchEvents は ReadEvents(limit 1)の読み直しで答える(記録の service には待ちの口を足さない — 出自の issue は #1019)。
;;; 時計は doeff-time(GetMonotonic で経過を測り、Delay で眠る・GetTime で保持の期限を刻む)。仮想の時計の下では一瞬で終わる。
;;; PostgreSQL の置き場は読み直しの間隔で起きず、呼び鈴(LISTEN / NOTIFY)で起きる wait-for-signal を使う(#3073)。
;;; 間隔で読み直す wait-for-changes は HTTP の口の client だけが使う(#3074 で long-poll に替わる)。
(require doeff-hy.macros [defk <- var val])
(val MODULE-TAGS {:context "records" :role "foundation"})
(import collections.abc [Callable])
(import doeff_time [Delay GetMonotonic GetTime WaitWithin])
(import doeff_records.values [Changes Events EventsMoved EventsQuiet Reset Unreachable])
(import doeff_records.admission [epoch-ms])


(defk moved-of [answer]
  {:pre [(: answer (| Events Unreachable))] :post [(: % (| EventsMoved EventsQuiet Unreachable))]
   :tags {:context "records" :role "foundation"}}
  "ReadEvents(limit 1)の答え → WatchEvents の 1 回ぶんの答え(PostgreSQL の置き場と HTTP の口の client が、列の待ちを読み直しで答えるため)。"
  (match answer
    (Events :items items) (if items (EventsMoved) (EventsQuiet))
    (Unreachable) answer))


(defk waiting? [answer]
  {:pre [(: answer (| Changes Reset EventsMoved EventsQuiet Unreachable))] :post [(: % bool)]
   :tags {:context "records" :role "foundation"}}
  "待ち続ける答え(空の Changes・EventsQuiet)か — Reset・Unreachable・EventsMoved・中身の在る Changes はすぐ返す。"
  (match answer
    (Changes :items items) (not items)
    (EventsQuiet) True
    _ False))


(defk wait-for-signal [scan hang drop timeout]
  {:pre [(: scan Callable) (: hang Callable) (: drop Callable) (: timeout (| int float))]
   :post [(: % (| Changes Reset EventsMoved EventsQuiet Unreachable))]
   :tags {:context "records" :role "foundation"}}
  ;; scan = 今の刻(epoch ミリ秒)→ 1 回ぶんの答えを返す Program(待たない)。hang = () → 呼び鈴(外の promise)| Unreachable の Program・
  ;; drop = (呼び鈴) → None の Program(鳴らなかった呼び鈴を外す)。
  "置き場の書きの合図(呼び鈴)が鳴るか timeout 秒が過ぎるまで待つため。読む前に呼び鈴を掛ける(読みと掛けの間の書きを取りこぼさない)。
   鳴ったら読み直す — 合図は「変わったかもしれない」だけで中身を運ばない。時間が尽きたら最後に 1 度読んで返す。待ちは doeff-time の
   期限つきの待ち WaitWithin の 1 つ(仮想の時計の下でも期限が来る — park)。"
  (<- start (GetMonotonic))
  (while True
    (<- bell (hang))
    (when (isinstance bell Unreachable)
      (return bell))
    (<- now (GetTime))
    (<- answer (scan (epoch-ms now)))
    (<- at (GetMonotonic))
    (val left (- timeout (- at start)))
    (<- still (waiting? answer))
    (when (or (not still) (<= left 0))
      (<- (drop bell))
      (return answer))
    (<- _woke (WaitWithin bell.future left :park True))
    (<- (drop bell))))


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
