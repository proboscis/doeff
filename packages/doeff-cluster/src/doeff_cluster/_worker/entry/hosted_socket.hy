;;; 預かりの process の口(shared/intent/hosted_model)の unix socket の両側。
;;;
;;;   hosted-serve-loop  worker の中の拍の外の背景の task: 口(foundation/line_socket の LineInbox)に来た 1 行の頼みを効果
;;;                      (LaunchHosted・StopHosted・ListHosted)に読み、頼みごとの task で答え手(worker/protocol/hosted_host の hosted-host)へ
;;;                      渡し、答えを 1 行で返す。頼みごとに task を分けるので、止めの猶予を待つ頼みが他の起こしを待たせない。
;;;   hosted-client      job の中の答え手: 同じ 3 つの効果を、worker の口へ 1 行で頼んで答える。口の path は worker が job の子へ
;;;                      環境変数(worker/core/hosted_rules の SOCKET-VAR)で渡す — hosted-socket-path が読む。
;;;
;;; 行の綴りは worker/protocol/hosted_host(頼みと答えの defwire)。層 foundation は intent の型を読まないので、繋ぎはこの入口に置く
;;; (worker/entry/retirement_notices と同じ形)。
(require doeff-hy.macros [defhandler defk <- val])
(val MODULE-TAGS {:context "worker" :role "main"})
(import doeff_core_effects [ReadEnvironment Spawn slog])
(import doeff_cluster._foundation.line_socket [LineCall LineCallFailed LineInbox answer-call line-call next-calls])
(import doeff_cluster._shared.intent.hosted_model [HostedProcess HostedRefused LaunchHosted StopHosted ListHosted])
(import doeff_cluster._worker.core.hosted_rules [SOCKET-VAR])
(import doeff_cluster._worker.protocol.hosted_host [hosted-answer-of hosted-answer-text hosted-request-of hosted-request-text])

;; 頼む側の往復の上限(秒)。止めは worker の猶予(STOP-GRACE-MS の 2 倍まで)を待つので、それより長く取る。
(val CALL-SECONDS 30.0)


(defk answered [call]
  {:pre [(: call LineCall)] :post [(: % None)] :tags {:context "worker" :role "main"}}
  "頼み 1 つを読み、効果として答え手へ渡し、答えを 1 行で返すため。答え手の思わぬ例外も断りの 1 行にして返す(頼み手を待たせない)。"
  (<- request (| LaunchHosted StopHosted ListHosted HostedRefused) (hosted-request-of call.text))
  (when (isinstance request HostedRefused)
    (<- refused str (hosted-answer-text request))
    (answer-call call refused)
    (return None))
  (try
    (<- answer (| HostedProcess HostedRefused tuple)
        (match request
          (LaunchHosted) (LaunchHosted request.launch)
          (StopHosted) (StopHosted request.id)
          _ (ListHosted)))
    (<- text str (hosted-answer-text answer))
    (answer-call call text)
    (except [error Exception]
      (<- (slog "worker: 預かりの口の頼みに答えられない" :level "warning" :error (repr error)))
      (<- failed str (hosted-answer-text (HostedRefused :detail (.format "worker の中の失敗: {!r}" error))))
      (answer-call call failed)))
  None)


(defk hosted-serve-loop [inbox]
  {:pre [(: inbox LineInbox)] :post [(: % None)] :tags {:context "worker" :role "main"}}
  "口の背景の task の本体(頭の註): 頼みが来るたびに、頼みごとの task を起こす。worker の終わりまで回る(daemon の task)。"
  (while True
    (<- calls tuple (next-calls inbox))
    (for [call calls]
      (<- (Spawn (answered call) :daemon True))))
  None)


(defk hosted-socket-path []
  {:pre [] :post [(: % (| str None))] :tags {:context "worker" :role "main" :reads "env"}}
  "job の中で、worker が渡した口の socket の path を読むため(None = worker の子でないか、口を持たない worker)。"
  (<- read tuple (ReadEnvironment #(SOCKET-VAR)))
  (next (gfor e read :if (and (= e.name SOCKET-VAR) e.value) e.value) None))


(defk client-call [socket request]
  {:pre [(: socket (| str None)) (: request (| LaunchHosted StopHosted ListHosted))] :post [(: % (| HostedProcess HostedRefused tuple))]
   :tags {:context "worker" :role "main"}}
  "頼み 1 つを worker の口へ 1 行で送り、答えを型に読むため。口が無い・届かない時は断り。"
  (when (is socket None)
    (return (HostedRefused :detail (.format "worker の口が無い(環境変数 {} が無い)" SOCKET-VAR))))
  (<- text str (hosted-request-text request))
  (val raw (line-call socket text CALL-SECONDS))
  (if (isinstance raw LineCallFailed)
      (HostedRefused :detail (+ "worker の口に届かない: " raw.detail))
      (do (<- answer (| HostedProcess HostedRefused tuple) (hosted-answer-of raw (isinstance request ListHosted)))
          answer)))


(defhandler hosted-client [#^ (| str None) socket]
  "job の中の預かりの process の答え手(頭の註)。socket = worker の口の path(hosted-socket-path の答え — None なら全部断る)。"
  {:tags {:context "worker" :role "main"}}
  ;; 引数に残す理由: 口の path は job の process ごとに 1 度だけ読む環境の値。
  (LaunchHosted [launch]
    (<- answer (| HostedProcess HostedRefused) (client-call socket effect))
    (resume answer))
  (StopHosted [id]
    (<- answer (| HostedProcess HostedRefused) (client-call socket effect))
    (resume answer))
  (ListHosted []
    (<- answer (| tuple HostedRefused) (client-call socket effect))
    (resume answer)))
