;;; 計器の断面(MeterSnapshot)を Prometheus の text(exposition format 0.0.4)に描く純関数(#2709)。I/O なし・label なし。
;;;
;;; 何のためか: 計器の effect(meter_effects.hy)で数えた断面を、待ち受けの GET /metrics の本文にするため。描き手を使い手ごとに
;;; 持たないよう、計器の effect の隣に 1 つだけ置く。名の綴り(Prometheus の名)は呼び手が決める(この module は語彙を持たない)。
;;;
;;; 形(使い手の画面の計器の描き手と同じ — 画面が乗り換えられるように):
;;;   - 名の昇順で、counter → gauge → 秒の観測 の順。末尾に改行 1 つ(空の断面は改行 1 つだけ)
;;;   - counter   # TYPE <名>_total counter / <名>_total <値>
;;;   - gauge     # TYPE <名> gauge / <名> <値>
;;;   - 秒の観測  # TYPE <名>_seconds summary / <名>_seconds_sum <合計> / <名>_seconds_count <回数>
;;;   - helps(名 → 説明)に在る名だけ、# TYPE の前に # HELP <描く名> <説明> を置く(説明の \ と改行は escape する)。helps が空なら
;;;     画面の描き手と 1 byte も違わない
;;; 値は Python の数の綴りのまま(counter と gauge は float — 1 は 1.0)。
(require doeff-hy.macros [defk val])
(import doeff_hy.frozen [FrozenMap])
(import doeff_core_effects.meter_effects [MeterSnapshot])

;; GET /metrics の答えの Content-Type(Prometheus の text の版 0.0.4)。
(val CONTENT-TYPE "text/plain; version=0.0.4; charset=utf-8")

;; # HELP の説明の escape(\ と改行)。
(val _HELP-ESCAPES (str.maketrans {"\\" "\\\\" "\n" "\\n"}))


(defk render-prometheus [snapshot helps]
  {:pre [(: snapshot MeterSnapshot) (: helps (get FrozenMap str))] :post [(: % str)] :tags {:context "meter" :role "judgment"}}
  "計器の断面を GET /metrics の本文(Prometheus の text)にするため(形は頭の註 — 同じ断面と helps は同じ文字列)。"
  (val counters (tuple (gfor name (sorted snapshot.counters)
                             line (+ (if (in name helps)
                                         #((.format "# HELP {}_total {}" name (.translate (get helps name) _HELP-ESCAPES)))
                                         #())
                                     #((.format "# TYPE {}_total counter" name)
                                       (.format "{}_total {}" name (get snapshot.counters name))))
                             line)))
  (val gauges (tuple (gfor name (sorted snapshot.gauges)
                           line (+ (if (in name helps)
                                       #((.format "# HELP {} {}" name (.translate (get helps name) _HELP-ESCAPES)))
                                       #())
                                   #((.format "# TYPE {} gauge" name)
                                     (.format "{} {}" name (get snapshot.gauges name))))
                           line)))
  (val durations (tuple (gfor name (sorted snapshot.durations)
                              line (+ (if (in name helps)
                                          #((.format "# HELP {}_seconds {}" name (.translate (get helps name) _HELP-ESCAPES)))
                                          #())
                                      #((.format "# TYPE {}_seconds summary" name)
                                        (.format "{}_seconds_sum {}" name (. (get snapshot.durations name) total))
                                        (.format "{}_seconds_count {}" name (. (get snapshot.durations name) count))))
                              line)))
  (+ (.join "\n" (+ counters gauges durations)) "\n"))
