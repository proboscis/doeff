;; worker の ServiceAccount の token の古さを heartbeat で名乗る(card ki-d31166f84ad0)。
;; 2026-10-24 の zeus では kubelet が投影の token の置き直しを止め(20:37:48Z に置いた token が最後)、exp 21:37:48Z の 81 秒後から Pod の中で
;; API server を呼ぶ物が全部 401 になった。止まった事は 401 が出るまで誰にも見えなかった。
;;   (1) token-state は発行(iat)から 55 分を越えた token を stale、exp を過ぎた token を expired と読む(10-24 の刻で確かめる)
;;   (2) coordinator への口は token の file を読んで heartbeat の accountToken に名乗る。file を読まない設定・file の無い worker は名乗らない
;;   (3) coordinator の Worker の資源の status は accountToken が ok でない間だけ載せる(読み手が GET /resources/Worker で 401 の前に知る)
;; 失敗ケース = 古さを読まない(exp まで ok)・heartbeat に載せない・status に載せない(見回りが知らせにできない)。
(require doeff-hy.macros [deff defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import base64)
(import json)
(import time)
(import httpx)
(import tests.link_rig [LinkRig])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.worker.core.account_token [TokenClaims TokenState STALE-AFTER-MS token-claims token-state token-read-due])
(import doeff_cluster.worker.protocol.coordinator_link [token-line])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState])
(import doeff_cluster.coordinator.protocol.request_bodies [responded])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.core.resource_policy [worker-row])

;; 10-24 の zeus の最後の token(20:37:48Z に発行・期限 1 時間)と、401 が始まった刻(exp の 81 秒後)。
(val LAST-IAT 1792874268)
(val LAST-EXP 1792877868)
(val FIRST-401-MS (* (+ LAST-EXP 81) 1000))
(val T (ClusterTiming))


