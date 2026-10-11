;;; 走っている仕事を持つ handoff の service の見本(worker の作り直しで仕事が切れるかの検 — test_worker_swap_keeps_running_work.hy)。
;;;
;;; service は起きると盤の <prefix>/<世代>/begun に刻を書き、仕事の秒だけ働いてから <prefix>/<世代>/done に刻を書く(走っている仕事の代役 —
;;; 道具を実行している手番)。働く間も拍ごとに準備できた(ReportReady)を報告する。仕事の後は退く知らせ(AwaitRetirement — 入れ替えで
;;; 退く事になった)を待ち、受けたら <prefix>/<世代>/left に刻を書いて自分で終わる(退く知らせを仕事の途中で受けても、仕事は終わりまで
;;; 回す — 本番の turn-host が走っているターンを終えてから退くのと同じ)。退く知らせが来なければ止めの合図で止まる。
;;; 筋書きは盤の行から、どの世代の仕事が終わりまで回ったかを読む。
(require doeff-hy.macros [defk defsystem <- val])
(import collections.abc [Callable])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.scheduler [Spawn Task])
(import doeff_time [Delay])
(import doeff_hy.json_value [OpaqueJson])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.shared_model [WriteShared])
(import doeff_cluster.shared.intent.run_context [RunContext])
(import doeff_cluster.foundation.host_contract [HOST-CONTRACT])
(import doeff_cluster.worker.intent.worker_model [Retired HandoffAbandoned])
(import doeff_cluster.worker.intent.retirement_model [AwaitRetirement])
(import tests.fixtures.retirement_programs [ready-beats])

;; 盤の行の頭(筋書きが SharedRows で読む)。
(val WORK-PREFIX "work")


(defk working-body [prefix work-seconds]
  {:pre [(: prefix str) (: work-seconds float)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "準備の報告を並べ、盤に仕事の始まりを書き、work-seconds 秒働いて終わりを書き、退く知らせを待って退いた刻を書いて終わる。"
  (<- ctx RunContext (Ask HOST-CONTRACT.run-context-key))
  (<- _beats Task (Spawn (ready-beats True 1.0 0.0)))
  (<- begun int (now-epoch-ms))
  (<- (WriteShared (.format "{}/{}/begun" prefix ctx.instance) (OpaqueJson.of {"at" begun})))
  (<- (Delay work-seconds))
  (<- done int (now-epoch-ms))
  (<- (WriteShared (.format "{}/{}/done" prefix ctx.instance) (OpaqueJson.of {"at" done})))
  (<- _told (| Retired HandoffAbandoned) (AwaitRetirement))
  (<- left int (now-epoch-ms))
  (<- (WriteShared (.format "{}/{}/left" prefix ctx.instance) (OpaqueJson.of {"at" left})))
  "retired")


(defk working-program [foundation prefix work-seconds]
  {:pre [(: foundation Callable) (: prefix str) (: work-seconds float)] :post [(: % str)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "service: working-body を土台で包む。"
  (<- reason str (foundation (working-body prefix work-seconds)))
  reason)


;; 系の宣言の Program の引数と宣言は literal(defsystem の決まり)— 盤の行の頭は WORK-PREFIX と同じ "work"・仕事は 12 秒(worker の止めの
;; stop-grace 10 秒より長い)。needs は cluster-net だけ(筋書きの worker は 1 台なので、他の worker へ移せない)。
(defsystem working-hosts [#^ Callable foundation]
  "見本の系: handoff で入れ替える、12 秒の仕事を持つ service(退いた旧の寿命の上限 600 秒)"
  (host (working-program foundation "work" 12.0) :replicas 1 :needs #{"cluster-net"} :update "handoff"
        :readiness {"windowSeconds" 5 "retiredSeconds" 600}))


;; --- recreate の job の世代をまたぐ受け渡し(AL-D2f): 止められるまで動き続ける service。起きた刻を盤に書く。 --------------------------
(val KEEP-PREFIX "keep")


(defk keeping-body [prefix]
  {:pre [(: prefix str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "準備の報告を並べ、盤に起きた刻を書き、止められるまで動き続けるため(recreate の job の受け渡しの検 — 起きた刻と止まった刻で
   2 つの世代の process が重ならず、間が短い事を読む)。"
  (<- ctx RunContext (Ask HOST-CONTRACT.run-context-key))
  (<- _beats Task (Spawn (ready-beats True 1.0 0.0)))
  (<- begun int (now-epoch-ms))
  (<- (WriteShared (.format "{}/{}/begun" prefix ctx.instance) (OpaqueJson.of {"at" begun})))
  (<- (Delay 3600.0))
  "kept")


(defk keeping-program [foundation prefix]
  {:pre [(: foundation Callable) (: prefix str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "service: keeping-body を土台で包む。"
  (<- reason str (foundation (keeping-body prefix)))
  reason)


;; 盤の行の頭は KEEP-PREFIX と同じ "keep"。needs は cluster-net だけ(筋書きの worker は 1 台なので、他の worker へ移せない)。
(defsystem keeping-hosts [#^ Callable foundation]
  "見本の系: recreate で入れ替える、止められるまで動き続ける service"
  (keeper (keeping-program foundation "keep") :replicas 1 :needs #{"cluster-net"} :update "recreate"))
