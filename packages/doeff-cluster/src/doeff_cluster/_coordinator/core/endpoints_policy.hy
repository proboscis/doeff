;;; job の Service の振り先(k8s の EndpointSlice)の純粋な判断。I/O は呼び手(core/program.endpoints-tick — 見張りと書きの effect は
;;; intent/kube_model の FollowJobServices・ApplyEndpointSlice)。
;;;
;;; job の Service(label kube_service_model.JOB-ENDPOINTS-LABEL を持つ k8s の Service)は selector を持たず、振り先を coordinator が job
;;; ごとの Ready から書く:
;;;   振り先 = lease の内の worker の世代のうち、その job の process を running と報告し、その process から window の内に ready で
;;;            role active の準備の報告を受けている世代の Pod の IP の組(heartbeat の podIp)。
;;; 世代をまたぐ受け渡し(RollingUpdate — 新しい Pod は受け渡しが済むまで NotReady・AL-D2k)の間も、job を今動かしている process の在る
;;; Pod へ振る: 新旧どちらも active の間は両方・standby の process しか無い世代は入れない・lease の切れた世代(黙った Pod)は外す。Pod の
;;; Ready(readinessProbe)は Deployment の入れ替えの進みにだけ使い、振り先には使わない。
;;; 以前は Pod の Ready ∧ Pod の label job.doeff.dev/<job>=placed で振った: 受け渡しの間に旧い Pod の process が終わると、新しい Pod に
;;; Ready な process が在っても新しい Pod が Ready になるまで届かなかった(2026-11-06 06:53:44〜06:57:10 UTC の 3 分半)。
;;;
;;;   RESYNC-MS          同じ宣言の slice を書き直す間(手で変えた・消した slice を戻す)
;;;   RETRY-MS           書けなかった slice を書き直すまでの間(API server に届かない間、歩ごとに書きに行かない)
;;;   generation-reports lease の内の worker の世代の報告の列(今の世代と退いた世代)
;;;   serving-addresses  job 1 つの振り先の Pod の IP の組
;;;   settled-from       coordinator の起動の後、job の振り先を書いてよい最初の刻
;;;   wanted-slices      見張っている job の Service の全部の、今の振り先の slice の宣言
;;;   due-slices         wanted のうち、今書く物
;;;   slice-writes       書いた・書けなかった結末と、消えた Service の行を外す、表への書き
;;;   slice-line         書いた・書けなかった slice 1 つの log の 1 行
;;;   endpoints-due      振り先の判断が時刻で変わる次の刻(調停ループを起こす — wake_policy.next-wake)
;;;
;;; 気をつける事(前の形より遅れる場面): coordinator が止まっている間は slice が前のまま残る — その間に死んだ worker の Pod の IP は、
;;; coordinator が起き直して settled-from を過ぎるまで振り先に残る(前の形は kubelet が Pod ごとに readinessProbe を読み、coordinator の
;;; 止まりと無関係に外した)。起動の直後(settled-from の前)は heartbeat と準備の報告が揃っていないので書かない(空の組を書いて全部の
;;; 振り先を消さない)。lease の窓(ClusterTiming.lease-ms)は readinessProbe の失敗の数え(period × failureThreshold)と同じ程の長さ。
(require doeff-hy.macros [defk val var <-])
(val MODULE-TAGS {:context "coordinator" :role "judgment"})
(import doeff_hy._table [TableWrite])
(import doeff_cluster._shared.intent.protocol [ClusterTiming])
(import doeff_cluster._shared.intent.job_model [JobPhase])
(import doeff_cluster._coordinator.intent.cluster_model [ClusterState JobEndpointSlice JobServicesSeen SliceWritten SliceRefused])
(import doeff_cluster._coordinator.core.resource_policy [active-ready-senders])
(import doeff_cluster._shared.intent.due_model [DueAt DueNow DueNever])
(import doeff_cluster._shared.core.due_policy [earliest-due])

;; 同じ宣言の slice を書き直す間(5 分)と、書けなかった slice を書き直すまでの間(10 秒)。
(val RESYNC-MS (* 5 60 1000))
(val RETRY-MS (* 10 1000))