(defk jwt-of [claims]
  {:pre [(: claims dict)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "claims を載せた JWT の形の字(署名は読まないので中身は何でもよい)を作るため — 検の材料。"
  (.join "." ["eyJhbGciOiJSUzI1NiJ9"
              (.rstrip (.decode (base64.urlsafe-b64encode (.encode (json.dumps claims) "utf-8")) "ascii") "=")
              "c2ln"]))


(deftest test-the-token-of-10-24-is-stale-before-it-expires
  (<- text str (jwt-of {"iat" LAST-IAT "exp" LAST-EXP "sub" "system:serviceaccount:w"}))
  (<- claims (| TokenClaims None) (token-claims text))
  (assert (= claims (TokenClaims :issued-at LAST-IAT :expires-at LAST-EXP)) claims)
  ;; kubelet が置き直す頃(発行から 48 分)は ok・55 分を越えたら stale(期限の 5 分前に分かる)・401 の始まりは expired。
  (<- at-48 TokenState (token-state claims (* (+ LAST-IAT (* 48 60)) 1000)))
  (<- past-55 TokenState (token-state claims (+ (* LAST-IAT 1000) STALE-AFTER-MS 1)))
  (<- at-401 TokenState (token-state claims FIRST-401-MS))
  (assert (= #(at-48 past-55 at-401) #(TokenState.OK TokenState.STALE TokenState.EXPIRED))))


(deftest test-an-extended-token-is-still-stale-when-the-kubelet-stops-refreshing-it
  ;; 3607 秒の要求の token は exp が 1 年先 — 401 にはならないが、置き直しの止まりは古さで分かる。
  (<- text str (jwt-of {"iat" LAST-IAT "exp" (+ LAST-IAT (* 365 24 3600))}))
  (<- claims (| TokenClaims None) (token-claims text))
  (<- named TokenState (token-state claims FIRST-401-MS))
  (assert (= named TokenState.STALE) named))


(deftest test-a-file-that-is-not-a-jwt-is-unreadable
  (<- plain (| TokenClaims None) (token-claims "not-a-token"))
  (<- no-exp-text str (jwt-of {"iat" LAST-IAT}))
  (<- bool-exp-text str (jwt-of {"iat" LAST-IAT "exp" True}))
  (<- no-exp (| TokenClaims None) (token-claims no-exp-text))
  (<- bool-exp (| TokenClaims None) (token-claims bool-exp-text))
  (assert (= #(plain no-exp bool-exp) #(None None None)))
  (<- named TokenState (token-state None FIRST-401-MS))
  (assert (= named TokenState.UNREADABLE) named))


(deftest test-the-token-file-is-read-again-only-after-a-minute
  (<- first bool (token-read-due None 0))
  (<- soon bool (token-read-due 0 59999))
  (<- later bool (token-read-due 0 60000))
  (<- back bool (token-read-due 60000 0))
  (assert (= #(first soon later back) #(True False True True))))


(deftest test-the-log-line-names-the-move-and-the-minutes-left
  (<- line str (token-line TokenState.OK TokenState.STALE (TokenClaims :issued-at LAST-IAT :expires-at LAST-EXP)
                           (* (+ LAST-IAT (* 56 60)) 1000) ""))
  (assert (= line "worker: ServiceAccount の token ok → stale(発行から 56 分・期限まで 4 分)") line))


(deftest test-the-heartbeat-names-the-token-state-read-from-the-file [tmp-path]
  (setv sent #())
  (deff handle [#^ httpx.Request request]  ; defk にできない: httpx.MockTransport が呼ぶ callback
    {:pre [(: request httpx.Request)] :post [(: % httpx.Response)] :tags {:context "doeff-cluster-test" :role "foundation"}}
    "口が送った heartbeat の本文を控え、宣言の空の返事で答えるため。"
    (nonlocal sent)
    (setv sent (+ sent #((json.loads request.content))))
    (httpx.Response 200 :json {"jobs" [] "tasks" [] "warm" [] "draining" False}))
  (val token (/ tmp-path "token"))
  (val now (int (time.time)))
  (<- text str (jwt-of {"iat" (- now 3600) "exp" (+ now 3600)}))
  (.write-text token text)
  (val link (LinkRig "http://coord" "w" #() 1 0 60000 :task-dir (str (/ tmp-path "tasks")) :transport (httpx.MockTransport handle)))
  (setv link.state.token-file (str token))
  (.poll link)
  (assert (= (.get (get sent 0) "accountToken") "stale") sent)
  ;; 読まない設定(Pod の外の worker の検の口)と、file の無い worker は名乗らない。
  (val plain (LinkRig "http://coord" "w" #() 1 0 60000 :task-dir (str (/ tmp-path "plain")) :transport (httpx.MockTransport handle)))
  (.poll plain)
  (val missing (LinkRig "http://coord" "w" #() 1 0 60000 :task-dir (str (/ tmp-path "missing")) :transport (httpx.MockTransport handle)))
  (setv missing.state.token-file (str (/ tmp-path "no-such-token")))
  (.poll missing)
  (assert (= (lfor body (cut sent 1 None) (in "accountToken" body)) [False False]) sent))


(deftest test-the-worker-status-carries-the-token-state-only-when-not-ok
  ;; coordinator は heartbeat の accountToken を worker の行に写し、Worker の資源の status に ok でない間だけ載せる。名乗らない worker は ok。
  (<- named (http-request "POST" "/heartbeat" {} {"name" "w1" "provides" ["net"] "capacity" 2 "taskReserve" 0 "statuses" []
                                                  "accountToken" "stale"} :actor "c-test"))
  (<- unnamed (http-request "POST" "/heartbeat" {} {"name" "w2" "provides" ["net"] "capacity" 2 "taskReserve" 0 "statuses" []}
                            :actor "c-test"))
  (val after-stale (get (responded (ClusterState) named 1000 T) 0))
  (val after-both (get (responded after-stale unnamed 1000 T) 0))
  (val stale (.row after-both.workers "w1"))
  (val silent (.row after-both.workers "w2"))
  (assert (= #(stale.account-token silent.account-token) #("stale" "ok")))
  (assert (= (.get (get (worker-row after-both stale 1000 T) "status") "accountToken") "stale"))
  (assert (not-in "accountToken" (get (worker-row after-both silent 1000 T) "status"))))
