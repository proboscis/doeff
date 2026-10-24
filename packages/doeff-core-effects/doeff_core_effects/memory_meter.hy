;;; 計器の memory の答え手 memory-meter-handler(agora-redesign #1440・ADR-DOE-CORE-EFFECTS-004)— CountMetric・ObserveSeconds・
;;; ObserveSecondsBatch・SetGauge・ReadMeter に、1 つの run の中の状態(session の値)で答える。
;;;
;;; 何のためか: 模擬と検では、本物(process_meter.hy)と同じ契約で計器を確かめたいが、時計・gc・thread に触れたくない。計算は本物と同じ
;;; 純関数(meter_effects.hy の metered・gauged)で、違うのは置き場だけ(process に 1 つ → この run の中)。GC の停止は見ない(設定の
;;; gc-pause-name は読まない)。別の run とは共有しない。外側に state の handler が要る。
;;;
;;; 書きの溜め(card ki-5c3e73834e73): 数えと秒の観測の書きは、置き場の溜まり(MeterHold の pending — 撃った順)に足すだけで答え、
;;; 読み(ReadMeter)の時か溜まりが METERED-WRITES 本に届いた時に、metered で 1 度に断面へ畳む。読みは必ず畳んでから答えるので、読み手が
;;; 見る断面は 1 つずつ積んだ断面と同じ(丸めまで — metered の註)。書きのたびに断面の写像を丸ごと写さない(要求ごとに秒の観測と数えを
;;; 積む記録の service で、断面の写しが scheduler の thread の CPU の 1 割ほどを占めていた)。gauge の置き換えは溜めずに断面へ書く(数えと
;;; 秒の観測が触る写像と重ならない)。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "meter" :role "foundation"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff_core_effects.meter_effects [CountMetric EMPTY-METER MeterSettings MeterSnapshot ObserveSeconds ObserveSecondsBatch ReadMeter
                                         SetGauge gauged metered])

;; 溜まりを断面へ畳む書きの本数(読みが来なくても、溜まりの長さとその写しを抑える)。
(val METERED-WRITES 256)


(defrecord MeterHold
  "memory の答え手の置き場の値(頭の註の書きの溜め): snapshot = 畳み終えた断面・pending = まだ畳んでいない数えと秒の観測の書きの列
   (撃った順の CountMetric・ObserveSeconds・ObserveSecondsBatch)。"
  {:tags {:context "meter" :role "foundation"}}
  (#^ MeterSnapshot snapshot)
  (#^ tuple pending))


;; 何も積んでいない置き場。
(val EMPTY-HOLD (MeterHold :snapshot EMPTY-METER :pending #()))


(defk held-write [hold write]
  {:pre [(: hold MeterHold) (: write (| CountMetric ObserveSeconds ObserveSecondsBatch))]
   :post [(: % MeterHold)] :tags {:context "meter" :role "judgment"}}
  "数えか秒の観測の書き write を置き場の溜まりの末尾に足すため(畳むのは答え手の節 — 頭の註の書きの溜め。ここは effect を出さず、書き
   1 つごとに Program を回さない)。"
  (MeterHold :snapshot hold.snapshot :pending (+ hold.pending #(write))))


(defk folded [hold settings]
  {:pre [(: hold MeterHold) (: settings MeterSettings)] :post [(: % MeterHold)] :tags {:context "meter" :role "judgment"}}
  "置き場の溜まりを断面へ畳み、溜まりの無い置き場にするため(読みの前 — 読み手は 1 つずつ積んだのと同じ断面を見る)。"
  (if hold.pending
      (MeterHold :snapshot (! (metered hold.snapshot settings hold.pending)) :pending #())
      hold))


(defhandler memory-meter-handler [#^ MeterSettings settings]
  "CountMetric・ObserveSeconds・ObserveSecondsBatch・SetGauge・ReadMeter に、この run の中の断面で答える(頭の註)。"
  ;; 引数に残す理由: settings は本物と同じ桁の表 — 入れる所ごとに決まる値で、Ask では区別できない。
  ;; 数えと秒の観測は溜まりに足し、溜まりが METERED-WRITES 本に届いた書きだけが断面へ畳む(頭の註の書きの溜め)。
  (session var hold EMPTY-HOLD)
  (CountMetric [name amount]
    (val written (! (held-write hold effect)))
    (:= hold (if (< (len written.pending) METERED-WRITES) written (! (folded written settings))))
    (resume None))
  (ObserveSeconds [name seconds]
    (val written (! (held-write hold effect)))
    (:= hold (if (< (len written.pending) METERED-WRITES) written (! (folded written settings))))
    (resume None))
  (ObserveSecondsBatch [observations]
    (val written (! (held-write hold effect)))
    (:= hold (if (< (len written.pending) METERED-WRITES) written (! (folded written settings))))
    (resume None))
  (SetGauge [name value]
    (:= hold (MeterHold :snapshot (! (gauged hold.snapshot name value)) :pending hold.pending))
    (resume None))
  (ReadMeter []
    (val read (! (folded hold settings)))
    (:= hold read)
    (resume read.snapshot)))
