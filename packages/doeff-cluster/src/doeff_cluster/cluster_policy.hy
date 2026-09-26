;;; coordinator の純粋な判断。宣言された job・task・worker の生存・今の割り当て・時刻から、次の割り当てを導く。
;;; HTTP の要求 1 件への返事も、状態と要求と時刻から (次の状態 status 本文) を返す純粋な関数にする。I/O はしない。
;;; 割り当ては安定させる: 担い手が移し替えの期限内に生きていれば動かさない。
(import dataclasses [replace asdict])
(import json)

(import .worker_model [JobSpec])
(import .cluster_model [ClusterJob WorkerInfo Placement ClusterTiming ClusterState TaskRecord Request Drain
                        requirements-of component-versions-of task-record-to-json task-record-from-json ACCEPTED-FORMATS format-refusal])
(import .semaphore_model [SEMAPHORE-PREFIX lease-op semaphore-write-refusal semaphore-key])
(import .base_follow_policy [FULL-SHA])
(import doeff [run])
(import .runtime_env_model [runtime-env-of-json RuntimeEnvInvalid])

(setv JOB-ENTRY "doeff_cluster.job_entry")
(setv MAX-EVENTS 200)

;; --- 盤と task の容量(2026-09-25) ---
;; 盤は coordinator の状態の一部で、全部が memory に載り、まとめ直し(snapshot)のたびに全部を書き直す。置き場の volume は 256 MiB。
;; 上限を越える書きは 507 で断る(消す書き・小さくする書きは通す)。上限の 8 割を越えたら計器 doeff_worker_board_bytes から alert。
;; 実測(2026-09-25 01:5x): 292 行・3.9 MB・最大の行 132 KB(大きな shadow の区画)。
(setv BOARD-MAX-VALUE-BYTES (* 1 1024 1024))    ; 1 行の値
(setv BOARD-MAX-ROWS 20000)                       ; 行の数
(setv BOARD-MAX-BYTES (* 64 1024 1024))           ; 値の合計
(setv BOARD-MAX-TTL-SECONDS (* 30 24 3600))       ; 期限つきの行の期限の上限
;; task: 呼び手が問い合わせを止めると lease-ms の後に落ちる(終わった task もそれで回収する)。lease は 1 時間まで・終わっていない
;; task は 2000 本まで(越えたら 429)。
(setv TASK-MAX-LEASE-SECONDS 3600)
(setv TASK-MAX-OPEN 2000)
;; 沈黙した worker を忘れるまで(置き先と task を持たない worker だけ)。Mac は眠り・持ち出しで数日沈黙するので 7 日。
(setv WORKER-FORGET-MS (* 7 24 3600 1000))


;; --- 宣言の読み書き(JSON ⇄ 型) ----------------------------------------------------

(defn #^ str declared-revision [#^ dict item]
  "宣言 1 行の定義の版(spec.revision)。版の組(2026-09-25): baseFrom を持つ宣言は、overlay が在れば overlay(明示の上書き)、
   無ければ base と同じ commit(重ねない木)。base をまだ観測していない間は宣言の revision のまま。baseFrom の無い宣言は revision。"
  (cond
    (.get item "overlay") (get item "overlay")
    (and (.get item "baseFrom") (.get item "base")) (get item "base")
    True (get item "revision")))

(defn #^ JobSpec spec-of-declaration [#^ dict item]
  "宣言 1 行 → worker が起動する形。run.kind = service なら job_entry の service 入口、無ければ entry と args をそのまま。"
  (setv run (.get item "run") revision (declared-revision item))
  (cond
    (is run None)
      (JobSpec (get item "name") (get item "entry") (tuple (.get item "args" [])) revision
               :base (.get item "base") :handoff (= (.get item "update") "handoff"))
    (= (.get run "kind") "service")
      (JobSpec (get item "name") JOB-ENTRY
               #("service" "--factory" (get run "factory") "--env" (get run "env")
                 "--config" (json.dumps (.get run "config" {}) :sort-keys True :ensure-ascii False))
               revision
               :base (.get item "base") :handoff (= (.get item "update") "handoff"))
    True (raise (ValueError (+ "知らない run.kind: " (repr (.get run "kind")))))))


