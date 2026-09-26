;;; coordinator の資源(Service・Worker・Task・Rollout)の純粋な判断。I/O はしない。
;;;
;;; - 版と出来事の記録: stamp が「前の状態と後の状態」の差から、変わった資源ごとに resourceVersion を進め(spec が変われば
;;;   generation も)、送り手・時刻・前後の版を出来事の記録へ足す。変化を起こした経路(API・heartbeat・調停・Rollout)を問わず
;;;   ここ 1 か所で付くので、版の付け忘れ・記録の漏れが起きない。
;;; - 書きの口: Service と Rollout は資源 1 つずつの compare-and-set(PUT は resourceVersion 必須・古ければ 409)。
;;;   一覧の丸ごとの上書きはしない。宣言を消せるのは所有者か、明示の force つきの delete だけ。
;;; - readiness: Service が Ready か(service-readiness)。process の生存(worker の報告の running)と、宣言が readiness を
;;;   持てば ReportReady の直近の報告の両方で決める。
(import dataclasses [replace])
(import json)
(import .cluster_model [ClusterState ClusterTiming Placement])
(import .worker_model [spec-hash])
(import .cluster_policy [job-from-json job-to-json alive still-live-somewhere unplaced-jobs task-summary])
(import .rollout_policy [validate-rollout-spec rollout-targets target-key TERMINAL-PHASES])
(import .base_follow_policy [base-observation])
(import .readiness_model [handoff-timeout-ms])

