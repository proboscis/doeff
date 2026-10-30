;; 置き先の無い Service の読み(種類・足りない能力・いつから — card ki-53b8677f1374)。
;;
;; 失敗ケース(直す前・2026-10-26 22:45〜23:21 JST): 13 の Service がどの worker にも置かれず、GET /state の unplaced は Service の名 →
;; 理由の文だけで、いつから置き先が無いかはどこにも無かった。外の見張りは文を読み解かなければ足りない能力を読めず、止まりの長さは人が
;; 気づくまでの長さになった。直した後は、GET /state の unplacedDetail と Service の資源の status.unplaced に、種類・効く needs・足りない
;; 能力・置き先が無いと最初に見た刻(sinceMs — 後の拍でも最初の刻を保つ)が出て、置かれた拍に消える。
(require doeff-hy.macros [deftest defk <- val])
(import doeff_cluster.shared.intent.protocol [ClusterTiming Request])
(import doeff_cluster [ClusterState])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster [responded])
(import doeff_cluster [tick])
(import tests.program_rows [SAMPLE-RUN])

(val T (ClusterTiming))
(val V {"python" "3.14.0"})
(val OWNER "declare-tool")


(defk req [method path [body None] [actor OWNER]]
  {:pre [(: method str) (: path str) (: body (| dict None)) (: actor (| str None))] :post [(: % Request)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "検の HTTP の要求の値 Request を作るため(送り元の番地 10.0.0.9・送り手 actor)。"
  (! (http-request method path {} body :actor actor :peer "10.0.0.9")))


(defk call [state method path [body None] [actor OWNER] [now 1000]]
  {:pre [(: state ClusterState) (: method str) (: path str) (: body (| dict None)) (: actor (| str None)) (: now int)] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "要求を 1 つ coordinator の判断(responded — 本番の受け口と同じ振り分け・settle・調停)に渡し、#(状態 status 本文) を返すため。"
  (responded state (! (req method path body actor)) now T))


(defk beat [state name provides now]
  {:pre [(: state ClusterState) (: name str) (: provides list) (: now int)] :post [(: % ClusterState)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker name が能力 provides を名乗る heartbeat を 1 つ受けた後の状態を返すため。"
  (<- answer tuple (call state "POST" "/heartbeat"
                         {"name" name "provides" provides "exclusive" [] "backgroundRoom" 10 "taskReserve" 0 "versions" V
                          "boot" "b1" "bootAt" 1 "statuses" []}
                         :actor None :now now))
  (assert (= (get answer 1) 200) answer)
  (get answer 0))


(defk stranded-world []
  {:pre [] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "宣言の道具が Service a(needs net・gpu)を刻 1000 に宣言し、worker zeus(net だけ)が名乗った状態を作るため(a を置ける worker が無い —
   足りない能力は gpu)。"
  (<- created tuple (call (ClusterState) "POST" "/resources/Service"
                          {"name" "a" "spec" {"revision" "r1" "needs" ["net" "gpu"] "run" SAMPLE-RUN "peakMiB" 200 "settleMiB" 100}} :now 1000))
  (assert (= (get created 1) 201) created)
  (<- world ClusterState (beat (get created 0) "zeus" ["net"] 1000))
  world)


(deftest test-a-service-no-worker-can-take-shows-its-kind-missing-capability-and-since
  ;; 受入の条 cafb722e5 の coordinator の側: 置き先の無い Service a の種類・足りない能力・いつからが GET /state と Service の資源に出る。
  (<- world ClusterState (stranded-world))
  (val later (+ 1000 (* 3 T.lease-ms)))
  (<- kept ClusterState (beat world "zeus" ["net"] later))
  (<- ticked ClusterState (tick kept later T))
  (<- seen tuple (call ticked "GET" "/state" :now later))
  (assert (in "a" (get seen 2 "unplaced")) seen)
  (assert (= (get seen 2 "unplacedDetail")
             {"a" {"kind" "no-eligible-worker" "needs" ["gpu" "net"] "missing" ["gpu"] "pin" None "sinceMs" 1000}})
          (get seen 2 "unplacedDetail"))
  (<- service tuple (call ticked "GET" "/resources/Service/a" :now later))
  (assert (= (get service 2 "status" "unplaced")
             {"kind" "no-eligible-worker" "needs" ["gpu" "net"] "missing" ["gpu"] "pin" None "sinceMs" 1000})
          service))


(deftest test-the-reading-goes-away-in-the-step-the-service-is-placed
  ;; 能力 gpu を名乗る worker が来た拍に a が置かれ、unplacedDetail と status.unplaced から消える(刻の行も外れる)。
  (<- world ClusterState (stranded-world))
  (<- placed ClusterState (beat world "zeus" ["net" "gpu"] 2000))
  (assert (is (.row placed.unplaced-since "a") None) placed.unplaced-since)
  (<- seen tuple (call placed "GET" "/state" :now 2000))
  (assert (= (get seen 2 "unplacedDetail") {}) seen)
  (<- service tuple (call placed "GET" "/resources/Service/a" :now 2000))
  (assert (is (get service 2 "status" "unplaced") None) service))


(deftest test-a-worker-that-drops-the-capability-starts-a-new-since
  ;; 置かれていた a の担い手が能力を失った拍から、新しい刻で数え直す(前の置き先の無い間の刻を持ち越さない)。
  (<- world ClusterState (stranded-world))
  (<- placed ClusterState (beat world "zeus" ["net" "gpu"] 2000))
  (<- lost ClusterState (beat placed "zeus" ["net"] 3000))
  (<- seen tuple (call lost "GET" "/state" :now 3000))
  (assert (= (get seen 2 "unplacedDetail" "a" "sinceMs") 3000) seen)
  (assert (= (get seen 2 "unplacedDetail" "a" "missing") ["gpu"]) seen))
