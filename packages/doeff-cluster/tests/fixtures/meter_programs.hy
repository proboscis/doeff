;;; sim-cluster の検(test_meter_report.hy)の service の Program — 記録の client で書く service が、計器の断面を橋で coordinator へ送る(#2740)。
;;;
;;; 記録の service とその前の口の代役は、client の送る HttpRequest に、仮想の時計の窓の間は「届かない」(接続できない)で、その後は
;;; 「書けた」(200)で答える。client に渡す計器(RecordsEndpoint.meter)と橋が読む計器は同じ答え手(memory-meter-handler — 断面は run
;;; ごとに 1 つ)。橋は引数で差し替える(本物 = with-meter-report・反例 = 送らない橋)。
(require doeff-hy.macros [defk defhandler defsystem <- val])
(import json)
(import collections.abc [Callable])
(import doeff [with-handlers EffectBase Program])
(import doeff_time [Delay])
(import doeff_core_effects.http_effects [HttpRequest HttpResponse HttpFailed HttpFailureKind])
(import doeff_core_effects.meter_effects [MeterSettings])
(import doeff_core_effects.memory_meter [memory-meter-handler])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.effects [PutRow])
(import doeff_records.values [ExpectAny])
(import doeff_records.http_client [RecordsEndpoint http-records-handler zero-client-metrics])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.protocol.meter_report [with-meter-report])

;; 窓の後の書きへの答えの本文(PutRow の 200 の答え — wire の written)。
(val WRITTEN-BODY (.encode (json.dumps {"kind" "written" "version" 1 "value" {"note" "a"}}) "utf-8"))
;; 書き 1 つ(表と行は代役が見ない)。
(val NOTE (PutRow "notes" #("a") (FrozenMap {"note" "a"}) (ExpectAny)))


(defhandler records-outage-http [#^ int until-ms]
  ;; 引数に残す理由: 届かない窓の終わりの時刻(service の始まりから数える — 走りごとに違う)。
  "client の送る HttpRequest に、仮想の時計が until-ms に届くまでは「届かない」で、その後は「書けた」で答えるため(記録の service と
   その前の口の代役 — 届かない時間枠を作る)。"
  {:tags {:context "doeff-cluster-test" :role "protocol"}}
  (HttpRequest []
    (<- now int (now-epoch-ms))
    (if (< now until-ms)
        (resume (HttpFailed :url effect.url :detail "接続できない(検の届かない窓)" :kind HttpFailureKind.CONNECT-FAILED))
        (resume (HttpResponse 200 {} WRITTEN-BODY (.decode WRITTEN-BODY "utf-8") effect.url 0.0)))))


(defk writes-then-idle [endpoint writes every]
  {:pre [(: endpoint RecordsEndpoint) (: writes int) (: every float)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "記録の client で書きを every 秒ごとに writes 回撃ち、その後は止められるまで待つため(待つ間も橋が数えを送り続ける)。"
  (<- (zero-client-metrics endpoint))
  (for [_ (range writes)]
    (<- NOTE)
    (<- (Delay every)))
  (while True
    (<- (Delay 60.0)))
  writes)


(defk silent-report [body]
  {:pre [(: body (| Program EffectBase))] :post [(: % "body の答え")] :tags {:context "doeff-cluster-test" :role "program"}}
  "橋の反例: 計器の断面を送らずに本体だけを走らせる(送りを忘れた橋 — 検がこれを赤にできることを確かめるため)。"
  (<- answer body)
  answer)


(defk records-writer-program [foundation bridge writes outage-seconds]
  {:pre [(: foundation Callable) (: bridge Callable) (: writes int) (: outage-seconds float)] :post [(: % int)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "service: 始まりから outage-seconds 秒の届かない窓の上で、記録の client の書きを 1 秒ごとに writes 回撃ち、計器を bridge で
   coordinator へ送る。"
  (<- start int (now-epoch-ms))
  (val endpoint (RecordsEndpoint "http://records.test" :meter (memory-meter-handler (MeterSettings))))
  (<- n int (foundation (with-handlers [(memory-meter-handler (MeterSettings))
                                        (records-outage-http (+ start (int (* 1000 outage-seconds))))
                                        (http-records-handler endpoint)]
                                       (bridge (writes-then-idle endpoint writes 1.0)))))
  n)


(defk reporting-writer-program [foundation writes outage-seconds]
  {:pre [(: foundation Callable) (: writes int) (: outage-seconds float)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "service: records-writer-program を本物の橋 with-meter-report で走らせる(系の宣言の引数は土台と literal だけなので、橋ごとに入口を置く)。"
  (<- n int (records-writer-program foundation with-meter-report writes outage-seconds))
  n)


(defk silent-writer-program [foundation writes outage-seconds]
  {:pre [(: foundation Callable) (: writes int) (: outage-seconds float)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "service の反例: records-writer-program を、計器の断面を送らない橋 silent-report で走らせる。"
  (<- n int (records-writer-program foundation silent-report writes outage-seconds))
  n)


(defsystem records-writers [foundation]
  "見本の系: 届かない窓(10 秒)の間に記録の client で書きを 3 件撃つ service 1 つ(本物の橋 with-meter-report)"
  (writer (reporting-writer-program foundation 3 10.0) :needs #{"cluster-net"}))


(defsystem silent-records-writers [foundation]
  "records-writers の反例: 橋が計器の断面を送らない"
  (writer (silent-writer-program foundation 3 10.0) :needs #{"cluster-net"}))
