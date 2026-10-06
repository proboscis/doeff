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
(import doeff_cluster.shared.intent.cluster_control [ServiceReadiness ServiceFailed ReadinessWaitExpired FAILED-PHASES
                                                     JobProcessSeen JobProcessWaitExpired])

;; AwaitReadiness・AwaitJobProcess が coordinator を読み直す間隔(秒 — 筋書きの待ちは数十秒なので、読みの CPU を小さく保つ)。
(val WAIT-PROBE-SECONDS 1.0)


(defk readiness-of-body [code body]
  {:pre [(: code (| int None)) (: body (| dict str))] :post [(: % ServiceReadiness)] :tags {:context "doeff-cluster" :role "protocol"}}
  "GET /resources/Service/<名> の答え(code・200 なら JSON の object・それ以外は本文)→ 準備の状態(無ければ Missing)。本番の読みと
   sim の読み(sim/local.hy)が同じこの 1 つを通る — 読みの写しを 2 か所に持たない。"
  (if (and (= code 200) (isinstance body dict))
      (do (val status (get body "status"))
          (ServiceReadiness :state (get status "ready") :reason (str (.get status "readyReason" ""))))
      (ServiceReadiness :state "Missing" :reason (str body))))


(defk failure-of-body [name state code body last waited]
  {:pre [(: name str) (: state str) (: code (| int None)) (: body (| dict str)) (: last ServiceReadiness) (: waited float)]
   :post [(: % (| ServiceFailed None))] :tags {:context "doeff-cluster" :role "protocol"}}
  "同じ答えから、担い手が落ちたと数えるか(ServiceFailed)を読むため: coordinator の版の判定(status.version.state)が Blocked で、担い手の行
   (status.process)の phase が FAILED-PHASES の時だけ。前の版の行・置き先の無さは coordinator が Blocked に数えないので、ここでは版を
   比べない(判断の写しを作らない)。"
  (val status (if (and (= code 200) (isinstance body dict)) (.get body "status") None))
  (val version (if (isinstance status dict) (.get status "version") None))
  (val process (if (isinstance status dict) (.get status "process") None))
  (val spelled (if (isinstance process dict) (.get process "phase") None))
  ;; 行の phase の綴り → FAILED-PHASES の JobPhase(落ちたと数えない phase・知らない綴りは None)。
  (val phase (next (gfor p FAILED-PHASES :if (= p.value spelled) p) None))
  (if (and (isinstance version dict) (= (.get version "state") "Blocked") (is-not phase None))
      (ServiceFailed :name name :state state :phase phase :failure-kind (.get process "failureKind")
                     :reason (str (.get version "reason" "")) :last last :waited-seconds waited)
      None))


(defk readiness-wait-answer [name state code body waited seconds]
  {:pre [(: name str) (: state str) (: code (| int None)) (: body (| dict str)) (: waited float) (: seconds float)]
   :post [(: % (| ServiceReadiness ServiceFailed ReadinessWaitExpired None))] :tags {:context "doeff-cluster" :role "protocol"}}
  "AwaitReadiness の 1 回の読みの答え → 待ちの答え(None = まだ待つ)。state に届いたらその準備の状態・届く前に落ちたと分かれば
   ServiceFailed・waited が seconds に届いたら ReadinessWaitExpired。本番の待ち(readiness-awaited)と sim の待ちが同じこの判断を通る。"
  (<- seen ServiceReadiness (readiness-of-body code body))
  (<- failed (| ServiceFailed None) (failure-of-body name state code body seen waited))
  (cond
    (= seen.state state) seen
    (is-not failed None) failed
    (>= waited seconds) (ReadinessWaitExpired :name name :state state :last seen :waited-seconds waited)
    True None))


