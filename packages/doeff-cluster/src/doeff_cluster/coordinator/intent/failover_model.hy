;;; 1 台の worker が止まっても常駐の job が残りの worker に入るか(N+1)の判断の型(card ki-4689c7008147)。判断は
;;; coordinator/core/failover_policy.failover-refusals。宣言の側(上に載る系の検・宣言の前の照らし)が値を集めて渡す — coordinator の
;;; 状態は読まない(置き方の判断 placeable・job-room-of と同じ層の、値を受ける純粋な判断)。
(require doeff-hy.macros [val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "coordinator" :role "intent"})
(import dataclasses [dataclass])
(import doeff_cluster.coordinator.intent.cluster_model [WorkerInfo])
(import doeff_cluster.shared.intent.service_model [UPDATE-FORMS])


;; --- 入力 ---------------------------------------------------------------------------------------------------------------------

(defrecord FailoverWorker
  "照らす worker 1 台: info = 置き方の判断が受ける worker の値(名・能力・枠・予約)・memory-mib = その worker で常駐の job に使ってよい
   memory の予算(MiB・無ければ None — 置ける job が在れば赤)。"
  {:tags {:context "coordinator" :role "type"}
   :check [(or (is memory-mib None) (>= memory-mib 0))]}
  (#^ WorkerInfo info)
  (#^ (| int None) memory-mib))


(defrecord ResidentJob
  "照らす常駐の job 1 つ: name = job の名・needs = 要る能力・replicas = 置く数(0 = 宣言だけ残して置かない — 照らさない)・update =
   入れ替えの形(recreate・handoff — handoff は並べた置き先 surge を使う)・peak-mib = 起動の memory の山(MiB)・settle-mib = 落ち着いた
   memory(MiB・山以下)。"
  {:tags {:context "coordinator" :role "type"}
   :check [(>= replicas 0) (in update UPDATE-FORMS) (>= settle-mib 0) (>= peak-mib settle-mib)]}
  (#^ str name)
  (#^ (get tuple #(str ...)) needs)
  (#^ int replicas)
  (#^ str update)
  (#^ int peak-mib)
  (#^ int settle-mib))


(defrecord ExtraProvides
  "枠の file の worker 1 台に足す能力: name = worker の名・provides = その worker の env の WORKER_PROVIDES_EXTRA の名(雛形の能力の後ろに
   起動の引数で渡す — 上に載る系が env の file を持つ worker だけ)。"
  {:tags {:context "coordinator" :role "type"}
   :check [(.strip name)]}
  (#^ str name)
  (#^ (get tuple #(str ...)) provides))


(defrecord ExemptJob
  "例外の表の 1 行: job = 置き先が 1 台でよい job の名・reason = 1 台に縛る訳(機体の disk の data を持つ・移す途中 など)。規則 1(置き先が
   2 台以上)だけを当てない — 枠と memory には数える。"
  {:tags {:context "coordinator" :role "type"}
   :check [(.strip job) (.strip reason)]}
  (#^ str job)
  (#^ str reason))


(defrecord KeptApart
  "離す組 1 つ: 同じ機体に置いてはいけない job の名の対(番人とその番人など — 同じ機体に載ると 1 台の停止で一緒に止まる)。"
  {:tags {:context "coordinator" :role "type"}
   :check [(.strip first) (.strip second) (!= first second)]}
  (#^ str first)
  (#^ str second))


;; --- 判断の途中の値 -----------------------------------------------------------------------------------------------------------

(defrecord JobTakers
  "置く常駐の job 1 つと、置ける worker: job = 常駐の job・takers = 置ける worker の名(照らす worker の順)・per-machine = 機体ごとの
   job(coordinator/core/failover_policy の per-machine? — needs が雛形の能力だけか、それに機体の印を 1 つ足して機体ごとに広げた物。
   置ける worker のそれぞれに 1 つずつ動き、他の機体へ移らない)か。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ ResidentJob job)
  (#^ (get tuple #(str ...)) takers)
  (#^ bool per-machine))


(defrecord DownSpike
  "worker 1 台から見た、別の 1 台が止まった時の memory の上乗せ: down = 止まる worker の名・moved = 止まる機体から移りうる job(両方に
   置けて機体ごとでない物)・extra-mib = その job の(山 − 落ち着いた値)× 台数の和。"
  {:tags {:context "coordinator" :role "type"}
   :check [(>= extra-mib 0)]}
  (#^ str down)
  (#^ (get tuple #(ResidentJob ...)) moved)
  (#^ int extra-mib))
