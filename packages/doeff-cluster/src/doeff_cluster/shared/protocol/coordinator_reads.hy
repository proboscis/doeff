;;; coordinator の口(GET /resources/Service/<名>・GET /state)を読む部品 — 契約の effect(ReadinessOf・AwaitReadiness・AwaitJobProcess —
;;; shared/intent/cluster_control.hy)に、coordinator の URL だけで答える境界の handler が共有する(#3294)。
;;;
;;;   使い手  手元の 1 台の cluster(sim/machine.hy の machine-answers — 自分で起こした worker の行に絞る job の待ちは向こうに残す)・
;;;           配備の cluster(shared/entry/deployed_cluster.hy の deployed-cluster-answers — どの worker の上かを問わない)。
;;;
;;; 本物の coordinator は長い待ちの読みを持たないので、待ちは境界の handler が WAIT-PROBE-SECONDS ごとに読み直し、期限を過ぎたら時間切れを
;;; 値で返す(detached-cluster の AwaitProcessEnded と同じ — #3053)。時間は境界の handler だけが使い、筋書きの Program は待つ effect を出す。
;;; 2026-10-04 まで readiness-read・readiness-awaited・state-of は sim/machine.hy に在った(本番の code は sim の dir を import しないので、
;;; 配備の handler と共有するために移した)。
(require doeff-hy.macros [defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(import json)
(import urllib.parse [quote :as url-quote])
(import doeff_core_effects.http_effects [HttpRequest HttpResponse])
(import doeff_time [Delay])
(import doeff_cluster.shared.intent.cluster_control [ServiceReadiness ReadinessWaitExpired JobProcessSeen JobProcessWaitExpired])

;; AwaitReadiness・AwaitJobProcess が coordinator を読み直す間隔(秒 — 筋書きの待ちは数十秒なので、読みの CPU を小さく保つ)。
(val WAIT-PROBE-SECONDS 1.0)


(defk readiness-read [url name]
  {:pre [(: url str) (: name str)] :post [(: % ServiceReadiness)] :tags {:context "doeff-cluster" :role "protocol"}}
  "coordinator の GET /resources/Service/<name> から準備の状態を読むため(ReadinessOf と AwaitReadiness の読み — 無ければ Missing)。"
  (<- answer (HttpRequest "GET" (+ url "/resources/Service/" (url-quote name :safe "")) :timeout-seconds 10.0 :max-retries 0
                          :failures-as-values True))
  (when (not (isinstance answer HttpResponse))
    (raise (RuntimeError (+ "coordinator に届かない: Service " name " — " (repr answer)))))
  (if (= answer.status 200)
      (do (val status (get (json.loads answer.text) "status"))
          (ServiceReadiness :state (get status "ready") :reason (str (.get status "readyReason" ""))))
      (ServiceReadiness :state "Missing" :reason answer.text)))


(defk readiness-awaited [url name state seconds]
  {:pre [(: url str) (: name str) (: state str) (: seconds float)] :post [(: % (| ServiceReadiness ReadinessWaitExpired))]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "AwaitReadiness に答えるため: 本物の coordinator は長い待ちの読みを持たないので、境界のこの handler が WAIT-PROBE-SECONDS ごとに
   準備の状態を読み、state になるか seconds を過ぎたら答える(過ぎたら最後に読んだ状態を添えた ReadinessWaitExpired)。"
  (<- first ServiceReadiness (readiness-read url name))
  (var seen first)
  (var waited 0.0)
  (while (and (!= seen.state state) (< waited seconds))
    (<- (Delay WAIT-PROBE-SECONDS))
    (:= waited (+ waited WAIT-PROBE-SECONDS))
    (<- again ServiceReadiness (readiness-read url name))
    (:= seen again))
  (if (= seen.state state)
      seen
      (ReadinessWaitExpired :name name :state state :last seen :waited-seconds waited)))


(defk state-of [url]
  {:pre [(: url str)] :post [(: % (| dict None))] :tags {:context "doeff-cluster" :role "protocol"}}
  "coordinator の GET /state を 1 度読むため(届かない・200 でなければ None — 起き上がりの途中)。HTTP の答えを読む境界なので、
   JSON の object を dict のまま返す(読む所は job の pid の引きと、手元の 1 台の起き上がりの待ち)。"
  (<- answer (HttpRequest "GET" (+ url "/state") :timeout-seconds 5.0 :max-retries 0 :failures-as-values True))
  (if (and (isinstance answer HttpResponse) (= answer.status 200))
      (json.loads answer.text)
      None))


(defk job-pids-of [state name]
  {:pre [(: state dict) (: name str)] :post [(: % (get tuple #(int ...)))] :tags {:context "doeff-cluster" :role "protocol"}}
  "coordinator の /state の statuses(worker の名 → 名乗った job の行)から、どの worker の上かを問わず job name の process の pid を引くため
   (pid の無い行 — 起こす前・終わった後 — は外す)。配備の cluster では worker を自分で起こさないので、名乗った全部の worker を読む。"
  (val statuses (.get state "statuses"))
  (if (not (isinstance statuses dict))
      #()
      (tuple (gfor status (.values statuses)
                   row (.get status "jobs" [])
                   :if (and (= (.get row "name") name) (isinstance (.get row "pid") int))
                   (get row "pid")))))


(defk job-process-awaited [url job excluding seconds]
  {:pre [(: url str) (: job str) (: excluding (get tuple #(int ...))) (: seconds float)]
   :post [(: % (| JobProcessSeen JobProcessWaitExpired))] :tags {:context "doeff-cluster" :role "protocol"}}
  "AwaitJobProcess に答えるため: coordinator の GET /state にどれかの worker が名乗った job の pid のうち、excluding の外の物(小さい順の
   最初)が出るまで WAIT-PROBE-SECONDS ごとに読む(seconds を過ぎたら JobProcessWaitExpired・届かない読みは名乗り無しと数える)。"
  (var found None)
  (var waited 0.0)
  (while (and (is found None) (<= waited seconds))
    (<- state (| dict None) (state-of url))
    (when (is-not state None)
      (<- pids (get tuple #(int ...)) (job-pids-of state job))
      (val fresh (sorted (gfor pid pids :if (not-in pid excluding) pid)))
      (when fresh
        (:= found (get fresh 0))))
    (when (is found None)
      (<- (Delay WAIT-PROBE-SECONDS))
      (:= waited (+ waited WAIT-PROBE-SECONDS))))
  (if (is found None)
      (JobProcessWaitExpired :job job :excluding excluding :waited-seconds waited)
      (JobProcessSeen :job job :pid found)))
