;;; 計器の memory の答え手 memory-meter-handler(agora-redesign #1440・ADR-DOE-CORE-EFFECTS-004)— CountMetric・ObserveSeconds・SetGauge・
;;; ReadMeter に、1 つの run の中の状態(session の値)で答える。
;;;
;;; 何のためか: 模擬と検では、本物(process_meter.hy)と同じ契約で計器を確かめたいが、時計・gc・thread に触れたくない。計算は本物と同じ
;;; 純関数(meter_effects.hy の counted・observed・gauged)で、違うのは置き場だけ(process に 1 つ → この run の中)。GC の停止は見ない
;;; (設定の gc-pause-name は読まない)。別の run とは共有しない。外側に state の handler が要る。
(require doeff-hy.macros [defhandler <- val var])
(val MODULE-TAGS {:context "meter" :role "foundation"})
(import doeff_core_effects.meter_effects [CountMetric EMPTY-METER MeterSettings MeterSnapshot ObserveSeconds ReadMeter SetGauge
                                         counted gauged observed])


(defhandler memory-meter-handler [#^ MeterSettings settings]
  "CountMetric・ObserveSeconds・SetGauge・ReadMeter に、この run の中の断面で答える(頭の註)。"
  ;; 引数に残す理由: settings は本物と同じ桁の表 — 入れる所ごとに決まる値で、Ask では区別できない。
  (session var snapshot EMPTY-METER)
  (CountMetric [name amount]
    (<- next MeterSnapshot (counted snapshot name amount))
    (:= snapshot next)
    (resume None))
  (ObserveSeconds [name seconds]
    (<- next MeterSnapshot (observed snapshot settings name seconds))
    (:= snapshot next)
    (resume None))
  (SetGauge [name value]
    (<- next MeterSnapshot (gauged snapshot name value))
    (:= snapshot next)
    (resume None))
  (ReadMeter []
    (resume snapshot)))
