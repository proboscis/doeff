;;; k3s の handler(本番)— 公開 effect 5 つと故障の注入 RunVanish に、kubectl で答える。
;;;
;;; run 1 つ = Pod 1 つ(container 1 つ・restartPolicy Never)。Pod の名 = run の名で、取っ手の run-id は Pod の label(doeff-run.run-id)に
;;; 置く — 名の持ち主の label が取っ手の run-id と違えば、取っ手の run は消えた物として答える。状態は Pod の答えから毎回引き直し、
;;; handler が覚える物は無い(K3sRunHost は kubectl の呼び方・namespace・片付けの印だけ — handler を作り直しても run は読める):
;;;   - 跡 = Pod は finalizer(doeff-run/keep-trace)つきで作る。止める(delete)と Pod は終了 code を持ったまま Terminating で残り、
;;;     RunRemove が finalizer を外して初めて消える(docker の「止めた container は rm まで残る」と同じ約束 — 名は片付けるまで空かない)。
;;;   - 訳 = Pod の annotation(doeff-run/ended)に handler が止めの訳(requested・never-began)と刻と文を書く。k8s の Pod は
;;;     止めを頼まれて止まった物と自分で終わった物、起きずに削除された訳(取り寄せられない image)を残さない。止めの頼みは最初の 1 つが勝つ
;;;     (上書きしない)ので、2 度目の止めは始まった止めの終わりを待つ。
;;;   - env = Pod の宣言に値を書かない(会話は ServiceAccount で Pod を読めるので、書くと他の会話の資格が読める)。値は Secret に置き、
;;;     Pod は名と key で引く(secretKeyRef)。Secret は Pod が持ち主(ownerReference)なので、Pod が消えると一緒に消える。
;;;     Pod を先に作り Secret を後に作る — 逆だと、Pod を作る前に落ちた時に持ち主の無い Secret(資格)が残る。間は container が
;;;     CreateContainerConfigError で待つ(Pending と読む)。
;;;
;;; 出来事は Pod の答えから決まった形で引く: 起きた刻(startedAt)に RunLive・止まった刻(finishedAt)に RunStopped。番号は列の位置なので、
;;; 何度引いても同じ番号になる(k8s の刻は秒まで)。読みの待ちは、読んだ版(resourceVersion)からのその Pod の変化の最初の 1 行で起き、
;;; 時間で見に行かない — `kubectl get -w` は版を渡せず読みとの間の変化を取りこぼすので、API の watch を `kubectl get --raw` で頼む。
;;;
;;; 起きなかった run(NEVER-BEGAN): 取り寄せられない image(ErrImagePull・ImagePullBackOff など)と、無い PVC(Unschedulable)は、
;;; k8s では backoff で待ち続けて終わらない。読みで見つけたら annotation に訳を書いて Pod を止め(settle)、起きなかった止まりとして答える。
;;; 空きが無くて置けない Pod は後で置かれるので Pending のまま。命令の無い Pod は kubelet が StartError で終わらせる。
;;;
;;; 子 process は RunProcess で出す(外側に offloaded-subprocess-handler と scheduled — 待つ間も他の task が回る)。Pod と Secret の宣言は
;;; kubectl の標準入力で渡し、命令の引数に値を載せない(ps に写る)。
(require doeff-hy.macros [defhandler defk <- val var])
(import json)
(import math)
(import re)
(import shlex)
(import uuid)
(import datetime [datetime])
(import doeff_time [GetMonotonic GetTime])
(import doeff_core_effects [RunProcess ProcessOutcome EnvMode])
(import doeff_run._values [RunSpec RunHandle RunPending RunLive RunStopped RunGone RunState StopCause])
(import doeff_run._effects [RunStart RunStop RunRemove RunStatus RunReadEvents RunAccepted RunNameInUse RunRefused RefusalWhy
                            StopFailed RunStillAlive RunEvent RunEventPage])
(import doeff_run._faults [RunVanish])

