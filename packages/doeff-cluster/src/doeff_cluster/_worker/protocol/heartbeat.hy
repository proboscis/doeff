;;; worker が coordinator へ送る heartbeat の本文の形 — 実行環境の root の名乗り・生存と能力と版・状態の行と結果(本番の coordinator への口 と
;;; 手元の sim-cluster の宿 sim/local が同じ関数で作る — 本文を写さない)。handlers.hy から分けた(#2026)。判断は worker/core/heartbeat_rules。
(require doeff-hy.macros [defk deff <- val var])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import doeff_cluster.shared.intent.protocol [PROTOCOL-FORMAT])
(import doeff_cluster._worker.intent.worker_model [CodeState CodeView JobStatus])
(import doeff_cluster._worker.core.worker_rules [ENV-KEY-PREFIX])
(import doeff_cluster._worker.core.heartbeat_rules [finished-task-id])
(import doeff_cluster._worker.core.room_rules [announced-provides])


(deff env-report [#^ (get tuple #(CodeView ...)) views #^ str capacity #^ (get frozenset str) unmeasured]  ; defk にできない: worker の root の言い換え(env-host)と sim の宿が同じ形を作る純粋な判断
  {:pre [(: views (get tuple #(CodeView ...))) (: capacity str) (: unmeasured (get frozenset str))] :post [(: % (get dict #(str object)))] :tags {:context "worker" :role "protocol"}}
  "実行環境の root の観測(CodeView — 鍵が env- で始まる物だけを読む)と disk の条件を、heartbeat で名乗る root の姿(準備済み・準備中・
   失敗のキーを env- を外して・disk の条件)にするため。unmeasured = 先の組みを memory を測らずに始めた root のキー(#3748 — 観測に在る root だけを
   memoryUnmeasured に名乗る)。"
  (let [roots (lfor v views :if (.startswith v.revision ENV-KEY-PREFIX) v)
        bare (fn [k] (cut k (len ENV-KEY-PREFIX) None))]
    {"ready" (sorted (gfor v roots :if (= v.state CodeState.READY) (bare v.revision)))
     "preparing" (sorted (gfor v roots :if (= v.state CodeState.PREPARING) (bare v.revision)))
     "failed" (lfor v roots :if (and (= v.state CodeState.FAILED) (is-not v.failure None))
                    {"key" (bare v.revision) "kind" v.failure.kind.value "detail" v.failure.detail
                     "retryable" v.failure.retryable})
     "memoryUnmeasured" (sorted (gfor v roots :if (in v.revision unmeasured) (bare v.revision)))
     "capacity" capacity}))


(deff env-heartbeat-part [#^ (get dict #(str object)) report #^ str platform]  ; defk にできない: worker の coordinator への口(worker/protocol/coordinator_link)と sim の宿が同じ形を作る純粋な判断
  {:pre [(: report (get dict #(str object))) (: platform str)] :post [(: % (get dict #(str object)))] :tags {:context "worker" :role "protocol"}}
  "root の姿(env-report)を heartbeat の本文に足す欄(platform・envs・envCapacity)にするため。"
  {"platform" platform
   "envs" {"ready" (get report "ready") "preparing" (get report "preparing") "failed" (get report "failed")
           "memoryUnmeasured" (get report "memoryUnmeasured")}
   "envCapacity" (get report "capacity")})


(defk heartbeat-body [* name provides exclusive node task-reserve background-room versions statuses endpoint boot boot-at tools kept [stopping False]
                      [overlaps-elder False] [doeff-commit ""] [pools #()] [memory-mib None]]
  {:pre [(: name str) (: provides (get tuple #(str ...))) (: exclusive (get tuple #(str ...))) (: node str)
         (: task-reserve int) (: background-room int)
         (: versions (get dict #(str str))) (: statuses (get list (get dict #(str object)))) (: endpoint str) (: boot str) (: boot-at int)
         (: tools (get dict #(str object))) (: kept (get tuple #(str ...))) (: stopping bool)
         (: overlaps-elder bool) (: doeff-commit str) (: pools (get tuple #(str ...))) (: memory-mib (| int None))]
   :post [(: % (get dict #(str object)))]
   :tags {:context "worker" :role "protocol"}}
  "POST /heartbeat の本文(生存・能力・版・状態の報告・世代・持っている印・止まり始め)を作るため。実行環境の root の名乗り(env-body)は
   本番の worker だけが足す。task-reserve = task のために空けておく数・background-room = 背景の仕事を同時に置ける数(どちらも必ず書く — coordinator の task の
   空きは予約 + 背景の枠 − 置いた task。capacity は送らない — card ki-2acb63aec0fd)。
   kept = 途絶しても動かし続けてよい印を今持っている job の名(worker_policy.keep-marks-held — #2804)。欄を毎回
   書く(空でも)— coordinator は欄の在る worker だけを「印を知る worker」と数え、欄の無い本文(古い worker)には印の約束を持たない。
   stopping = この世代が止まり始めた(coordinator はこの世代へ新しく置かない — drain の頼みを通らない止めの名乗り・#2819)。
   overlaps-elder = この世代は同じ名の前の世代がまだ走っている間に起きた(起動の script が渡す — coordinator は新しい世代の名乗りで
   前の世代の process の lease を外さない・AL-D2e)。真の時だけ欄を書く(偽の本文は前と同じ形)。doeff-commit = この process の doeff の
   版(起動の script が自己起動した commit の WORKER_DOEFF_COMMIT・card ki-6e83e03d146d — coordinator は Worker の spec.doeffCommit に
   出す)。空でない時だけ欄を書く(版を持たない worker の本文は前と同じ形)。pools = 枠の file の行の置ける組の印(card ki-ced3c45980a5 —
   provides の後ろに足して名乗る。起動の引数の能力との和で、coordinator は heartbeat ごとに Worker の行の provides を書き直すので、次の
   heartbeat から置き方に効く)。memory-mib = 常駐の job の memory の予算(MiB — 名乗る時だけ欄 memoryMiB を書く。coordinator は置いた job の
   settle の和 + 新しい job の settle がこの値以下の時だけ置き、名乗らない worker は memory で縛らない)。"
  (<- named (get tuple #(str ...)) (announced-provides provides pools))
  {"name" name "provides" (list named) "exclusive" (list exclusive) "node" node
   "taskReserve" task-reserve "backgroundRoom" background-room
   "versions" versions
   "statuses" statuses "endpoint" endpoint "boot" boot "bootAt" boot-at
   "format" PROTOCOL-FORMAT
   "tools" tools
   "keptWhenCutOff" (list kept)
   "stopping" stopping
   #** (if overlaps-elder {"overlapsElder" True} {})
   #** (if doeff-commit {"doeffCommit" doeff-commit} {})
   #** (if (is memory-mib None) {} {"memoryMiB" memory-mib})})


(defk status-report [statuses task-echo results]
  {:pre [(: statuses (get tuple #(JobStatus ...))) (: task-echo (get dict #(str (get dict #(str object))))) (: results (get dict #(str (| str None))))]
   :post [(: % (get list (get dict #(str object))))] :tags {:context "worker" :role "protocol" :spells "json"}}
  "状態の行の列を heartbeat の statuses にするため。終わった task には結果(results の task の id → 詰めた結果の文字列 か None =
   結果なし)を、切り離した task には置かれた時の返事の行(task-echo の id → 行 — 欄 task)を添える。"
  (<- rows (get tuple #((get dict #(str object)) ...)) (status-rows-json statuses))
  (lfor #(s row) (zip statuses rows)
    :setv echo (if (.startswith s.name "task/") (.get task-echo (cut s.name 5 None)) None)
    :setv row (if (is echo None) row (| row {"task" echo}))
    :setv done (finished-task-id s)
    (if (is done None) row (| row {"result" (.get results done)}))))


(defk status-rows-json [statuses]
  {:pre [(: statuses (get tuple #(JobStatus ...)))] :post [(: % (get tuple #((get dict #(str object)) ...)))] :tags {:context "worker" :role "protocol" :spells "json"}}
  "状態の行の列を、行ごとの JSON の形(status-row)の列に綴るため(heartbeat の statuses と状態の file の jobs が同じ綴りを使う)。"
  (var rows #())
  (for [s statuses]
    (<- row (get dict #(str object)) (status-row s))
    (:= rows (+ rows #(row))))
  rows)


(defk status-row [s]
  {:pre [(: s JobStatus)] :post [(: % (get dict #(str object)))] :tags {:context "worker" :role "protocol" :spells "json"}}
  "状態の行 1 つを、heartbeat と状態の file が載せる JSON の形に綴るため。"
  {"name" s.name "phase" s.phase.value "desiredRevision" s.desired-revision
   "runningRevision" s.running-revision "pid" s.pid "attempts" s.attempts "detail" s.detail
   ;; 落ちた事実(#3477): 続けて落ちた回数と最後の終わりの code と時刻。
   "failures" s.failures "lastExitCode" s.last-exit-code "lastExitAtMs" s.last-exit-at-ms
   ;; 動いている process の世代(coordinator の readiness と計器はこれと一致する報告だけを数える)。
   "instance" s.instance "specHash" s.spec-hash "placement" s.placement "retiredFrom" s.retired-from
   ;; 実行環境の準備の失敗(ENV-FAILED の行だけ): coordinator が置き直すか・答えの型を決める。
   #** (if (is s.failure None) {} {"failureKind" s.failure.kind.value "retryable" s.failure.retryable})
   ;; 入口の検めの姿(検めが通っていない間だけ — 2026-09-27): 状態・今の検めの経過の秒・回数・直前の失敗の理由。
   #** (if (is s.probe None) {} {"probe" {"state" s.probe.state "elapsedSeconds" s.probe.elapsed-seconds
                                          "attempts" s.probe.attempts "lastFailure" s.probe.last-failure}})})
