;;; RemoteJob の本番の handler(remote-cluster — coordinator へ出し、worker の子 process で走らせる)。
;;;
;;; 手元で RemoteJob を確かめるのは sim-cluster(local.hy)だけ: 偽の宿が task の Program を別の process(別のスコープ)として走らせ、
;;; 呼び手の handler を継がない。以前ここに在った remote-inline(同じ VM で Spawn して待ち、呼び手の外側の handler をそのまま継ぐ)は、
;;; task の Program が自分の handler を全部持つ約束(ADR-DOE-CLUSTER-001 R1・R2)の下では足りない handler を呼び手が黙って補うので消した
;;; (段 5)。
;;;
;;; task の本文の形(task-submit-body)と問い合わせの答えの読み(outcome-of・settled-value)は、この handler と sim-cluster の偽の宿が
;;; 同じ関数を使う(本文を写さない)。
(require doeff-hy.macros [defhandler defk deff <- val])
(import json)
(import time)
(import httpx)
(import .coordinator_http [CoordinatorEndpoint send-idempotent put-program REPLY-SECONDS IDEMPOTENT-DEADLINE-SECONDS])
(import doeff_time [Delay])
(import doeff [run])
(import doeff_cluster.shared.intent.protocol [PROTOCOL-FORMAT])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv runtime-env->json])
(import doeff_cluster.shared.intent.remote_model [RemoteJob RemoteJobFailed EnvUnavailable TaskSucceeded TaskFailed
                       encode-program decode-outcome])
(import .process_versions [current-versions])


