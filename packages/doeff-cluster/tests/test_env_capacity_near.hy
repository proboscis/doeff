;; worker の作業の disk の空きの予告。
;; 2026-10-10 13:39 JST に zeus の worker の空きが新しい版の準備の下限 WORKER_ENV_MIN_FREE_GIB(25 GiB)を切り、zeus へ置く job の新しい版の
;; 準備が全部止まった。heartbeat の envCapacity は exhausted / ok の 2 語だけで、下限を切ってから分かった(1 時間に約 5 GB 減っていた)。
;;   (1) env-capacity は下限 + NEAR-MARGIN-BYTES(20 GB)を切った空きに near を名乗る(下限を切れば今までどおり exhausted・下限 0 は ok)
;;   (2) coordinator の Worker の資源の status は envCapacity が ok でない間だけ載せる(読み手が GET /resources/Worker で読む —
;;       ok の worker の status の形と版は以前と同じ)
;;   (3) ok でない語を名乗った後は、空きがその線の上 NEAR-RETURN-BYTES(2 GB)を越えるまで near のまま名乗る(依頼
;;       lt-JJVZWCWHC91PJ5MF15W3YDKSQC)。2026-10-17 の mac-native は APFS の local snapshot の切れと版の env の組みで空きが線の上下 1 GB を
;;       行き来し、名乗りが near と ok を往復するたびに、見回りの責任 workers-stay-live が同じ訳の failing を新しい 1 続きとして作り直した。
;;       env-host は最後に名乗った語を拍を跨いで持ち、次の名乗りに渡す。
;; 失敗ケース = near を名乗らない(下限を切るまで ok)・status に載せない(読み手が予告を読めない)・戻りの幅を持たない(線の上 0.76 GB で ok に
;; 戻る)・env-host が前の名乗りを持たない(ok へ戻った後の線の上の揺れで near に戻る)。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defeffect の展開が名指す
(import pathlib [Path])
(import doeff [Program with-handlers])
(import doeff_core_effects [DiskUsage ReadDiskUsage])
(import doeff_core_effects [slog-handler state])
(import doeff_core_effects [os-file-handler])
(import doeff_core_effects [subprocess-handler])
(import doeff_time [sync-time-handler])
(import tests.state_rows [table-from-rows])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster._worker.core.env_upkeep [env-capacity NEAR-MARGIN-BYTES NEAR-RETURN-BYTES])
(import doeff_cluster [EnvReport])
(import doeff_cluster._worker.protocol.code_store [PREPARE-TOOL])
(import doeff_cluster [env-host]
        doeff_cluster._worker.protocol.env_store [EnvSettings])
(import doeff_cluster [ClusterState WorkerInfo])
(import doeff_cluster._coordinator.core.resource_policy [worker-row])

