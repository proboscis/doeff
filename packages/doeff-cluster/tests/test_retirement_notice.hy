;; 退きの知らせ(worker/intent/retirement_model の AwaitRetirement — #3672): 入れ替え(update = handoff)で退く旧の process は、新の
;; Ready と自分の止めの合図(SIGTERM)より前に、出来事で「退く」(Retired)を受ける。入れ替えが諦められると(新が Ready にならない)、同じ
;; process が「退きを取り消した」(HandoffAbandoned)を受け、宣言が変わって諦めが解ければもう一度「退く」を受ける。入れ替えの無い止めでは
;; 何も受けない。
;;
;; 本物の coordinator と本物の worker の判断を、sim-cluster(仮想の時計)の上で回す。service(tests/fixtures/retirement_programs)は
;; 知らせを受けた刻と止めの合図を受けた刻を盤に書き、筋書きは盤の行と coordinator に届いた報告から順を読む。
(require doeff-hy.macros [deftest defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass replace])  ; dataclass = defrecord の展開が名指す
(import doeff_time [Delay])
(import doeff_cluster.sim.local [sim-cluster SimWorker SimProcess Redeclare ReportsOf ProcessesOf SharedRows])
(import doeff_cluster.shared.intent.service_model [System])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.worker.intent.worker_model [ProcessView WorldView NoticeJob Retired HandoffAbandoned])
(import doeff_cluster.worker.core.policy [notice-actions])
(import doeff_cluster.worker.core.invariants [RetirementSeen retiring-process-hears-first])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.replicas [with-replicas])
(import tests.fixtures.retirement_programs [RETIRE-PREFIX retiring-beacons retiring-beacons-v2 retiring-beacons-stuck retiring-beacons-v3])