(defk service-answer [url name]
  {:pre [(: url str) (: name str)] :post [(: % HttpResponse)] :tags {:context "doeff-cluster" :role "protocol"}}
  "coordinator の GET /resources/Service/<name> を 1 回読むため(届かなければ名指して落ちる — 値ではない)。"
  (<- answer (HttpRequest "GET" (+ url "/resources/Service/" (url-quote name :safe "")) :timeout-seconds 10.0 :max-retries 0
                          :failures-as-values True))
  (when (not (isinstance answer HttpResponse))
    (raise (RuntimeError (+ "coordinator に届かない: Service " name " — " (repr answer)))))
  answer)


(defk answer-body [answer]
  {:pre [(: answer HttpResponse)] :post [(: % (| dict str))] :tags {:context "doeff-cluster" :role "protocol"}}
  "HTTP の答え → 読みの部品が受ける本文(200 なら JSON の object・それ以外は本文の字のまま)。"
  (if (= answer.status 200) (json.loads answer.text) answer.text))


(defk readiness-read [url name]
  {:pre [(: url str) (: name str)] :post [(: % ServiceReadiness)] :tags {:context "doeff-cluster" :role "protocol"}}
  "coordinator の GET /resources/Service/<name> から準備の状態を読むため(ReadinessOf の読み — 無ければ Missing)。"
  (<- answer HttpResponse (service-answer url name))
  (<- body (| dict str) (answer-body answer))
  (<- readiness ServiceReadiness (readiness-of-body answer.status body))
  readiness)


(defk readiness-awaited [url name state seconds]
  {:pre [(: url str) (: name str) (: state str) (: seconds float)]
   :post [(: % (| ServiceReadiness ServiceFailed ReadinessWaitExpired))] :tags {:context "doeff-cluster" :role "protocol"}}
  "AwaitReadiness に答えるため: 本物の coordinator は長い待ちの読みを持たないので、境界のこの handler が WAIT-PROBE-SECONDS ごとに
   読み、readiness-wait-answer が答えを出したら返す(state に届いた・落ちたと分かった・seconds を過ぎた)。間隔の読み直しは今までどおり
   (この変更で足した loop ではない)。"
  (var waited 0.0)
  (var answered None)
  (while (is answered None)
    (<- answer HttpResponse (service-answer url name))
    (<- body (| dict str) (answer-body answer))
    (<- step (| ServiceReadiness ServiceFailed ReadinessWaitExpired None) (readiness-wait-answer name state answer.status body waited seconds))
    (:= answered step)
    (when (is answered None)
      (<- (Delay WAIT-PROBE-SECONDS))
      (:= waited (+ waited WAIT-PROBE-SECONDS))))
  answered)


(defk state-of [url]
  {:pre [(: url str)] :post [(: % (| dict None))] :tags {:context "doeff-cluster" :role "protocol"}}
  "coordinator の GET /state を 1 度読むため(届かない・200 でなければ None — 起き上がりの途中)。HTTP の答えを読む境界なので、
   JSON の object を dict のまま返す(読む所は job の pid の引きと、手元の 1 台の起き上がりの待ち)。"
  (<- answer (HttpRequest "GET" (+ url "/state") :timeout-seconds 5.0 :max-retries 0 :failures-as-values True))
  (if (and (isinstance answer HttpResponse) (= answer.status 200))
      (json.loads answer.text)
      None))


(defk coordinator-commit-of-state [state]
  {:pre [(: state dict)] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "protocol"}}
  "coordinator の GET /state の答え(JSON の object)から、答えた coordinator の process が走っている doeff の版(欄 coordinatorCommit —
   起動の時に読んだ WORKER_DOEFF_COMMIT)を引くため。欄が無い・文字でない・空なら None(その coordinator は版を申告していない)。
   版上げの Program の名簿の読みは、本番(配備する側の handler)も模擬の Flux もこの 1 つを通る — 宣言した版や Pod の作り直しの数から
   推さず、答えた process の版で「新しい版の coordinator が答えた」を判じる(#3772)。"
  (val commit (.get state "coordinatorCommit"))
  (if (and (isinstance commit str) commit) commit None))


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
