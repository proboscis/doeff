;; coordinator を worker より先に上げた時、古い worker が heartbeat の返事の job の行の入口の名をそのまま指紋に入れても、coordinator の
;; 宣言の指紋と同じ値になる(2026-11-02 08:44〜09:16 JST の本番)。
;;   doeff 3bfc5f6d7 は coordinator が送る入口の名を doeff_cluster._worker.entry.job_entry へ替え、指紋の材料だけを区画を隠す前の綴り
;;   (JobSpec.entry-at LEGACY-ENTRY-PLACE)へ戻した。古い worker(762359834b7a)は入口の名を置き場を替えずに材料へ入れるので、
;;   (1) coordinator を作り直して名乗り直した拍に、全部の job を「spec が変わった」で止めて起こし直し(service の口が数秒空き、
;;   その間に届いた要求が落ちた)、(2) 起こし直した process の指紋が宣言の指紋と合わず、worker を上げるまで 32 分 NotReady / Updating
;;   (「前の宣言で起こした物」)のままだった。Service の戻りを Ready で待つ client(service-back-within)は、その間ずっと待って落ちた。
;; 直した後は、送る名を指紋の材料と同じ綴りにする: 古い worker の指紋も新しい worker の指紋も宣言の指紋と一致し、coordinator の版上げで
;; process を起こし直さない(新しい worker は root の完成の印の置き場の名へ替えて起こす — JobSpec.entry-at)。
(require doeff-hy.macros [deftest defk val <-])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import hashlib)
(import json)
(import doeff_cluster [ClusterState ClusterTiming])
(import doeff_cluster [http-request responded])
(import doeff_cluster [JobSpec] doeff_cluster [spec-hash])
(import doeff_cluster._shared.intent.job_model [env-without-commits])
(import doeff_cluster._coordinator.core.resource_policy [running-process version-state])
(import doeff_cluster._coordinator.core.api_policy :as api-policy)
(import doeff_cluster._coordinator.protocol.durable_kv [full-kv state-from-kv])
(import doeff_cluster._coordinator.intent.cluster_model [VersionState])
(import doeff_cluster._worker.protocol.declared [declared-reply-of-json declared-job-specs])
(import tests.room_rows [ROOM-JSONS])
(import tests.program_rows [SAMPLE-RUN])

(val T (ClusterTiming))
(val START 1000000)
(val SPEC {"revision" "r1" "needs" ["net"] "run" SAMPLE-RUN "peakMiB" 200 "settleMiB" 100})
;; 中身の鍵の在る宣言(本番の service はどれも鍵を持つ — 指紋は版の代わりに鍵を材料にする)。
(val CONTENT-SPEC (| SPEC {"content" (+ "sha256:" (* "c" 64))}))