(setv LEGACY-OWNER "legacy:jobs")        ; 旧い PUT /jobs の頃からの宣言の所有者(誰でも 1 度だけ引き取れる)
(setv COORDINATOR "coordinator")          ; 調停(割り当て・task の置き先)の送り手
(setv MIGRATION "migration")              ; 旧い形の状態の file に版を振った送り手
(setv AUDIT-PER-KIND 300)                 ; 出来事の記録の上限(kind ごと)
(setv KINDS #("Service" "Worker" "Task" "Rollout"))


(defclass Refused [Exception]
  "要求を断る(HTTP の status と本文を持つ)。"
  (defn __init__ [self #^ int status #^ dict body]
    (.__init__ (super) (.get body "error" ""))
    (setv self.status status self.body body)))


(defn refuse [#^ int status #^ str message #** extra]
  (raise (Refused status (| {"error" message} extra))))


(defn #^ str key-of [#^ str kind #^ str name] (+ kind "/" name))


(defn #^ tuple split-key [#^ str key]
  (setv #(kind name) (.split key "/" 1))
  #(kind name))


(defn #^ (| str None) valid-actor [actor]
  (if (and (isinstance actor str) (< 0 (len (.strip actor)) 200)) (.strip actor) None))


(defn #^ str require-actor [actor]
  (or (valid-actor actor)
      (refuse 400 "送り手が無い。書きには header X-Actor(依頼の主体の id・作業係の名・worker の名)が要る")))


;; --- readiness -------------------------------------------------------------------------------

(defn #^ (| dict None) job-status-row [#^ ClusterState state #^ str worker #^ str name]
  (setv st (.get state.statuses worker))
  (when (is st None) (return None))
  (for [row (.get st "jobs" [])]
    (when (= (.get row "name") name) (return row)))
  None)


;; process の世代(2026-09-24 の実弾で改めた): readiness と計器の報告は、送った process の世代(worker が起こした時に振った名
;; instance・試行の番号 attempt・起こした spec の指紋 specHash・割り当ての世代 placement)を持つ。数えるのは「今の宣言の spec で、
;; 担い手の worker が running と報告している process」が出した報告だけ。以前は同じ担い手・同じ版の直近の報告なら数えたので、
;; 設定だけを変えた時(版は同じ)に、止めた前の process の Ready が window に残り、新しい process が 1 拍も終えないうちに Rollout が
;; 本番を止めた(Rollout 2026-09-25 05:11:12)。spec(設定を含む)が変われば指紋が変わり、以前の報告は数えない。
(setv REPORTS-KEPT 4)            ; Service ごとに残す報告(process の世代ごとに最新 1 つ・直近の 4 世代)


(defn #^ dict running-process [#^ ClusterState state #^ str name #^ int now #^ ClusterTiming timing
                               #^ (| Placement None) [placement None]]
  "Service を今動かしている process。ok = 担い手の worker が今の宣言の spec(版と設定の指紋・割り当ての世代)で running と
   報告している。ok でなければ state(NotReady | Unknown)と reason を持つ。
   placement = 見る置き先(既定 = いまの置き先。drain で並べた置き先(surge)の process を見る時はそれを渡す — drain_policy)。"
  (setv job (next (gfor j state.jobs :if (= j.spec.name name) j) None))
  (defn no [s reason] {"ok" False "state" s "reason" reason "job" job})
  (when (is job None) (return (no "NotReady" "宣言が無い")))
  (when (= job.replicas 0) (return (no "NotReady" "replicas 0(置かない)")))
  (setv a (if (is placement None) (.get state.placements name) placement))
  (when (is a None)
    (return (no "NotReady" (+ "置き先が無い: " (.get (unplaced-jobs now state timing) name "")))))
  (setv warming (< (- now state.started-ms) timing.lease-ms)
        st (.get state.statuses a.worker))
  ;; 担い手の報告が古い: 移し替えの期限(reassign-after-ms)の内なら「分からない」(Unknown — 途絶の間。Rollout は失敗と数えない)。
  ;; 期限を過ぎた担い手からは job を他へ移すので NotReady(2026-09-25: 以前は heartbeat が 10 秒途絶えただけで NotReady と言い、
  ;; 書き手が書き先へ書けているのに Rollout が戻しに入りえた)。
  (when (or (is st None) (> (- now (get st "at")) timing.lease-ms))
    (setv carrier (.get state.workers a.worker)
          silent (and carrier (alive now carrier timing.reassign-after-ms)))
    (return (no (if (or warming silent) "Unknown" "NotReady") (.format "担い手 {} の報告が無い・古い" a.worker))))
  (setv row (job-status-row state a.worker name))
  (when (or (is row None) (!= (.get row "phase") "running"))
    (return (no "NotReady" (.format "担い手 {} の上で {}" a.worker (if row (.get row "phase") "まだ起動していない")))))
  ;; 担い手の行の detail(入れ替えの途中・新の入口を読み込めない理由 — worker_policy.statuses)を添える: Service の status.readyReason
  ;; から「なぜ新が起きないか」が読める(2026-09-25)。
  (setv note (if (.get row "detail") (+ ":" (get row "detail")) ""))
  (when (!= (.get row "runningRevision") job.spec.revision)
    (return (no "NotReady" (.format "版が違う(動いている版 {}・宣言 {}){}" (.get row "runningRevision") job.spec.revision note))))
  (setv want (spec-hash job.spec))
  (when (not (.get row "instance"))
    (return (no "NotReady" (.format "担い手 {} が process の世代を報告しない(世代を知らない古い worker)" a.worker))))
  (when (!= (.get row "specHash") want)
    (return (no "NotReady" (.format "動いている process は前の宣言(設定か版)で起こした物(指紋 {}・宣言 {})— 起こし直しを待っている{}"
                                    (.get row "specHash") want note))))
  (when (and (is-not (.get row "placement") None) (!= (.get row "placement") a.generation))
    (return (no "NotReady" (.format "動いている process は前の割り当ての世代 {} で起こした物(いま {})" (.get row "placement") a.generation))))
  {"ok" True "job" job "worker" a.worker "row" row "instance" (get row "instance") "attempt" (str (.get row "attempts"))
   "specHash" want "placement" (.get row "placement")})


(defn #^ bool report-matches [#^ dict report #^ dict proc]
  "報告が、今動いている process(running-process の ok の答え)の物か。worker・世代の名・試行の番号・spec の指紋・割り当ての世代が全部一致する。"
  (and (get proc "ok")
       (= (.get report "worker") (get proc "worker"))
       (= (.get report "instance") (get proc "instance"))
       (= (str (.get report "attempt")) (get proc "attempt"))
       (= (.get report "specHash") (get proc "specHash"))
       (= (.get report "placement") (get proc "placement"))))


(defn #^ (| dict None) current-report [reports #^ dict proc]
  "報告の列(古い順)のうち、今動いている process の最新の物。"
  (next (gfor r (reversed (or reports #())) :if (report-matches r proc) r) None))


(defn #^ tuple keep-report [reports #^ dict report]
  "世代ごとに最新 1 つ・直近の REPORTS-KEPT 世代だけ残す(古い順)。担い手の heartbeat より先に新しい process の報告が届いても、
   heartbeat が追いついた時にその報告を数えられるよう、1 つに畳まない。"
  (setv others (lfor r (or reports #()) :if (!= (.get r "instance") (.get report "instance")) r))
  (tuple (cut (+ others [report]) (- REPORTS-KEPT) None)))


(defn #^ dict report-fields [#^ dict body #^ int now]
  "報告の本文から、送り手の process の世代と時刻を取り出す(readiness と計器で同じ)。"
  {"worker" (get body "worker") "pid" (.get body "pid") "revision" (get body "revision")
   "instance" (.get body "instance") "attempt" (.get body "attempt") "specHash" (.get body "specHash")
   "placement" (.get body "placement") "at" now})


(defn #^ dict service-readiness [#^ ClusterState state #^ str name #^ int now #^ ClusterTiming timing
                                 #^ (| Placement None) [placement None]]
  "Service が Ready か。state = Ready | NotReady | Unknown(coordinator が起動した直後で報告が揃っていない)。
   placement = 見る置き先(既定 = いまの置き先 — running-process)。"
  (defn verdict [s reason] {"state" s "reason" reason})
  (setv proc (running-process state name now timing placement))
  (when (not (get proc "ok")) (return (verdict (get proc "state") (get proc "reason"))))
  (setv job (get proc "job"))
  (when (is job.readiness None)
    (return (verdict "Ready" "process が動いている(readiness の宣言なし)")))
  (setv window-ms (int (* 1000 (get job.readiness "windowSeconds")))
        report (current-report (.get state.readiness name) proc))
  (when (is report None)
    (return (verdict (if (< (- now state.started-ms) window-ms) "Unknown" "NotReady")
                     (.format "今動いている process(世代 {})からの準備できたの報告がまだ無い" (get proc "instance")))))
  (setv age (- now (get report "at")))
  (setv role (.get report "role" "active"))
  (cond
    (> age window-ms) (verdict "NotReady" (.format "最後の報告から {} 秒(window {} 秒)" (// age 1000) (// window-ms 1000)))
    (not (get report "ready")) (| (verdict "NotReady" (+ "報告: " (.get report "reason" ""))) {"role" role})
    True (| (verdict "Ready" (.get report "reason" "")) {"role" role})))


(defn #^ ClusterState record-readiness [#^ ClusterState state #^ str name #^ dict body #^ int now]
  (when (not (any (gfor j state.jobs (= j.spec.name name))))
    (refuse 404 (+ "無い Service: " name)))
  (setv report (| (report-fields body now)
                  {"ready" (bool (get body "ready")) "reason" (cut (str (.get body "reason" "")) 0 300)
                   ;; active(仕事をしている)か standby(lease を他が持つ間の待機)。旧い報告は active。
                   "role" (if (= (.get body "role") "standby") "standby" "active")}))
  (replace state :readiness (| state.readiness {name (keep-report (.get state.readiness name) report)})))


;; --- 資源の写し(版と記録の比べる単位) ------------------------------------------------------------

(defn #^ dict service-spec [job]
  (dfor #(k v) (.items (job-to-json job)) :if (!= k "name") k v))


(defn #^ dict snapshot [#^ ClusterState state #^ int now #^ ClusterTiming timing]
  "資源ごとの {spec status}。比べて版を進める単位。変わりやすい観測(生存の時刻・lease の期限)は入れない。"
  (setv out {})
  (for [job state.jobs]
    (setv a (.get state.placements job.spec.name))
    (setv (get out (key-of "Service" job.spec.name))
          {"spec" (service-spec job)
           "status" (| {"worker" (if a a.worker None) "placement" (if a a.generation None)
                        "ready" (get (service-readiness state job.spec.name now timing) "state")}
                       ;; 土台の版の追随の観測(本番の Deployment の image と、その LABEL の commit)。追う宣言にだけ載せる。
                       (if job.base-from {"base" (base-observation state job now)} {})
                       ;; drain で並べた置き先(2026-09-25)。在る間だけ載せる(無い Service の status の形・版は以前と同じ)。
                       (if (in job.spec.name state.surges)
                           {"surge" (. (get state.surges job.spec.name) worker)}
                           {})
                       ;; 入れ替えの期限の見張り(2026-09-26 — handoff_policy)。Ready を待つ間と諦めた間だけ載せる: 段・起点・期限、
                       ;; 諦めたなら時刻と理由(期限と最後の NotReady の理由)と新の世代の最後の ReportReady(偽)の reason。
                       (if (in job.spec.name state.handoffs)
                           {"handoff" (.status-json (get state.handoffs job.spec.name) (handoff-timeout-ms job.readiness))}
                           {}))}))
  (for [w (.values state.workers)]
    (setv (get out (key-of "Worker" w.name))
          {"spec" {"labels" (dict w.labels) "capacity" w.capacity "versions" (dict w.versions)}
           ;; drain(2026-09-25)の始まりと頼み手(誰が・いつ空けさせたかを出来事の記録に残す)。期限は頼み直すたびに延びるので入れない。
           "status" (if (in w.name state.drains)
                        {"drain" {"sinceMs" (. (get state.drains w.name) since-ms) "actor" (. (get state.drains w.name) actor)}}
                        {})}))
  (for [t (.values state.tasks)]
    (setv (get out (key-of "Task" t.id))
          {"spec" (| {"name" t.name "env" t.env "revision" t.revision "requires" (dict t.requires)}
                     (if t.detached {"key" t.key} {}))
           "status" {"phase" t.phase "worker" t.worker "detail" t.detail}}))
  (for [#(name r) (.items state.rollouts)]
    (setv (get out (key-of "Rollout" name)) {"spec" (get r "spec") "status" (get r "status")}))
  out)


(defn short-value [v]
  (setv text (json.dumps v :ensure-ascii False :sort-keys True :default str))
  (if (> (len text) 160) (+ (cut text 0 157) "…") v))


(defn #^ dict changed-fields [old new]
  "何が変わったか(spec / status の一段目の欄ごとに前と後。長い値は切る)。"
  (setv out {})
  (for [part #("spec" "status")]
    (setv a (if old (get old part) {}) b (if new (get new part) {}))
    (for [k (sorted (| (set a) (set b)))]
      (when (!= (.get a k) (.get b k))
        (setv (get out (+ part "." k)) [(short-value (.get a k)) (short-value (.get b k))]))))
  out)


(defn #^ tuple trim-audit [#^ list audit]
  "kind ごとに直近 AUDIT-PER-KIND 件だけ残す(順は保つ)。"
  (setv counts {} keep [])
  (for [event (reversed audit)]
    (setv kind (get event "kind") n (.get counts kind 0))
    (when (< n AUDIT-PER-KIND)
      (setv (get counts kind) (+ n 1))
      (.append keep event)))
  (tuple (reversed keep)))


(defn #^ ClusterState stamp [#^ ClusterState before #^ ClusterState after #^ str actor #^ int now #^ ClusterTiming timing]
  "before → after の差を資源ごとに版へ写す。変わった資源の resourceVersion を coordinator 全体の番号で進め、spec が変われば
   generation も進め、出来事を 1 件記録する。版の欄の無い資源(旧い形の file から読んだ物)は adopt として版を振る。"
  (when (is before after) (return after))
  (setv b (snapshot before now timing) a (snapshot after now timing)
        meta (dict after.meta) audit (list after.audit) rev after.revision seq after.audit-seq)
  (for [key (sorted (| (set a) (set b)))]
    (setv old (.get b key) new (.get a key))
    (when (and (= old new) (or (is new None) (in key meta))) (continue))
    (+= rev 1)
    (+= seq 1)
    (setv #(kind name) (split-key key) current (.get meta key))
    (cond
      (is new None)
        (do (.pop meta key None)
            (setv verb "delete" from (if current (get current "resourceVersion") None) to None
                  generation (if current (get current "generation") None)))
      (is current None)
        (do (setv verb (if (is old None) "create" "adopt") from None to rev generation 1)
            (setv (get meta key) {"resourceVersion" rev "generation" 1 "createdBy" actor "createdMs" now
                                  "updatedBy" actor "updatedMs" now}))
      True
        (do (setv spec-changed (!= (get old "spec") (get new "spec"))
                  verb (if spec-changed "update" "status") from (get current "resourceVersion") to rev
                  generation (+ (get current "generation") (if spec-changed 1 0)))
            (setv (get meta key) (| current {"resourceVersion" rev "generation" generation
                                             "updatedBy" actor "updatedMs" now}))))
    (.append audit {"seq" seq "at" now "actor" actor "verb" verb "kind" kind "name" name
                    "fromVersion" from "toVersion" to "generation" generation
                    "changes" (changed-fields old new)}))
  (replace after :meta meta :revision rev :audit (trim-audit audit) :audit-seq seq))


(defn #^ ClusterState adopt-legacy [#^ ClusterState state #^ int now #^ ClusterTiming timing]
  "旧い形の状態の file(版の欄が無い)を読んだ直後に 1 度: 所有者の無い宣言を LEGACY-OWNER にし、全資源に版を振る(送り手 = migration)。"
  (setv owned (replace state :jobs (tuple (gfor j state.jobs (if (is j.owner None) (replace j :owner LEGACY-OWNER) j)))))
  (stamp (replace state :meta state.meta) owned MIGRATION now timing))


;; --- 見せる形 --------------------------------------------------------------------------------

(defn #^ dict resource-json [#^ ClusterState state #^ str key #^ dict snap #^ int now #^ ClusterTiming timing]
  (setv #(kind name) (split-key key) m (.get state.meta key {}) row (get snap key))
  (setv status (dict (get row "status")))
  (cond
    (= kind "Service")
      (do (setv a (.get state.placements name) verdict (service-readiness state name now timing)
                reports (.get state.readiness name))
          (.update status {"readyReason" (get verdict "reason")
                           "lastReadiness" (if reports (get reports -1) None)
                           "process" (if a (job-status-row state a.worker name) None)}))
    (= kind "Worker")
      (do (setv w (get state.workers name))
          (.update status {"silentMs" (- now w.last-seen-ms) "alive" (alive now w timing.lease-ms)}))
    (= kind "Task") (.update status (task-summary (get state.tasks name)))
    (= kind "Rollout")
      (.update status {"observed" (dfor t (rollout-targets (get row "spec"))
                                        :if (= (.get t "kind") "Deployment")
                                        (target-key t) (.get state.deployments (+ (get t "namespace") "/" (get t "name"))))}))
  {"kind" kind "name" name
   "resourceVersion" (.get m "resourceVersion") "generation" (.get m "generation")
   "owner" (.get (get row "spec") "owner")
   "createdBy" (.get m "createdBy") "createdMs" (.get m "createdMs")
   "updatedBy" (.get m "updatedBy") "updatedMs" (.get m "updatedMs")
   "spec" (get row "spec") "status" status})


(defn #^ dict list-resources [#^ ClusterState state #^ str kind #^ int now #^ ClusterTiming timing]
  (when (not-in kind KINDS) (refuse 404 (+ "知らない kind: " kind)))
  (setv snap (snapshot state now timing))
  {"kind" kind "revision" state.revision
   "items" (lfor key (sorted snap) :if (.startswith key (+ kind "/")) (resource-json state key snap now timing))})


(defn #^ dict get-resource [#^ ClusterState state #^ str kind #^ str name #^ int now #^ ClusterTiming timing]
  (when (not-in kind KINDS) (refuse 404 (+ "知らない kind: " kind)))
  (setv snap (snapshot state now timing) key (key-of kind name))
  (when (not-in key snap) (refuse 404 (+ "無い資源: " key)))
  (resource-json state key snap now timing))


(defn #^ dict events-view [#^ ClusterState state #^ dict query]
  (setv kind (.get query "kind") name (.get query "name") since (int (.get query "since" 0))
        limit (min (int (.get query "limit" 200)) 2000))
  (setv rows (lfor e state.audit
                   :if (and (> (get e "seq") since) (or (not kind) (= (get e "kind") kind)) (or (not name) (= (get e "name") name)))
                   e))
  {"revision" state.revision "seq" state.audit-seq "events" (cut rows (- limit) None)})


;; --- 書きの口(Service と Rollout)--------------------------------------------------------------

(defn check-version [#^ ClusterState state #^ str key version [required True]]
  (setv current (.get (.get state.meta key {}) "resourceVersion"))
  (cond
    (and required (is version None))
      (refuse 400 "resourceVersion が要る(読んだ時の版を付けて書く — 古い版の書きは 409 で断る)" :current current)
    (and (is-not version None) (!= version current))
      (refuse 409 (.format "版が古い: 送られた {}・いまの {}(読み直してから書く)" version current) :current current)))


(defn #^ tuple active-rollouts-touching [#^ ClusterState state target-keys [skip None]]
  (tuple (gfor #(name r) (.items state.rollouts)
               :if (and (!= name skip) (not-in (.get (get r "status") "phase") TERMINAL-PHASES)
                        (& (set target-keys) (sfor t (rollout-targets (get r "spec")) (target-key t))))
               name)))


(defn #^ ClusterState create-resource [#^ ClusterState state #^ str kind #^ dict body #^ str actor #^ int now]
  (setv name (get body "name") spec (dict (.get body "spec" {})))
  (when (not (and (isinstance name str) name (not-in "/" name))) (refuse 400 (.format "名前が正しくない: {!r}" name)))
  (setv owner (or (valid-actor (.get spec "owner")) actor))
  (cond
    (= kind "Service")
      (do (when (any (gfor j state.jobs (= j.spec.name name))) (refuse 409 (+ "もう在る Service: " name)))
          (setv job (job-from-json (| spec {"name" name "owner" owner})))
          (replace state :jobs (+ state.jobs #(job))))
    (= kind "Rollout")
      (do (when (in name state.rollouts) (refuse 409 (+ "もう在る Rollout: " name)))
          (setv spec (validate-rollout-spec (| spec {"owner" owner})))
          (for [t (rollout-targets spec)]
            (when (and (= (get t "kind") "Service") (not (any (gfor j state.jobs (= j.spec.name (get t "name"))))))
              (refuse 400 (+ "Rollout の相手の Service が無い(先に作る): " (get t "name")))))
          (setv busy (active-rollouts-touching state (lfor t (rollout-targets spec) (target-key t))))
          (when busy (refuse 409 (+ "同じ相手を扱う Rollout が進行中: " (.join ", " busy))))
          (replace state :rollouts (| state.rollouts {name {"spec" spec "status" {"phase" "Pending" "createdMs" now}}})))
    True (refuse 405 (+ "この kind は API から作れない: " kind))))


(defn check-owner-change [current-owner new-owner actor]
  (when (and new-owner (!= new-owner current-owner) (!= actor current-owner) (!= current-owner LEGACY-OWNER))
    (refuse 403 (.format "所有者を変えられるのは所有者({})だけ" current-owner))))


(defn #^ ClusterState update-resource [#^ ClusterState state #^ str kind #^ str name #^ dict body #^ str actor]
  (setv key (key-of kind name) spec (dict (.get body "spec" {})))
  (cond
    (= kind "Service")
      (do (setv current (next (gfor j state.jobs :if (= j.spec.name name) j) None))
          (when (is current None) (refuse 404 (+ "無い Service: " name)))
          (check-version state key (.get body "resourceVersion"))
          (check-owner-change current.owner (.get spec "owner") actor)
          ;; base は追随の係(base-follow)が持つ欄: baseFrom を持つ宣言の書き換えが base を書かなければ、いまの値を保つ
          ;; (宣言し直すたびに base が落ちて、土台の無い木への入れ替えと追随の係による戻しの 2 回の入れ替えが起きないように)。
          (when (and (.get spec "baseFrom") (not-in "base" spec) (is-not current.spec.base None))
            (setv (get spec "base") current.spec.base))
          (setv job (job-from-json (| spec {"name" name "owner" (or (valid-actor (.get spec "owner")) current.owner)})))
          (replace state :jobs (tuple (gfor j state.jobs (if (= j.spec.name name) job j)))))
    (= kind "Rollout")
      (do (setv current (.get state.rollouts name))
          (when (is current None) (refuse 404 (+ "無い Rollout: " name)))
          (check-version state key (.get body "resourceVersion"))
          (setv old (get current "spec"))
          ;; spec は作った後に変えない。変えてよいのは中止(abort)だけ(旧を先に戻してから新を止める)。
          ;; 送られた spec は作る時と同じ形に揃えてから比べる({"abort": true} だけを送ってもよい)。
          (setv incoming (if (or (in "from" spec) (in "to" spec))
                             (validate-rollout-spec (| spec {"owner" (.get spec "owner" (get old "owner"))}))
                             (| old spec)))
          (when (!= (dfor #(k v) (.items incoming) :if (!= k "abort") k v)
                    (dfor #(k v) (.items old) :if (!= k "abort") k v))
            (refuse 409 "Rollout の spec は変えられない(変えてよいのは abort だけ。別の向きは新しい Rollout で表す)"))
          (replace state :rollouts (| state.rollouts {name (| current {"spec" (| old {"abort" (bool (.get spec "abort"))})})})))
    True (refuse 405 (+ "この kind は API から書けない: " kind))))


(defn #^ ClusterState delete-resource [#^ ClusterState state #^ str kind #^ str name #^ dict query #^ str actor
                                      #^ int now #^ ClusterTiming timing]
  (setv key (key-of kind name) force (in (.get query "force" "") #("true" "1"))
        version (if (in "resourceVersion" query) (int (get query "resourceVersion")) None))
  (check-version state key version :required False)
  (cond
    (= kind "Service")
      (do (setv current (next (gfor j state.jobs :if (= j.spec.name name) j) None))
          (when (is current None) (refuse 404 (+ "無い Service: " name)))
          (when (and (!= actor current.owner) (not force))
            (refuse 403 (.format "宣言を消せるのは所有者({})か、明示の force つきの delete だけ" current.owner)))
          (setv busy (active-rollouts-touching state [(target-key {"kind" "Service" "name" name})]))
          (when (and busy (not force)) (refuse 409 (+ "この Service を扱う Rollout が進行中: " (.join ", " busy))))
          (replace state :jobs (tuple (gfor j state.jobs :if (!= j.spec.name name) j))
                         :readiness (dfor #(k v) (.items state.readiness) :if (!= k name) k v)
                         :metrics (dfor #(k v) (.items state.metrics) :if (!= k name) k v)))
    (= kind "Rollout")
      (do (setv current (.get state.rollouts name))
          (when (is current None) (refuse 404 (+ "無い Rollout: " name)))
          (setv owner (.get (get current "spec") "owner"))
          (when (and (!= actor owner) (not force))
            (refuse 403 (.format "Rollout を消せるのは所有者({})か、明示の force つきの delete だけ" owner)))
          (when (and (not-in (.get (get current "status") "phase") TERMINAL-PHASES) (not force))
            (refuse 409 "進行中の Rollout は消せない(中止は abort を書く。旧を先に戻してから新を止める)"))
          (replace state :rollouts (dfor #(k v) (.items state.rollouts) :if (!= k name) k v)))
    (= kind "Worker")
      (do (setv w (.get state.workers name))
          (when (is w None) (refuse 404 (+ "無い Worker: " name)))
          (when (alive now w timing.reassign-after-ms)
            (refuse 409 "生きている worker は忘れられない(移し替えの期限まで沈黙した worker だけ)"))
          (replace state :workers (dfor #(k v) (.items state.workers) :if (!= k name) k v)
                         :statuses (dfor #(k v) (.items state.statuses) :if (!= k name) k v)))
    (= kind "Task")
      (do (when (not-in name state.tasks) (refuse 404 (+ "無い Task: " name)))
          (replace state :tasks (dfor #(k v) (.items state.tasks) :if (!= k name) k v)))
    True (refuse 404 (+ "知らない kind: " kind))))


;; --- 旧い PUT /jobs(移行の間だけ)-------------------------------------------------------------

(defn #^ tuple legacy-put-jobs [#^ ClusterState state #^ list rows #^ str actor]
  "旧い口 PUT /jobs を資源ごとの compare-and-set に写す(2026-09-24 決定)。
   - 行に resourceVersion があれば、その版の時だけその Service を書き換える。
   - 行に resourceVersion が無ければ、無い Service を作るだけ(在って中身が違えば競合)。中身が同じなら何もしない。
   - 一覧に無い Service は消さない(消すのは DELETE だけ)。返事の untouched に並べる。
   - 1 行でも競合すれば何も書かない(全部か無しか)。返事 409 に行ごとの理由。"
  (setv current (dfor j state.jobs j.spec.name j) jobs (list state.jobs) conflicts [] results {})
  (for [row rows]
    (setv name (get row "name") have (.get current name) version (.get row "resourceVersion"))
    (setv owner (if have have.owner (or (valid-actor (.get row "owner")) actor)))
    (setv job (job-from-json (| {"replicas" (if have have.replicas 1) "readiness" (if have have.readiness None)}
                                (dfor #(k v) (.items row) :if (!= k "resourceVersion") k v)
                                {"owner" owner})))
    (cond
      (is have None)
        (if (is-not version None)
            (.append conflicts {"name" name "error" "版が付いているが、その Service は無い(消された)"})
            (do (.append jobs job) (setv (get results name) "created")))
      (= job have) (setv (get results name) "unchanged")
      (is version None)
        (.append conflicts {"name" name "error" "resourceVersion の無い行で既存の宣言は変えられない(GET /state の行の版を付ける)"
                            "current" (.get (.get state.meta (key-of "Service" name) {}) "resourceVersion")})
      (!= version (.get (.get state.meta (key-of "Service" name) {}) "resourceVersion"))
        (.append conflicts {"name" name "error" "版が古い"
                            "current" (.get (.get state.meta (key-of "Service" name) {}) "resourceVersion")})
      (and (!= (.get row "owner" have.owner) have.owner) (!= actor have.owner) (!= have.owner LEGACY-OWNER))
        (.append conflicts {"name" name "error" (.format "所有者を変えられるのは所有者({})だけ" have.owner)})
      True
        (do (setv jobs (lfor j jobs (if (= j.spec.name name) job j)))
            (setv (get results name) "updated"))))
  (setv untouched (lfor n (sorted current) :if (not-in n (sfor r rows (get r "name"))) n))
  (if conflicts
      #(state 409 {"error" "競合した行がある(何も書いていない)" "conflicts" conflicts})
      #((replace state :jobs (tuple jobs)) 200 {"jobs" (len rows) "results" results "untouched" untouched
                                                "note" "一覧に無い Service は消していない(消すのは DELETE /resources/Service/<名>)"})))