(defrecord RetireSeen
  "筋書きが読んだ姿: processes = beacon の process(起きた順)・reports = coordinator に届いた報告・rows = 盤の retire/ の行。"
  (#^ tuple processes)
  (#^ tuple reports)
  (#^ dict rows))


(defrecord Told
  "盤の行から読んだ知らせ 1 つ: n = 世代の中の番号・word = 知らせの語・at = 受けた刻(epoch ms)。"
  (#^ int n)
  (#^ str word)
  (#^ int at))


(defk retire-seen []
  {:pre [] :post [(: % RetireSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの読み: beacon の process・届いた報告・盤の retire/ の行を読む。"
  (<- processes tuple (ProcessesOf "beacon"))
  (<- reports tuple (ReportsOf "beacon"))
  (<- rows dict (SharedRows (+ RETIRE-PREFIX "/")))
  (RetireSeen :processes processes :reports reports :rows rows))


(defk notices-of [seen instance]
  {:pre [(: seen RetireSeen) (: instance str)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "世代 instance が受けた知らせ(Told)を受けた順に並べるため(盤の <prefix>/<instance>/<番号> の行)。"
  (val head (.format "{}/{}/" RETIRE-PREFIX instance))
  (tuple (sorted (gfor #(key value) (.items seen.rows)
                       :if (and (.startswith key head) (.isdigit (cut key (len head) None)))
                       (Told :n (int (cut key (len head) None)) :word (get value "notice") :at (get value "at")))
                 :key (fn [t] t.n))))


(defk stop-of [seen instance]
  {:pre [(: seen RetireSeen) (: instance str)] :post [(: % (| int None))] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "世代 instance が止めの合図を受けた刻(盤の <prefix>/<instance>/stop — 受けていなければ None)を読むため。"
  (val row (.get seen.rows (.format "{}/{}/stop" RETIRE-PREFIX instance)))
  (if (is row None) None (get row "at")))


(defk first-ready-of [seen instance]
  {:pre [(: seen RetireSeen) (: instance str)] :post [(: % (| int None))] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "世代 instance の最初の Ready の報告が coordinator に届いた刻(無ければ None)を読むため。"
  (min (gfor r seen.reports :if (and (= r.instance instance) (= r.kind "readiness") r.ready) r.at) :default None))


(defk redeclared [system wait]
  {:pre [(: system System) (: wait float)] :post [(: % RetireSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 8 秒待って系 system で宣言し直し、wait 秒待って読む。"
  (<- (Delay 8.0))
  (<- _names tuple (Redeclare system))
  (<- (Delay wait))
  (<- seen RetireSeen (retire-seen))
  seen)


(defk retirement-record [seen old new]
  {:pre [(: seen RetireSeen) (: old SimProcess) (: new SimProcess)] :post [(: % RetirementSeen)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "条 W2 の判断に渡す記録を、筋書きが読んだ姿から組むため: 退いた世代 old が「退く」を最初に受けた刻・後継 new の最初の Ready の刻・
   old が止めの合図を受けた刻。"
  (<- told tuple (notices-of seen old.instance))
  (<- ready (| int None) (first-ready-of seen new.instance))
  (<- stopped (| int None) (stop-of seen old.instance))
  (RetirementSeen :instance old.instance
                  :told-ms (min (gfor t told :if (= t.word "retired") t.at) :default None)
                  :successor-ready-ms ready
                  :stopped-ms stopped))


(deftest test-the-retiring-process-hears-it-before-the-new-ready-and-its-own-stop
  ;; 入れ替え: 旧は「退く」を 1 つだけ受け、その刻は新の最初の Ready の報告より前、旧が止めの合図を受けるより前(条 W2 — architecture.hy の
  ;; worker の :invariants)。新は何も受けない。
  (<- seen RetireSeen (sim-cluster (retiring-beacons sim-foundation) (redeclared (retiring-beacons-v2 sim-foundation) 15.0)))
  (assert (= (len seen.processes) 2) seen.processes)
  (val old (get seen.processes 0))
  (val new (get seen.processes 1))
  (<- told tuple (notices-of seen old.instance))
  (assert (= (tuple (gfor t told t.word)) #("retired")) #(told seen.rows))
  (<- record RetirementSeen (retirement-record seen old new))
  (assert (and (is-not record.successor-ready-ms None) (is-not record.stopped-ms None)) #(record seen.rows))
  (assert (< record.told-ms record.successor-ready-ms) #("知らせが新の Ready より後" record))
  (assert (< record.told-ms record.stopped-ms) #("知らせが旧の止めの合図より後" record))
  (<- breaches tuple (retiring-process-hears-first #(record)))
  (assert (= breaches #()) breaches)
  (<- new-told tuple (notices-of seen new.instance))
  (assert (= new-told #()) new-told))


(deftest test-a-counterexample-worker-that-does-not-tell-the-retiring-process-breaks-w2
  ;; 反例(条 W2): 入れ替えで旧を名から外す RetireJob が観測だけ書いて process へ知らせない壊れた worker(silent-notices)では、旧は
  ;; 何も受けないまま新が Ready になり止められ、W2 の判断がその世代を名指す — 本物の宿が名から外す時に知らせていることの裏返し。
  (val workers #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :silent-notices True :task-reserve 0)))
  (<- seen RetireSeen (sim-cluster (retiring-beacons sim-foundation) (redeclared (retiring-beacons-v2 sim-foundation) 15.0)
                                   :workers workers))
  (val old (get seen.processes 0))
  (val new (get seen.processes 1))
  (<- record RetirementSeen (retirement-record seen old new))
  (<- breaches tuple (retiring-process-hears-first #(record)))
  (assert (= breaches #(record)) #(breaches record)))


(defrecord AbandonSeen
  "諦めの筋書きが読んだ姿: abandoned = 入れ替えが諦められた後・redeclared = 宣言し直して入れ替えが終わった後。"
  (#^ RetireSeen abandoned)
  (#^ RetireSeen redeclared))


(defk abandoned-then-redeclared [stuck good]
  {:pre [(: stuck System) (: good System)] :post [(: % AbandonSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 8 秒待って Ready にならない版 stuck で宣言し直し、入れ替えの期限(20 秒)を越えて 40 秒待って読み、良い版 good で宣言し直して
   20 秒待ってもう一度読む。"
  (<- (Delay 8.0))
  (<- _stuck tuple (Redeclare stuck))
  (<- (Delay 40.0))
  (<- abandoned RetireSeen (retire-seen))
  (<- _good tuple (Redeclare good))
  (<- (Delay 20.0))
  (<- after RetireSeen (retire-seen))
  (AbandonSeen :abandoned abandoned :redeclared after))


(deftest test-an-abandoned-handoff-withdraws-the-retirement-and-a-new-declaration-retires-again
  ;; 諦め: 新が Ready にならないまま期限を越えると、旧は「退く」の次に「退きを取り消した」を受けて動き続ける(止めの合図を受けない)。
  ;; 宣言し直して諦めが解けると、同じ旧が もう一度「退く」を受け、その刻は次の新の最初の Ready より前で、その後に止められる。
  (<- seen AbandonSeen (sim-cluster (retiring-beacons sim-foundation)
                                    (abandoned-then-redeclared (retiring-beacons-stuck sim-foundation) (retiring-beacons-v3 sim-foundation))))
  (val first (get seen.abandoned.processes 0))
  (<- told tuple (notices-of seen.abandoned first.instance))
  (assert (= (tuple (gfor t told t.word)) #("retired" "handoff-abandoned")) #(told seen.abandoned.rows))
  (<- held (| int None) (stop-of seen.abandoned first.instance))
  (assert (and (is first.exit-code None) (is held None)) #("諦めの後も旧は動き続ける" first held))
  (val later (get seen.redeclared.processes 0))
  (<- again tuple (notices-of seen.redeclared later.instance))
  (assert (= (tuple (gfor t again t.word)) #("retired" "handoff-abandoned" "retired")) #(again seen.redeclared.rows))
  (val successor (get seen.redeclared.processes -1))
  (<- ready (| int None) (first-ready-of seen.redeclared successor.instance))
  (<- stopped (| int None) (stop-of seen.redeclared later.instance))
  (assert (and (is-not ready None) (is-not stopped None)) #(ready stopped seen.redeclared.rows))
  (assert (< (. (get again 2) at) ready) #("もう一度の知らせが次の新の Ready より後" again ready)))


(defk withdrawn [system]
  {:pre [(: system System)] :post [(: % RetireSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 8 秒待って同じ系の台数を 0 にして宣言し直し(入れ替えの無い止め)、12 秒待って読む。"
  (<- (Delay 8.0))
  (<- none System (with-replicas system 0))
  (<- _names tuple (Redeclare none))
  (<- (Delay 12.0))
  (<- seen RetireSeen (retire-seen))
  seen)


(deftest test-a-stop-without-a-handoff-sends-no-retirement
  ;; 入れ替えの無い止め(宣言から外れた): process は止めの合図を受けて終わり、退きの知らせは何も受けない。
  (<- seen RetireSeen (sim-cluster (retiring-beacons sim-foundation) (withdrawn (retiring-beacons sim-foundation))))
  (assert (= (len seen.processes) 1) seen.processes)
  (val only (get seen.processes 0))
  (<- stopped (| int None) (stop-of seen only.instance))
  (<- told tuple (notices-of seen only.instance))
  (assert (is-not stopped None) seen.rows)
  (assert (= told #()) told))


;; --- 判断(worker/core/policy の notice-actions)の表 -------------------------------------------------------

(val H1 (JobSpec "a" "jobs.a" #() "rev1" :handoff True))
(val H2 (replace H1 :revision "rev2"))


(defk retired-view [notice exit-code]
  {:pre [(: notice (| Retired HandoffAbandoned None)) (: exit-code (| int None))] :post [(: % ProcessView)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "名 a から退いた旧の process(版 1・pid 10)の観測を、最後に受けた知らせ notice と終わり exit-code で組むため。"
  (ProcessView "a#retired-1-old" H1 1 10 0 :instance "1-old" :retired-from "a" :notice notice :exit-code exit-code))


(deftest test-the-notices-to-a-retired-process-follow-the-abandonment-of-its-handoff
  ;; 新の Ready を待つ間は送らない(名から外した RetireJob の「退く」のまま)・諦められたら「取り消した」・諦めが解けたら もう一度「退く」・
  ;; 元の job が宣言から消えたら(旧は止められる)「退く」。まだ何も受けていない(RetireJob の前の観測)・終わった process には送らない。
  (val abandoned (replace H2 :handoff-abandoned True))
  (val told (! (retired-view (Retired) None)))
  (val withdrawn (! (retired-view (HandoffAbandoned) None)))
  (assert (= (! (notice-actions #(H2) (WorldView #() #(told)))) #()))
  (assert (= (! (notice-actions #(abandoned) (WorldView #() #(told)))) #((NoticeJob told.name 10 (HandoffAbandoned)))))
  (assert (= (! (notice-actions #(abandoned) (WorldView #() #(withdrawn)))) #()))
  (assert (= (! (notice-actions #(H2) (WorldView #() #(withdrawn)))) #((NoticeJob told.name 10 (Retired)))))
  (assert (= (! (notice-actions #() (WorldView #() #(withdrawn)))) #((NoticeJob told.name 10 (Retired)))))
  (assert (= (! (notice-actions #(abandoned) (WorldView #() #((! (retired-view None None)))))) #()))
  (assert (= (! (notice-actions #(abandoned) (WorldView #() #((! (retired-view (Retired) -15)))))) #())))
