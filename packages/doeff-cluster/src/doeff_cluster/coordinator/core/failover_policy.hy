;;; 1 台の worker が止まっても常駐の job が残りの worker の memory に入るか(N+1)の純粋な判断(card ki-4689c7008147)。I/O はしない。
;;;
;;; coordinator は、他に置ける worker が在る job を、担い手が移し替えの期限を過ぎて黙ったら外して他へ移す(cluster_policy の #2804 の註)。
;;; 移る先が在るか・移った後に起動の山まで memory に入るかは置く時に照らさない(置く時は落ち着いた値の和だけを見る —
;;; cluster_policy.memory-misfit)ので、宣言の側がこの判断で前もって照らす。照らす規則は 3 つ(1 件目で止めず、破りをみな文にする):
;;;   1. 置き先: 機体ごとの job(per-machine? — needs が雛形の能力だけか、それに機体の印 host-<機体> を 1 つ足して機体ごとに広げた
;;;      物 — 置ける worker のそれぞれに 1 つずつ動く)と、1 台に縛る訳を宣言した job(job の :single-host — ResidentJob.single-host)を
;;;      除き、どの常駐の job も置ける worker が 2 台以上ある。言い換えると、どの 1 台を止めても、その機体にしか置けない job が 0。
;;;   2. (消した — card ki-2acb63aec0fd)数の枠: 手書きの capacity から job の数を引いた空きで照らしていた。coordinator は job を数でなく
;;;      memory で縛る(cluster_policy.memory-misfit)ので、memory の規則 3 だけが照らす。規則の番号は他の文書が引くので詰めない。
;;;   3. memory: 各 worker w で、置ける job の落ち着いた値の和 + max(止まる機体 m について、w と m の両方に置けて m から移りうる job の
;;;      (山 − 落ち着いた値)の和)≤ w の memory の予算。予算の無い worker に置ける job が在れば赤。機体ごとの job は移らないので山の
;;;      上乗せに数えない。
;;;   4. 離す組: 同じ機体に置いてはいけない job の対(番人とその番人など — 1 台の停止で一緒に止まる)の両方を置ける worker が在れば赤。
;;; replicas 0 の job は置かないので、どの規則にも数えない。
(require doeff-hy.macros [defk <- val var])
(val MODULE-TAGS {:context "coordinator" :role "judgment"})
(import doeff_cluster.coordinator.intent.failover_model [FailoverWorker ResidentJob KeptApart JobTakers DownSpike])
(import doeff_cluster.coordinator.core.cluster_policy [placeable])
(import doeff_cluster.shared.core.capabilities [HOST-PREFIX])

(defk per-machine? [needs template]
  {:pre [(: needs (get tuple #(str ...))) (: template (get tuple #(str ...)))] :post [(: % bool)] :tags {:context "coordinator" :role "judgment"}}
  "needs の job が機体ごとの job(置ける worker のそれぞれに 1 つずつ動き、止まった機体から他の機体へ移らない job)かを知るため — 定義点は
   ここ 1 つ(この判断の規則 1・3 と、上に載る系が「止まった worker が移せない job」を分ける数えが同じ答えを読む)。機体ごとの job =
   needs が雛形の能力 template だけの job(雛形の job — どの機体の worker も名乗るので、どの機体にも置ける)か、雛形の能力の 1 つ以上に
   機体の印 host-<機体> を 1 つだけ足した job(雛形を機体ごとに広げた job — 広げた機体の上でだけ動き、雛形の機体ごとを継ぐ)。雛形の能力を
   持たず機体の印だけを持つ job は、1 台に縛った job で機体ごとの job ではない(止まれば移る先が無い — 規則 1 が赤にする)。"
  (val marks (- (set needs) (set template)))
  (or (not marks)
      (and (= (len marks) 1)
           (.startswith (next (iter marks)) HOST-PREFIX)
           (bool (& (set needs) (set template))))))


(defk takers-of [job workers template]
  {:pre [(: job ResidentJob) (: workers tuple) (: template tuple)] :post [(: % JobTakers)] :tags {:context "coordinator" :role "judgment"}}
  "job を置ける worker(workers の順)と、機体ごとの job か(per-machine? — 雛形の能力 template で判じる)を知るため — 置き方の規則
   placeable をそのまま呼ぶ。"
  (<- per-machine bool (per-machine? job.needs template))
  (JobTakers :job job
             :takers (tuple (gfor w workers :if (placeable job.needs w.info) w.info.name))
             :per-machine per-machine))


(defk place-refusal [placed]
  {:pre [(: placed JobTakers)] :post [(: % (| str None))] :tags {:context "coordinator" :role "judgment"}}
  "規則 1(置き先が 2 台以上)の破りを、どの 1 台を止めると置き先を失うかを名指す文にするため(破りが無ければ None)。"
  (val job placed.job)
  (cond
    (not placed.takers)
      (.format "job {}(needs {})はどの worker にも置けない — 置ける worker を 2 台以上にするか、1 台に縛る訳を job の宣言に :single-host \"<訳>\" で書く"
               job.name (list job.needs))
    (= (len placed.takers) 1)
      (.format "worker {} を止めると、{} にしか置けない job {}(needs {})が置き先を失う — 置ける worker を 2 台以上にするか、1 台に縛る訳を job の宣言に :single-host \"<訳>\" で書く"
               (get placed.takers 0) (get placed.takers 0) job.name (list job.needs))
    True None))


(defk spike-of [worker down here]
  {:pre [(: worker FailoverWorker) (: down FailoverWorker) (: here tuple)] :post [(: % DownSpike)] :tags {:context "coordinator" :role "judgment"}}
  "worker down が止まった時に、worker へ移りうる job の起動の山の上乗せを知るため。移りうる job = worker に置ける job(here)のうち、
   down にも置けて機体ごとでない物(機体ごとの job は止まった機体の上の物が移らない)。"
  (val moved (tuple (gfor p here :if (and (not p.per-machine) (in down.info.name p.takers)) p.job)))
  (DownSpike :down down.info.name :moved moved :extra-mib (sum (gfor j moved (* j.replicas (- j.peak-mib j.settle-mib))))))


(defk memory-refusal [worker here workers]
  {:pre [(: worker FailoverWorker) (: here tuple) (: workers tuple)] :post [(: % (| str None))] :tags {:context "coordinator" :role "judgment"}}
  "規則 3(memory)の破りを文にするため(入れば None)。here = worker に置ける job(JobTakers)。止まる機体ごとの山の上乗せのうち最大
   (同じなら workers の順で先)を、落ち着いた値の和に足して予算と比べる。"
  (val name worker.info.name)
  (val settled (sum (gfor p here (* p.job.replicas p.job.settle-mib))))
  (var spikes #())
  (for [down workers]
    (when (!= down.info.name name)
      (<- spike DownSpike (spike-of worker down here))
      (:= spikes (+ spikes #(spike)))))
  (val worst (max spikes :key (fn [s] s.extra-mib) :default (DownSpike :down "" :moved #() :extra-mib 0)))
  (cond
    (not here) None
    (is worker.memory-mib None)
      (.format "worker {}: memory の予算が無い — 置ける常駐の job {} の memory を照らせない" name (.join ", " (gfor p here p.job.name)))
    (> (+ settled worst.extra-mib) worker.memory-mib)
      (.format "worker {}: 置ける常駐の job の落ち着いた値の和 {} MiB({})+ {} が止まった時に移る job の起動の山の上乗せ {} MiB({})= {} MiB が memory の予算 {} MiB を越える"
               name settled (.join ", " (gfor p here (.format "{} {}" p.job.name p.job.settle-mib))) (or worst.down "なし") worst.extra-mib
               (or (.join ", " (gfor j worst.moved (.format "{} +{}" j.name (- j.peak-mib j.settle-mib)))) "なし")
               (+ settled worst.extra-mib) worker.memory-mib)
    True None))


(defk apart-refusals [pair placed workers]
  {:pre [(: pair KeptApart) (: placed tuple) (: workers tuple)] :post [(: % (get tuple #(str ...)))] :tags {:context "coordinator" :role "judgment"}}
  "規則 4(離す組の両方を置ける worker が無い)の破りを、両方を置ける worker ごとの文にするため。placed = 置く job(JobTakers — replicas 0 と
   列に無い job は置かないので数えない)。"
  (val takers-of-pair (tuple (gfor name #(pair.first pair.second)
                                   (frozenset (sum (gfor p placed :if (= p.job.name name) p.takers) #())))))
  (tuple (gfor w workers :if (all (gfor t takers-of-pair (in w.info.name t)))
               (.format "worker {}: 離す組の job {}・{} を両方とも置ける — 同じ機体に載ると 1 台の停止で一緒に止まる(どちらかの置ける worker から {} を外す)"
                        w.info.name pair.first pair.second w.info.name))))


(defk failover-refusals [workers jobs template apart]
  {:pre [(: workers (get tuple #(FailoverWorker ...))) (: jobs (get tuple #(ResidentJob ...))) (: template (get tuple #(str ...)))
         (: apart (get tuple #(KeptApart ...)))
         (all (gfor w workers (isinstance w FailoverWorker))) (all (gfor j jobs (isinstance j ResidentJob)))
         (all (gfor a apart (isinstance a KeptApart)))
         (= (len (set (gfor w workers w.info.name))) (len workers)) (= (len (set (gfor j jobs j.name))) (len jobs))]
   :post [(: % (get tuple #(str ...)))] :tags {:context "coordinator" :role "judgment"}}
  "どの 1 台の worker が止まっても、その機体の常駐の job が残りの worker の memory に入るか(頭の註の規則 1・3・4)を判じるため。
   workers = 照らす worker(名は重ならない)・jobs = 常駐の job(名は重ならない)・template = 雛形の能力(どの機体の worker も名乗る能力 —
   needs がこれだけの job は機体ごとの job)・apart = 同じ機体に置いてはいけない job の対。規則 1 を当てない job は、job の値の
   single-host(1 台に縛る訳 — job の宣言の :single-host)が持つ。
   答え = 破りの文の列(空 = 通る — 規則 1・各 worker の規則 3・規則 4 の順)。1 件目で止めない。"
  (var placed #())
  (for [j jobs]
    (when (> j.replicas 0)
      (<- p JobTakers (takers-of j workers template))
      (:= placed (+ placed #(p)))))
  (var out #())
  (for [p placed]
    (when (and (not p.per-machine) (is p.job.single-host None))
      (<- text (| str None) (place-refusal p))
      (when (is-not text None) (:= out (+ out #(text))))))
  (for [w workers]
    (val here (tuple (gfor p placed :if (in w.info.name p.takers) p)))
    (<- memory (| str None) (memory-refusal w here workers))
    (when (is-not memory None) (:= out (+ out #(memory)))))
  (for [pair apart]
    (<- texts (get tuple #(str ...)) (apart-refusals pair placed workers))
    (:= out (+ out texts)))
  out)
