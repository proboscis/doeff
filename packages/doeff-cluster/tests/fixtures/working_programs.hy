;;; 走っている仕事を持つ handoff の service の見本(worker の作り直しで仕事が切れるかの検 — test_worker_swap_keeps_running_work.hy)。
;;;
;;; service は起きると盤の <prefix>/<世代>/begun に刻を書き、仕事の秒だけ働いてから <prefix>/<世代>/done に刻を書く(走っている仕事の代役 —
;;; 道具を実行している手番)。働く間も拍ごとに準備できた(ReportReady)を報告する。仕事の後は退く知らせ(AwaitRetirement — 入れ替えで
;;; 退く事になった)を待ち、受けたら <prefix>/<世代>/left に刻を書いて自分で終わる(退く知らせを仕事の途中で受けても、仕事は終わりまで
;;; 回す — 本番の turn-host が走っているターンを終えてから退くのと同じ)。退く知らせが来なければ止めの合図で止まる。
;;; 筋書きは盤の行から、どの世代の仕事が終わりまで回ったかを読む。
(require doeff-hy.macros [defk <- val])
(require doeff-cluster.shared.entry.macros [defsystem])
(import collections.abc [Callable])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.scheduler [Spawn Task])
(import doeff_time [Delay])
(import doeff_hy._json_value [OpaqueJson])
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


;; --- 退く知らせを待たない handoff の service(退いた process の寿命の上限を宣言しない): 止められるまで動き続ける。起きた刻を盤に書く。 ----
;; 本番の待ち受けの service の形(待ち受けを開いて動き続け、AwaitRetirement を待たない・宣言の readiness に retiredSeconds が無い — card ki-7c11722e3621)。
(val SERVE-PREFIX "serve")


(defsystem serving-hosts [#^ Callable foundation]
  "見本の系: handoff で入れ替える、退く知らせを待たずに止められるまで動き続ける service(退いた旧の寿命の上限を宣言しない)"
  (server (keeping-program foundation "serve") :replicas 1 :needs #{"cluster-net"} :update "handoff"
          :readiness {"windowSeconds" 5}))


;; --- 退きの保ちを持つ handoff の service(card ki-e3531030eb37): 仕事を走らせたまま退く知らせを待ち、受けたら退きの保ち(名前付きの lease
;; RETIREMENT-HOLD)を取り、仕事の終わりを待って保ちを返し、退いた刻を書いて自分で終わる(上に載る系が、受付を渡した後に走っている仕事を
;; 終わりまで回す形)。保ちを取らない版(hold = 偽)は、寿命の上限で止められる対照。土台は名前付きの lease の担い手を並べた物
;; (tests/fixtures/lease_programs.hy の lease-sim-foundation)。
(import doeff_core_effects.scheduler [AcquireSemaphore ReleaseSemaphore Semaphore Wait])
(import doeff_cluster.shared.intent.semaphore_model [CreateNamedSemaphore])
(import doeff_cluster.shared.intent.readiness_model [RETIREMENT-HOLD RETIREMENT-HOLD-PERMITS])

(val HOLD-PREFIX "hold")


(defk timed-work [prefix instance work-seconds]
  {:pre [(: prefix str) (: instance str) (: work-seconds float)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "盤に仕事の始まりを書き、work-seconds 秒働いて終わりを書くため(走っている仕事の代役)。答え = 終わりの刻。"
  (<- begun int (now-epoch-ms))
  (<- (WriteShared (.format "{}/{}/begun" prefix instance) (OpaqueJson.of {"at" begun})))
  (<- (Delay work-seconds))
  (<- done int (now-epoch-ms))
  (<- (WriteShared (.format "{}/{}/done" prefix instance) (OpaqueJson.of {"at" done})))
  done)


(defk holding-body [prefix work-seconds hold]
  {:pre [(: prefix str) (: work-seconds float) (: hold bool)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "準備の報告を並べ、仕事を走らせ、退く知らせを受けたら(hold が真なら)退きの保ちを取り、仕事の終わりを待って保ちを返し、退いた刻を
   書いて終わるため。"
  (<- ctx RunContext (Ask HOST-CONTRACT.run-context-key))
  (<- _beats Task (Spawn (ready-beats True 1.0 0.0)))
  (<- work Task (Spawn (timed-work prefix ctx.instance work-seconds)))
  (<- _told (| Retired HandoffAbandoned) (AwaitRetirement))
  (if hold
      (do (<- kept Semaphore (CreateNamedSemaphore RETIREMENT-HOLD RETIREMENT-HOLD-PERMITS))
          (<- (AcquireSemaphore kept))
          (<- _held int (Wait work))
          (<- (ReleaseSemaphore kept)))
      (<- _done int (Wait work)))
  (<- left int (now-epoch-ms))
  (<- (WriteShared (.format "{}/{}/left" prefix ctx.instance) (OpaqueJson.of {"at" left})))
  "retired")


(defk holding-program [foundation prefix work-seconds hold]
  {:pre [(: foundation Callable) (: prefix str) (: work-seconds float) (: hold bool)] :post [(: % str)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "service: holding-body を土台で包む。"
  (<- reason str (foundation (holding-body prefix work-seconds hold)))
  reason)


;; 仕事は 30 秒・退いた旧の寿命の上限は 10 秒(仕事の途中で寿命が切れる)。版 2 は仕事の秒を変えた物(入れ替わる)。
(defsystem holding-hosts [#^ Callable foundation]
  "見本の系: handoff で入れ替える、30 秒の仕事を持ち、退く間は退きの保ちを持つ service(退いた旧の寿命の上限 10 秒)"
  (holder (holding-program foundation "hold" 30.0 True) :replicas 1 :needs #{"cluster-net"} :update "handoff"
          :readiness {"windowSeconds" 5 "retiredSeconds" 10}))


(defsystem holding-hosts-v2 [#^ Callable foundation]
  "holding-hosts の版 2(仕事の秒を変えた — 入れ替わる)"
  (holder (holding-program foundation "hold" 31.0 True) :replicas 1 :needs #{"cluster-net"} :update "handoff"
          :readiness {"windowSeconds" 5 "retiredSeconds" 10}))


(defsystem unheld-hosts [#^ Callable foundation]
  "対照の系: holding-hosts と同じ仕事と寿命の上限で、退きの保ちを取らない service"
  (holder (holding-program foundation "hold" 30.0 False) :replicas 1 :needs #{"cluster-net"} :update "handoff"
          :readiness {"windowSeconds" 5 "retiredSeconds" 10}))


(defsystem unheld-hosts-v2 [#^ Callable foundation]
  "unheld-hosts の版 2(仕事の秒を変えた — 入れ替わる)"
  (holder (holding-program foundation "hold" 31.0 False) :replicas 1 :needs #{"cluster-net"} :update "handoff"
          :readiness {"windowSeconds" 5 "retiredSeconds" 10}))
