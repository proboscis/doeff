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
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff_cluster.foundation.coordinator_http [IDEMPOTENT-DEADLINE-SECONDS RESEND-PAUSE-SECONDS])
(import doeff_cluster.shared.protocol.coordinator_route [RouteCell RouteOptions RoutedReply routed-request resent-request answer-json])
(import doeff_time [Delay])
(import doeff [run])
(import doeff_cluster.shared.intent.protocol [PROTOCOL-FORMAT])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])
(import doeff_cluster.shared.core.runtime_env_rules [runtime-env->json])
(import doeff_cluster.shared.intent.remote_model [RemoteJob RemoteJobFailed EnvUnavailable TaskSucceeded TaskFailed])
(import doeff_cluster.shared.protocol.program_codec [encode-program decode-outcome])
(import doeff_cluster.shared.core.remote_rules [program-sha])


(deff task-submit-body [#^ str sha #^ str revision #^ frozenset needs #^ str name #^ float lease-seconds
                        #^ (| RuntimeEnv None) runtime-env #^ dict environ]  ; defk にできない: 本番の client(Program の外の I/O の道具)と sim の宿が同じ形を作る純粋な判断
  {:pre [(: sha str) (: revision str) (: needs frozenset) (: name str) (: lease-seconds float) (: runtime-env (| RuntimeEnv None)) (: environ dict)] :post [(: % dict)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "POST /tasks の本文を作るため(本番の remote-cluster と sim の宿で同じ形)。詰めた Program は先に PUT /programs/<sha> で置き、本文は
   sha だけを運ぶ(service の宣言と同じ運び方 — ADR-DOE-CLUSTER-001 R3b)。environ = 子の環境変数(RemoteJob.environ — 空なら欄を置かない)。"
  (| {"program" sha "revision" revision
      "needs" (sorted needs) "name" name "leaseSeconds" lease-seconds "format" PROTOCOL-FORMAT}
     (if (is runtime-env None) {} {"runtimeEnv" (run (runtime-env->json runtime-env))})
     (if environ {"environ" (dict environ)} {})))


(defrecord TaskSender
  "task の送り手: revision = 送り手の commit(受け側はこの版のコードを準備してから復元する)・versions = 送り手の版の識別(blob に添える —
   組み立てが宿の契約の Ask versions-key で読んで渡す・この層は読まない #2345)・runtime-env = 実行環境の宣言(在れば worker は env の
   root を準備して、その中の子 process で走らせる — revision は使わない)。"
  {:tags {:context "doeff-cluster" :role "protocol"}}
  (#^ str revision)
  (#^ dict versions)
  (#^ (| RuntimeEnv None) runtime-env))


(defk program-put [cell options blob versions deadline-seconds]
  {:pre [(: cell RouteCell) (: options RouteOptions) (: blob str) (: versions dict) (: deadline-seconds float)]
   :post [(: % tuple) (= (len %) 2)] :tags {:context "doeff-cluster" :role "protocol"}}
  "task を送る前に、詰めた Program を coordinator の置き場 PUT /programs/<sha> に版と一緒に置くため(task の本文は sha だけを運ぶ —
   service の宣言と同じ運び方・ADR-DOE-CLUSTER-001 R3b)。同じ中身は同じキーの同じ行なので、何度送っても同じ意味 — 失敗は
   deadline-seconds まで送り直す(resent-request)。答え = #(sha 答え)(答えの読みは呼び手 — 断りの型は口ごとに違う: remote-cluster は
   answer-json・detached-cluster は detached-refusal)。"
  (val sha (program-sha blob))
  (<- reply RoutedReply (resent-request cell.route "PUT" (+ "/programs/" sha) options None {"blob" blob "versions" versions}
                                        deadline-seconds RESEND-PAUSE-SECONDS))
  (setv cell.route reply.route)
  #(sha reply.answer))


(defk task-submitted [cell options sender blob needs name lease-seconds environ]
  {:pre [(: cell RouteCell) (: options RouteOptions) (: sender TaskSender) (: blob str) (: needs frozenset) (: name str)
         (: lease-seconds float) (: environ dict)]
   :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol"}}
  "task を 1 本出すため: 詰めた Program を置き場に先に置き(program-put)、本文は sha だけを運ぶ POST /tasks を送る。書きなので送り直しは
   接続の段だけ(routed-request)。答え = coordinator の振った task の id。"
  (<- put tuple (program-put cell options blob sender.versions IDEMPOTENT-DEADLINE-SECONDS))
  (setv #(sha stored) put)
  (<- _stored (answer-json stored))
  (val body (task-submit-body sha sender.revision needs name lease-seconds sender.runtime-env environ))
  (<- reply RoutedReply (routed-request cell.route "POST" "/tasks" options None body))
  (setv cell.route reply.route)
  (<- answer dict (answer-json reply.answer))
  (get answer "task"))


(defk task-view [cell options task]
  {:pre [(: cell RouteCell) (: options RouteOptions) (: task str)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "protocol"}}
  "task の今の様子を問い合わせるため(GET /tasks/<id> — 問い合わせが lease を延ばす。呼び手が止まれば問い合わせも止まり、coordinator が
   task を落とす)。読みなので失敗は期限まで送り直す。"
  (<- reply RoutedReply (resent-request cell.route "GET" (+ "/tasks/" task) options None None
                                        IDEMPOTENT-DEADLINE-SECONDS RESEND-PAUSE-SECONDS))
  (setv cell.route reply.route)
  (<- view dict (answer-json reply.answer))
  view)


(defk task-dropped [cell options task]
  {:pre [(: cell RouteCell) (: options RouteOptions) (: task str)] :post [(: % None)] :tags {:context "doeff-cluster" :role "protocol"}}
  "task を落とすため(DELETE /tasks/<id> — 落とせば担い手は次の拍で子 process を止める)。答えは読まない: 届かなければ問い合わせが
   途絶えて lease が切れた時に coordinator が落とす。"
  (<- reply RoutedReply (routed-request cell.route "DELETE" (+ "/tasks/" task) options None None))
  (setv cell.route reply.route)
  None)


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


(defk wait-outcome [cell options sender task poll-seconds]
  {:pre [(: cell RouteCell) (: options RouteOptions) (: sender TaskSender) (: task str) (: poll-seconds float)]
   :post [(: % (| TaskSucceeded TaskFailed))] :tags {:context "doeff-cluster" :role "protocol"}}
  "出した task が終わるまで問い合わせて、結果を返すため。眠りは Delay(外側の doeff-time の handler)なので同じ VM の他の task を
   塞がない。抜ける時は、結果でも失敗でも取り消し(呼び手の Cancel)でも task を落とす。"
  (try
    (while True
      (<- (Delay poll-seconds))
      (<- view dict (task-view cell options task))
      (val outcome (outcome-of view task sender.revision))
      (when (is-not outcome None) (return outcome)))
    (finally
      (<- (task-dropped cell options task)))))


;; 本物の RemoteJob: coordinator の /programs と /tasks へ、汎用の HttpRequest で話す(#2337 の 4b — httpx を直に持っていた TaskClient を
;; 替えた)。宛先の順・切り替え・送り直しは宛先の部品(coordinator_route.hy)— 宛先の状態は組み立てが渡す入れ物(RouteCell)。
;; 出す HttpRequest に答える本物の I/O の答え手は、process の組み立ての根が外側に積む。
(defhandler remote-cluster [#^ RouteCell cell #^ RouteOptions options #^ TaskSender sender #^ float [poll-seconds 1.0] #^ float [lease-seconds 15.0]]
  (RemoteJob [program needs name environ]
    ;; 送れない値は送る前に断る(encode-program が UnsendableProgram を投げ、呼び手へ届く)。
    (val blob (encode-program program))
    (<- task str (task-submitted cell options sender blob needs name lease-seconds (or environ {})))
    (<- outcome (wait-outcome cell options sender task poll-seconds))
    (resume (settled-value outcome))))
