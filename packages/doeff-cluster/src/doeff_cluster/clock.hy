;;; 時計の物差しの換算。時計の語彙は doeff-time ちょうど 1 つ — GetTime(壁時計・timezone つきの datetime)/ GetMonotonic /
;;; Delay。答えるのは composition root が被せる doeff-time の handler(本番 = async-time-handler / sync-time-handler・検と模擬環境 =
;;; sim-time-handler と SimClock)。
;;;
;;; この module は effect も handler も持たない — クラスタの状態が刻む物差し(epoch ミリ秒)との換算の純関数と、GetTime を 1 回
;;; 出してその物差しで答える小さな Program だけ。
(require doeff-hy.macros [defk <-])
(import datetime [datetime timedelta timezone])
(import doeff_time [GetTime])

(setv EPOCH (datetime 1970 1 1 :tzinfo timezone.utc))
(setv ONE-MS (timedelta :milliseconds 1))


(defn #^ int epoch-ms-of [#^ datetime at]
  "timezone つきの時刻 → epoch ミリ秒(整数の割り算 — float の timestamp を 1000 倍して切ると 1 ms ずれることがある)。"
  (// (- at EPOCH) ONE-MS))


(defn #^ datetime datetime-of-epoch-ms [#^ int ms]
  "epoch ミリ秒 → UTC の時刻(仮想の時計の起点を物差しで書くため)。"
  (+ EPOCH (* ms ONE-MS)))


(defk now-epoch-ms []
  {:pre [] :post [(: % int)]}
  ;; いまの時刻を epoch ミリ秒で。
  (<- at datetime (GetTime))
  (epoch-ms-of at))
