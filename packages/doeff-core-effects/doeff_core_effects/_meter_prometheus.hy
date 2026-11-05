;;; Pure function that renders a meter snapshot (MeterSnapshot) as Prometheus text (exposition format 0.0.4) (#2709). No I/O, no labels.
;;;
;;; Purpose: to turn the snapshot counted by the meter effect (meter_effects.hy) into the body of the listener's GET /metrics. To avoid each user
;;; keeping its own renderer, exactly one is placed next to the meter effect. The spelling of names (Prometheus names) is decided by the caller (this module has no vocabulary).
;;;
;;; Shape (the same as the user screen's meter renderer -- so the screen can be switched over):
;;;   - In ascending name order, counter -> gauge -> seconds observation. One trailing newline (an empty snapshot is just one newline)
;;;   - counter   # TYPE <name>_total counter / <name>_total <value>
;;;   - gauge     # TYPE <name> gauge / <name> <value>
;;;   - seconds observation  # TYPE <name>_seconds summary / <name>_seconds_sum <total> / <name>_seconds_count <count>
;;;   - Only names present in helps (name -> description) get a # HELP <rendered name> <description> before # TYPE (\ and newlines in the description are escaped). If helps is empty,
;;;     the output differs from the screen's renderer by not even 1 byte
;;; Values keep Python's number spelling (counter and gauge are float -- 1 is 1.0).
(require doeff-hy.macros [defk val])
(import doeff_hy._frozen [FrozenMap])
(import doeff_core_effects._meter_effects [MeterSnapshot])

;; Content-Type of the GET /metrics answer (Prometheus text version 0.0.4).
(val CONTENT-TYPE "text/plain; version=0.0.4; charset=utf-8")

;; Escaping of the # HELP description (\ and newline).
(val _HELP-ESCAPES (str.maketrans {"\\" "\\\\" "\n" "\\n"}))


(defk render-prometheus [snapshot helps]
  {:pre [(: snapshot MeterSnapshot) (: helps (get FrozenMap str))] :post [(: % str)] :tags {:context "meter" :role "judgment"}}
  "To turn a meter snapshot into the GET /metrics body (Prometheus text) (shape: see the header comment -- the same snapshot and helps give the same string)."
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
