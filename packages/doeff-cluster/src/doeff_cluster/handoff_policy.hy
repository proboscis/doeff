;;; 入れ替え(update = handoff)の期限の純粋な判断(2026-09-26)。I/O はしない。
;;;
;;; 入れ替えの Service は、spec が変わると worker が旧を止めずに名から外し(#retired-<世代>)、新を同じ名で並べて起こし、coordinator が
;;; 新を Ready と数えた後に旧を止める(worker_policy.handoff-actions / retired-actions)。新が Ready にならないと、以前は新と旧が並んだ
;;; まま黙って旧が動き続けた(期限も、旧へ戻す規則も無かった)。ここはその期限を持つ:
;;;
;;;   1. 見張りの始まり: 担い手の worker の報告に、今の宣言の spec の新の process(名 = Service の名・specHash = 今の宣言の指紋)と、
;;;      退いた旧の process(retiredFrom = Service の名)が共に動いていると初めて見た coordinator の時刻(HandoffWatch.since-ms)。
;;;      新の準備(commit の木・実行環境の root — 冷えた機体では 30 分かかりうる)と入口の検めの間は数えない(新がまだ動いていない)。
;;;   2. 諦め: 起点から宣言の readiness の handoffTimeoutSeconds(既定 300 秒)を越えても Service が Ready にならず、NotReady と
;;;      判じられている(Unknown — 報告の途絶・起動の直後 — の間は諦めない)。段を ABANDONED にし、期限と最後の NotReady の理由と、
;;;      新の世代の最後の ReportReady(偽)の reason を記録する。heartbeat の返事の job に handoffAbandoned が載り、worker は新を止めて
;;;      起こし直さず、退いた旧を動かし続ける(書き手の空白を作らない)。
;;;   3. 見張りを捨てる: 宣言が変わった(宣言の指紋が違う — 諦めも解け、新しい spec の入れ替えが始まる)・Service が Ready になった・
;;;      退いた旧が動いていない(戻る先が無い — 新を止めると何も動かなくなる)・宣言が消えた・handoff でなくなった・replicas 0。
;;;      担い手の新しい報告が無い間(coordinator の作り直しの直後・途絶の間)は判じず、見張りも諦めもそのまま持つ。
;;;
;;; 見張りは coordinator の状態(ClusterState.handoffs)に保存し、Service の資源の status.handoff に段と理由を出す(resource_policy.snapshot)。
;;; 期限の無い recreate の Service と、期限の内に Ready になった handoff の Service は見張りを残さない(今までと同じ振る舞い)。
(import dataclasses [replace])
(import hashlib)
(import json)
(import .cluster_model [ClusterState ClusterTiming HandoffWatch HandoffPhase])
(import .cluster_policy [job-to-json LIVE-PHASES])
(import .resource_policy [service-readiness])
(import .readiness_model [handoff-timeout-ms])
(import .worker_model [spec-hash])


(defn #^ str declaration-fingerprint [job]  ; defk にできない: coordinator の純粋な判断(Program の外 — api_policy.settle)が呼ぶ
  "諦めを解く合図にする宣言の指紋: Service の宣言の保存の形(job-to-json)全体の指紋。process の形(spec-hash)に加えて、replicas・
   readiness(期限を延ばす書き換えを含む)・条件の書き換えでも変わる — 宣言を書き換えれば、同じ process の形でも入れ替えを試し直す。"
  (cut (.hexdigest (hashlib.sha256 (.encode (json.dumps (job-to-json job) :sort-keys True :ensure-ascii False :separators #("," ":"))
                                            "utf-8")))
       0 16))


(defn #^ (| list None) carrier-rows [#^ ClusterState state #^ str name #^ int now #^ ClusterTiming timing]  ; defk にできない: coordinator の純粋な判断が呼ぶ
  "Service name の担い手の worker が報告した job の行(報告が lease-ms より新しい時だけ)。置き先が無い・
   報告が無い・古い時は None(判じない — 作り直しの直後に「旧が居ない」と取り違えて諦めを捨てないため)。"
  (setv placed (.get state.placements name)
        st (if (is placed None) None (.get state.statuses placed.worker)))
  (if (or (is st None) (> (- now (get st "at")) timing.lease-ms))
      None
      (.get st "jobs" [])))


(defn #^ bool retired-live [#^ list rows #^ str name]  ; defk にできない: coordinator の純粋な判断が呼ぶ
  "担い手の行に、Service name の退いた旧の process(retiredFrom = name)が動いている形で在るか(戻る先が在るか)。"
  (any (gfor row rows (and (= (.get row "retiredFrom") name) (in (.get row "phase") LIVE-PHASES)))))


(defn #^ bool new-generation-live [#^ list rows #^ str name #^ str want]  ; defk にできない: coordinator の純粋な判断が呼ぶ
  "担い手の行に、今の宣言の spec(指紋 want)で起こした Service name の process が動いている形で在るか(期限の起点)。"
  (any (gfor row rows (and (= (.get row "name") name) (= (.get row "specHash") want) (in (.get row "phase") LIVE-PHASES)))))


(defn #^ (| str None) last-refusal [#^ ClusterState state #^ str name #^ str want]  ; defk にできない: coordinator の純粋な判断が呼ぶ
  "今の宣言の spec(指紋 want)の process が送った最後の ReportReady(偽)の reason(業務の側が「なぜ準備できないか」を書く)。無ければ None。"
  (next (gfor report (reversed (or (.get state.readiness name) #()))
              :if (and (= (.get report "specHash") want) (not (.get report "ready")))
              (.get report "reason" ""))
        None))


(defn #^ (| HandoffWatch None) next-watch [#^ int now #^ ClusterState state job #^ ClusterTiming timing]  ; defk にできない: coordinator の純粋な判断が呼ぶ
  "Service job の 1 拍後の見張り(持たないなら None)。規則は頭注の 1〜3。"
  (setv name job.spec.name
        fingerprint (declaration-fingerprint job)
        kept (.get state.handoffs name)
        watch (if (and (is-not kept None) (= kept.declaration fingerprint)) kept None))
  (when (not (and job.spec.handoff (> job.replicas 0)))
    (return None))
  (setv rows (carrier-rows state name now timing))
  (cond
    (is rows None) watch
    (not (retired-live rows name)) None
    (and (is-not watch None) (= watch.phase HandoffPhase.ABANDONED)) watch
    True
      (do (setv verdict (service-readiness state name now timing)
                want (spec-hash job.spec)
                timeout-ms (handoff-timeout-ms job.readiness))
          (cond
            (= (get verdict "state") "Ready") None
            (is watch None)
              (if (new-generation-live rows name want) (HandoffWatch :declaration fingerprint :since-ms now) None)
            (and (>= (- now watch.since-ms) timeout-ms) (= (get verdict "state") "NotReady"))
              (replace watch :phase HandoffPhase.ABANDONED :abandoned-ms now
                       :reason (.format "新の世代が {:g} 秒の間 Ready にならなかった(新を止めて旧を残す): {}"
                                        (/ timeout-ms 1000) (get verdict "reason"))
                       :last-report (last-refusal state name want))
            True watch))))


(defn #^ ClusterState watch-handoffs [#^ int now #^ ClusterState state #^ ClusterTiming timing]  ; defk にできない: coordinator の純粋な判断(api_policy.settle)が呼ぶ
  "1 拍: 入れ替えの Service ごとに期限の見張りを進める(始める・諦める・捨てる)。変える物が無ければ同じ object(版と保存を進めない)。"
  (when (and (not state.handoffs) (not (any (gfor job state.jobs job.spec.handoff))))
    (return state))
  (setv watches (dfor job state.jobs
                      :setv watch (next-watch now state job timing)
                      :if (is-not watch None)
                      job.spec.name watch))
  (if (= watches state.handoffs) state (replace state :handoffs watches)))
