;;; 切り離した task の HTTP の口の純粋な判断(2026-09-25・effect は detached_model.hy)。I/O はしない。
;;;
;;;   PUT    /detached/<key>          送る(job id = key で冪等)。{program revision needs name leaseSeconds retainSeconds environ}
;;;                                   program = 先に PUT /programs/<sha> で置いた詰めた Program の sha(版は置いた時の版 — service の宣言と同じ運び方)
;;;                                   → {"key" "task" "created" "phase"}。同じ key が在れば何も作らず created = false
;;;   GET    /detached/<key>          読む(lease に触らない)→ {"key" "phase" "detail" "result" "worker"}。知らない key は phase = unknown
;;;                                   (coordinator が起きた直後の猶予の内は 503・phase = warming — detached-read)
;;;   POST   /detached/<key>/cancel   取り消す → {"key" "cancelled" "phase"}(終わっていれば cancelled = false・結果は保持)
;;;   DELETE /detached/<key>          終わった task の保持を解く → {"key" "released"}。まだ終わっていなければ 409
;;;   GET    /outbox?prefix=<頭>[&kinds=<種類,…>]  出口の行を読む(知らせ先の頭と行の種類で選ぶ — 種類の既定は ended だけ・外さない)
;;;                                   → {"entries" [{id key notify phase detail failureKind finishedMs kind}]}
;;;   DELETE /outbox/<id>             出口の行を受領して外す → {"id" "acked"}(知らない id は acked = false)
;;;
;;; 寿命の規則(置く・lease を worker が延ばす・worker の死 = lost か名乗った task の走らせ直し・保持の期限)は cluster_policy の task の節(settle-detached・gone-detached・
;;; renew-detached・absorb-detached-report)。ここは要求 1 件 → Reply(次の状態・status・本文)だけ。
(require doeff-hy.macros [defk <- val var])
(val MODULE-TAGS {:context "coordinator" :role "judgment"})
(import dataclasses [replace])
(import typing [NamedTuple])
(import doeff_hy._table [TableWrite])
(import doeff_cluster._shared.intent.protocol [ClusterTiming])
(import doeff [run])
(import doeff_cluster._shared.core.capabilities [environ-pairs])
(import doeff_cluster._shared.core.detached_rules [task-priority-refusal task-limits-refusal task-limits-of task-notify-refusal task-rerun-refusal task-memory-required-refusal task-shape-refusal task-ungated-refusal task-room-claim-refusal task-preemptible-refusal])
(import doeff_cluster._coordinator.intent.cluster_model [ClusterState TaskRecord ErrorReply DetachedSubmitted DetachedProgress DetachedUnknown DetachedWarming DetachedCancelled DetachedReleased])
(import doeff_cluster._coordinator.core.cluster_rules [format-version-refusal])
(import doeff_cluster._coordinator.intent.request_bodies [TaskBody])
(import doeff_cluster._coordinator.core.cluster_policy [DETACHED-TERMINAL TASK-MAX-OPEN end-detached rerun-id runtime-env-value-refusal task-id task-body-refusal needs-named program-versions
                                                     limits-undeclared-refusal scan-rows memory-need-refusal])
(import doeff_cluster._shared.intent.detached_model [DETACHED-DEFAULT-LEASE-SECONDS DETACHED-DEFAULT-RETAIN-SECONDS OPEN-PHASES WARMING-PHASE
                                                    DetachedEnded DetachedOutbox DetachedOutboxAcked OUTBOX-ENDED OUTBOX-QUEUED-OVERDUE OUTBOX-RERAN])

(setv DETACHED-MAX-LEASE-SECONDS 3600)
(setv DETACHED-MAX-RETAIN-SECONDS (* 30 24 3600))
(setv DETACHED-MAX-RECORDS 10000)                ; 切り離した task の行(終わって保持している物を含む)の上限
(setv DETACHED-MAX-KEY-LENGTH 200)