(defk slice-key [namespace service]
  {:pre [(: namespace str) (: service str)] :post [(: % str)] :tags {:context "coordinator" :role "judgment"}}
  "書いた slice の表(ClusterObservations.endpoint-slices)の鍵を作るため(「namespace/Service の名」)。"
  (+ namespace "/" service))


(defk generation-reports [state now timing]
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "振り先の候補の worker の世代の報告 #(worker の名 報告) の列を集めるため: 今の世代(statuses)と退いた世代(retired-statuses — 鍵
   <名>/<世代>)のうち、最後の報告が lease の窓の内の物(黙った世代は Pod ごと消えたと読む — cluster_policy.sweep-stopped-generations と
   同じ窓)。"
  (+ (tuple (gfor #(name report) (.items state.observations.statuses) :if (<= now (+ report.at timing.lease-ms)) #(name report)))
     (tuple (gfor #(key report) (.items state.observations.retired-statuses) :if (<= now (+ report.at timing.lease-ms))
                  #((get (.split key "/" 1) 0) report)))))


(defk serving-addresses [state job now timing]
  {:pre [(: state ClusterState) (: job str) (: now int) (: timing ClusterTiming)] :post [(: % (get tuple #(str ...)))]
   :tags {:context "coordinator" :role "judgment"}}
  "job の Service の振り先の Pod の IP の組(重ねない・順)を決めるため(頭の註): lease の内の世代の報告のうち、job の process(名が job か、
   入れ替えで job から退いた process)を running と報告し、その process から ready で role active の準備の報告を受けている世代の Pod の IP。
   readiness を宣言しない job は running の process の在る世代。Pod の IP を名乗らない世代(Pod の外・旧い worker)は数えない。"
  (<- senders (| frozenset None) (active-ready-senders state job now))
  (<- reports tuple (generation-reports state now timing))
  (tuple (sorted (set (gfor #(worker report) reports
                            :if (and report.pod-ip
                                     (any (gfor row report.jobs
                                                :if (and (= (or row.retired-from row.name) job) (= row.phase JobPhase.RUNNING.value) row.instance)
                                                (or (is senders None) (in #(worker row.instance) senders)))))
                            report.pod-ip)))))


(defk settled-from [state job]
  {:pre [(: state ClusterState) (: job str)] :post [(: % int)] :tags {:context "coordinator" :role "judgment"}}
  "coordinator の起動の後、job の振り先を書いてよい最初の刻を知るため(頭の註): 起動の刻 + 準備の報告の window(readiness を宣言しない
   job は 0)。lease の窓は呼び手が足す — ここは job の宣言だけを読む。"
  (val spec (next (gfor j state.jobs :if (= j.spec.name job) j) None))
  (val window-ms (if (or (is spec None) (is spec.readiness None)) 0 (int (* 1000 (get spec.readiness "windowSeconds")))))
  (+ state.started-ms window-ms))


(defk wanted-slices [state now timing]
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming)] :post [(: % (get tuple #(JobEndpointSlice ...)))]
   :tags {:context "coordinator" :role "judgment"}}
  "見張っている job の Service(読めた namespace の一覧 — 読めない namespace の slice は書き直さない)の全部について、今の振り先の slice の
   宣言を決めるため。起動の後 settled-from + lease の窓を過ぎていない job の Service は載せない(頭の註)。"
  (var wanted #())
  (for [seen (.rows state.observations.job-services)]
    (when (isinstance seen JobServicesSeen)
      (for [service seen.services]
        (<- from int (settled-from state service.job))
        (when (>= now (+ from timing.lease-ms))
          (<- addresses tuple (serving-addresses state service.job now timing))
          (:= wanted (+ wanted #((JobEndpointSlice :namespace service.namespace :service service.name :service-uid service.uid
                                                   :job service.job :addresses addresses :ports service.ports))))))))
  wanted)


(defk due-slices [state wanted now]
  {:pre [(: state ClusterState) (: wanted tuple) (: now int)] :post [(: % (get tuple #(JobEndpointSlice ...)))]
   :tags {:context "coordinator" :role "judgment"}}
  "wanted のうち、今書く slice を選ぶため: まだ書いていない・前に書いた宣言と違う・同じ宣言を書いてから RESYNC-MS を過ぎた物。書けなかった
   slice は RETRY-MS を過ぎるまで書き直さない(宣言が変わっても — API server に届かない間、歩ごとに書きに行かない)。壁の時計が戻った
   (前の刻が今より後)時は書く。"
  (var due #())
  (for [slice wanted]
    (<- key str (slice-key slice.namespace slice.service))
    (val prior (.row state.observations.endpoint-slices key))
    (when (cond
            (is prior None) True
            (< now prior.at) True
            (isinstance prior SliceRefused) (>= (- now prior.at) RETRY-MS)
            (!= prior.slice slice) True
            True (>= (- now prior.at) RESYNC-MS))
      (:= due (+ due #(slice)))))
  due)


(defk slice-writes [state outcomes]
  {:pre [(: state ClusterState) (: outcomes tuple)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "書いた・書けなかった結末 outcomes(SliceWritten / SliceRefused の列)を、書いた slice の表への書きにするため。読めた namespace の一覧に
   無くなった Service の行も外す(k8s の GC が ownerReference で slice ごと消す — 表は書いた物の比べの相手なので、残すと同じ名の Service を
   作り直した時に書かない)。"
  (var listed #())
  (var seen-namespaces #())
  (for [#(namespace seen) (.items state.observations.job-services)]
    (when (isinstance seen JobServicesSeen)
      (:= seen-namespaces (+ seen-namespaces #(namespace)))
      (for [service seen.services]
        (<- key str (slice-key service.namespace service.name))
        (:= listed (+ listed #(key))))))
  (var writes #())
  (for [outcome outcomes]
    (<- key str (slice-key outcome.slice.namespace outcome.slice.service))
    (:= writes (+ writes #((TableWrite key outcome)))))
  (+ writes (tuple (gfor #(key prior) (.items state.observations.endpoint-slices)
                         :if (and (in prior.slice.namespace seen-namespaces) (not-in key listed))
                         (TableWrite key None)))))


(defk slice-line [outcome]
  {:pre [(: outcome (| SliceWritten SliceRefused))] :post [(: % str)] :tags {:context "coordinator" :role "judgment"}}
  "書いた・書けなかった slice 1 つを coordinator の log の 1 行にするため(振り先の移りを後から刻つきで読む)。"
  (val slice outcome.slice)
  (val where (.format "{}/{}(job {})" slice.namespace slice.service slice.job))
  (if (isinstance outcome SliceWritten)
      (.format "coordinator: job の Service の振り先を書いた — {}: {}" where (or (.join "・" slice.addresses) "無し"))
      (.format "coordinator: job の Service の振り先を書けない — {}: {}({} 秒の後に書き直す)" where outcome.error (// RETRY-MS 1000))))


(defk endpoints-due [state now timing]
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming)] :post [(: % (| DueAt DueNow DueNever))]
   :tags {:context "coordinator" :role "judgment"}}
  "振り先の判断が時刻で変わる次の刻を知るため(wake_policy.next-wake — 要求の無い間も、振り先を書く刻に調停ループを起こす): 見張っている
   job の Service ごとに、起動の後に書き始める刻(settled-from + lease の窓)・書けなかった slice を書き直す刻(RETRY-MS)・同じ宣言を
   書き直す刻(RESYNC-MS)と、振り先の候補の世代の報告が lease の窓を外れる刻(黙った世代の Pod を外す — 全部の worker が黙っても
   起きる)のいちばん早い刻。書き始めた後でまだ書いていない slice は今すぐ。見張る Service が無ければ無し。"
  (var dues #())
  (for [seen (.rows state.observations.job-services)]
    (when (isinstance seen JobServicesSeen)
      (for [service seen.services]
        (<- from int (settled-from state service.job))
        (<- key str (slice-key service.namespace service.name))
        (val prior (.row state.observations.endpoint-slices key))
        (val settled (+ from timing.lease-ms))
        (:= dues (+ dues #((cond
                             (< now settled) (DueAt :at settled)
                             (is prior None) (DueNow)
                             (isinstance prior SliceRefused) (DueAt :at (+ prior.at RETRY-MS))
                             True (DueAt :at (+ prior.at RESYNC-MS)))))))))
  (when dues
    (<- reports tuple (generation-reports state now timing))
    (for [#(_ report) reports]
      (:= dues (+ dues #((DueAt :at (+ report.at timing.lease-ms 1)))))))
  (<- answer (| DueAt DueNow DueNever) (earliest-due dues))
  answer)
