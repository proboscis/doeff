;;; 退きの知らせ(worker/intent/retirement_model の AwaitRetirement — #3672)の検の service の見本。
;;;
;;; service は 3 つの事を並べる: 退きの知らせを待ち、来るたびに盤の <prefix>/<世代>/<番号> に語と刻を書く task・準備できたか
;;; (ReportReady)を拍ごとに報告する task・本体は止めの合図(AwaitStop)を待ち、来たら盤の <prefix>/<世代>/stop に刻を書いて終わる。
;;; 筋書きは盤の行から「知らせを受けた刻」と「止めの合図を受けた刻」を、届いた報告から「新の世代の最初の Ready の刻」を読む。
(require doeff-hy.macros [defk defsystem <- val var])
(import collections.abc [Callable])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.scheduler [Spawn Task])
(import doeff_core_effects.stop_signal_effects [AwaitStop])
(import doeff_time [Delay])
(import doeff_hy.json_value [OpaqueJson])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.readiness_model [ReportReady])
(import doeff_cluster.shared.intent.shared_model [WriteShared])
(import doeff_cluster.shared.intent.run_context [RunContext])
(import doeff_cluster.foundation.host_contract [HOST-CONTRACT])
(import doeff_cluster.worker.intent.worker_model [Retired HandoffAbandoned])
(import doeff_cluster.worker.intent.retirement_model [AwaitRetirement])

;; 盤の行の頭(筋書きが SharedRows で読む)。
(val RETIRE-PREFIX "retire")


(defk notice-word [notice]
  {:pre [(: notice (| Retired HandoffAbandoned))] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "退きの知らせを盤の行の語にするため(検の側の綴り — 本番の語の綴りを借りない)。"
  (match notice
    (Retired) "retired"
    (HandoffAbandoned) "handoff-abandoned"))


(defk minding-retirement [prefix instance]
  {:pre [(: prefix str) (: instance str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "退きの知らせを待ち、来るたびに盤の <prefix>/<instance>/<番号> に語と刻を書き、受けた知らせを after にして次を待つ(止められるまで)。"
  (var after None)
  (var n 0)
  (while True
    (<- told (| Retired HandoffAbandoned) (AwaitRetirement :after after))
    (<- at int (now-epoch-ms))
    (<- word str (notice-word told))
    (:= n (+ n 1))
    (<- (WriteShared (.format "{}/{}/{}" prefix instance n) (OpaqueJson.of {"notice" word "at" at})))
    (:= after told))
  n)


(defk ready-beats [ready every]
  {:pre [(: ready bool) (: every float)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "拍ごとに準備できたか(ready)を報告する(止められるまで)。ready が偽の版は、入れ替えの新が Ready にならない形の代役。"
  (while True
    (<- (ReportReady ready (if ready "書けた" "準備できない(検の版)")))
    (<- (Delay every)))
  None)


(defk retiring-body [prefix ready every]
  {:pre [(: prefix str) (: ready bool) (: every float)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "退きの知らせの見張りと準備の報告を並べ、止めの合図を待ち、来たら盤の <prefix>/<世代>/stop に刻と理由を書いて終わる。"
  (<- ctx RunContext (Ask HOST-CONTRACT.run-context-key))
  (<- _notices Task (Spawn (minding-retirement prefix ctx.instance)))
  (<- _beats Task (Spawn (ready-beats ready every)))
  (<- reason str (AwaitStop))
  (<- at int (now-epoch-ms))
  (<- (WriteShared (.format "{}/{}/stop" prefix ctx.instance) (OpaqueJson.of {"at" at "reason" reason})))
  reason)


(defk retiring-program [foundation prefix ready every]
  {:pre [(: foundation Callable) (: prefix str) (: ready bool) (: every float)] :post [(: % str)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "service: retiring-body を土台で包む。"
  (<- reason str (foundation (retiring-body prefix ready every)))
  reason)


;; 系の宣言の Program の引数と宣言は literal(defsystem の決まり)— 盤の行の頭は RETIRE-PREFIX と同じ "retire"、入れ替えの期限
;; (新が Ready にならない時に諦めるまでの秒)は諦めの筋書きを短くするため 20 秒。


(defsystem retiring-beacons [#^ Callable foundation]
  "見本の系: handoff で入れ替える、退きの知らせを書く service(版 1)"
  (beacon (retiring-program foundation "retire" True 1.0) :replicas 1 :needs #{"cluster-net"} :update "handoff"
          :readiness {"windowSeconds" 5 "handoffTimeoutSeconds" 20}))


(defsystem retiring-beacons-v2 [#^ Callable foundation]
  "retiring-beacons の版 2(本体の引数 every を変えた — 入れ替わる)"
  (beacon (retiring-program foundation "retire" True 2.0) :replicas 1 :needs #{"cluster-net"} :update "handoff"
          :readiness {"windowSeconds" 5 "handoffTimeoutSeconds" 20}))


(defsystem retiring-beacons-stuck [#^ Callable foundation]
  "retiring-beacons の版 2'(Ready にならない — 入れ替えは期限で諦められる)"
  (beacon (retiring-program foundation "retire" False 2.0) :replicas 1 :needs #{"cluster-net"} :update "handoff"
          :readiness {"windowSeconds" 5 "handoffTimeoutSeconds" 20}))


(defsystem retiring-beacons-v3 [#^ Callable foundation]
  "retiring-beacons の版 3(本体の引数 every を版 2 からも変えた — 諦めの後の宣言し直し)"
  (beacon (retiring-program foundation "retire" True 3.0) :replicas 1 :needs #{"cluster-net"} :update "handoff"
          :readiness {"windowSeconds" 5 "handoffTimeoutSeconds" 20}))
