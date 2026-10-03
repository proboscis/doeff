;;; PostgreSQL の置き場の WatchChanges と WatchEvents の待ち — 置き場の書きの合図(呼び鈴・LISTEN / NOTIFY)が鳴るか timeout 秒が過ぎる
;;; まで待ち、鳴ったら読み直す(wait-for-signal・#3073)。memory の置き場はこれを使わない(書きが待ち手を起こす呼び鈴 — memory.hy の
;;; memory-watch)。HTTP の口の client も使わない(待ちは service の中の置き場の待ちへ渡す long-poll — #3074。前は間隔で読み直す
;;; wait-for-changes を使っていた — 使い手が無くなったので消した)。WatchEvents は ReadEvents(limit 1)の読みを moved-of で答えにする。
;;; 時計は doeff-time(GetMonotonic で経過を測り、GetTime で保持の期限を刻む)。
(require doeff-hy.macros [defk <- var val])
(val MODULE-TAGS {:context "records" :role "foundation"})
(import collections.abc [Callable])
(import doeff_time [GetMonotonic GetTime WaitWithin])
(import doeff_records.values [Changes Events EventsMoved EventsQuiet Reset Unreachable])
(import doeff_records.admission [epoch-ms])


(defk moved-of [answer]
  {:pre [(: answer (| Events Unreachable))] :post [(: % (| EventsMoved EventsQuiet Unreachable))]
   :tags {:context "records" :role "foundation"}}
  "ReadEvents(limit 1)の答え → WatchEvents の 1 回ぶんの答え(PostgreSQL の置き場が、列の待ちを合図の後の読みで答えるため)。"
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