(defclass Reply [NamedTuple]
  "要求 1 件への答え: state = 次の状態(変えなければ受けた状態そのもの)・status = HTTP の status・body = 返す本文
   (答えの型の値 — JSON は coordinator/protocol/replies が綴る・#2614)。"
  (#^ ClusterState state)
  (#^ int status)
  (#^ (| DetachedSubmitted DetachedProgress DetachedUnknown DetachedWarming DetachedCancelled DetachedReleased DetachedOutbox DetachedOutboxAcked
         ErrorReply) body))


(defn #^ (| TaskRecord None) task-by-key [#^ ClusterState state #^ str key]
  (for [t (.rows state.tasks)]
    (when (and t.detached (= t.key key)) (return t)))
  None)


;; value は本文の欄の値そのもの(数かどうかを確かめる)。
(defn #^ (| str None) seconds-refusal [#^ str label #^ object value #^ float limit]
  "純粋: 秒の欄が 0 より大きく limit 以下の数でなければ理由の文。"
  (if (and (isinstance value #(int float)) (not (isinstance value bool)) (< 0 value (+ limit 1)))
      None
      (.format "{} は 0 より大きく {} 以下の数: {!r}" label limit value)))


(defn #^ (| str None) key-refusal [#^ str key]
  (cond
    (not key) "key(job id)が空"
    (> (len key) DETACHED-MAX-KEY-LENGTH) (.format "key(job id)は {} 文字まで" DETACHED-MAX-KEY-LENGTH)
    True None))


(defn #^ Reply submit-detached [#^ ClusterState state #^ str key #^ TaskBody body #^ int now]
  "PUT /detached/<key>: 同じ key の行が在ればそれを返す(created = false)。name・needs・実行環境・子の環境変数(environ)が違えば 409
   (同じ job id を別の仕事に使った呼び手の誤り — environ は Program の読む設定なので、違えば別の仕事)。無ければ待ちの行を作る。
   memory の要り(memoryMiB)の無い本文と、要りが needs の合うどの worker の線よりも大きい新しい task は 400 で断る
   (task-memory-required-refusal・memory-need-refusal)。測った空きの門の外を名乗る本文(ungated)は要りを名乗らず組を 1 つ以上名乗る
   (task-room-claim-refusal — 違えば 400)。場所を譲る名乗り(preemptible)は走らせ直し(rerun)も名乗る本文だけが持てる
   (task-preemptible-refusal — 違えば 400)。"
  (setv lease (if (is body.lease-seconds None) DETACHED-DEFAULT-LEASE-SECONDS body.lease-seconds)
        retain (if (is body.retain-seconds None) DETACHED-DEFAULT-RETAIN-SECONDS body.retain-seconds)
        refusal (or (format-version-refusal body.format)
                    (task-body-refusal state body)
                    (runtime-env-value-refusal body.runtime-env)
                    (key-refusal key)
                    (seconds-refusal "leaseSeconds" lease DETACHED-MAX-LEASE-SECONDS)
                    (seconds-refusal "retainSeconds" retain DETACHED-MAX-RETAIN-SECONDS)
                    (task-priority-refusal body.priority)
                    (task-limits-refusal body.limits)
                    (task-notify-refusal body.notify)
                    (task-rerun-refusal body.rerun)
                    ;; 測った空きの門の外の名乗り(2026-11-11 — 名乗らなくてよい)。
                    (task-ungated-refusal body.ungated)
                    ;; 場所を譲る名乗り(2026-11-11 — 名乗らなくてよい。名乗る task は走らせ直しも名乗る)。
                    (task-preemptible-refusal body.preemptible (is body.rerun True))
                    ;; 切り離した task は memory の要りを必須で名乗る(2026-11-02 — 要りの無い本文も断る)。門の外の task は要りを名乗らず、
                    ;; 組を 1 つ以上名乗る(2026-11-11)。組の列の形は上の task-limits-refusal が先に検める。
                    (task-room-claim-refusal (is body.ungated True) body.memory-MiB (len (or body.limits [])))
                    ;; 仕事の形(2026-11-10 — 名乗らなくてよい)。
                    (task-shape-refusal body.shape)
                    ;; 名乗った組は系の宣言に在る物だけ(値と範囲は宣言が持つ)。
                    (run (limits-undeclared-refusal state.group-limits body.limits))))
  (when refusal (return (Reply state 400 (ErrorReply :message refusal))))
  (setv needs (needs-named body.needs body.requires "切り離した task の needs")
        environ (environ-pairs (or body.environ {}))
        existing (task-by-key state key))
  (when (is-not existing None)
    (return
      (if (= #(existing.name existing.needs existing.runtime-env existing.environ)
             #(body.name needs body.runtime-env environ))
          (Reply state 200 (DetachedSubmitted :key key :id existing.id :created False :phase existing.phase))
          (Reply state 409 (ErrorReply :message (.format "key {} は別の仕事(name {!r}・needs {}・environ の名 {})に使われている"
                                        key existing.name (list existing.needs) (lfor #(k _) existing.environ k)))))))
  ;; needs の合うどの worker の線よりも大きい要りは、待っても置けないので積まずに断る(2026-11-02 — 今だけ空きの足りない task は積んで待つ)。
  (setv too-big (run (memory-need-refusal state needs body.memory-MiB)))
  (when too-big (return (Reply state 400 (ErrorReply :message too-big))))
  (setv open-count (len (lfor t (scan-rows state.tasks) :if (in t.phase OPEN-PHASES) t))
        detached-count (len (lfor t (.rows state.tasks) :if t.detached t)))
  (when (>= open-count TASK-MAX-OPEN)
    (return (Reply state 429 (ErrorReply :message (.format "終わっていない task が上限 {} 本に達している" TASK-MAX-OPEN) :open open-count))))
  (when (>= detached-count DETACHED-MAX-RECORDS)
    (return (Reply state 429 (ErrorReply :message (.format "切り離した task の行(保持中を含む)が上限 {} 本に達している — 終わった物を解放する"
                                          DETACHED-MAX-RECORDS)))))
  (setv id (task-id state)
        lease-ms (int (* 1000 lease))
        task (TaskRecord id body.name body.program body.revision
                         (program-versions state body.program)
                         needs lease-ms (+ now lease-ms) now
                         :detached True :key key :retain-ms (int (* 1000 retain))
                         :runtime-env body.runtime-env :environ environ
                         :priority (or body.priority 0) :limits (task-limits-of body.limits)
                         :notify (or body.notify "") :memory-mib body.memory-MiB :rerun (is body.rerun True)
                         :shape (or body.shape "") :ungated (is body.ungated True)
                         :preemptible (is body.preemptible True)))
  (Reply (replace state :tasks (.with-writes state.tasks #((TableWrite id task))) :next-task (+ state.next-task 1))
         200 (DetachedSubmitted :key key :id id :created True :phase task.phase)))


(defn #^ Reply detached-read [#^ ClusterState state #^ str key #^ int now #^ ClusterTiming timing]
  "GET /detached/<key> の答え(2026-09-27 — #757)。coordinator が起きてから観測が揃うまで(lease-ms の猶予 — resource_policy の
   warming と同じ)は、行の無い key を unknown と言わない: 置き場を失った coordinator は、担い手の worker の最初の heartbeat で
   走っている task を引き取る(cluster_policy.adopt-running-detached)ので、その前の unknown は呼び手に送り直させ、引き取った
   旧い task と並走させる。猶予の内の行の無い key は 503・phase warming(client は届かないと同じに扱う — DetachedUnreachable)。"
  (setv view (detached-view state key))
  (if (and (isinstance view DetachedUnknown) (< (- now state.started-ms) timing.lease-ms))
      (Reply state 503 (DetachedWarming :key key :phase WARMING-PHASE
                                        :reason "coordinator が起きた直後で、担い手の報告が揃っていない(行の無い key を知らないと言えない)"))
      (Reply state 200 view)))


(defn #^ (| DetachedProgress DetachedUnknown) detached-view [#^ ClusterState state #^ str key]
  "GET /detached/<key>: いまの phase と(終わっていれば)結果の blob。lease に触らない(呼び手の問い合わせは寿命と無関係)。"
  (setv task (task-by-key state key))
  (if (is task None)
      (DetachedUnknown :key key)
      (DetachedProgress :key key :id task.id :phase task.phase :detail task.detail :result task.result :worker task.worker
                        :failure-kind task.failure-kind :retryable task.retryable)))


(defn #^ Reply cancel-detached [#^ ClusterState state #^ str key #^ int now]
  "POST /detached/<key>/cancel: 終わっていなければ cancelled にする(担い手は次の heartbeat の返事から外れた子 process を止める)。
   終わっていれば何もしない(結果は保持)。status は常に 200。"
  (setv task (task-by-key state key))
  (cond
    (is task None) (Reply state 200 (DetachedCancelled :key key :cancelled False :phase "unknown"))
    (in task.phase DETACHED-TERMINAL) (Reply state 200 (DetachedCancelled :key key :cancelled False :phase task.phase))
    True (Reply (replace state :tasks (.with-writes state.tasks #((TableWrite task.id (end-detached task "cancelled" now "取り消された")))))
                200 (DetachedCancelled :key key :cancelled True :phase "cancelled"))))


(defn #^ Reply release-detached [#^ ClusterState state #^ str key]
  "DELETE /detached/<key>: 終わった task の行を消す(以後その key は unknown・同じ key で送り直せる)。まだ終わっていなければ 409。"
  (setv task (task-by-key state key))
  (cond
    (is task None) (Reply state 200 (DetachedReleased :key key :released False))
    (not-in task.phase DETACHED-TERMINAL)
      (Reply state 409 (ErrorReply :message (.format "key {} はまだ終わっていない({})— 先に取り消す" key task.phase)))
    True (Reply (replace state :tasks (.with-writes state.tasks #((TableWrite task.id None)))) 200
                (DetachedReleased :key key :released True))))


;; --- 終わりの出口(outbox — 2026-10-21)-----------------------------------------------------------
;; 知らせ先を名乗った切り離した task の結末を、終わりを決めた所から 1 度だけ読み手へ渡す出口(型と読み手の契約は detached_model の頭の註の出口)。
;; 積むのは coordinator の 1 歩が状態を保存する直前の 1 点(program.coordinator-step が outbox-noted を呼ぶ)— 終わりへの道は取り消し
;; (cancel-detached)・担い手の途絶(settle-detached・absorb-task-reports-into)・走らなかった(place-tasks・absorb-detached-report)・
;; 実行環境を用意できない(end-env-failed)・値か例外の報告(task-finished)と散っているが、どの道もその歩の前後の task の行の差に現れる。
;; 道ごとに積まないので、道を足しても出口へ積み忘れない。task の process は知らせに関わらない(process の外で決まる終わりも同じ 1 点を通る)。
;; 走らせ直し(cluster_policy.gone-detached)も同じ 1 点を通る: 行の id が替わるので、前の行が消えて次の試行の行が在る歩を、終わり(lost)でなく
;; 走らせ直しの行(kind reran)として積む。

;; 出口の行の保持の長さ(読み手が受領しないまま — 知らせの係が止まっていた間の結末を失わない長さ)。
(setv OUTBOX-RETAIN-MS (* 7 24 3600 1000))
;; 走らせ直しの行の id の後ろ(id = <次の試行の task の id><この後ろ> — 終わりの行の id = task の id と重ならない)。
(val RERAN-SUFFIX "/reran")


(defn #^ (| DetachedEnded None) ended-of [#^ (| TaskRecord None) before #^ (| TaskRecord None) after #^ int now]
  "純粋: 1 歩の前後の task の行 1 つから、出口へ積む行を知るため(None = 積まない)。積むのは、知らせ先を名乗った切り離した task が
   この歩で終わりへ移った時だけ — 前の行が無いか開いていて、後の行が終わりの phase(DETACHED-TERMINAL)。開いたまま行が消えた時
   (DELETE /tasks/<id>)は lost として積む(終わりを知らせずに失わない)。"
  (setv was-open (or (is before None) (not-in before.phase DETACHED-TERMINAL))
        row (if (is after None) before after))
  (cond
    (or (is row None) (not row.detached) (not row.notify) (not was-open)) None
    (is after None)
      (DetachedEnded :id row.id :key (or row.key "") :notify row.notify :phase "lost" :detail "終わりを記録する前に task の行が消された"
                     :failure-kind "" :finished-ms now)
    (in after.phase DETACHED-TERMINAL)
      (DetachedEnded :id after.id :key (or after.key "") :notify after.notify :phase after.phase :detail after.detail
                     :failure-kind after.failure-kind :finished-ms (or after.finished-ms now))
    True None))


(defn #^ (| TaskRecord None) next-attempt-of [#^ (| TaskRecord None) before #^ (get Table TaskRecord) after]
  "純粋: 1 歩の前の開いた task の行 before が走らせ直されたなら、歩の後の表 after の次の試行の行(None = 走らせ直されていない)。"
  (if (and (is-not before None) before.rerun (not-in before.phase DETACHED-TERMINAL) (is (.row after before.id) None))
      (.row after (rerun-id before))
      None))


(defn #^ (| DetachedEnded None) reran-of [#^ TaskRecord before #^ TaskRecord attempt #^ int now]
  "純粋: 走らせ直された行 before と次の試行の行 attempt から、出口へ積む走らせ直しの行を知るため(知らせ先の無い task は None)。"
  (if before.notify
      (DetachedEnded :id (+ attempt.id RERAN-SUFFIX) :key (or attempt.key "") :notify attempt.notify :phase "queued"
                     ;; 場所を譲って止めた試行(cluster_policy.preempted-detached)は、担い手は消えていないので譲った訳を書く。
                     :detail (if attempt.preempted-by
                                 (.format "優先の高い task {} に場所を譲るため worker {} で止めた — 試行 {} を最初から走らせる"
                                          attempt.preempted-by before.worker attempt.attempt)
                                 (.format "担い手の worker {} が消えた — 試行 {} を最初から走らせる" before.worker attempt.attempt))
                     :failure-kind "" :finished-ms now :kind OUTBOX-RERAN)
      None))


(defn #^ ClusterState outbox-noted [#^ ClusterState before #^ ClusterState after #^ int now]
  "1 歩の前後の状態 before・after から、この歩で終わりへ移った知らせ先つきの task と走らせ直された task を出口へ 1 行ずつ積み、出口の保持の
   期限(OUTBOX-RETAIN-MS)を過ぎた行を外すため(節の頭の註 — 終わりへの移りが通る 1 点)。同じ id の行が出口に在れば積み直さない。
   終わった task・走らせ直された task の待ちの行(queued-overdue)がまだ受領されずに在れば外す(待ちの知らせは古い — 終わりの行か走らせ直しの行が
   代わりに知らせる)。変わらなければ after をそのまま返す。"
  (setv changed (.changed-keys before.tasks after.tasks)
        reruns (tuple (gfor key changed
                            :setv attempt (next-attempt-of (.row before.tasks key) after.tasks)
                            :if (is-not attempt None)
                            #((.row before.tasks key) attempt)))
        replaced (frozenset (gfor #(row _) reruns row.id))
        endings (tuple (gfor key changed
                             :if (not-in key replaced)
                             :setv ended (ended-of (.row before.tasks key) (.row after.tasks key) now)
                             :if (is-not ended None)
                             ended))
        rerans (tuple (gfor #(row attempt) reruns
                            :setv reran (reran-of row attempt now)
                            :if (is-not reran None)
                            reran))
        noted (tuple (gfor ended (+ endings rerans) :if (is (.row after.outbox ended.id) None) (TableWrite ended.id ended)))
        stale (tuple (gfor id (+ (tuple (gfor ended endings ended.id)) (tuple replaced)) :setv overdue (+ id QUEUED-OVERDUE-SUFFIX)
                           :if (is-not (.row after.outbox overdue) None)
                           (TableWrite overdue None)))
        expired (tuple (gfor e (.rows after.outbox) :if (< (+ e.finished-ms OUTBOX-RETAIN-MS) now) (TableWrite e.id None))))
  (if (or noted stale expired)
      (replace after :outbox (.with-writes after.outbox (+ noted stale expired)))
      after))


;; --- queued の期限超えの知らせ(2026-10-29)------------------------------------------------------------
;; 知らせ先を名乗った切り離した task が queued のまま ClusterTiming.queued-notice-ms を越えたら、出口へ待ちの行(kind queued-overdue)を
;; task ごとに 1 度だけ積む(型の註は detached_model の行の種類)。積む歩で task の行に印 queued-noticed-ms を置くので、待ち続けても・読み手が
;; 受領して行を外しても積み直さない。終わりの行と同じく保存の直前の 1 点(program.coordinator-step)で、時刻だけを見て決める(待ちは
;; 前後の差に現れない — 期限を越えた歩の now で見る)。

;; 待ちの行の id の後ろ(id = <task の id><この後ろ> — 終わりの行の id = task の id と重ならない)。
(val QUEUED-OVERDUE-SUFFIX "/queued-overdue")


(defk overdue-of [task now timing]
  {:pre [(: task TaskRecord) (: now int) (: timing ClusterTiming)] :post [(: % (| DetachedEnded None))]
   :tags {:context "coordinator" :role "judgment"}}
  "task の行 1 つが、この歩で待ちの行を積む物かを知るため(None = 積まない)。積むのは、知らせ先を名乗った切り離した task が queued のまま
   置いた刻から timing.queued-notice-ms 以上待ち、まだ積んでいない(印 queued-noticed-ms が無い)時だけ。detail は coordinator が task に
   書いた置かれない理由の文。"
  (if (and task.detached task.notify (= task.phase "queued") (is task.queued-noticed-ms None)
           (>= (- now task.submitted-ms) timing.queued-notice-ms))
      (DetachedEnded :id (+ task.id QUEUED-OVERDUE-SUFFIX) :key (or task.key "") :notify task.notify :phase task.phase :detail task.detail
                     :failure-kind "" :finished-ms now :kind OUTBOX-QUEUED-OVERDUE)
      None))


(defk queued-noticed [state now timing]
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "judgment"}}
  "状態 state の queued の task のうち、この歩で期限を越えた物ごとに出口へ待ちの行を 1 つ積み、task の行に印 queued-noticed-ms = now を
   置くため(節の頭の註)。変わらなければ state をそのまま返す。"
  (var marks #())
  (var rows #())
  (for [task (.rows state.tasks)]
    (<- row (| DetachedEnded None) (overdue-of task now timing))
    (when (is-not row None)
      (:= marks (+ marks #((TableWrite task.id (replace task :queued-noticed-ms now)))))
      (:= rows (+ rows #((TableWrite row.id row))))))
  (if rows
      (replace state :tasks (.with-writes state.tasks marks) :outbox (.with-writes state.outbox rows))
      state))


(defn #^ Reply outbox-read [#^ ClusterState state #^ str prefix #^ (get frozenset str) [kinds (frozenset #(OUTBOX-ENDED))]]
  "GET /outbox?prefix=<頭>&kinds=<種類,…>: 知らせ先が prefix で始まり、種類が kinds に在る出口の行(刻・id の順)。kinds を名乗らない
   読み手には終わりの行だけを返す(detached_model の行の種類)。行を外さない(外すのは受領だけ)。"
  (Reply state 200 (DetachedOutbox :entries (tuple (sorted (gfor e (.rows state.outbox) :if (and (.startswith e.notify prefix) (in e.kind kinds)) e)
                                                           :key (fn [e] #(e.finished-ms e.id)))))))


(defn #^ Reply outbox-ack [#^ ClusterState state #^ str id]
  "DELETE /outbox/<id>: 知らせ終えた出口の行を外す(受領 — 何度送っても同じ意味)。知らない id(受領済み・期限で外れた)は acked = False。"
  (if (is (.row state.outbox id) None)
      (Reply state 200 (DetachedOutboxAcked :id id :acked False))
      (Reply (replace state :outbox (.with-writes state.outbox #((TableWrite id None)))) 200 (DetachedOutboxAcked :id id :acked True))))
