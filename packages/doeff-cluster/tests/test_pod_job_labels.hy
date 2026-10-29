;; worker が置かれた常駐の job を自分の Pod の label に付け外しする(card ki-a66bb71f4152)。常駐の job の置き先は coordinator の GET /state
;; にしか出ず、k8s の Service は機体の label(doeff.dev/node)で 1 台を名指していた — job が別の機体へ移ると口が届かない。
;;   (1) 判断: label の鍵は job.doeff.dev/<job の名>・値は placed。task と label の名に成らない名は載せない。前置きの label だけを揃え、
;;       ほかの label には触らない。揃えるのは宣言の job が変わった拍と 60 秒ごと、失敗の後は 10 秒から倍にして 5 分まで待つ
;;   (2) 答え手: 拍の宣言の読み ReadDesired の答えをそのまま返し、置かれた job の label を付け、外れた job と残りの label を外す。自分の
;;       Pod は node で絞った list の uid で見分け、patch にも uid を載せる。同じ node の別の Pod には触らない
;;   (3) 失敗しても job を止めない: API server が断る・届かない・token が読めない・list に自分が無い時も宣言はそのまま返し、訳を 1 度
;;       log に出して、待ってから試し直し、戻ったら 1 行
;;   (4) 退いた世代(返事の superseded)は label に触らず、後の途絶で絞った宣言の拍にも触らない。宣言が読めない拍も触らない
;; 失敗ケース = 退いた世代が新しい世代の付けた label を外す・API server が落ちている間に拍ごとに要求の上限まで待つ・失敗で宣言を
;; 落として job を止める・同じ node の別の Pod の label を書く。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import urllib.parse [urlsplit])
(import doeff [with_handlers])
(import doeff_core_effects [SlogEffect])
(import doeff_core_effects [MemoryFile MemoryFiles])
(import doeff_core_effects [state])
(import doeff_core_effects.http_effects [HttpFailed HttpFailureKind HttpRequest HttpResponse])
(import doeff_core_effects [memory-file-handler])
(import doeff_time [GetTimeEffect])
(import doeff_cluster.shared.core.clock [datetime-of-epoch-ms])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.shared.protocol.supplied_values [KUBE-API SERVICE-ACCOUNT-DIR SERVICE-ACCOUNT-TOKEN-FILE])
(import doeff_cluster.worker.intent.worker_model [CutOff DesiredJobs DesiredUnreadable ReadDesired])
(import doeff_cluster.worker.core.pod_label_rules [JobLabels LabelChange PodLabel job-label-key job-labels-wanted job-labels-patch
                                                   labels-sync-due retry-after-ms labels-line])
(import doeff_cluster.worker.protocol.pod_labels [PodLabelCell PodLabelSettings pod-job-labels])
(import doeff_cluster.worker.entry.main [pod-label-parts])

(val TOKEN "worker-identity-token")
(val NAMESPACE-FILE (+ SERVICE-ACCOUNT-DIR "/namespace"))
(val MY-UID "c1849b4d-0000-4c0a-8bc1-9dabdb16bda5")
(val MY-POD "doeff-worker-k3s-3-69b587bfb7-tcfvg")
(val SETTINGS (PodLabelSettings :api KUBE-API :token-file SERVICE-ACCOUNT-TOKEN-FILE :namespace-file NAMESPACE-FILE :node "k3s-3"
                                :pod-uid MY-UID))
(val FILES (MemoryFiles :files #((MemoryFile :path SERVICE-ACCOUNT-TOKEN-FILE :content (.encode (+ TOKEN "\n") "utf-8"))
                                 (MemoryFile :path NAMESPACE-FILE :content b"agent-worker"))))