(defn #^ ClusterJob job-from-json [#^ dict item]
  (setv replicas (.get item "replicas" 1) readiness (.get item "readiness"))
  (when (not-in replicas #(0 1))
    (raise (ValueError (.format "replicas は 0 か 1(Service は 1 つだけ動かす): {!r}" replicas))))
  (when (and (is-not readiness None)
             (not (and (isinstance readiness dict) (isinstance (.get readiness "windowSeconds") #(int float))
                       (> (get readiness "windowSeconds") 0))))
    (raise (ValueError (.format "readiness は windowSeconds(正の数)を持つ dict: {!r}" readiness))))
  (setv update (.get item "update" "recreate") base-from (.get item "baseFrom"))
  (when (not-in update #("recreate" "handoff"))
    (raise (ValueError (.format "update は recreate か handoff: {!r}" update))))
  (when (and (is-not base-from None)
             (not (and (isinstance base-from dict) (= (.get base-from "kind") "Deployment")
                       (isinstance (.get base-from "namespace") str) (isinstance (.get base-from "name") str)
                       (isinstance (.get base-from "container" "") str))))
    (raise (ValueError (.format "baseFrom は {{kind: Deployment, namespace, name, container?}}: {!r}" base-from))))
  (setv overlay (.get item "overlay"))
  (when (and (is-not overlay None) (not (and (isinstance overlay str) (FULL-SHA.match overlay))))
    (raise (ValueError (.format "overlay は 40 桁の commit: {!r}" overlay))))
  (when (and (is-not overlay None) (is base-from None))
    (raise (ValueError "overlay は baseFrom を持つ Service だけが使う(baseFrom の無い宣言は revision がそのまま定義の版)")))
  (ClusterJob (spec-of-declaration item)
              (tuple (sorted (.items (.get item "requires" {}))))
              (.get item "pin")
              (.get item "run")
              replicas
              readiness
              (.get item "owner")
              update
              base-from
              overlay))


(defn #^ dict job-to-json [#^ ClusterJob job]
  (setv base {"name" job.spec.name "revision" job.spec.revision
              "requires" (dict job.requires) "pin" job.pin
              "replicas" job.replicas "readiness" job.readiness "owner" job.owner})
  ;; 版の追随と入れ替えの欄は、使う宣言にだけ書く(使わない宣言の spec の形・版は以前と同じ)。
  (setv extra (| (if (= job.update "recreate") {} {"update" job.update})
                 (if (is job.base-from None) {} {"baseFrom" job.base-from})
                 (if (is job.spec.base None) {} {"base" job.spec.base})
                 (if (is job.overlay None) {} {"overlay" job.overlay})))
  (if (is job.run None)
      (| base extra {"entry" job.spec.entry "args" (list job.spec.args)})
      (| base extra {"run" job.run})))


(defn #^ dict spec-json [#^ JobSpec spec]
  (| {"name" spec.name "entry" spec.entry "args" (list spec.args) "revision" spec.revision "once" spec.once}
     (if (is spec.placement None) {} {"placement" spec.placement})
     (if (is spec.base None) {} {"base" spec.base})
     ;; 入れ替え(handoff)の job だけ: 形と、coordinator が Ready と数えている process の世代の名(worker は旧をこの後に止める)。
     (if spec.handoff {"handoff" True "readyInstance" spec.ready-instance} {})))


(defn #^ dict task-summary [#^ TaskRecord task]
  "状態表示と保存に使う形(blob と結果は大きいので保存の時だけ別に足す)。切り離した task だけ呼び手の job id を足す
   (RemoteJob の task の形は以前と同じ)。"
  (| {"id" task.id "name" task.name "env" task.env "revision" task.revision "phase" task.phase
      "worker" task.worker "detail" task.detail "submittedMs" task.submitted-ms
      "startedMs" task.started-ms "finishedMs" task.finished-ms "leaseUntilMs" task.lease-until-ms}
     (if task.detached {"detached" True "key" task.key} {})))


(defn #^ dict state-to-json [#^ ClusterState state]
  "資源の状態の保存の形。盤は入れない(盤は行ごとに別の file — SaveBoardRow)。"
  {"formatVersion" 2
   "jobs" (lfor j state.jobs (job-to-json j))
   "placements" (dfor #(k v) (.items state.placements) k (asdict v))
   "workers" (lfor w (.values state.workers)
                   {"name" w.name "labels" (dict w.labels) "capacity" w.capacity "versions" (dict w.versions)})
   "tasks" (lfor t (.values state.tasks) (task-record-to-json t))
   "nextTask" state.next-task
   "meta" state.meta
   "revision" state.revision
   "audit" (list state.audit)
   "auditSeq" state.audit-seq
   "rollouts" state.rollouts
   "drains" (dfor #(k v) (.items state.drains) k (asdict v))
   "surges" (dfor #(k v) (.items state.surges) k (asdict v))})


(defn #^ ClusterState state-from-json [#^ dict data #^ int now [board None] [board-versions None]]
  "保存した状態から作り直す。知っていた worker は全員「いま生きていた」とみなす。生存を捨てると、最初に heartbeat を
   送った worker へ全 job が移り、元の担い手がまだ動いていれば二重に動く(実測 2026-09-23)。戻らない worker の job は、
   この時点から移し替えの期限が過ぎた後に移る(その頃には自分で止まっている)。
   board = 行ごとの file から読んだ盤(渡さなければ、旧い形の file に在った盤)。資源の版の欄が無い旧い形の file は、
   読んだ後の最初の書きで resource_policy.stamp が版を振る(送り手 = 移し替え)。"
  (ClusterState
    :jobs (tuple (gfor j (get data "jobs") (job-from-json j)))
    :workers (dfor w (.get data "workers" [])
                   (get w "name")
                   (WorkerInfo (get w "name") (tuple (sorted (.items (get w "labels")))) (get w "capacity") now
                               (component-versions-of (.get w "versions" {}))))
    ;; 改名の前の file は置き先を旧い名の欄に持つ(durable_kv.LEGACY-PLACEMENT と同じ改名)。両方を読み、新しい欄が勝つ。
    :placements (dfor #(k v) (.items (| (.get data "assignments" {}) (.get data "placements" {}))) k (Placement #** v))
    :tasks (dfor t (.get data "tasks" [])
                 (get t "id")
                 (task-record-from-json t))
    :next-task (.get data "nextTask" 1)
    :board (if (is board None) (.get data "board" {}) board)
    :board-versions (or board-versions (dfor k (.get data "board" {}) k 1))
    :board-sizes (dfor #(k v) (.items (if (is board None) (.get data "board" {}) board)) k (value-size v))
    :meta (.get data "meta" {})
    :revision (.get data "revision" 0)
    :audit (tuple (.get data "audit" []))
    :audit-seq (.get data "auditSeq" 0)
    :rollouts (.get data "rollouts" {})
    ;; drain の欄(2026-09-25)は、それより前の file には無い(空として読む)。
    :drains (dfor #(k v) (.items (.get data "drains" {})) k (Drain #** v))
    :surges (dfor #(k v) (.items (.get data "surges" {})) k (Placement #** v))
    :started-ms now))


;; --- 割り当て ------------------------------------------------------------------------

(defn #^ bool alive [#^ int now #^ WorkerInfo worker #^ int window-ms]
  (<= (- now worker.last-seen-ms) window-ms))


(defn #^ bool labels-satisfy [#^ tuple requires #^ WorkerInfo worker]
  (setv labels (dict worker.labels))
  (all (gfor #(k v) requires (= (.get labels k) v))))


;; 専用の印(k8s の taint に当たる)。worker の label `dedicated=<k>=<v>` は「label <k>=<v> を明示的に求める job / task だけを
;; 置く」の意味。例: Mac の worker は label `role=agent` と `dedicated=role=agent` を持ち、`requires: {role: agent}` の
;; agent の仕事だけを受ける。印を求めない一般の job は置かない。
(setv DEDICATED-KEY "dedicated")


(defn #^ tuple dedicated-to [#^ WorkerInfo worker]
  "worker の専用の印が求める label の組(無ければ空)。"
  (setv value (.get (dict worker.labels) DEDICATED-KEY))
  (if (and value (in "=" value))
      (tuple (.split value "=" 1))
      #()))


(defn #^ bool tolerates [#^ tuple requires #^ WorkerInfo worker]
  "requires が worker の専用の印を明示的に求めているか(印の無い worker には何でも置ける)。"
  (setv mark (dedicated-to worker))
  (or (not mark) (in mark (tuple (gfor #(k v) requires #(k v))))))


(defn #^ bool eligible [#^ ClusterJob job #^ WorkerInfo worker]
  (and (or (is job.pin None) (= job.pin worker.name))
       (labels-satisfy job.requires worker)
       (tolerates job.requires worker)))


(setv LIVE-PHASES #{"preparing" "starting" "backoff" "running" "stopping" "stop-unconfirmed"})


(defn #^ bool still-live-somewhere [#^ int now #^ ClusterState state #^ str name #^ ClusterTiming timing]
  "生きている worker の最新の報告に、その job がまだ動いている形で載っているか。載っている間は他へ置かない
   (動いている担い手から移す時、元の担い手が止め終えるまで新しい担い手を起動しない = 同じ job を 2 つ動かさない)。"
  (any (gfor #(wname st) (.items state.statuses)
             :setv w (.get state.workers wname)
             (and (is-not w None) (alive now w timing.lease-ms)
                  ;; 入れ替えで退いた process(行の名は <名>#retired-<世代>・retiredFrom = 名)も、その job がまだ動いていると数える。
                  (any (gfor row (.get st "jobs" [])
                             (and (or (= (.get row "name") name) (= (.get row "retiredFrom") name))
                                  (in (.get row "phase") LIVE-PHASES))))))))


;; --- drain(2026-09-25) -----------------------------------------------------------------
;; worker の Pod を入れ替える前に、その上の書き手を別の worker へ移す。drain 中の worker には新しい置き先(job・task)を割り当てない。
;; 入れ替え(handoff)の Service の並べた置き先(surge)と付け替えは drain_policy.advance-drains(readiness を読む)。ここは置き先の
;; 判断が drain を知る部分だけ。

(defn #^ frozenset draining-workers [#^ ClusterState state #^ int now]
  "期限の内の drain を持つ worker の名。"
  (frozenset (gfor #(name d) (.items state.drains) :if (> d.until-ms now) name)))


(defn #^ bool can-take [#^ int now #^ ClusterState state #^ ClusterJob job #^ WorkerInfo w #^ dict load #^ ClusterTiming timing
                        #^ (| frozenset None) [draining None]]
  "job を新しく置ける worker か(生きている・条件を満たす・drain 中でない・空きがある)。"
  (and (alive now w timing.lease-ms) (eligible job w)
       (not-in w.name (if (is draining None) (draining-workers state now) draining))
       (< (.get load w.name 0) w.capacity)))


(defn #^ ClusterState absorb-boot [#^ ClusterState state #^ str name #^ (| str None) boot]
  "heartbeat の worker の process の世代を drain へ写す: 頼まれた時の世代と違う世代(Pod を作り直した後の worker)が来たら drain を解く。
   世代を知らずに頼まれた drain(読み直しの直後など)は、最初に来た世代を持つ。"
  (setv d (.get state.drains name))
  (cond
    (or (is d None) (is boot None)) state
    (is d.boot None) (replace state :drains (| state.drains {name (replace d :boot boot)}))
    (!= d.boot boot) (replace state :drains (dfor #(k v) (.items state.drains) :if (!= k name) k v))
    True state))


(defn #^ dict load-of [#^ ClusterState state #^ dict placements]
  "worker ごとの担っている数(job・並べた置き先(surge)・実行中の task)。"
  (setv load (dfor name state.workers name 0))
  (for [a (+ (list (.values placements)) (list (.values state.surges)))]
    (when (in a.worker load) (+= (get load a.worker) 1)))
  (for [t (.values state.tasks)]
    (when (and (= t.phase "assigned") (in t.worker load)) (+= (get load t.worker) 1)))
  load)


(defn #^ tuple active-jobs [#^ ClusterState state]
  "置く対象の宣言(replicas 0 の Service は宣言に残るが置かない — 担い手は次の heartbeat で止める)。"
  (tuple (gfor job state.jobs :if (> job.replicas 0) job)))


(defn #^ dict place-jobs [#^ int now #^ ClusterState state #^ ClusterTiming timing]
  (setv jobs (active-jobs state)
        names (sfor job jobs job.spec.name)
        draining (draining-workers state now)
        kept {})
  ;; 1. 続けてよい割り当てを残す(宣言に在り・replicas 1・条件を満たし・担い手が移し替えの期限内)。
  ;;    drain 中の worker の上の入れ替えでない job は、他に置ける worker が在る時だけ外す(止めて移す)。置ける先が無ければ残す
  ;;    (空白を作らない)。入れ替えの job は残す(drain_policy が並べてから付け替える)。
  (setv current-load (load-of state state.placements))
  (for [job jobs]
    (setv current (.get state.placements job.spec.name))
    (when (is-not current None)
      (setv worker (.get state.workers current.worker))
      (when (and (is-not worker None) (eligible job worker)
                 (alive now worker timing.reassign-after-ms)
                 (not (and (in current.worker draining) (not job.spec.handoff)
                           (any (gfor w (.values state.workers)
                                      (and (!= w.name current.worker)
                                           (can-take now state job w current-load timing draining)))))))
        (setv (get kept job.spec.name) current))))
  ;; 2. 担い手の無い job を、生きている worker のうち空きの多い順へ置く(同点は名前順)。
  ;;    どこかの生きた worker がまだその job を動かしている間は置かない(条件が変わって生きた担い手から外した job は、
  ;;    元の担い手が止め終えたと報告してから置く)。drain 中の worker には置かない。
  ;;    drain で並べた置き先(surge)を持つ job は、その置き先へ付け替える(そこで動いている process をそのまま使う — 旧い担い手が
  ;;    沈黙して外れた時)。
  (setv load (load-of state kept))
  (setv result (dict kept))
  (for [job (sorted jobs :key (fn [j] j.spec.name))]
    (when (in job.spec.name result) (continue))
    (setv surge (.get state.surges job.spec.name)
          surge-worker (if surge (.get state.workers surge.worker) None))
    (when (and (is-not surge-worker None) (alive now surge-worker timing.lease-ms) (eligible job surge-worker))
      (setv (get result job.spec.name) surge)
      (continue))
    (when (still-live-somewhere now state job.spec.name timing) (continue))
    (setv candidates (sorted
      (lfor w (.values state.workers)
            :if (can-take now state job w load timing draining)
            w)
      :key (fn [w] #((get load w.name) w.name))))
    (when candidates
      (setv chosen (get candidates 0)
            previous (.get state.placements job.spec.name))
      (+= (get load chosen.name) 1)
      (setv (get result job.spec.name)
        (Placement job.spec.name chosen.name (if previous (+ previous.generation 1) 1) now))))
  ;; 宣言から消えた job の割り当ては残さない(担い手は次の heartbeat で止める)。
  (dfor #(name a) (.items result) :if (in name names) name a))


(defn #^ tuple jobs-for [#^ ClusterState state #^ str worker [ready-instances None]]
  "worker に割り当てた job の spec。割り当ての世代(placement)を載せる — worker は起こす process へ渡し、process は readiness と
   計器の報告に載せる(比べない欄なので、世代だけが変わっても worker は process を起こし直さない)。
   ready-instances = Service の名 → Ready と数えている process の世代の名(入れ替えの job に載せる・api_policy が求める)。
   drain で並べた置き先(surge)がこの worker に在る job も載せる(新しい process を起こし、standby で lease を待つ)。"
  (tuple (gfor job state.jobs
               :setv placed (.get state.placements job.spec.name)
               :setv surge (.get state.surges job.spec.name)
               :setv a (cond (and placed (= placed.worker worker)) placed
                             (and surge (= surge.worker worker) (> job.replicas 0)) surge
                             True None)
               :if a
               (replace job.spec :placement a.generation
                        :ready-instance (.get (or ready-instances {}) job.spec.name)))))


;; --- task ----------------------------------------------------------------------------

(defn #^ bool tools-satisfy [#^ TaskRecord task #^ WorkerInfo worker]
  "実行環境の宣言の道具(tools)を worker が全部名乗っているか。版が空の要求は名だけ、版の在る要求は同じ版を求める。"
  (setv named (dict worker.tools))
  (all (gfor tool (.get (or task.runtime-env {}) "tools" [])
             (and (in (get tool "name") named)
                  (or (not (.get tool "version" "")) (= (.get tool "version") (get named (get tool "name"))))))))


(defn #^ bool can-run-task [#^ TaskRecord task #^ WorkerInfo worker]
  "版が同じ worker にだけ送る(cloudpickle は版をまたいで復元できる保証が無い)。実行環境の task は worker の版と比べない —
   子 process は worker の venv ではなく env の root で走り、版の突き合わせは子 process が env の版と行う。準備に一時の失敗をした
   worker(avoid)には置き直さない。"
  (and (or (is-not task.runtime-env None) (= task.versions worker.versions))
       (labels-satisfy task.requires worker) (tolerates task.requires worker)
       (not-in worker.name task.avoid)
       (tools-satisfy task worker)))


(defn #^ str versions-note [#^ TaskRecord task #^ ClusterState state #^ int now #^ ClusterTiming timing]
  (setv seen (lfor w (sorted (.values state.workers) :key (fn [w] w.name))
                   :if (alive now w timing.lease-ms)
                   (.format "{}({})" w.name
                            (.join "・" (lfor #(k v) w.versions :if (!= v (.get (dict task.versions) k))
                                              (.format "{}={}" k v))))))
  (.format "版と label(専用の印を含む)が合う worker が無い。求める label {}・送り手の版 {}。生きている worker と版の違う所: {}"
           (dict task.requires) (dict task.versions) (or (.join " / " seen) "(生きている worker が無い)")))


(defn #^ dict unplaced-jobs [#^ int now #^ ClusterState state #^ ClusterTiming timing]
  "担い手の無い job と、その理由(状態の表示用)。"
  (dfor job (active-jobs state)
        :if (not-in job.spec.name state.placements)
        job.spec.name
        (cond
          (still-live-somewhere now state job.spec.name timing) "前の担い手が止め終えるのを待っている"
          (not (any (gfor w (.values state.workers) (and (alive now w timing.lease-ms) (eligible job w)
                                                          (not-in w.name (draining-workers state now))))))
            (.format "置ける worker が無い(求める label {}・固定 {}。専用の印を持つ worker には、その印を求める job だけを置く。drain 中の worker には置かない)"
                     (dict job.requires) job.pin)
          True "置ける worker に空きが無い")))


(setv DETACHED-TERMINAL #("finished" "code-failed" "env-failed" "failed" "version-mismatch" "lost" "cancelled"))
;; 実行環境の準備の一時の失敗を、別の worker へ置き直す回数の上限(起動前なので同じ task を 2 度実行しない)。
(setv ENV-RETRIES 2)


(defn #^ TaskRecord end-detached [#^ TaskRecord task #^ str phase #^ int now #^ str detail #^ (| str None) [result None]]
  "純粋: 切り離した task を終わりの phase にする(終わりの phase は二度と変わらない)。blob は捨てて結果だけ持つ。"
  (replace task :phase phase :finished-ms now :detail detail :result result :blob ""))


(defn #^ (| TaskRecord None) settle-detached [#^ TaskRecord task #^ int now]
  "純粋: 切り離した task 1 本の期限の判断。保持の期限を過ぎた終わりの行は None(消す)。
   置いた task の lease(担い手の worker の heartbeat が延ばす)が切れた = worker の死 = lost(走らせ直さない)。
   呼び手の問い合わせは lease に触らない(呼び手が消えても task は続く)。"
  (cond
    (in task.phase DETACHED-TERMINAL)
      (if (> now (+ (or task.finished-ms now) task.retain-ms)) None task)
    (and (= task.phase "assigned") (> now task.lease-until-ms))
      (end-detached task "lost" now
                    (.format "担い手の worker {} の lease が切れた(worker の死とみなす — task は走らせ直さない)" task.worker))
    True task))


(defn #^ str unplaceable-phase [#^ TaskRecord task #^ ClusterState state #^ int now #^ ClusterTiming timing]
  "置ける worker が無い切り離した task の終わりの phase。label の合う生きた worker はいるのに版だけが違う = version-mismatch。"
  (if (any (gfor w (.values state.workers)
                 (and (alive now w timing.lease-ms) (labels-satisfy task.requires w) (tolerates task.requires w))))
      "version-mismatch"
      "failed"))


(defn #^ dict place-tasks [#^ int now #^ ClusterState state #^ dict placements #^ ClusterTiming timing]
  "task の期限切れを落とし、担い手が沈黙した task を失敗にし、待っている task を置く。切り離した task は settle-detached の規則。"
  (setv tasks {})
  (for [#(id task) (.items state.tasks)]
    (cond
      task.detached
        (do (setv kept (settle-detached task now))
            (when (is-not kept None) (setv (get tasks id) kept)))
      ;; 呼び手が問い合わせを止めた(止まった)= task も要らない。担い手は次の heartbeat で子 process を止める。
      (> now task.lease-until-ms) None
      (and (= task.phase "assigned")
           (or (not-in task.worker state.workers)
               (not (alive now (get state.workers task.worker) timing.reassign-after-ms))))
        (setv (get tasks id) (replace task :phase "failed" :finished-ms now
                                      :detail (.format "担い手の worker {} が沈黙した(task は走らせ直さない)" task.worker)))
      True (setv (get tasks id) task)))
  (setv placed (replace state :tasks tasks))
  (setv load (load-of placed placements)
        draining (draining-workers state now))
  (for [id (sorted tasks)]
    (setv task (get tasks id))
    (when (= task.phase "queued")
      ;; drain 中の worker には新しい task を置かない(置ける先が他に無ければ、drain が解けるまで待つ — 失敗にはしない)。
      (setv able (lfor w (.values state.workers) :if (and (alive now w timing.lease-ms) (can-run-task task w)) w)
            able-now (lfor w able :if (not-in w.name draining) w))
      (setv free (sorted (lfor w able-now :if (< (get load w.name) w.capacity) w)
                         :key (fn [w] #((get load w.name) w.name))))
      (cond
        free (do (setv chosen (get free 0))
                 (+= (get load chosen.name) 1)
                 ;; 切り離した task は置いた worker の process の世代を覚え、lease を置いた時から数える。
                 (setv (get tasks id) (if task.detached
                                          (replace task :phase "assigned" :worker chosen.name :started-ms now
                                                   :boot chosen.boot :lease-until-ms (+ now task.lease-ms))
                                          (replace task :phase "assigned" :worker chosen.name :started-ms now))))
        ;; 準備の一時の失敗の後に、置き直せる別の worker が無い: 最後の失敗で終える。
        (and (not able) task.failure-kind)
          (setv (get tasks id) (end-env-failed task now task.detail))
        ;; label の合う生きた worker はいるが、宣言の道具を名乗る worker が無い。
        (and (not able) (is-not task.runtime-env None)
             (any (gfor w (.values state.workers)
                        (and (alive now w timing.lease-ms) (labels-satisfy task.requires w) (tolerates task.requires w)))))
          (setv (get tasks id)
                (end-env-failed (replace task :failure-kind "tool-missing" :retryable False) now
                                (.format "宣言の道具 {} を名乗る worker が無い"
                                         (lfor t (.get task.runtime-env "tools" []) (.format "{}{}" (get t "name")
                                                                                             (if (.get t "version") (+ "=" (get t "version")) ""))))))
        (and (not able) task.detached)
          (setv (get tasks id) (end-detached task (unplaceable-phase task state now timing) now
                                             (versions-note task state now timing)))
        (not able) (setv (get tasks id) (replace task :phase "failed" :finished-ms now
                                                 :detail (versions-note task state now timing))))))
  tasks)


(defn #^ bool same-boot [#^ TaskRecord task #^ (| str None) boot]
  "切り離した task の担い手の process の世代が、置いた時と同じか(どちらかを知らなければ同じとみなす)。"
  (or (not task.detached) (is task.boot None) (is boot None) (= task.boot boot)))


(defn #^ TaskRecord end-env-failed [#^ TaskRecord task #^ int now #^ str detail]
  "純粋: 実行環境を準備できなかった task を終える(切り離した task は終わりの phase・blob を捨てる)。"
  (if task.detached
      (replace (end-detached task "env-failed" now detail) :failure-kind task.failure-kind :retryable task.retryable)
      (replace task :phase "env-failed" :finished-ms now :detail detail)))


(defn #^ TaskRecord absorb-env-failure [#^ TaskRecord task #^ str worker #^ dict status #^ int now]
  "純粋: worker の「実行環境を準備できない」の報告 → 一時の失敗で置き直しの回数が残れば、その worker を避けて待ちへ戻す。
   それ以外は終える。どちらも子 process を起こす前(Program は走っていない)。"
  (setv kind (.get status "failureKind" "") retryable (bool (.get status "retryable" False))
        detail (.format "worker {} で実行環境を準備できない({}): {}" worker kind (.get status "detail" ""))
        failed (replace task :failure-kind kind :retryable retryable :detail detail))
  (if (and retryable (< task.env-attempts ENV-RETRIES))
      (replace failed :phase "queued" :worker None :boot None :started-ms None
               :avoid (+ task.avoid #(worker)) :env-attempts (+ task.env-attempts 1))
      (end-env-failed failed now detail)))


(defn #^ list tasks-for [#^ ClusterState state #^ str worker]
  ;; 切り離した task は、置いた時と同じ process の世代の worker にだけ送る(作り直した worker の process で走らせ直さない)。
  (setv boot (. (.get state.workers worker (WorkerInfo worker #() 0 0)) boot))
  (lfor task (sorted (.values state.tasks) :key (fn [t] t.id))
        :if (and (= task.phase "assigned") (= task.worker worker) (same-boot task boot))
        (| {"id" task.id "name" task.name "env" task.env "revision" task.revision
            "versions" (dict task.versions) "blob" task.blob}
           (if task.detached {"detached" True} {})
           (if (is-not task.runtime-env None) {"runtimeEnv" task.runtime-env} {}))))


(defn #^ TaskRecord absorb-detached-report [#^ TaskRecord task #^ dict status #^ int now]
  "切り離した task の終わりの報告 → 終わりの phase。結果を書かずに終わった子 process は lost(結果が無い = 消失)。"
  (setv phase (.get status "phase") detail (.get status "detail" ""))
  (cond
    (and (= phase "finished") (is-not (.get status "result") None))
      (end-detached task "finished" now detail (get status "result"))
    (= phase "finished")
      (end-detached task "lost" now (.format "子 process が結果を書かずに終わった({})" detail))
    (= phase "code-failed") (end-detached task "code-failed" now detail)
    True task))


(defn #^ dict absorb-task-reports [#^ ClusterState state #^ str worker #^ list statuses #^ int now #^ (| str None) [boot None]]
  "worker の状態の報告のうち、task の終わりを task の記録へ写す。切り離した task は置いた時と同じ process の世代の報告だけ。"
  (setv tasks (dict state.tasks))
  (for [status statuses]
    (setv name (.get status "name" ""))
    (when (.startswith name "task/")
      (setv id (cut name 5 None) task (.get tasks id))
      (when (and task (= task.phase "assigned") (= task.worker worker) (same-boot task boot))
        (setv phase (.get status "phase"))
        (cond
          (= phase "env-failed") (setv (get tasks id) (absorb-env-failure task worker status now))
          task.detached (setv (get tasks id) (absorb-detached-report task status now))
          (= phase "finished")
            (setv (get tasks id) (replace task :phase "finished" :finished-ms now :result (.get status "result")
                                          :detail (.get status "detail" "")))
          (= phase "code-failed")
            (setv (get tasks id) (replace task :phase "code-failed" :finished-ms now
                                          :detail (.get status "detail" "")))))))
  tasks)


(defn #^ dict renew-detached [#^ dict tasks #^ str worker #^ (| str None) boot #^ int now]
  "担い手の heartbeat: その worker に置いた切り離した task の lease を延ばす(lease は worker が延ばす)。置いた時と違う process の
   世代の heartbeat なら lost(worker の process が作り直された = その上の task は消えた・走らせ直さない)。"
  (dfor #(id t) (.items tasks)
        id (cond
             (not (and t.detached (= t.phase "assigned") (= t.worker worker))) t
             (not (same-boot t boot))
               (end-detached t "lost" now
                             (.format "担い手の worker {} の process が作り直された(task は走らせ直さない)" worker))
             True (replace t :lease-until-ms (+ now t.lease-ms)))))


;; --- 盤 --------------------------------------------------------------------------------

(defn #^ bool board-allows [current #^ bool present #^ bool has-expect expect]
  "compare-and-set: expect が無ければ無条件・None なら行が無い時だけ・値ならいまの値がそれと等しい時だけ書いてよい。"
  (cond
    (not has-expect) True
    (is expect None) (not present)
    True (and present (= current expect))))


;; --- 1 拍の調停 ------------------------------------------------------------------------

(defn #^ ClusterState sweep-board [#^ ClusterState state #^ int now]
  "純粋: 期限を過ぎた盤の行を消した状態(期限つきの行が無ければ同じ object)。"
  (setv gone (lfor #(k at) (.items state.board-expiry) :if (<= at now) k))
  (if (not gone)
      state
      (do (setv drop (set gone))
          (replace state
                   :board (dfor #(k v) (.items state.board) :if (not-in k drop) k v)
                   :board-versions (dfor #(k v) (.items state.board-versions) :if (not-in k drop) k v)
                   :board-expiry (dfor #(k v) (.items state.board-expiry) :if (not-in k drop) k v)
                   :board-sizes (dfor #(k v) (.items state.board-sizes) :if (not-in k drop) k v)))))


(defn #^ ClusterState forget-silent-workers [#^ ClusterState state #^ int now]
  "純粋: WORKER-FORGET-MS より長く沈黙し、置き先も task も持たない worker を忘れた状態(忘れる物が無ければ同じ object)。"
  (setv busy (| (sfor a (+ (list (.values state.placements)) (list (.values state.surges))) a.worker)
                ;; 終わって結果を持っているだけの切り離した task は worker を引き留めない。
                (sfor t (.values state.tasks) :if (and t.worker (not (and t.detached (in t.phase DETACHED-TERMINAL)))) t.worker))
        gone (lfor #(n w) (.items state.workers) :if (and (> (- now w.last-seen-ms) WORKER-FORGET-MS) (not-in n busy)) n))
  (if (not gone)
      state
      (replace state :workers (dfor #(n w) (.items state.workers) :if (not-in n gone) n w)
                     :statuses (dfor #(n s) (.items state.statuses) :if (not-in n gone) n s)
                     :drains (dfor #(n d) (.items state.drains) :if (not-in n gone) n d))))


(defn #^ ClusterState sweep-drains [#^ ClusterState state #^ int now]
  "純粋: 期限を過ぎた drain を消した状態(消す物が無ければ同じ object)。頼み手(worker の preStop)は期限の内に頼み直し続ける。"
  (if (all (gfor d (.values state.drains) (> d.until-ms now)))
      state
      (replace state :drains (dfor #(n d) (.items state.drains) :if (> d.until-ms now) n d))))


(defn #^ ClusterState reconcile [#^ int now #^ ClusterState state #^ ClusterTiming timing]
  (setv state (forget-silent-workers (sweep-drains (sweep-board state now) now) now))
  (setv before state.placements
        after (place-jobs now state timing)
        tasks (place-tasks now state after timing)
        events (list state.events))
  (for [name (sorted (| (set before) (set after)))]
    (setv old (.get before name) new (.get after name))
    (when (!= old new)
      (.append events {"at" now "job" name
                       "from" (if old old.worker None) "to" (if new new.worker None)
                       "generation" (if new new.generation None)})))
  (replace state :placements after :tasks tasks :events (tuple (cut events (- MAX-EVENTS) None))))


(defn #^ bool durable-changed [#^ ClusterState before #^ ClusterState after]
  "資源の状態の file(state.json)の保存が要る変化か。worker の生存の時刻・状態の報告・readiness・k8s の観測は保存しない。
   盤は含まない(盤は行ごとの file へ別に書く — board-changes)。"
  (or (!= before.jobs after.jobs) (!= before.placements after.placements)
      (!= before.tasks after.tasks)
      (!= before.next-task after.next-task)
      (!= before.revision after.revision)
      (!= before.rollouts after.rollouts)
      (!= before.drains after.drains) (!= before.surges after.surges)
      (!= (set before.workers) (set after.workers))
      (any (gfor #(n w) (.items after.workers)
                 :setv b (.get before.workers n)
                 (or (is b None) (!= #(b.labels b.capacity b.versions) #(w.labels w.capacity w.versions)))))))


(defn #^ list board-changes [#^ ClusterState before #^ ClusterState after]
  "書き直しが要る盤の行の鍵(書かれた・消えた)。変わらない行は同じ object のまま引き継がれるので、同一性で比べる(盤全体を
   値で比べない — 大きな shadow の盤は 2.3 MB)。"
  (if (is before.board after.board)
      []
      (+ (lfor #(k v) (.items after.board) :if (is-not (.get before.board k) v) k)
         (lfor k before.board :if (not-in k after.board) k))))


;; --- HTTP の要求への返事(判断の部品。要求の振り分けは api_policy) -----------------------------------

(defn #^ ClusterState register-heartbeat [#^ ClusterState state #^ dict body #^ int now]
  "heartbeat の中身(worker の label・容量・版と、各 job / task の状態)を状態へ写す。割り当ての調停はしない(呼び手が別の送り手
   = coordinator として調停する)。"
  (setv name (get body "name")
        info (WorkerInfo name (tuple (sorted (.items (.get body "labels" {}))))
                         (int (.get body "capacity" 10)) now
                         (component-versions-of (.get body "versions" {}))
                         (.get body "boot")
                         (component-versions-of (.get body "tools" {})))
        statuses (.get body "statuses" [])
        state (replace (absorb-boot state name (.get body "boot"))
                :workers (| state.workers {name info})
                :statuses (| state.statuses {name {"at" now "endpoint" (.get body "endpoint")
                                                  "jobs" (lfor s statuses (dfor #(k v) (.items s) :if (!= k "result") k v))}})))
  (replace state :tasks (renew-detached (absorb-task-reports state name statuses now (.get body "boot")) name (.get body "boot") now)))


(defn #^ dict heartbeat-reply [#^ ClusterState state #^ str name #^ ClusterTiming timing [ready-instances None]]
  {"jobs" (lfor s (jobs-for state name ready-instances) (spec-json s))
   "tasks" (tasks-for state name)
   "timing" (asdict timing)
   ;; この worker が drain 中か(2026-09-25): worker は返事ごとに Pod の中の ready の file へ写し、readinessProbe は sh でそれを読む
   ;; (hy を起こす probe は込んだ node で 10 秒の timeout を越え、両方の Pod が同時に NotReady → DaemonSet が 2 台を同時に消した)。
   "draining" (in name state.drains)
   ;; 受け入れる本文の形の版(2026-09-26 — cluster_model.ACCEPTED-FORMATS)。
   "formats" (list ACCEPTED-FORMATS)})


(defn #^ dict state-view [#^ ClusterState state #^ int now #^ ClusterTiming timing]
  {"now" now
   "jobs" (lfor j state.jobs (| (job-to-json j)
                                {"resourceVersion" (.get (.get state.meta (+ "Service/" j.spec.name) {}) "resourceVersion")}))
   "workers" (dfor #(n w) (.items state.workers)
                   n {"labels" (dict w.labels) "capacity" w.capacity "silentMs" (- now w.last-seen-ms)
                      "versions" (dict w.versions)})
   "placements" (dfor #(k v) (.items state.placements) k (asdict v))
   "unplaced" (unplaced-jobs now state timing)
   ;; 沈黙した worker の最後の報告は「いま動いている」の証拠にならない。古さを付けて返す。
   "statuses" (dfor #(n st) (.items state.statuses) n (| st {"stale" (> (- now (get st "at")) timing.lease-ms)}))
   "tasks" (lfor t (sorted (.values state.tasks) :key (fn [t] t.id)) (task-summary t))
   "boardKeys" (len state.board)
   "surges" (dfor #(k v) (.items state.surges) k (asdict v))
   "events" (list (cut state.events -50 None))
   "revision" state.revision})


(defn #^ (| str None) runtime-env-refusal [#^ dict body]
  "本文の runtimeEnv(在れば)が宣言として読めなければ理由の文(送り手の誤り — 400)。"
  (setv value (.get body "runtimeEnv"))
  (cond
    (is value None) None
    (not (isinstance value dict)) (.format "runtimeEnv は JSON の object: {!r}" (type value))
    True (try (do (run (runtime-env-of-json value)) None)
              (except [error RuntimeEnvInvalid] (.format "runtimeEnv が誤っている: {}" error)))))


(defn #^ tuple submit-task [#^ ClusterState state #^ dict body #^ int now [owner None]]
  (setv refusal (or (format-refusal body) (runtime-env-refusal body)))
  (when refusal (return #(state 400 {"error" refusal})))
  (setv lease-seconds (float (.get body "leaseSeconds" 15.0))
        open-count (len (lfor t (.values state.tasks) :if (in t.phase #("queued" "assigned")) t)))
  (when (not (< 0 lease-seconds (+ TASK-MAX-LEASE-SECONDS 1)))
    (return #(state 400 {"error" (.format "leaseSeconds は 0 より大きく {} 以下: {}" TASK-MAX-LEASE-SECONDS lease-seconds)})))
  (when (>= open-count TASK-MAX-OPEN)
    (return #(state 429 {"error" (.format "終わっていない task が上限 {} 本に達している" TASK-MAX-OPEN) "open" open-count})))
  (setv id (.format "t{}" state.next-task)
        lease-ms (int (* 1000 lease-seconds))
        task (TaskRecord id (.get body "name" "") (get body "env") (get body "blob") (get body "revision")
                         (component-versions-of (.get body "versions" {}))
                         (requirements-of (.get body "requires" {}))
                         lease-ms (+ now lease-ms) now
                         :runtime-env (.get body "runtimeEnv")))
  #((replace state :tasks (| state.tasks {id task}) :next-task (+ state.next-task 1)) 200 {"task" id}))


(defn #^ tuple poll-task [#^ ClusterState state #^ str id #^ int now]
  "呼び手の問い合わせ。lease を延ばし、いまの様子を返す。"
  (setv task (.get state.tasks id))
  (when (is task None)
    (return #(state 200 {"phase" "missing"})))
  (setv task (replace task :lease-until-ms (+ now task.lease-ms)))
  #((replace state :tasks (| state.tasks {id task})) 200
    {"phase" task.phase "worker" task.worker "detail" task.detail "result" task.result
     "failureKind" task.failure-kind "retryable" task.retryable}))


(defn #^ tuple lease-write [#^ ClusterState state #^ str name #^ dict body #^ int now]
  "POST /leases/<名>: lease の操作 1 つを coordinator の時計で当てる(semaphore_model.lease-op)。行が変われば盤へ書く
   (版を 1 進める・盤の書きと同じく永続化してから返事をする)。返り値 #(次の状態 status 答え)。"
  (setv key (semaphore-key name) current (.get state.board key)
        #(row answer) (lease-op current (get body "op") (get body "token") (int (.get body "permits" 1))
                                (int (.get body "ttlMs" 0)) now))
  (if (or (is row current) (is row None))
      #(state 200 answer)
      (do (setv version (.get state.board-versions key (if (is current None) 0 1)))
          #((replace state :board (| state.board {key row})
                           :board-versions (| state.board-versions {key (+ version 1)})
                           :board-expiry (dfor #(k v) (.items state.board-expiry) :if (!= k key) k v)
                           :board-sizes (| state.board-sizes {key (value-size row)}))
            200 answer))))


(defn #^ int value-size [#^ (| dict list str int float bool None) value]
  "盤の値の大きさ(JSON の utf-8 の byte 数)。容量の上限の判断と計器に使う。"
  (len (.encode (json.dumps value :ensure-ascii False :separators #("," ":")) "utf-8")))


(defn #^ dict board-usage [#^ ClusterState state]
  {"rows" (len state.board) "bytes" (sum (.values state.board-sizes)) "expiring" (len state.board-expiry)
   "maxRows" BOARD-MAX-ROWS "maxBytes" BOARD-MAX-BYTES "maxValueBytes" BOARD-MAX-VALUE-BYTES})


(defn #^ (| str None) board-capacity-refusal [#^ ClusterState state #^ str key #^ int size]
  "純粋: key へ size byte の値を書くと上限を越えるなら理由の文。越えないなら None。小さくする書きは(合計が上限の上でも)通す。"
  (setv old (.get state.board-sizes key 0)
        total (sum (.values state.board-sizes)))
  (cond
    (> size BOARD-MAX-VALUE-BYTES) (.format "値が {} byte で、1 行の上限 {} byte を越える" size BOARD-MAX-VALUE-BYTES)
    (and (not-in key state.board) (>= (len state.board) BOARD-MAX-ROWS))
      (.format "盤の行が上限 {} 行に達している(期限つきの行 {} 行)" BOARD-MAX-ROWS (len state.board-expiry))
    (and (> size old) (> (+ (- total old) size) BOARD-MAX-BYTES))
      (.format "盤の値の合計が {} byte になり、上限 {} byte を越える" (+ (- total old) size) BOARD-MAX-BYTES)
    True None))


(defn #^ tuple board-write [#^ ClusterState state #^ str key #^ dict body #^ int [now 0]]
  "盤の行 1 つの compare-and-set。expect = 値で比べる(従来)・expectVersion = 行の版で比べる(0 = 行が無い時だけ)。
   両方あれば両方を満たす時だけ書く。value が null で delete が真なら行を消す。返事に行の新しい版を載せる。
   ttlSeconds(2026-09-25)= 行の期限。期限を過ぎた行は調停が消す(sweep-board)。付けない書きは期限を外す(ずっと残す)。
   上限(board-capacity-refusal)を越える書きは 507 で断る。"
  (setv ttl (.get body "ttlSeconds"))
  (when (and (is-not ttl None) (not (and (isinstance ttl #(int float)) (< 0 ttl (+ BOARD-MAX-TTL-SECONDS 1)))))
    (return #(state 400 {"ok" False "error" (.format "ttlSeconds は 0 より大きく {} 以下: {!r}" BOARD-MAX-TTL-SECONDS ttl)})))
  (setv present (in key state.board)
        version (.get state.board-versions key (if present 1 0))
        ok (and (board-allows (.get state.board key) present (in "expect" body) (.get body "expect"))
                (or (not-in "expectVersion" body) (= (get body "expectVersion") version))))
  (cond
    (not ok) #(state 409 {"ok" False "current" (.get state.board key) "resourceVersion" version})
    ;; lease の行への直の書き(旧い版の process)は、coordinator の時計でまだ切れていない担い手を追い出せない(2026-09-25)。
    ;; 409 = 旧い版は compare-and-set の競合として読み直す。
    (and (.startswith key SEMAPHORE-PREFIX) (not (.get body "delete"))
         (is-not (semaphore-write-refusal (.get state.board key) (get body "value") now) None))
      #(state 409 {"ok" False "current" (.get state.board key) "resourceVersion" version
                   "error" (semaphore-write-refusal (.get state.board key) (get body "value") now)})
    (.get body "delete")
      #((replace state :board (dfor #(k v) (.items state.board) :if (!= k key) k v)
                       :board-versions (dfor #(k v) (.items state.board-versions) :if (!= k key) k v)
                       :board-expiry (dfor #(k v) (.items state.board-expiry) :if (!= k key) k v)
                       :board-sizes (dfor #(k v) (.items state.board-sizes) :if (!= k key) k v))
        200 {"ok" True "resourceVersion" None})
    True
      (do (setv size (value-size (get body "value"))
                refusal (board-capacity-refusal state key size))
          (if (is-not refusal None)
              #(state 507 {"ok" False "error" refusal "usage" (board-usage state)})
              #((replace state :board (| state.board {key (get body "value")})
                               :board-versions (| state.board-versions {key (+ version 1)})
                               :board-expiry (if (is ttl None)
                                                 (dfor #(k v) (.items state.board-expiry) :if (!= k key) k v)
                                                 (| state.board-expiry {key (+ now (int (* 1000 ttl)))}))
                               :board-sizes (| state.board-sizes {key size}))
                200 {"ok" True "resourceVersion" (+ version 1)})))))
