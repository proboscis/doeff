;;; 時計の物差しの換算。時計の語彙は doeff-time ちょうど 1 つ — GetTime(壁時計・timezone つきの datetime)/ GetMonotonic /
;;; Delay。答えるのは composition root が被せる doeff-time の handler(本番 = async-time-handler / sync-time-handler・検と模擬環境 =
;;; sim-time-handler と SimClock)。
;;;
;;; この module は effect も handler も持たない — クラスタの状態が刻む物差し(epoch ミリ秒)から時刻へ戻す純関数と、GetTime を 1 回
;;; 出してその物差しで答える小さな Program だけ。時刻 → epoch ミリ秒の丸めは doeff-time の epoch-ms-of の 1 つ(#3855)。
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "program"})
(import datetime [datetime timedelta timezone])
(import doeff_time [GetTime epoch-ms-of])

(setv #^ datetime EPOCH (datetime 1970 1 1 :tzinfo timezone.utc))
(setv #^ timedelta ONE-MS (timedelta :milliseconds 1))


(defn #^ datetime datetime-of-epoch-ms [#^ int ms]
  "epoch ミリ秒 → UTC の時刻(仮想の時計の起点を物差しで書くため)。"
  (+ EPOCH (* ms ONE-MS)))


(defk now-epoch-ms []
  {:pre [] :post [(: % int)]}
  ;; いまの時刻を epoch ミリ秒で。
  (<- at datetime (GetTime))
  (epoch-ms-of at))