(deff task-submit-body [#^ str sha #^ str revision #^ frozenset needs #^ str name #^ float lease-seconds
                        #^ (| RuntimeEnv None) runtime-env #^ dict environ]  ; defk にできない: 本番の client(Program の外の I/O の道具)と sim の宿が同じ形を作る純粋な判断
  {:pre [(: sha str) (: revision str) (: needs frozenset) (: name str) (: lease-seconds float) (: runtime-env (| RuntimeEnv None)) (: environ dict)] :post [(: % dict)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "POST /tasks の本文を作るため(本番の TaskClient と sim の宿で同じ形)。詰めた Program は先に PUT /programs/<sha> で置き、本文は
   sha だけを運ぶ(service の宣言と同じ運び方 — ADR-DOE-CLUSTER-001 R3b)。environ = 子の環境変数(RemoteJob.environ — 空なら欄を置かない)。"
  (| {"program" sha "revision" revision
      "needs" (sorted needs) "name" name "leaseSeconds" lease-seconds "format" PROTOCOL-FORMAT}
     (if (is runtime-env None) {} {"runtimeEnv" (run (runtime-env->json runtime-env))})
     (if environ {"environ" (dict environ)} {})))


(defclass TaskClient []
  "coordinator の /tasks との連絡(I/O)。revision = 送り手の commit(受け側はこの版のコードを準備してから復元する)。
   runtime-env = 実行環境の宣言(在れば worker は env の root を準備して、その中の子 process で走らせる — revision は使わない)。
   transport = httpx の transport(DetachedClient・WarmClient と同じ — 検が coordinator の模擬を後ろに置く。既定 None = 網)。"
  (defn #^ None __init__ [self #^ str url #^ str revision #^ float [timeout REPLY-SECONDS] #^ (| RuntimeEnv None) [runtime-env None]
                  #^ (| httpx.BaseTransport None) [transport None]]
    (setv self.revision revision self.runtime-env runtime-env
          self.endpoint (CoordinatorEndpoint url timeout 4 :transport transport)))

  (defn #^ str submit [self #^ str blob #^ frozenset needs #^ dict versions #^ str name #^ float lease-seconds #^ (| dict None) [environ None]]
    "task を 1 本出す: 詰めた Program を版と一緒に置き場 /programs/<sha> に先に置き、本文は sha だけを運ぶ(service の宣言と同じ運び方 —
     ADR-DOE-CLUSTER-001 R3b)。答え = coordinator の振った task の id。"
    (setv #(sha put) (put-program self.endpoint blob versions IDEMPOTENT-DEADLINE-SECONDS))
    (.raise-for-status put)
    (setv response (.request self.endpoint "POST" "/tasks"
      :json (task-submit-body sha self.revision needs name lease-seconds self.runtime-env (or environ {}))))
    (.raise-for-status response)
    (get (.json response) "task"))

  (defn #^ dict poll [self #^ str task]
    ;; 問い合わせが lease を延ばす。呼び手が止まれば問い合わせも止まり、coordinator が task を落とす。
    (setv response (send-idempotent (fn [] (.request self.endpoint "GET" (+ "/tasks/" task)))))
    (.raise-for-status response)
    (.json response))

  (defn #^ None drop [self #^ str task]
    (try (.request self.endpoint "DELETE" (+ "/tasks/" task))
         (except [Exception] None))
    None))


(defn #^ (| TaskSucceeded TaskFailed None) outcome-of [#^ dict view #^ str task #^ str revision]
  "純粋: 問い合わせの答え 1 つ → 結果(まだなら None)。走らせられなかった時は RemoteJobFailed を投げる。"
  (setv phase (.get view "phase"))
  (cond
    (= phase "finished")
      (if (is (.get view "result") None)
          (raise (RemoteJobFailed (.format "worker の子 process が結果を書かずに終わった(task {}・{})" task (.get view "detail"))))
          (decode-outcome (get view "result")))
    (= phase "code-failed")
      (raise (RemoteJobFailed (.format "実行先で commit {} のコードを準備できない: {}" revision (.get view "detail"))))
    (= phase "env-failed")
      (raise (EnvUnavailable (.get view "failureKind" "") (.get view "detail" "")))
    ;; 送る先が無い(版と label が合う worker が無い)・担い手が沈黙した。業務の例外ではない。
    (= phase "failed")
      (raise (RemoteJobFailed (.format "task {} を走らせられない: {}" task (.get view "detail"))))
    (= phase "missing")
      (raise (RemoteJobFailed (.format "coordinator が task {} を失った(lease 切れか作り直し)" task)))
    True None))


(deff settled-value [outcome]  ; defk にできない: 本番の handler と sim の宿の節が、結果を呼び手への答えか例外に変える純粋な判断
  {:pre [(: outcome (| TaskSucceeded TaskFailed))] :post [(: % "task の Program の戻り値(型は Program ごと)")]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "task の結果を、呼び手の RemoteJob の答え(戻り値)か、呼び手へ届ける例外(Program が投げた例外そのもの — 詰められない例外なら
   RemoteJobFailed)にするため。"
  (match outcome
    (TaskSucceeded :value value) value
    (TaskFailed) (raise (or outcome.error
                            (RemoteJobFailed (.format "{}: {}\n{}" outcome.kind outcome.message outcome.traceback))))))


(defk wait-outcome [client task poll-seconds]
  {:pre [(: client TaskClient) (: task str) (: poll-seconds float)] :post [(: % (| TaskSucceeded TaskFailed))]}
  ;; 終わるまで問い合わせる。眠りは Delay(外側の doeff-time の handler)なので同じ VM の他の task を塞がない。
  ;; 抜ける時は、結果でも失敗でも取り消し(呼び手の Cancel)でも task を落とす — 落とせば担い手は次の拍で子 process を止める。
  ;; 落とす前に呼び手の process ごと消えた時は、問い合わせが途絶えて lease が切れた時に coordinator が落とす。
  (try
    (while True
      (<- (Delay poll-seconds))
      (val outcome (outcome-of (.poll client task) task client.revision))
      (when (is-not outcome None) (return outcome)))
    (finally
      (.drop client task))))


(defhandler remote-cluster [#^ TaskClient client #^ float [poll-seconds 1.0] #^ float [lease-seconds 15.0]]
  (RemoteJob [program needs name environ]
    ;; 送れない値は送る前に断る(encode-program が UnsendableProgram を投げ、呼び手へ届く)。
    (val blob (encode-program program))
    (val task (.submit client blob needs (current-versions) name lease-seconds environ))
    (<- outcome (wait-outcome client task poll-seconds))
    (resume (settled-value outcome))))