(val SECRETARY (JobSpec "secretary" "pkg.secretary" #() "r1"))
(val MAIL (JobSpec "mail-derivation" "pkg.mail" #() "r1"))
(val TASK (JobSpec "task/t-1" "pkg.task" #() "r1" :once True))
(val ODD (JobSpec "odd/name" "pkg.odd" #() "r1"))


(defclass FakeApi []
  "API server の相手役が持つ物: pods = Pod の名 → {node uid labels}・status = list に答える status(None = 普通に答える)・unreachable =
   届かない(HttpFailed で答える)・requests = 届いた要求の #(method path params 本文の JSON) の列・auth = 届いた要求の authorization の列。"
  (defn #^ None __init__ [self]
    (setv self.pods {MY-POD {"node" "k3s-3" "uid" MY-UID
                             "labels" {"app" "doeff-worker" "doeff.dev/node" "k3s-3" "job.doeff.dev/stale" "placed"}}
                     "jev-proxy-548b4fc6fd-vggqq" {"node" "k3s-3" "uid" "755e8c60-other"
                                                   "labels" {"app" "jev-proxy" "job.doeff.dev/secretary" "placed"}}
                     "doeff-worker-atlas-c5c949896-8nnvz" {"node" "atlas" "uid" "a-uid" "labels" {"app" "doeff-worker"}}}
          self.status None self.unreachable False self.requests #() self.auth #())))


(defclass Box []
  "検の拍の外の世界: ms = 壁の時計の今(epoch ms)・answer = 外側の宣言の読みが答える物・lines = 出た log の行の (level 文) の列。"
  (defn #^ None __init__ [self]
    (setv self.ms 1793068000000 self.answer None self.lines #())))


(defk api-answer [api request]
  {:pre [(: api FakeApi) (: request HttpRequest)] :post [(: % (| HttpResponse HttpFailed))]}
  "API server の相手役として、Pod の list(fieldSelector の spec.nodeName で絞る)と、Pod 1 つへの merge patch(uid が違えば 422)に答えるため。"
  (val parts (urlsplit request.url))
  (val body (if (is request.body None) None (json.loads request.body)))
  (setv api.requests (+ api.requests #(#(request.method parts.path (dict (or request.params {})) body)))
        api.auth (+ api.auth #((.get request.headers "authorization"))))
  (val respond (fn [status payload] (HttpResponse :status status :headers {} :content b"" :text (json.dumps payload) :url request.url
                                                  :elapsed-seconds 0.0)))
  (cond
    api.unreachable (HttpFailed :url request.url :detail "connection refused" :kind HttpFailureKind.CONNECT-FAILED)
    (is-not api.status None) (respond api.status {"kind" "Status" "message" "internal error"})
    (= request.method "GET")
      (do (val node (cut (get request.params "fieldSelector") (len "spec.nodeName=") None))
          (respond 200 {"kind" "PodList"
                        "items" (lfor #(name pod) (.items api.pods) :if (= (get pod "node") node)
                                  {"metadata" {"name" name "uid" (get pod "uid") "labels" (dict (get pod "labels"))}})}))
    True
      (do (val name (get (.rsplit parts.path "/" 1) 1))
          (val pod (get api.pods name))
          (if (!= (get body "metadata" "uid") (get pod "uid"))
              (respond 422 {"kind" "Status" "message" "metadata.uid: field is immutable"})
              (do (setv (get pod "labels") (dfor #(key value) (.items (| (get pod "labels") (get body "metadata" "labels")))
                                             :if (is-not value None) key value))
                  (respond 200 {"metadata" {"name" name "uid" (get pod "uid") "labels" (dict (get pod "labels"))}}))))))


(defhandler fake-api [#^ FakeApi api]
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 検ごとに別の相手役の状態で並べる。
  (HttpRequest []
    (<- answer (| HttpResponse HttpFailed) (api-answer api effect))
    (resume answer)))


(defhandler world-of [#^ Box box]
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 検ごとに別の時計と宣言で並べる。外側の答え手の代役: 宣言の読み(coordinator への口)・壁の時計・log。
  (ReadDesired [env-report stopping] (resume box.answer))
  (GetTimeEffect [] (resume (datetime-of-epoch-ms box.ms)))
  (SlogEffect []
    (setv box.lines (+ box.lines #(#((.get effect.kwargs "level" "info") effect.msg))))
    (resume None)))


(defk tick [api box cell answer at-ms]
  {:pre [(: api FakeApi) (: box Box) (: cell PodLabelCell) (: answer (| DesiredJobs DesiredUnreadable)) (: at-ms int)]
   :post [(: % (| DesiredJobs DesiredUnreadable))]}
  "壁の時計 at-ms の拍で、外側が answer で答える宣言の読み 1 つを答え手 pod-job-labels の下で出し、その答えを得るため。"
  (setv box.ms at-ms box.answer answer)
  (<- read (with_handlers [(state) (memory-file-handler FILES) (fake-api api) (world-of box) (pod-job-labels SETTINGS cell)]
                          (ReadDesired)))
  read)


(defk pod-labels-of [api name]
  {:pre [(: api FakeApi) (: name str)] :post [(: % dict)]}
  "相手役の Pod name の今の label のうち、job の label だけを読むため。"
  (dfor #(key value) (.items (get api.pods name "labels")) :if (.startswith key "job.doeff.dev/") key value))


(deftest test-the-label-key-is-the-job-name-under-the-worker-prefix
  (var keys #())
  (for [name ["secretary" "mail-derivation" "a.b_c-1" "x" "odd/name" "-lead" "trail-" "" (* "n" 63) (* "n" 64)]]
    (<- key (| str None) (job-label-key name))
    (:= keys (+ keys #(key))))
  (assert (= keys #("job.doeff.dev/secretary" "job.doeff.dev/mail-derivation" "job.doeff.dev/a.b_c-1" "job.doeff.dev/x" None None None None
                    (+ "job.doeff.dev/" (* "n" 63)) None))
          keys)
  ;; task は載せず、label の名に成らない job は名を挙げる。
  (<- labels JobLabels (job-labels-wanted #(SECRETARY TASK ODD MAIL)))
  (assert (= labels (JobLabels :wanted (frozenset #("job.doeff.dev/secretary" "job.doeff.dev/mail-derivation"))
                               :unlabelled #("odd/name")))
          labels))


(deftest test-the-patch-touches-only-the-worker-prefix
  (val current #((PodLabel :key "app" :value "doeff-worker") (PodLabel :key "doeff.dev/node" :value "k3s-3")
                 (PodLabel :key "job.doeff.dev/old" :value "placed") (PodLabel :key "job.doeff.dev/keep" :value "placed")
                 (PodLabel :key "job.doeff.dev/odd" :value "hand-written")))
  (val wanted (frozenset #("job.doeff.dev/keep" "job.doeff.dev/odd" "job.doeff.dev/new")))
  (<- patch tuple (job-labels-patch current wanted))
  (assert (= patch #((LabelChange :key "job.doeff.dev/new" :value "placed") (LabelChange :key "job.doeff.dev/odd" :value "placed")
                     (LabelChange :key "job.doeff.dev/old" :value None)))
          patch)
  (val settled #((PodLabel :key "app" :value "doeff-worker") (PodLabel :key "job.doeff.dev/keep" :value "placed")
                 (PodLabel :key "job.doeff.dev/odd" :value "placed") (PodLabel :key "job.doeff.dev/new" :value "placed")))
  (<- same tuple (job-labels-patch settled wanted))
  (assert (= same #()) same)
  (<- line str (labels-line patch))
  (assert (= line "worker: Pod の job の label を揃えた — 付けた new・odd・外した old") line))


(deftest test-labels-are-synced-when-the-jobs-change-and-every-minute-and-wait-after-a-failure
  (val w (frozenset #("job.doeff.dev/secretary")))
  (val v (frozenset #("job.doeff.dev/mail-derivation")))
  (var dues #())
  (for [#(wanted synced synced-ms retry-at now) [[w None None None 0]       ; まだ揃えていない
                                                 [w w 0 None 59999]         ; 同じ宣言の 1 分の内
                                                 [w w 0 None 60000]         ; 1 分ごとに読み直す
                                                 [v w 0 None 1]             ; 宣言の job が変わった
                                                 [w w 10000 None 0]         ; 壁の時計が戻った
                                                 [v w 0 5000 4999]          ; 失敗の後は変わっても待つ
                                                 [v w 0 5000 5000]]]
    (<- due bool (labels-sync-due wanted synced synced-ms retry-at now))
    (:= dues (+ dues #(due))))
  (assert (= dues #(True False True True True False True)) dues)
  (var waits #())
  (for [n [1 2 3 5 6 40]]
    (<- wait int (retry-after-ms n))
    (:= waits (+ waits #(wait))))
  (assert (= waits #(10000 20000 40000 160000 300000 300000)) waits))


(deftest test-the-handler-puts-placed-jobs-on-its-own-pod-and-returns-the-declaration
  (val api (FakeApi))
  (val box (Box))
  (val cell (PodLabelCell))
  (val t0 box.ms)
  (val first (DesiredJobs #(SECRETARY TASK)))
  (<- read (tick api box cell first t0))
  ;; 宣言はそのまま返る。自分の Pod(uid で見分ける)だけに付け、残りの label を外す。同じ node の別の Pod と別の node の Pod には触らない。
  (assert (is read first) read)
  (<- mine dict (pod-labels-of api MY-POD))
  (assert (= mine {"job.doeff.dev/secretary" "placed"}) mine)
  (<- other dict (pod-labels-of api "jev-proxy-548b4fc6fd-vggqq"))
  (assert (= other {"job.doeff.dev/secretary" "placed"}) other)
  (assert (= (get api.pods MY-POD "labels" "app") "doeff-worker"))
  (assert (= api.requests
             #(#("GET" "/api/v1/namespaces/agent-worker/pods" {"fieldSelector" "spec.nodeName=k3s-3"} None)
              #("PATCH" (+ "/api/v1/namespaces/agent-worker/pods/" MY-POD) {}
                {"metadata" {"uid" MY-UID "labels" {"job.doeff.dev/secretary" "placed" "job.doeff.dev/stale" None}}})))
          api.requests)
  (assert (= (set api.auth) #{(+ "Bearer " TOKEN)}) api.auth)
  (assert (in #("info" "worker: Pod の job の label を揃えた — 付けた secretary・外した stale") box.lines) box.lines)
  ;; 同じ宣言の 1 分の内の拍は API server に問わない。
  (setv api.requests #())
  (<- (tick api box cell first (+ t0 1000)))
  (assert (= api.requests #()) api.requests)
  ;; job が足された拍に付け、外れた拍に外す。
  (<- (tick api box cell (DesiredJobs #(SECRETARY MAIL)) (+ t0 2000)))
  (<- both dict (pod-labels-of api MY-POD))
  (assert (= both {"job.doeff.dev/secretary" "placed" "job.doeff.dev/mail-derivation" "placed"}) both)
  (<- (tick api box cell (DesiredJobs #(MAIL)) (+ t0 3000)))
  (<- moved dict (pod-labels-of api MY-POD))
  (assert (= moved {"job.doeff.dev/mail-derivation" "placed"}) moved)
  ;; 手で外された label は、1 分ごとの読み直しで戻る(揃っていれば list だけで patch しない)。
  (setv (get api.pods MY-POD "labels") (dfor #(key value) (.items (get api.pods MY-POD "labels"))
                                          :if (!= key "job.doeff.dev/mail-derivation") key value))
  (setv api.requests #())
  (<- (tick api box cell (DesiredJobs #(MAIL)) (+ t0 3000 60000)))
  (<- back dict (pod-labels-of api MY-POD))
  (assert (= back {"job.doeff.dev/mail-derivation" "placed"}) back)
  (setv api.requests #())
  (<- (tick api box cell (DesiredJobs #(MAIL)) (+ t0 3000 120000)))
  (assert (= (lfor r api.requests (get r 0)) ["GET"]) api.requests))


(deftest test-a-failure-keeps-the-declaration-and-waits-before-trying-again
  (val api (FakeApi))
  (val box (Box))
  (val cell (PodLabelCell))
  (val t0 box.ms)
  (val desired (DesiredJobs #(SECRETARY)))
  (setv api.status 500)
  (<- read (tick api box cell desired t0))
  (assert (is read desired) read)
  (assert (in "500" cell.failure) cell.failure)
  (val warned (lfor #(level line) box.lines :if (= level "warning") line))
  (assert (= (len warned) 1) box.lines)
  ;; 待つ間(10 秒)は拍ごとに問わない。
  (setv api.requests #())
  (<- (tick api box cell desired (+ t0 9999)))
  (assert (= api.requests #()) api.requests)
  ;; 届かない時も同じ訳の扱い(訳が変わったので 1 行)。次の待ちは倍の 20 秒。
  (setv api.status None api.unreachable True)
  (<- (tick api box cell desired (+ t0 10000)))
  (assert (in "API server に届かない" cell.failure) cell.failure)
  (assert (= cell.retry-at-ms (+ t0 10000 20000)) cell.retry-at-ms)
  (setv api.requests #())
  (<- (tick api box cell desired (+ t0 29999)))
  (assert (= api.requests #()) api.requests)
  ;; 戻れば揃え、戻った事を 1 行出す。
  (setv api.unreachable False)
  (<- (tick api box cell desired (+ t0 30000)))
  (<- mine dict (pod-labels-of api MY-POD))
  (assert (= mine {"job.doeff.dev/secretary" "placed"}) mine)
  (assert (= #(cell.failure cell.failures cell.retry-at-ms) #("" 0 None)))
  (val warnings (lfor #(level line) box.lines :if (= level "warning") line))
  (assert (= (len warnings) 2) box.lines)
  (assert (in #("info" "worker: Pod の job の label を揃えられるようになりました") box.lines) box.lines))


(deftest test-a-pod-missing-from-the-list-or-an-unreadable-token-is-a-reason-not-a-stop
  ;; list に自分の uid が無い(別の Pod の名乗り)時は patch せずに訳にする。
  (val api (FakeApi))
  (val box (Box))
  (val cell (PodLabelCell))
  (setv (get api.pods MY-POD "uid") "someone-else")
  (val desired (DesiredJobs #(SECRETARY)))
  (<- read (tick api box cell desired box.ms))
  (assert (is read desired) read)
  (assert (= (lfor r api.requests (get r 0)) ["GET"]) api.requests)
  (assert (in "list に無い" cell.failure) cell.failure)
  ;; token の file が読めない worker は API server に問わない。
  (val lone (FakeApi))
  (val lone-cell (PodLabelCell))
  (setv box.answer desired box.ms (+ box.ms 1))
  (<- answer (with_handlers [(state) (memory-file-handler (MemoryFiles)) (fake-api lone) (world-of box) (pod-job-labels SETTINGS lone-cell)]
                            (ReadDesired)))
  (assert (is answer desired) answer)
  (assert (= lone.requests #()) lone.requests)
  (assert (in "token の file が読めない" lone-cell.failure) lone-cell.failure))


(deftest test-a-superseded-generation-and-an-unreadable-declaration-leave-the-labels
  (val api (FakeApi))
  (val box (Box))
  (val cell (PodLabelCell))
  (val t0 box.ms)
  ;; 退いた世代: 新しい世代が付けた label を、自分の返事の job が減っても外さない。後の途絶で絞った宣言(superseded を持たない)でも。
  (<- read (tick api box cell (DesiredJobs #() :superseded True) t0))
  (assert (= read (DesiredJobs #())) read)
  (<- (tick api box cell (DesiredJobs #() :cut-off (CutOff :silent-ms 30000)) (+ t0 120000)))
  (assert (= api.requests #()) api.requests)
  (assert cell.superseded)
  (<- kept dict (pod-labels-of api MY-POD))
  (assert (= kept {"job.doeff.dev/stale" "placed"}) kept)
  ;; 宣言が読めない拍は触らない(読めた宣言の無い世代が全部を外さない)。
  (val fresh (PodLabelCell))
  (val unreadable (DesiredUnreadable "返事の形が違う"))
  (<- answer (tick api box fresh unreadable (+ t0 120001)))
  (assert (is answer unreadable) answer)
  (assert (= api.requests #()) api.requests))


(deftest test-only-a-worker-with-a-pod-uid-and-a-node-labels-its-pod
  (<- outside (| tuple None) (pod-label-parts None "zeus"))
  (<- no-node (| tuple None) (pod-label-parts MY-UID ""))
  (<- inside (| tuple None) (pod-label-parts MY-UID "k3s-3"))
  (assert (= #(outside no-node) #(None None)) #(outside no-node))
  (assert (= (get inside 0) SETTINGS) inside)
  (assert (isinstance (get inside 1) PodLabelCell) inside))