(defk pre-place-hash [spec]
  {:pre [(: spec JobSpec)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "置き場を読まない古い worker(doeff 762359834b7a の JobSpec.fingerprint)が計る指紋: 入口の名を綴りのまま材料に入れる。"
  (val materials (if (is spec.content None)
                     (+ [spec.name spec.entry (list spec.args) spec.revision spec.once]
                        (if spec.runtime-env [spec.runtime-env] []))
                     (+ ["content" spec.name spec.entry (list spec.args) spec.once spec.content]
                        (if spec.runtime-env [(env-without-commits spec.runtime-env)] []))))
  (cut (.hexdigest (hashlib.sha256 (.encode (json.dumps (+ materials (if spec.environ [(lfor #(k v) spec.environ [k v])] []))
                                                        :ensure-ascii False :separators #("," ":"))
                                            "utf-8")))
       0 16))


(defk answered [state method path [body None]]
  {:pre [(: state ClusterState) (: method str) (: path str) (: body (| (get dict #(str object)) None))]
   :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "coordinator の本物の返事(api_policy.respond)へ要求を 1 件送り、#(次の状態 本文) を返す。"
  (val got (responded state (! (http-request method path {} body :actor (if (= path "/heartbeat") None "c-me"))) START T))
  (assert (< (get got 1) 300) #(method path (get got 1) (get got 2)))
  #((get got 0) (get got 2)))


(defk heartbeat [state rows]
  {:pre [(: state ClusterState) (: rows list)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "worker atlas が担い手の行 rows で名乗る(#(次の状態 返事の本文))。"
  (! (answered state "POST" "/heartbeat" {"name" "atlas" "provides" ["net"] "measured" (get ROOM-JSONS 10) "versions" {}
                                          "statuses" rows})))


(defk older-worker-row [state spec]
  {:pre [(: state ClusterState) (: spec JobSpec)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "古い worker が、返事から読んだ spec で起こした process の行(指紋は置き場を読まない計り方)。"
  (val a (.row state.placements spec.name))
  {"name" spec.name "phase" "running" "runningRevision" spec.revision "desiredRevision" spec.revision "pid" 100 "attempts" 1
   "detail" "" "instance" "1-a" "specHash" (! (pre-place-hash spec)) "placement" (if a a.generation None)})


(defk wire-spec [body]
  {:pre [(: body dict)] :post [(: % JobSpec)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "heartbeat の返事の job の行 1 本を、worker の本物の読み(declared-reply-of-json・declared-job-specs)で JobSpec にする。"
  (<- reply (declared-reply-of-json body))
  (<- specs tuple (declared-job-specs reply))
  (assert (= (len specs) 1) specs)
  (get specs 0))


(defk declared-state [spec]
  {:pre [(: spec dict)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "Service w を宣言した coordinator の状態。"
  (get (! (answered (ClusterState) "POST" "/resources/Service" {"name" "w" "spec" spec})) 0))


(deftest test-an-older-worker-fingerprints-the-wire-row-as-the-declaration
  ;; 失敗ケース(本番 08:44〜09:16): 返事の job の行を綴りのまま計る古い worker の指紋が、宣言の指紋と同じ。鍵の無い宣言・鍵の在る宣言の両方。
  (for [spec [SPEC CONTENT-SPEC]]
    (val placed (get (! (heartbeat (! (declared-state spec)) [])) 0))
    (val body (get (! (heartbeat placed [])) 1))
    (val read (! (wire-spec body)))
    (val job (next (gfor j placed.jobs :if (= j.spec.name "w") j)))
    (assert (= (! (pre-place-hash read)) (spec-hash job.spec)) #(spec read.entry (! (pre-place-hash read)) (spec-hash job.spec)))
    ;; 新しい worker の読み(置き場を替えた綴りで計る)も同じ指紋。
    (assert (= (spec-hash read) (spec-hash job.spec)) #(spec read.entry))))


(deftest test-an-older-worker-is-ready-again-after-the-coordinator-is-recreated
  ;; 同じ形の再現: 古い worker が w を動かしている間に coordinator を作り直し(保存から読み直す・止まっていた間を詰める)、worker が
  ;; 名乗り直す。返事の行を読んだ古い worker の process は、起こし直さずに(指紋が前の拍と同じ)今の宣言の物と数えられ、Ready・Current。
  (val placed (get (! (heartbeat (! (declared-state CONTENT-SPEC)) [])) 0))
  (val first (! (wire-spec (get (! (heartbeat placed [])) 1))))
  (val running (get (! (heartbeat placed [(! (older-worker-row placed first))])) 0))
  (assert (. (running-process running "w" START T) get) "作り直す前に Ready でない")
  (<- stored ClusterState (state-from-kv (! (full-kv running)) START))
  (val restarted (get (api-policy.resume-after-downtime stored START) 0))
  (val renamed (! (heartbeat restarted [(! (older-worker-row restarted first))])))
  (val again (get renamed 0))
  (val second (! (wire-spec (get renamed 1))))
  ;; 古い worker の「宣言が変わったか」は指紋の比べ — 変わっていなければ process を止めない(Service の口が空かない)。
  (assert (= (! (pre-place-hash second)) (! (pre-place-hash first))) #(first.entry second.entry))
  (val proc (running-process again "w" START T))
  (assert (get proc "ok") proc)
  (assert (= (. (version-state again "w" START T) state) VersionState.CURRENT) (version-state again "w" START T)))
