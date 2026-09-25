;;; RemoteJob の 2 つの handler。業務のコードは同じまま、composition root がどちらを被せるかで
;;; 「手元の 1 process で系全体をテストする」と「本番でクラスタに分散する」を切り替える。
(require doeff-hy.macros [defhandler defk <-])
(import json)
(import time)
(import .coordinator_http [CoordinatorEndpoint send-idempotent REPLY-SECONDS])
(import doeff_core_effects.scheduler [Spawn Wait])
(import doeff_time [Delay])
(import .remote_model [RemoteJob RemoteJobFailed TaskSucceeded TaskFailed
                       encode-program decode-outcome current-versions])


;; --- handler A: 同じ VM の中で Spawn して待つ(テスト用) --------------------------------------
;; 外側の handler(テストの fake)をそのまま継承する。Program の例外は Wait が再送出し、呼び手の yield 点へ届く。
(defhandler remote-inline []
  (RemoteJob [program env requires name]
    (<- task (Spawn program))
    (<- result (Wait task))
    (resume result)))


;; --- handler B: coordinator へ出し、worker の子 process で走らせる ------------------------------
(defclass TaskClient []
  "coordinator の /tasks との連絡(I/O)。revision = 送り手の commit(受け側はこの版のコードを準備してから復元する)。"
  (defn __init__ [self #^ str url #^ str revision [timeout REPLY-SECONDS]]
    (setv self.revision revision self.endpoint (CoordinatorEndpoint url timeout 4)))

  (defn #^ str submit [self #^ str blob #^ str env #^ tuple requires #^ dict versions #^ str name #^ float lease-seconds]
    (setv response (.request self.endpoint "POST" "/tasks"
      :json {"env" env "blob" blob "versions" versions "revision" self.revision
             "requires" (dict requires) "name" name "leaseSeconds" lease-seconds}))
    (.raise-for-status response)
    (get (.json response) "task"))

  (defn #^ dict poll [self #^ str task]
    ;; 問い合わせが lease を延ばす。呼び手が止まれば問い合わせも止まり、coordinator が task を落とす。
    (setv response (send-idempotent (fn [] (.request self.endpoint "GET" (+ "/tasks/" task)))))
    (.raise-for-status response)
    (.json response))

  (defn drop [self #^ str task]
    (try (.request self.endpoint "DELETE" (+ "/tasks/" task))
         (except [Exception] None))))


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
    ;; 送る先が無い(版と label が合う worker が無い)・担い手が沈黙した。業務の例外ではない。
    (= phase "failed")
      (raise (RemoteJobFailed (.format "task {} を走らせられない: {}" task (.get view "detail"))))
    (= phase "missing")
      (raise (RemoteJobFailed (.format "coordinator が task {} を失った(lease 切れか作り直し)" task)))
    True None))


(defk wait-outcome [client task poll-seconds]
  {:pre [(: client TaskClient) (: task str) (: poll-seconds float)] :post [(: % (| TaskSucceeded TaskFailed))]}
  ;; 終わるまで問い合わせる。眠りは Delay(外側の doeff-time の handler)なので同じ VM の他の task を塞がない。
  ;; 抜ける時は、結果でも失敗でも取り消し(呼び手の Cancel)でも task を落とす — 落とせば担い手は次の拍で子 process を止める。
  ;; 落とす前に呼び手の process ごと消えた時は、問い合わせが途絶えて lease が切れた時に coordinator が落とす。
  (try
    (while True
      (<- (Delay poll-seconds))
      (setv outcome (outcome-of (.poll client task) task client.revision))
      (when (is-not outcome None) (return outcome)))
    (finally
      (.drop client task))))


(defhandler remote-cluster [#^ TaskClient client [poll-seconds 1.0] [lease-seconds 15.0]]
  (RemoteJob [program env requires name]
    ;; 送れない値は送る前に断る(encode-program が UnsendableProgram を投げ、呼び手へ届く)。
    (setv blob (encode-program program))
    (setv task (.submit client blob env requires (current-versions) name lease-seconds))
    (<- outcome (wait-outcome client task poll-seconds))
    (if (isinstance outcome TaskSucceeded)
        (resume outcome.value)
        (raise (or outcome.error
                   (RemoteJobFailed (.format "{}: {}\n{}" outcome.kind outcome.message outcome.traceback)))))))