;; Pod に置く label: run-id = 取っ手の run-id・host = 作った K3sRunHost の印(片付けの的)。
(val RUN-ID-LABEL "doeff-run.run-id")
(val HOST-LABEL "doeff-run.host")
;; 止めの訳を書く annotation(頭の註)と、Pod の跡を残す finalizer。
(val ENDED-ANNOTATION "doeff-run/ended")
(val FINALIZER "doeff-run/keep-trace")
(val CONTAINER-NAME "run")
;; namespace の形(RFC 1123 の label)と、PVC の名の形(RFC 1123 の subdomain)。
(val NAMESPACE-NAME (re.compile r"[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?"))
(val PVC-NAME (re.compile r"[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?"))
;; Secret に置ける env の大きさ(Secret は 1 MiB まで — base64 で 3 分の 1 増えるので余裕を見る)。
(val ENV-MAX-BYTES (* 512 1024))
;; 起きられない image の waiting の訳。これらは backoff で待ち続けて自分では終わらない。
(val IMAGE-ERRORS #{"ErrImagePull" "ImagePullBackOff" "InvalidImageName" "ErrImageNeverPull"})
;; 無い PVC を scheduler が名指す文。
(val PVC-MISSING (re.compile r"persistentvolumeclaim \"([^\"]+)\" not found"))
;; 待たない呼び(get・create・annotate・patch・delete)の上限の秒(API server が答えない時に読みを止めない)。
(val CALL-TIMEOUT 60.0)
;; 止めの猶予の後に、Pod が止まるのを待つ余裕の秒。
(val STOP-MARGIN 30.0)
;; 読みの待ちの子(kubectl get --raw)を、期限で止める時の SIGTERM から SIGKILL までの猶予の秒。
(val WATCH-STOP-GRACE 1.0)
;; 期限の比べの余裕(秒)— 期限の直前で待ちを繰り返さない。
(val DEADLINE-SLACK 1e-3)
;; annotation に写す文の長さの上限(kubelet の長い文で annotation を膨らませない)。
(val DETAIL-MAX 500)
(val UNKNOWN-HANDLE "取っ手の run を k3s が知らない")
(val REMOVED "片付けた")
;; 版は API server が付ける数字の文字列(watch の URL に入れるので、形を確かめる)。
(val RESOURCE-VERSION (re.compile r"[0-9]+"))


(defclass K3sRunHost []
  "k3s の handler の宛先と片付けの印(頭の註)。kubectl = kubectl の命令の頭(既定 #(\"kubectl\") — 別の cluster・替え身の身元は
   --kubeconfig・--as を足して渡す)・namespace = run を立てる namespace(必須 — handler は namespace を作らず、権限も namespace の中だけ)・
   host-id = この host が作る Pod の label HOST-LABEL の値(既定は新しい印 — 片付けの的)。"
  (defn __init__ [self * #^ str namespace #^ tuple [kubectl #("kubectl")] #^ (| str None) [host-id None]]
    (when (not (and (isinstance kubectl tuple) kubectl (all (gfor part kubectl (and (isinstance part str) (bool part))))))
      (raise (ValueError (.format "K3sRunHost.kubectl は空でない文字列の空でない tuple: {!r}" kubectl))))
    (when (not (and (isinstance namespace str) (NAMESPACE-NAME.fullmatch namespace)))
      (raise (ValueError (.format "K3sRunHost.namespace は k8s の namespace の名(小文字・数字・- の 1〜63 字): {!r}" namespace))))
    (when (not (or (is host-id None) (and (isinstance host-id str) (bool host-id))))
      (raise (ValueError (.format "K3sRunHost.host_id は空でない文字列か None: {!r}" host-id))))
    (setv self.kubectl kubectl)
    (setv self.namespace namespace)
    (setv self.host-id (if (is host-id None) (+ "doeff-run-" (cut (. (uuid.uuid4) hex) 0 12)) host-id))))


;; --- 純な訳 --------------------------------------------------------------------------------------------------

(defn #^ (| datetime None) k8s-time [#^ object text]
  "k8s の時刻の文(RFC 3339 — 秒まで)を時刻帯つきの datetime へ。無い・読めない文と、k8s が「無い」の印に置く 0 の刻
   (0001 年・1970 年。命令を起こせなかった container の startedAt)は None。"
  (when (not (isinstance text str))
    (return None))
  (try
    (setv parsed (datetime.fromisoformat text))
    (except [ValueError]
      (return None)))
  (when (or (is parsed.tzinfo None) (< parsed.year 1971))
    (return None))
  parsed)

(defn #^ str ended-annotation [* #^ str cause #^ datetime at #^ str [detail ""]]
  "止めの訳の annotation の値(頭の註): cause = requested(止めを頼まれた)か never-began(起きなかった)・at = 頼んだ刻・
   detail = 起きなかった訳の文(長すぎる文は切る)。"
  (json.dumps {"cause" cause "at" (.isoformat at) "detail" (cut detail 0 DETAIL-MAX)} :ensure-ascii False))

(defn #^ (| dict None) read-ended [#^ dict pod]
  "Pod の annotation の止めの訳({cause at detail}・無ければ None)。"
  (setv text (.get (or (.get (get pod "metadata") "annotations") {}) ENDED-ANNOTATION))
  (when (is text None)
    (return None))
  (setv loaded (json.loads text))
  {"cause" (get loaded "cause") "at" (datetime.fromisoformat (get loaded "at")) "detail" (.get loaded "detail" "")})

(defn #^ (| str None) unsupported-of [#^ RunSpec spec]
  "k3s の handler が守れない設定の訳の文(守れるなら None): PVC の名でない volume の source(path は hostPath に読めるが、node の dir を
   会話に見せてしまうので作らない)・Secret に入らない大きさの env。"
  (for [volume spec.volumes]
    (when (not (PVC-NAME.fullmatch volume.source))
      (return (.format "volume の source {!r} は PVC の名(小文字・数字・- と . の 1〜253 字)でない" volume.source))))
  (setv env-bytes (sum (gfor #(key value) (.items spec.env) (+ (len (.encode key)) (len (.encode value))))))
  (when (> env-bytes ENV-MAX-BYTES)
    (return (.format "env の大きさ {} byte は Secret に置ける上限 {} byte を越える" env-bytes ENV-MAX-BYTES)))
  None)

(defn #^ str secret-name-of [#^ str run-id]
  "run の env を置く Secret の名(run-id ごとに 1 つ — 名の重なりで作り直しが要らない)。"
  (+ "env-" run-id))

(defn #^ dict pod-manifest [#^ K3sRunHost host #^ str name #^ str run-id #^ RunSpec spec]
  "Pod の宣言(頭の註): 命令は args(image の ENTRYPOINT は残す — docker create <image> <命令> と同じ)・env は Secret から引く・
   volume は PVC・資源の上限は requests と limits を同じ値(置く時の見積もりと殺す線を同じに)。ServiceAccount の token は渡さない。"
  (setv container {"name" CONTAINER-NAME "image" spec.image})
  (when spec.command
    (setv (get container "args") (list spec.command)))
  (when spec.env
    (setv (get container "env")
          (lfor key (sorted spec.env)
                {"name" key "valueFrom" {"secretKeyRef" {"name" (secret-name-of run-id) "key" key}}})))
  (when spec.volumes
    (setv (get container "volumeMounts")
          (lfor #(index volume) (enumerate spec.volumes)
                (if volume.read-only
                    {"name" (.format "v{}" index) "mountPath" volume.target "readOnly" True}
                    {"name" (.format "v{}" index) "mountPath" volume.target}))))
  (setv limits spec.limits)
  (setv resources {})
  (when (is-not limits.memory-bytes None)
    (setv (get resources "memory") (str limits.memory-bytes)))
  (when (is-not limits.cpu-millis None)
    (setv (get resources "cpu") (.format "{}m" limits.cpu-millis)))
  (when resources
    (setv (get container "resources") {"limits" resources "requests" (dict resources)}))
  (setv pod-spec {"restartPolicy" "Never" "automountServiceAccountToken" False "containers" [container]})
  (when spec.volumes
    (setv (get pod-spec "volumes")
          (lfor #(index volume) (enumerate spec.volumes)
                {"name" (.format "v{}" index) "persistentVolumeClaim" {"claimName" volume.source}})))
  {"apiVersion" "v1"
   "kind" "Pod"
   "metadata" {"name" name
               "labels" {RUN-ID-LABEL run-id HOST-LABEL host.host-id "app.kubernetes.io/managed-by" "doeff-run"}
               "finalizers" [FINALIZER]}
   "spec" pod-spec})

(defn #^ dict secret-manifest [#^ K3sRunHost host #^ str run-id #^ RunSpec spec #^ str pod-name #^ str pod-uid]
  "run の env を置く Secret の宣言: 持ち主は Pod(Pod が消えると一緒に消える)。値は stringData で運ぶ(標準入力で kubectl へ)。"
  {"apiVersion" "v1"
   "kind" "Secret"
   "metadata" {"name" (secret-name-of run-id)
               "labels" {RUN-ID-LABEL run-id HOST-LABEL host.host-id}
               "ownerReferences" [{"apiVersion" "v1" "kind" "Pod" "name" pod-name "uid" pod-uid}]}
   "type" "Opaque"
   "stringData" (dict (.items spec.env))})

(defn #^ (| str None) missing-claim-of [#^ dict pod]
  "scheduler が「無い PVC」を名指して置けないと言っている Pod の、その訳の文(違えば None)。空きが足りないなど、後で置かれうる訳は None。"
  (for [condition (or (.get (or (.get pod "status") {}) "conditions") [])]
    (when (and (= (.get condition "type") "PodScheduled") (= (.get condition "status") "False"))
      (setv found (PVC-MISSING.search (or (.get condition "message") "")))
      (when (is-not found None)
        (return (.format "volume の PVC {!r} が無い" (.group found 1))))))
  None)

(defn #^ tuple read-pod [#^ RunHandle handle #^ dict pod]
  "Pod の答えから、run の今の状態と出来事の列と settle(#(状態 出来事の tuple settle))を引く(頭の註)。settle = 起きなかった訳の文
   (取り寄せられない image・無い PVC — まだ止めていない Pod。呼び手が訳を annotation に書いて止める)・要らなければ None。"
  (setv metadata (get pod "metadata"))
  (setv status (or (.get pod "status") {}))
  (setv ended (read-ended pod))
  (setv cause (if (is ended None) None (get ended "cause")))
  (setv ended-at (if (is ended None) None (get ended "at")))
  (setv created (k8s-time (.get metadata "creationTimestamp")))
  (setv deleting (is-not (.get metadata "deletionTimestamp") None))
  (setv statuses (or (.get status "containerStatuses") []))
  (setv container-state (if statuses (or (.get (get statuses 0) "state") {}) {}))
  (setv done (.get container-state "terminated"))
  (setv running (.get container-state "running"))
  (setv waiting (.get container-state "waiting"))
  (defn stop-with [state at [started None] [settle None]]
    (setv live (if (is started None) #() #((RunEvent :seq 1 :at started :state (RunLive)))))
    (setv stopped-at (if (is started None) at (max at started)))
    #(state (+ live #((RunEvent :seq (+ (len live) 1) :at stopped-at :state state))) settle))
  (when (is-not done None)
    (setv started (k8s-time (.get done "startedAt")))
    (setv finished (k8s-time (.get done "finishedAt")))
    (setv message (or (.get done "message") ""))
    (return
      (cond
        (= (.get done "reason") "OOMKilled")
          (stop-with (RunStopped :exit-code (get done "exitCode") :cause StopCause.OUT-OF-MEMORY :detail message)
                     (or finished started created) started)
        ;; 起きずに終わった: 止めを頼まれて消された Pod(ContainerStatusUnknown)・取り寄せられず止めた Pod・命令を起こせなかった Pod(StartError)。
        (is started None)
          (cond
            (= cause "requested") (stop-with (RunStopped :exit-code None :cause StopCause.REQUESTED) ended-at)
            (= cause "never-began") (stop-with (RunStopped :exit-code None :cause StopCause.NEVER-BEGAN :detail (get ended "detail")) ended-at)
            True (stop-with (RunStopped :exit-code None :cause StopCause.NEVER-BEGAN :detail (or message (.get done "reason") ""))
                            (or finished created)))
        ;; 止めを頼んだ刻は秒より細かいので、秒へ切り下げて比べる(k8s の終わりの刻は秒まで)。
        True
          (stop-with (RunStopped :exit-code (get done "exitCode")
                                 :cause (if (and (= cause "requested") (is-not finished None) (>= finished (.replace ended-at :microsecond 0)))
                                            StopCause.REQUESTED
                                            StopCause.EXITED)
                                 :detail message)
                     (or finished started) started))))
  (when (is-not running None)
    (setv started (or (k8s-time (.get running "startedAt")) created))
    (return #((RunLive) #((RunEvent :seq 1 :at started :state (RunLive))) None)))
  ;; ここから、container がまだ起きていない Pod。
  (when (= cause "never-began")
    (return (stop-with (RunStopped :exit-code None :cause StopCause.NEVER-BEGAN :detail (get ended "detail")) ended-at)))
  ;; node の無い Pod は kubelet が報告しないので、止めの頼みと削除の印が付いたら止まったと読む。
  (when (and (= cause "requested") deleting (not (.get (or (.get pod "spec") {}) "nodeName")))
    (return (stop-with (RunStopped :exit-code None :cause StopCause.REQUESTED) ended-at)))
  (setv reason (if (is waiting None) "" (or (.get waiting "reason") "")))
  (when (in reason IMAGE-ERRORS)
    (setv why (or (.get waiting "message") reason))
    (return (stop-with (RunStopped :exit-code None :cause StopCause.NEVER-BEGAN :detail why) created None why)))
  (setv missing (missing-claim-of pod))
  (when (is-not missing None)
    (return (stop-with (RunStopped :exit-code None :cause StopCause.NEVER-BEGAN :detail missing) created None missing)))
  (when (in (.get status "phase") #("Failed" "Succeeded"))
    ;; container の報告が無いまま終わった Pod(置く前の追い出し など)。
    (if (= cause "requested")
        (return (stop-with (RunStopped :exit-code None :cause StopCause.REQUESTED) ended-at))
        (return (stop-with (RunStopped :exit-code None :cause StopCause.NEVER-BEGAN
                                       :detail (.join ": " (lfor part #((.get status "reason") (.get status "message")) :if part part)))
                           created))))
  #((RunPending) #() None))

(defn #^ str watch-script [#^ K3sRunHost host #^ str name #^ str resource-version #^ float seconds]
  "読みの待ちの子: 読んだ版 resource-version より後のその Pod の変化の最初の 1 行で終わる(seconds 秒で API server が閉じる)。
   kubectl の watch は bash の process substitution の子で走り、head が終わった後は RunProcess が process group ごと止める。"
  (when (not (RESOURCE-VERSION.fullmatch resource-version))
    (raise (ValueError (.format "Pod の版は数字の文字列: {!r}" resource-version))))
  (setv path (.format "/api/v1/namespaces/{}/pods?watch=1&fieldSelector=metadata.name%3D{}&resourceVersion={}&timeoutSeconds={}"
                      host.namespace name resource-version (max 1 (math.ceil seconds))))
  (setv argv (+ host.kubectl #("get" "--raw" path)))
  (.format "exec head -n 1 < <({} 2>/dev/null)" (.join " " (gfor part argv (shlex.quote part)))))

(defn #^ str last-line [#^ str text]
  "kubectl の誤りの文の最後の行(断りの detail に写す 1 行 — 長すぎる行は切る)。"
  (setv lines (lfor line (.splitlines text) :if (.strip line) (.strip line)))
  (if lines (cut (get lines -1) 0 300) ""))

(defn #^ bool not-found? [#^ str stderr]
  "kubectl の誤りが「その物が無い」か(API server が答えない など、他の誤りと見分ける)。"
  (bool (re.search r"(?i)not ?found" stderr)))

(defn #^ bool already-exists? [#^ str stderr]
  (bool (re.search r"(?i)already ?exists" stderr)))

(defn #^ bool annotation-kept? [#^ str stderr]
  "annotate が「その annotation は既に在る」で断られたか(止めの頼みは最初の 1 つが勝つ — 上書きしない)。"
  (bool (re.search r"--overwrite is false" stderr)))


;; --- kubectl の呼び -------------------------------------------------------------------------------------------

(defk kubectl-call [#^ K3sRunHost host #^ tuple args #^ (| str None) stdin]
  {:pre [(: host K3sRunHost) (: args tuple)] :post [(: % ProcessOutcome)]}
  "kubectl を 1 回走らせ、ProcessOutcome を返すため(待たない呼び — 上限 CALL-TIMEOUT 秒)。宣言は標準入力で渡す。"
  (<- outcome (RunProcess :argv (+ host.kubectl #("--namespace" host.namespace) args) :stdin stdin :timeout CALL-TIMEOUT
                          :env-mode EnvMode.EXTEND :env #()))
  outcome)

(defn #^ str failure-of [#^ str what #^ ProcessOutcome outcome]
  "kubectl の呼びが失敗した時の例外の文(標準入力の中身は写さない)。"
  (.format "{} が失敗した(code {}): {}" what outcome.exit-code
           (or (last-line outcome.stderr) (if outcome.started "" outcome.start-error) (if outcome.timed-out "時間切れ" ""))))

(defk get-pod [#^ K3sRunHost host #^ str name]
  {:pre [(: host K3sRunHost) (: name str)] :post [(: % (| dict None))]}
  "名の Pod の答え(無ければ None)。API server が答えない誤りは例外で上げる(消えた run と見分ける)。"
  (<- outcome (kubectl-call host #("get" "pod" name "--output" "json" "--ignore-not-found") None))
  (when (!= outcome.exit-code 0)
    (raise (RuntimeError (failure-of (.format "kubectl get pod {}" name) outcome))))
  (if (.strip outcome.stdout) (json.loads outcome.stdout) None))

(defn #^ bool ours? [#^ dict pod #^ RunHandle handle]
  "Pod の label の run-id が取っ手の run-id と同じか(違えば走らせ直した別の run・doeff-run の外の Pod)。"
  (= (.get (or (.get (get pod "metadata") "labels") {}) RUN-ID-LABEL) handle.run-id))

(defk find-run [#^ K3sRunHost host #^ RunHandle handle]
  {:pre [(: host K3sRunHost) (: handle RunHandle)] :post [(: % (| dict None))]}
  "取っ手の run の Pod を引くため: 名の Pod の label の run-id が同じなら Pod の答え・でなければ None(名が空いている・走らせ直した
   別の run・doeff-run の外の Pod)。"
  (<- pod (get-pod host handle.name))
  (if (and (is-not pod None) (ours? pod handle)) pod None))

(defk write-ended [#^ K3sRunHost host #^ str name #^ str cause #^ str detail]
  {:pre [(: host K3sRunHost) (: name str) (: cause str) (: detail str)] :post [(: % bool)]}
  "Pod に止めの訳を書く(上書きしない — 最初の頼みが勝つ)。答え = 書いたか(既に在る・Pod が無いなら False)。"
  (<- now-at (GetTime))
  (<- outcome (kubectl-call host #("annotate" "pod" name (+ ENDED-ANNOTATION "=" (ended-annotation :cause cause :at now-at :detail detail))) None))
  (when (= outcome.exit-code 0)
    (return True))
  (when (or (annotation-kept? outcome.stderr) (not-found? outcome.stderr))
    (return False))
  (raise (RuntimeError (failure-of (.format "kubectl annotate pod {}" name) outcome))))

(defk delete-pod [#^ K3sRunHost host #^ str name #^ int grace #^ bool wait]
  {:pre [(: host K3sRunHost) (: name str) (: grace int) (: wait bool)] :post [(: % None)]}
  "Pod を消す頼みを出す(finalizer が在れば Pod は跡を持って残る)。無ければ何もしない。wait の時は消えるまで待つ。"
  (<- outcome (kubectl-call host #("delete" "pod" name "--ignore-not-found" "--grace-period" (str grace) (.format "--wait={}" (if wait "true" "false"))) None))
  (when (!= outcome.exit-code 0)
    (raise (RuntimeError (failure-of (.format "kubectl delete pod {}" name) outcome))))
  None)

(defk release-pod [#^ K3sRunHost host #^ str name]
  {:pre [(: host K3sRunHost) (: name str)] :post [(: % None)]}
  "Pod の finalizer を外す(跡の保ちを終える)。Pod が無ければ何もしない。"
  (<- outcome (kubectl-call host #("patch" "pod" name "--type" "merge" "--patch" "{\"metadata\":{\"finalizers\":null}}") None))
  (when (and (!= outcome.exit-code 0) (not (not-found? outcome.stderr)))
    (raise (RuntimeError (failure-of (.format "kubectl patch pod {}" name) outcome))))
  None)

(defk settle-never-began [#^ K3sRunHost host #^ str name #^ str detail]
  {:pre [(: host K3sRunHost) (: name str) (: detail str)] :post [(: % None)]}
  "起きなかった Pod(取り寄せられない image・無い PVC)に訳を書いて止める: 訳の annotation を先に書き(止めた後は kubelet が container の
   報告を替えて訳が残らない)、Pod を止める。同じ Pod を同時に settle しても、最初の訳が勝つ。"
  (<- _written (write-ended host name "never-began" detail))
  (<- _deleted (delete-pod host name 1 False))
  None)

(defk state-of [#^ K3sRunHost host #^ RunHandle handle]
  {:pre [(: host K3sRunHost) (: handle RunHandle)] :post [(: % tuple)]}
  "取っ手の run の #(状態 出来事の列 Pod の答えか None)。知らない取っ手は #(RunGone #() None)。起きなかった Pod は、訳を書いて止めてから
   読み直した形で答える(読みを重ねても出来事は同じ)。"
  (<- pod (find-run host handle))
  (when (is pod None)
    (return #((RunGone :detail UNKNOWN-HANDLE) #() None)))
  (setv #(state events settle) (read-pod handle pod))
  (when (is settle None)
    (return #(state events pod)))
  (<- _settled (settle-never-began host handle.name settle))
  (<- after (find-run host handle))
  (when (is after None)
    (return #((RunGone :detail UNKNOWN-HANDLE) #() None)))
  (setv #(state events _again) (read-pod handle after))
  #(state events after))

(defn #^ str version-of [#^ dict pod]
  (get (get pod "metadata") "resourceVersion"))

(defk wait-change [#^ K3sRunHost host #^ str name #^ str resource-version #^ float seconds]
  {:pre [(: host K3sRunHost) (: name str) (: resource-version str) (: seconds float)] :post [(: % None)]}
  "読んだ版より後のその Pod の変化が起きるか seconds 秒が過ぎるまで待つ(時間で見に行かない — 頭の註)。"
  (<- _woke (RunProcess :argv #("bash" "-c" (watch-script host name resource-version seconds))
                        :timeout seconds :process-group True :stop-grace WATCH-STOP-GRACE
                        :env-mode EnvMode.EXTEND :env #()))
  None)

(defk await-ended [#^ K3sRunHost host #^ RunHandle handle #^ float deadline]
  {:pre [(: host K3sRunHost) (: handle RunHandle) (: deadline float)] :post [(: % RunState)]}
  "run が止まる(か消える)まで、deadline(単調な時計)まで待って、その時の状態を答えるため。"
  (while True
    (<- seen (state-of host handle))
    (val state (get seen 0))
    (when (isinstance state #(RunStopped RunGone))
      (return state))
    (<- now (GetMonotonic))
    (when (>= now (- deadline DEADLINE-SLACK))
      (return state))
    (<- _woke (wait-change host handle.name (version-of (get seen 2)) (- deadline now)))))


;; --- effect の答え -------------------------------------------------------------------------------------------

(defk holder-of [#^ K3sRunHost host #^ str name]
  {:pre [(: host K3sRunHost) (: name str)] :post [(: % (| RunHandle str None))]}
  "名の今の持ち主の取っ手(この handler の Pod)・doeff-run の外の Pod が持つなら断りの文・空いていれば None。"
  (<- pod (get-pod host name))
  (when (is pod None)
    (return None))
  (val run-id (.get (or (.get (get pod "metadata") "labels") {}) RUN-ID-LABEL))
  (if run-id
      (RunHandle :name name :run-id run-id)
      (.format "名 {!r} は doeff-run の作っていない Pod が使っている" name)))

(defk k3s-start [#^ K3sRunHost host #^ RunStart request]
  {:pre [(: host K3sRunHost) (: request RunStart)] :post [(: % (| RunAccepted RunNameInUse RunRefused))]}
  "走らせ始めを受け付けるため: 名の持ち主が在れば取っ手を答え・守れない設定は断り・それ以外は Pod を作り、env が在れば Secret を作る
   (Pod を先に — 頭の註)。image を取り寄せられない・無い PVC などは受け付けた後に分かるので、読みで起きなかった止まりとして届く。"
  (val name request.name)
  (val spec request.spec)
  (<- held (holder-of host name))
  (cond
    (isinstance held RunHandle) (return (RunNameInUse :handle held))
    (isinstance held str) (return (RunRefused :name name :why RefusalWhy.UNSUPPORTED :detail held)))
  (val refusal (unsupported-of spec))
  (when (is-not refusal None)
    (return (RunRefused :name name :why RefusalWhy.UNSUPPORTED :detail refusal)))
  (val handle (RunHandle :name name :run-id (+ "k3s-" (. (uuid.uuid4) hex))))
  (<- created (kubectl-call host #("create" "--filename" "-" "--output" "json") (json.dumps (pod-manifest host name handle.run-id spec))))
  (when (!= created.exit-code 0)
    ;; 名を同時に取った別の頼みが在れば、その持ち主を答える。でなければ API server が宣言を断った(割り当ての上限・宣言の誤り)。
    (when (already-exists? created.stderr)
      (<- holder (holder-of host name))
      (cond
        (isinstance holder RunHandle) (return (RunNameInUse :handle holder))
        (isinstance holder str) (return (RunRefused :name name :why RefusalWhy.UNSUPPORTED :detail holder))))
    (when (re.search r"(?i)exceeded quota" created.stderr)
      (return (RunRefused :name name :why RefusalWhy.NO-CAPACITY :detail (last-line created.stderr))))
    (when (re.search r"(?i)is invalid|denied the request|violates PodSecurity" created.stderr)
      (return (RunRefused :name name :why RefusalWhy.UNSUPPORTED :detail (last-line created.stderr))))
    (raise (RuntimeError (failure-of (.format "kubectl create pod {}" name) created))))
  (when spec.env
    (val pod-uid (get (get (json.loads created.stdout) "metadata") "uid"))
    (<- secret (kubectl-call host #("create" "--filename" "-" "--output" "name")
                             (json.dumps (secret-manifest host handle.run-id spec name pod-uid))))
    (when (!= secret.exit-code 0)
      ;; env を渡せない Pod は立てない(資格の無い run が走る事になる)— 作った Pod を片付けてから誤りを上げる。
      (<- _released (release-pod host name))
      (<- _removed (delete-pod host name 0 True))
      (raise (RuntimeError (failure-of (.format "kubectl create secret {}" (secret-name-of handle.run-id)) secret)))))
  (RunAccepted :handle handle))

(defk k3s-stop [#^ K3sRunHost host #^ RunStop request]
  {:pre [(: host K3sRunHost) (: request RunStop)] :post [(: % (| RunStopped RunGone StopFailed))]}
  "run を止めるため: 止まった run にはその状態・走っている(か起きる前の)run には止めの頼みを annotation に書いてから Pod を消し
   (SIGTERM → 猶予を秒へ切り上げて → SIGKILL は kubelet)、止まるのを待って止まった状態を答える。始まった止めの 2 度目の頼みは、
   頼みを書き直さずに始まった止めの終わりを待つ。"
  (val handle request.handle)
  (<- seen (state-of host handle))
  (val state (get seen 0))
  (when (isinstance state #(RunStopped RunGone))
    (return state))
  (val grace (max 1 (math.ceil request.grace-seconds)))
  (when (is (.get (get (get seen 2) "metadata") "deletionTimestamp") None)
    (<- _written (write-ended host handle.name "requested" ""))
    (<- _deleted (delete-pod host handle.name grace False)))
  (<- began (GetMonotonic))
  (<- ended (await-ended host handle (+ began grace STOP-MARGIN)))
  (cond
    (isinstance ended #(RunStopped RunGone)) ended
    True (StopFailed :handle handle :detail (.format "Pod の削除を頼んで {} 秒と余裕 {} 秒が過ぎても止まっていない" grace STOP-MARGIN))))

(defk k3s-remove [#^ K3sRunHost host #^ RunRemove request]
  {:pre [(: host K3sRunHost) (: request RunRemove)] :post [(: % (| RunGone RunStillAlive))]}
  "止まった run の跡を片付けるため: finalizer を外して Pod を消し(名を空ける — Secret は持ち主の Pod と一緒に消える)、消えるまで待つ。
   止まっていない run は断る。"
  (val handle request.handle)
  (<- seen (state-of host handle))
  (val state (get seen 0))
  (when (isinstance state RunGone)
    (return (RunGone :detail UNKNOWN-HANDLE)))
  (when (not (isinstance state RunStopped))
    (return (RunStillAlive :handle handle)))
  (<- _released (release-pod host handle.name))
  (<- _deleted (delete-pod host handle.name 1 True))
  (RunGone :detail REMOVED))

(defk k3s-status [#^ K3sRunHost host #^ RunStatus request]
  {:pre [(: host K3sRunHost) (: request RunStatus)] :post [(: % (| RunPending RunLive RunStopped RunGone))]}
  "run の今の状態を、Pod の答えから引いて答えるため。"
  (<- seen (state-of host request.handle))
  (get seen 0))

(defk k3s-read-events [#^ K3sRunHost host #^ RunReadEvents request]
  {:pre [(: host K3sRunHost) (: request RunReadEvents)] :post [(: % RunEventPage)]}
  "after-seq より後の出来事を、新しい出来事が起きるか終わっているか期限が来るまで待って頁にするため。待ちは読んだ版からのその Pod の
   変化の最初の 1 行で起きる。読みの途中で Pod が消えたら、RunGone を最後の出来事に足す(頭の註)。"
  (val handle request.handle)
  (val after request.after-seq)
  (<- started (GetMonotonic))
  (val deadline (+ started request.wait-up-to))
  (var known None)
  (while True
    (<- seen (state-of host handle))
    (val state (get seen 0))
    (var events (get seen 1))
    (when (and (isinstance state RunGone) (is-not known None))
      ;; この読みが見ていた run が消えた: 見た列に消えた出来事を足す。
      (<- now-at (GetTime))
      (:= events (+ known #((RunEvent :seq (+ (len known) 1) :at now-at :state state)))))
    (:= known events)
    (val fresh (tuple (gfor event events :if (> event.seq after) event)))
    (<- now (GetMonotonic))
    (when (or fresh (isinstance state #(RunStopped RunGone)) (>= now (- deadline DEADLINE-SLACK)))
      (return (RunEventPage :events fresh :next-seq (if fresh (. (get fresh -1) seq) after) :state state)))
    (<- _woke (wait-change host handle.name (version-of (get seen 2)) (- deadline now)))))

(defk k3s-vanish [#^ K3sRunHost host #^ RunVanish request]
  {:pre [(: host K3sRunHost) (: request RunVanish)] :post [(: % bool)]}
  "run を handler の知らない所で消すため(検の口): finalizer を外して猶予なしで Pod を消す。答え = 消す run が在ったか。"
  (val handle request.handle)
  (<- found (find-run host handle))
  (when (is found None)
    (return False))
  (<- _released (release-pod host handle.name))
  (<- outcome (kubectl-call host #("delete" "pod" handle.name "--ignore-not-found" "--grace-period" "0" "--force" "--wait=true") None))
  (when (!= outcome.exit-code 0)
    (raise (RuntimeError (failure-of (.format "kubectl delete pod --force {}" handle.name) outcome))))
  True)


;; --- handler -------------------------------------------------------------------------------------------------

;; 引数に残す理由: host は宛先(kubectl の呼び方・namespace)と片付けの印を持ち、検が同じ host を片付けの的に読む
;; (docker-run-handler の host と同じ形)。
(defhandler k3s-run-handler [#^ K3sRunHost host]
  "DoeffRun の k3s の handler: 公開 effect 5 つと故障の注入 RunVanish に、host の namespace の Pod を kubectl で操って答える。外側に
   RunProcess の答え手(offloaded-subprocess-handler)と時計(GetTime・GetMonotonic)と scheduled が要る。"
  (RunStart [name spec]
    (<- outcome (k3s-start host effect))
    (resume outcome))
  (RunStop [handle grace-seconds]
    (<- outcome (k3s-stop host effect))
    (resume outcome))
  (RunRemove [handle]
    (<- outcome (k3s-remove host effect))
    (resume outcome))
  (RunStatus [handle]
    (<- state (k3s-status host effect))
    (resume state))
  (RunReadEvents [handle after-seq wait-up-to]
    (<- page (k3s-read-events host effect))
    (resume page))
  (RunVanish [handle]
    (<- vanished (k3s-vanish host effect))
    (resume vanished)))