;; 下限(本番の既定 25 GiB)と、下限の上の空き(byte)。
(val MIN-FREE (* 25 1024 1024 1024))
(val GB (** 10 9))
;; near の線(下限 + 20 GB = 46.84 GB — 10-17 の mac-native の線)。
(val LINE (+ MIN-FREE NEAR-MARGIN-BYTES))
;; 10-17 の mac-native の空き(0.01 GB の単位): 20:56 46.59 GB(near)・21:33 47.60 GB(snapshot の切れ — 直す前は ok)・22:01 46.78 GB
;; (新しい版の env — near)と、その後に線の上 2 GB を越えた戻り 48.90 GB・ok のままの揺れ 47.60 GB・線を割った 46.59 GB。
(val CENTI-GB (** 10 7))
(val MAC-NATIVE-FREES (tuple (gfor n #(4659 4760 4678 4890 4760 4659) (* n CENTI-GB))))
(val MAC-NATIVE-SAID #("near" "near" "near" "ok" "ok" "near"))
;; 検の file system の総量(空きの答えに添える — env-host は読まない)。
(val TOTAL-BYTES (* 500 GB))


(deftest test-env-capacity-names-near-within-twenty-gb-above-the-floor
  (<- below str (env-capacity (- MIN-FREE 1) MIN-FREE "ok"))
  (<- near str (env-capacity (+ MIN-FREE (* 10 GB)) MIN-FREE "ok"))
  (<- edge str (env-capacity (+ MIN-FREE (* 20 GB)) MIN-FREE "ok"))
  (<- ok str (env-capacity (+ MIN-FREE (* 30 GB)) MIN-FREE "ok"))
  (<- unset str (env-capacity 0 0 "ok"))
  (assert (= #(below near edge ok unset) #("exhausted" "near" "ok" "ok" "ok"))))


(deftest test-near-holds-until-the-free-space-climbs-the-return-margin-past-the-line
  ;; 10-17 の mac-native の空きを、前に名乗った語を渡しながら順に名乗る(20:56 の前は ok)。
  (var named "ok")
  (var said #())
  (for [free MAC-NATIVE-FREES]
    (<- capacity str (env-capacity free MIN-FREE named))
    (:= named capacity)
    (:= said (+ said #(capacity))))
  (assert (= said MAC-NATIVE-SAID) said))


(deftest test-the-return-margin-delays-only-the-way-back-to-ok
  ;; ok から near へ入る所は遅らせない(線を 1 byte 割れば near)。
  (<- entering str (env-capacity (- LINE 1) MIN-FREE "ok"))
  ;; 戻りの幅の端: 線 + 2 GB ちょうどは ok・1 byte 下は near のまま。
  (<- held str (env-capacity (+ LINE NEAR-RETURN-BYTES -1) MIN-FREE "near"))
  (<- back str (env-capacity (+ LINE NEAR-RETURN-BYTES) MIN-FREE "near"))
  ;; exhausted の出入りは準備の process の disk-full と同じ線(幅を持たない)— 出た先が線の近くなら near、幅の外なら ok。
  (<- falling str (env-capacity (- MIN-FREE 1) MIN-FREE "near"))
  (<- rising str (env-capacity MIN-FREE MIN-FREE "exhausted"))
  (<- above str (env-capacity (+ LINE 1) MIN-FREE "exhausted"))
  (<- recovered str (env-capacity (+ LINE NEAR-RETURN-BYTES) MIN-FREE "exhausted"))
  ;; まだ名乗っていない(起き直した)worker は、幅の内を near と読む — 起き直す前に near を名乗っていたかもしれない。
  (<- fresh-held str (env-capacity (+ LINE 1) MIN-FREE None))
  (<- fresh-ok str (env-capacity (+ LINE NEAR-RETURN-BYTES) MIN-FREE None))
  ;; 下限を置かない worker は、前の語に依らず ok。
  (<- unset str (env-capacity 0 0 "near"))
  (assert (= #(entering held back falling rising above recovered fresh-held fresh-ok unset)
             #("near" "near" "ok" "exhausted" "near" "near" "ok" "near" "ok" "ok"))))


(defeffect SetDiskFree
  "次の空きの読みの答えを置く — 検だけの操作(scripted-disk が答える)。"
  {:fields [(: free int)] :answer None :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler scripted-disk
  "外の世界の disk の代役: 空きの読み(ReadDiskUsage)に、筋書きが SetDiskFree で置いた空きを答える(他の file system の効果は外の本物へ)。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var current 0)
  (SetDiskFree [free]
    (:= current free)
    (resume None))
  (ReadDiskUsage [path]
    (resume (DiskUsage :total TOTAL-BYTES :free current))))


(defk said-for [frees]
  {:pre [(: frees tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "空きの並び frees を 1 つずつ置いて heartbeat の名乗り(EnvReport)を問い、名乗った disk の条件の並びを返すため(拍ごとの名乗りと同じ)。"
  (var said #())
  (for [free frees]
    (<- (SetDiskFree free))
    (<- report dict (EnvReport))
    (:= said (+ said #((get report "capacity")))))
  said)


(deftest test-env-host-carries-its-last-name-into-the-next-heartbeat [tmp-path]
  ;; 最初の名乗りの前は ok の空き(線の上 3 GB)で ok を名乗らせ、10-17 の並びを続ける。env-host が前の名乗りを持たなければ、47.60 GB(まだ名乗って
  ;; いないと読む — near)は合っても、ok へ戻った後の 47.60 GB を near と名乗る。
  (val settings (EnvSettings :state (str (/ tmp-path "state")) :uv-cache (str (/ tmp-path "state" "uv-cache")) :hy-command "hy" :platform "test"
                             :code-prepare PREPARE-TOOL :roots-cap-bytes (** 2 62) :min-free-bytes MIN-FREE))
  (<- said tuple (with-handlers [(state) (sync-time-handler) slog-handler os-file-handler subprocess-handler scripted-disk (env-host settings)]
                                (said-for (+ #((+ LINE (* 3 GB))) MAC-NATIVE-FREES))))
  (assert (= said (+ #("ok") MAC-NATIVE-SAID)) said))


(deftest test-the-worker-status-carries-env-capacity-only-when-not-ok
  (val near (WorkerInfo "w1" #("net") 0 #() None #() :platform "linux-x86_64" :env-capacity "near" :task-reserve 0 :background-room 2))
  (val ok (WorkerInfo "w2" #("net") 0 #() None #() :platform "linux-x86_64" :env-capacity "ok" :task-reserve 0 :background-room 2))
  (val state (ClusterState :workers (! (table-from-rows {"w1" near "w2" ok}))))
  (assert (= (.get (get (worker-row state near 0 (ClusterTiming)) "status") "envCapacity") "near"))
  (assert (not-in "envCapacity" (get (worker-row state ok 0 (ClusterTiming)) "status"))))
