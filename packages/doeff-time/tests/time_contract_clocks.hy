;;; doeff-time の契約テストの解釈器(composition root)— 同じ契約の Program を、時計の handler だけ替えて走らせる。
;;;
;;;   async  本物の時計: async-time-handler(asyncio の sleep・壁時計・time.monotonic)
;;;   sync   本物の時計: sync-time-handler(time.sleep・threading.Timer)
;;;   sim    仮想の時計: sim-time-handler(SIM-START から始まり、待ちの分だけちょうど進む)
;;;
;;; 契約の Program は ClockUnderTest の effect で自分の時計の性質(ClockTraits — 時間の幅をどこまで緩く見るか)を読む。
;;; OuterProbe は時計の handler の外側に置いた handler だけが答える effect(ScheduleAt の Program から外側の handler が
;;; 見えることと、走った順を確かめる — 答えは到着の通し番号)。
;;; 使い手は conftest.py の doeff_interpreter(deftest の :interpreters の名 → under-clock)。
(require doeff-hy.macros [defk defhandler <- val])
(import dataclasses [dataclass])
(import functools [partial])
(import datetime [datetime timezone])
(import doeff [EffectBase Program with_handlers])
(import doeff_time [async-time-handler sync-time-handler sim-time-handler])

(val ASYNC "async")
(val SYNC "sync")
(val SIM "sim")
(val SIM-START (datetime 2024 1 1 :tzinfo timezone.utc))
;; 解釈器の名 → 時計の handler を作る関数(実行ごとに新しい handler — 仮想の時計を検どうしで共有しない)。
(val CLOCKS {ASYNC async-time-handler
             SYNC sync-time-handler
             SIM (partial sim-time-handler :start-time SIM-START)})


(defclass [(dataclass :frozen True)] ClockTraits []
  "時計の性質: exact = 待ちの分だけちょうど進む(仮想の時計)。本物の時計は early 秒だけ早く・late 秒だけ遅く起きても合格にする
   (early = asyncio の起床の時計の分解能と、壁時計と monotonic の食い違いの幅・late = 込んだ機体の scheduler の遅れの幅)。"
  (#^ str name)
  (#^ bool exact)
  (#^ float early)
  (#^ float late))


(val REAL-EARLY 0.005)
(val REAL-LATE 1.0)
(val TRAITS {ASYNC (ClockTraits ASYNC False REAL-EARLY REAL-LATE)
             SYNC (ClockTraits SYNC False REAL-EARLY REAL-LATE)
             SIM (ClockTraits SIM True 0.0 0.0)})


(defclass [(dataclass :frozen True)] ClockUnderTest [EffectBase])

(defclass [(dataclass :frozen True)] OuterProbe [EffectBase])


(defhandler clock-under-test [traits]
  ;; 引数に残す理由: 性質は解釈器の名ごとに組み立ての側で決まる値で、契約の Program の側から Ask で区別する名が無い。
  (ClockUnderTest []
    (resume traits)))


(defhandler outer-probe
  (session var arrivals 0)
  (OuterProbe []
    (:= arrivals (+ arrivals 1))
    (resume arrivals)))


(defk under-clock [name program]
  {:pre [(: name str) (in name CLOCKS) (: program Program)]
   :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "doeff-time-test" :role "foundation"}}
  "name の時計の handler の下で program を走らせる。外から順に: 時計の性質の答え手・外側の handler(OuterProbe)・時計の handler。"
  (<- answer (with_handlers [(clock-under-test (get TRAITS name)) outer-probe ((get CLOCKS name))] program))
  answer)
