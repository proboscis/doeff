;; 1 台の worker が止まっても常駐の job が残りの worker の枠と memory に入るか(N+1)の判断 failover-refusals(card ki-4689c7008147)。
;; 合成の世界で、規則ごとの反例を赤にし(1 台にしか置けない job・移る先の枠の不足・移る先の memory の不足・離す組を両方置ける worker・
;; memory の予算の無い worker)、赤にしてはいけない物(機体ごとの job・例外の表の job・replicas 0 の job)を赤にしない。
;; 失敗ケース = 1 台に縛られた job を通す・予備の枠を持つ worker(等号でない枠)を赤にする・枠の式を写して job-room-of とずれる・
;; 1 件目で止めて残りの破りを隠す。
(require doeff-hy.macros [defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import pathlib [Path])
(import re)
(import doeff_cluster.coordinator.intent.cluster_model [WorkerInfo])
(import doeff_cluster.coordinator.intent.failover_model [FailoverWorker ResidentJob ExemptJob KeptApart])
(import doeff_cluster.coordinator.core.failover_policy [failover-refusals])

;; 雛形の能力(どの機体の worker も名乗る — needs がこれだけの job は機体ごとの job)。
(val TEMPLATE #("host-facts"))


(defk host [name pools [capacity 3] [memory 4000] [reserve 0]]
  {:pre [(: name str) (: pools tuple) (: capacity int) (: memory (| int None)) (: reserve int)] :post [(: % FailoverWorker)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "機体の名の能力 host-<名> と雛形の能力と組の印 pools を名乗る、枠 capacity・予約 reserve・memory の予算 memory の worker を作るため。"
  (FailoverWorker :info (WorkerInfo name (tuple (sorted #(f"host-{name}" #* TEMPLATE #* pools))) capacity 0 :task-reserve reserve)
                  :memory-mib memory))


(defk resident [name needs [peak 1000] [settle 300] [replicas 1] [update "recreate"]]
  {:pre [(: name str) (: needs tuple) (: peak int) (: settle int) (: replicas int) (: update str)] :post [(: % ResidentJob)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "needs を要る常駐の job(起動の山 peak・落ち着いた値 settle MiB)を作るため。"
  (ResidentJob :name name :needs needs :replicas replicas :update update :peak-mib peak :settle-mib settle))


(defk judged [workers jobs [exempt #()] [apart #()]]
  {:pre [(: workers tuple) (: jobs tuple) (: exempt tuple) (: apart tuple)] :post [(: % (get tuple #(str ...)))]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "合成の世界(雛形の能力 TEMPLATE)を判じた答えを得るため。"
  (<- out (get tuple #(str ...)) (failover-refusals workers jobs TEMPLATE exempt apart))
  out)


;; --- 赤にする反例 ---------------------------------------------------------------------------------------------------------

(deftest test-a-job-placeable-on-one-worker-names-the-worker-whose-stop-strands-it
  ;; 10-27 の形: 派生の job が 1 台(c)の名でしか置けず、c が止まると置き先が無い。答えは止める 1 台と job を名指す。
  (val workers #((! (host "a" #("pool-x"))) (! (host "b" #("pool-x"))) (! (host "c" #()))))
  (<- out (judged workers #((! (resident "derive" #("host-c"))) (! (resident "nowhere" #("gpu"))) (! (resident "pooled" #("pool-x"))))))
  (assert (= out #("worker c を止めると、c にしか置けない job derive(needs ['host-c'])が置き先を失う — 置ける worker を 2 台以上にするか、1 台に縛る訳を例外の表に書く"
                   "job nowhere(needs ['gpu'])はどの worker にも置けない — 置ける worker を 2 台以上にするか、訳を例外の表に書く"))
          out))


(deftest test-a-worker-that-cannot-take-every-job-it-may-receive-is-red
  ;; 組 {a, b} に job が 3 つ。どの置き方の後のどの 1 台の停止でも残りの 1 台が 3 つを受けるので、各 worker の枠に 3 つを数える。
  ;; 枠 2 は赤・枠 3 は通る・枠 5(予備を持つ — 等号でない)も通る。
  (val jobs #((! (resident "p1" #("pool-x") :peak 0 :settle 0)) (! (resident "p2" #("pool-x") :peak 0 :settle 0))
              (! (resident "p3" #("pool-x") :peak 0 :settle 0))))
  (<- short (judged #((! (host "a" #("pool-x") :capacity 2)) (! (host "b" #("pool-x") :capacity 3))) jobs))
  (assert (= short #("worker a: 置ける常駐の job 3(p1, p2, p3)+ surge 0 を数えると job の側の空きが -1(job-room-of)— どの 1 台が止まっても移る先の枠が足りるように、枠を 1 足すか置ける job を減らす"))
          short)
  (<- exact (judged #((! (host "a" #("pool-x") :capacity 3)) (! (host "b" #("pool-x") :capacity 5))) jobs))
  (assert (= exact #()) exact))


(deftest test-the-room-counts-the-task-reserve-and-one-surge-for-handoff-jobs
  ;; 枠 4・予約 1 の worker に置ける job 2 つのうち 1 つが handoff: 2 + surge 1 = 3 = 4 − 1 で入る。予約を 2 にすると -1 で赤。
  (val jobs #((! (resident "web" #("pool-x") :peak 0 :settle 0 :update "handoff")) (! (resident "api" #("pool-x") :peak 0 :settle 0))))
  (<- fits (judged #((! (host "a" #("pool-x") :capacity 4 :reserve 1)) (! (host "b" #("pool-x") :capacity 4 :reserve 1))) jobs))
  (assert (= fits #()) fits)
  (<- tight (judged #((! (host "a" #("pool-x") :capacity 4 :reserve 2)) (! (host "b" #("pool-x") :capacity 4 :reserve 1))) jobs))
  (assert (= tight #("worker a: 置ける常駐の job 2(web, api)+ surge 1 を数えると job の側の空きが -1(job-room-of)— どの 1 台が止まっても移る先の枠が足りるように、枠を 1 足すか置ける job を減らす"))
          tight))


(deftest test-a-worker-whose-memory-cannot-take-the-start-peak-of-the-moved-jobs-is-red
  ;; 組 {a, b} の job 2 つ(山 1000・900、落ち着き 300・250)。a の勘定 = 落ち着きの和 550 + b が止まった時に移る 2 つの上乗せ
  ;; 700 + 650 = 1900 MiB。予算 1900 は通り(等号は通す)、1800 は赤。
  (val jobs #((! (resident "p1" #("pool-x") :peak 1000 :settle 300)) (! (resident "p2" #("pool-x") :peak 900 :settle 250))))
  (<- fits (judged #((! (host "a" #("pool-x") :memory 1900)) (! (host "b" #("pool-x") :memory 1900))) jobs))
  (assert (= fits #()) fits)
  (<- short (judged #((! (host "a" #("pool-x") :memory 1800)) (! (host "b" #("pool-x") :memory 1900))) jobs))
  (assert (= short #("worker a: 置ける常駐の job の落ち着いた値の和 550 MiB(p1 300, p2 250)+ b が止まった時に移る job の起動の山の上乗せ 1350 MiB(p1 +700, p2 +650)= 1900 MiB が memory の予算 1800 MiB を越える"))
          short))


(deftest test-the-peak-is-counted-for-the-worst-single-stop-only
  ;; a は組 x で b と、組 y で c と job を分け合う。止まるのは 1 台なので、上乗せは b の停止(x の 700)と c の停止(y の 500)の大きい方
  ;; だけを数える: 落ち着き 600 + 700 = 1300。両方を足す(1800)勘定なら 1300 の予算で赤になる。
  (val jobs #((! (resident "x1" #("pool-x") :peak 1000 :settle 300)) (! (resident "y1" #("pool-y") :peak 800 :settle 300))))
  (val workers #((! (host "a" #("pool-x" "pool-y") :memory 1300)) (! (host "b" #("pool-x") :memory 1300)) (! (host "c" #("pool-y") :memory 1300))))
  (<- out (judged workers jobs))
  (assert (= out #()) out)
  (<- short (judged (+ #((! (host "a" #("pool-x" "pool-y") :memory 1299))) (cut workers 1 None)) jobs))
  (assert (= short #("worker a: 置ける常駐の job の落ち着いた値の和 600 MiB(x1 300, y1 300)+ b が止まった時に移る job の起動の山の上乗せ 700 MiB(x1 +700)= 1300 MiB が memory の予算 1299 MiB を越える"))
          short))


(deftest test-a-worker-that-may-take-both-jobs-of-a-kept-apart-pair-is-red
  ;; 番人 watch とその番人 watch-watch は同じ機体で一緒に止まってはいけない。組を交えれば通り、交わる worker b が在れば b を名指す。
  (val workers #((! (host "a" #("pool-x"))) (! (host "b" #("pool-x" "pool-y"))) (! (host "c" #("pool-y")))))
  (val apart #((KeptApart :first "watch" :second "watch-watch")))
  (<- crossed (judged workers #((! (resident "watch" #("pool-x"))) (! (resident "watch-watch" #("pool-y")))) :apart apart))
  (assert (= crossed #("worker b: 離す組の job watch・watch-watch を両方とも置ける — 同じ機体に載ると 1 台の停止で一緒に止まる(どちらかの置ける worker から b を外す)"))
          crossed)
  (val split #((! (host "a" #("pool-x"))) (! (host "b" #("pool-x"))) (! (host "c" #("pool-y"))) (! (host "d" #("pool-y")))))
  (<- apart-ok (judged split #((! (resident "watch" #("pool-x"))) (! (resident "watch-watch" #("pool-y")))) :apart apart))
  (assert (= apart-ok #()) apart-ok))


(deftest test-a-worker-without-a-memory-allowance-is-red-only-when-it-may-take-a-job
  (<- out (judged #((! (host "a" #("pool-x") :memory None)) (! (host "b" #("pool-x"))) (! (host "idle" #() :memory None)))
                  #((! (resident "p1" #("pool-x"))))))
  (assert (= out #("worker a: memory の予算が無い — 置ける常駐の job p1 の memory を照らせない")) out))


(deftest test-every-breach-is-named-without-stopping-at-the-first
  ;; 1 台に縛られた job・枠の不足・memory の不足・離す組が同じ世界に在れば、規則の順(置き先・worker ごとの枠と memory・離す組)にみな並ぶ。
  (val workers #((! (host "a" #("pool-x") :capacity 0 :memory 100)) (! (host "b" #("pool-x")))))
  (<- out (judged workers #((! (resident "solo" #("host-b"))) (! (resident "p1" #("pool-x"))))
                  :apart #((KeptApart :first "solo" :second "p1"))))
  (assert (= (lfor text out (.rstrip (get (.split text "—") 0)))
             ["worker b を止めると、b にしか置けない job solo(needs ['host-b'])が置き先を失う"
              "worker a: 置ける常駐の job 1(p1)+ surge 0 を数えると job の側の空きが -1(job-room-of)"
              "worker a: 置ける常駐の job の落ち着いた値の和 300 MiB(p1 300)+ b が止まった時に移る job の起動の山の上乗せ 700 MiB(p1 +700)= 1000 MiB が memory の予算 100 MiB を越える"
              "worker b: 離す組の job solo・p1 を両方とも置ける"])
          out))


;; --- 赤にしない物 ---------------------------------------------------------------------------------------------------------

(deftest test-a-per-machine-job-counts-once-on-every-worker-and-never-moves
  ;; needs が雛形の能力だけの job は置ける worker のそれぞれに 1 つずつ動く: 1 台にしか置けなくても赤にせず、枠には各 worker に 1 つ
  ;; 数え(枠 2 = 組の job 1 + 機体ごと 1 で入る・枠 1 は赤)、memory は落ち着きだけを数えて山の上乗せには数えない(他の機体へ移らない)。
  (val jobs #((! (resident "facts" #("host-facts") :peak 5000 :settle 100)) (! (resident "p1" #("pool-x") :peak 1000 :settle 300))))
  (<- out (judged #((! (host "a" #("pool-x") :capacity 2 :memory 1100)) (! (host "b" #("pool-x") :capacity 2 :memory 1100))) jobs))
  (assert (= out #()) out)
  (<- alone (judged #((! (host "a" #() :capacity 1 :memory 100))) #((! (resident "facts" #("host-facts") :peak 5000 :settle 100)))))
  (assert (= alone #()) alone)
  (<- short (judged #((! (host "a" #("pool-x") :capacity 1 :memory 1100)) (! (host "b" #("pool-x") :capacity 2 :memory 1100))) jobs))
  (assert (= (len short) 1) short)
  (assert (.startswith (get short 0) "worker a: 置ける常駐の job 2(facts, p1)+ surge 0 を数えると job の側の空きが -1") short))


(deftest test-a-job-in-the-exempt-table-may-stay-on-one-worker-but-still-takes-its-room
  ;; 例外の表の job は置き先が 1 台でも赤にしない。枠と memory には数える(a の枠 1 に表の job と組の job で 2 — 赤)。
  (val exempt #((ExemptJob :job "store" :reason "機体の disk の data を持つ")))
  (val jobs #((! (resident "store" #("host-a") :peak 0 :settle 0)) (! (resident "p1" #("pool-x") :peak 0 :settle 0))))
  (<- out (judged #((! (host "a" #("pool-x") :capacity 2)) (! (host "b" #("pool-x")))) jobs :exempt exempt))
  (assert (= out #()) out)
  (<- full (judged #((! (host "a" #("pool-x") :capacity 1)) (! (host "b" #("pool-x")))) jobs :exempt exempt))
  (assert (= (len full) 1) full)
  (assert (.startswith (get full 0) "worker a: 置ける常駐の job 2(store, p1)") full))


(deftest test-a-job-with-zero-replicas-is-not-placed-and-not-counted
  ;; replicas 0 の job は置かないので、1 台に縛られていても・離す組でも赤にせず、枠と memory にも数えない(枠 1 に組の job 1 だけ)。
  (val jobs #((! (resident "parked" #("host-a") :replicas 0)) (! (resident "p1" #("pool-x") :peak 0 :settle 0))))
  (<- out (judged #((! (host "a" #("pool-x") :capacity 1 :memory 0)) (! (host "b" #("pool-x") :capacity 1 :memory 0))) jobs
                  :apart #((KeptApart :first "parked" :second "p1"))))
  (assert (= out #()) out))


;; --- 枠の式を写さない -----------------------------------------------------------------------------------------------------

(deftest test-the-judgment-asks-job-room-of-and-does-not-copy-the-room-formula
  ;; 枠の空きの定義元は coordinator の job-room-of 1 つ(capacity − task-reserve − 置き先と surge)。この判断がその式を写すと、
  ;; job-room-of の数え方が変わった時に宣言の側の照らしと coordinator の置き方がずれる。module は job-room-of を呼び、worker の枠と
  ;; 予約の欄を自分で読まない。
  (val source (.read-text (/ (. (Path __file__) parent parent) "src" "doeff_cluster" "coordinator" "core" "failover_policy.hy")
                          :encoding "utf-8"))
  (val code (.join "\n" (gfor line (.splitlines source) (get (.split line ";") 0))))
  (assert (re.search r"\(import doeff_cluster\.coordinator\.core\.cluster_policy \[[^\]]*\bjob-room-of\b" code) "job-room-of を import していない")
  (assert (re.search r"\(<- room int \(job-room-of " code) "job-room-of を呼んでいない")
  (assert (not (re.search r"\.capacity\b|task-reserve\b|task_reserve\b" code)) "worker の枠・予約の欄を読んでいる — 枠の式を写さず job-room-of を呼ぶ"))
