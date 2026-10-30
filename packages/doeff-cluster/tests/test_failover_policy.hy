;; 1 台の worker が止まっても常駐の job が残りの worker の memory に入るか(N+1)の判断 failover-refusals(card ki-4689c7008147)。
;; 合成の世界で、規則ごとの反例を赤にし(1 台にしか置けない job・移る先の memory の不足・離す組を両方置ける worker・memory の予算の無い
;; worker)、赤にしてはいけない物(機体ごとの job・1 台に縛る訳を宣言した job・replicas 0 の job)を赤にしない。数の枠の規則 2 は消した
;; (card ki-2acb63aec0fd — job は memory で数える): job の数が多くても memory に入れば赤にしない。
;; 失敗ケース = 1 台に縛られた job を通す・job の数で赤にする・1 件目で止めて残りの破りを隠す。
(require doeff-hy.macros [defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import pathlib [Path])
(import re)
(import doeff_cluster [WorkerInfo])
(import doeff_cluster [FailoverWorker ResidentJob KeptApart])
(import doeff_cluster [failover-refusals per-machine?])

;; 雛形の能力(どの機体の worker も名乗る — needs がこれだけの job は機体ごとの job)。
(val TEMPLATE #("host-facts"))


(defk host [name pools [memory 4000]]
  {:pre [(: name str) (: pools tuple) (: memory (| int None))] :post [(: % FailoverWorker)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "機体の名の能力 host-<名> と雛形の能力と組の印 pools を名乗る、memory の予算 memory の worker を作るため(task の枠は判断が読まない)。"
  (FailoverWorker :info (WorkerInfo name (tuple (sorted #(f"host-{name}" #* TEMPLATE #* pools))) 0 :task-reserve 0 :background-room 0
                                    :memory-mib memory)
                  :memory-mib memory))


(defk resident [name needs [peak 1000] [settle 300] [replicas 1] [update "recreate"] [single-host None]]
  {:pre [(: name str) (: needs tuple) (: peak int) (: settle int) (: replicas int) (: update str) (: single-host (| str None))]
   :post [(: % ResidentJob)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "needs を要る常駐の job(起動の山 peak・落ち着いた値 settle MiB・1 台に縛る訳 single-host)を作るため。"
  (ResidentJob :name name :needs needs :replicas replicas :update update :peak-mib peak :settle-mib settle :single-host single-host))


(defk judged [workers jobs [apart #()]]
  {:pre [(: workers tuple) (: jobs tuple) (: apart tuple)] :post [(: % (get tuple #(str ...)))]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "合成の世界(雛形の能力 TEMPLATE)を判じた答えを得るため。"
  (<- out (get tuple #(str ...)) (failover-refusals workers jobs TEMPLATE apart))
  out)


;; --- 赤にする反例 ---------------------------------------------------------------------------------------------------------

(deftest test-a-job-placeable-on-one-worker-names-the-worker-whose-stop-strands-it
  ;; 10-27 の形: 派生の job が 1 台(c)の名でしか置けず、c が止まると置き先が無い。答えは止める 1 台と job を名指す。
  (val workers #((! (host "a" #("pool-x"))) (! (host "b" #("pool-x"))) (! (host "c" #()))))
  (<- out (judged workers #((! (resident "derive" #("host-c"))) (! (resident "nowhere" #("gpu"))) (! (resident "pooled" #("pool-x"))))))
  (assert (= out #("worker c を止めると、c にしか置けない job derive(needs ['host-c'])が置き先を失う — 置ける worker を 2 台以上にするか、1 台に縛る訳を job の宣言に :single-host \"<訳>\" で書く"
                   "job nowhere(needs ['gpu'])はどの worker にも置けない — 置ける worker を 2 台以上にするか、1 台に縛る訳を job の宣言に :single-host \"<訳>\" で書く"))
          out))


(deftest test-many-jobs-within-the-memory-are-not-red-for-their-number
  ;; 規則 2(数の枠)は消した(card ki-2acb63aec0fd): 組 {a, b} に落ち着き 10・山 20 の job が 40 本でも、memory に入れば(40 × 10 + 40 × 10
  ;; = 800 ≤ 予算 800)赤にしない。予算を 1 減らすと memory の規則 3 が赤にする(数でなく memory が歯止め)。
  (val jobs (tuple (gfor n (range 40) (! (resident (.format "p{:02}" n) #("pool-x") :peak 20 :settle 10)))))
  (<- many (judged #((! (host "a" #("pool-x") :memory 800)) (! (host "b" #("pool-x") :memory 800))) jobs))
  (assert (= many #()) many)
  (<- short (judged #((! (host "a" #("pool-x") :memory 799)) (! (host "b" #("pool-x") :memory 800))) jobs))
  (assert (= (len short) 1) short)
  (assert (.startswith (get short 0) "worker a: 置ける常駐の job の落ち着いた値の和 400 MiB") short))


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
  ;; 1 台に縛られた job・memory の不足・離す組が同じ世界に在れば、規則の順(置き先・worker ごとの memory・離す組)にみな並ぶ。
  (val workers #((! (host "a" #("pool-x") :memory 100)) (! (host "b" #("pool-x")))))
  (<- out (judged workers #((! (resident "solo" #("host-b"))) (! (resident "p1" #("pool-x"))))
                  :apart #((KeptApart :first "solo" :second "p1"))))
  (assert (= (lfor text out (.rstrip (get (.split text "—") 0)))
             ["worker b を止めると、b にしか置けない job solo(needs ['host-b'])が置き先を失う"
              "worker a: 置ける常駐の job の落ち着いた値の和 300 MiB(p1 300)+ b が止まった時に移る job の起動の山の上乗せ 700 MiB(p1 +700)= 1000 MiB が memory の予算 100 MiB を越える"
              "worker b: 離す組の job solo・p1 を両方とも置ける"])
          out))


;; --- 赤にしない物 ---------------------------------------------------------------------------------------------------------

(deftest test-a-per-machine-job-counts-once-on-every-worker-and-never-moves
  ;; needs が雛形の能力だけの job は置ける worker のそれぞれに 1 つずつ動く: 1 台にしか置けなくても赤にせず、memory は落ち着きだけを
  ;; 数えて山の上乗せには数えない(他の機体へ移らない — 予算 1100 = 落ち着き 100 + 300 + 組の job の山の上乗せ 700 で入る・1099 は赤)。
  (val jobs #((! (resident "facts" #("host-facts") :peak 5000 :settle 100)) (! (resident "p1" #("pool-x") :peak 1000 :settle 300))))
  (<- out (judged #((! (host "a" #("pool-x") :memory 1100)) (! (host "b" #("pool-x") :memory 1100))) jobs))
  (assert (= out #()) out)
  (<- alone (judged #((! (host "a" #() :memory 100))) #((! (resident "facts" #("host-facts") :peak 5000 :settle 100)))))
  (assert (= alone #()) alone)
  (<- short (judged #((! (host "a" #("pool-x") :memory 1099)) (! (host "b" #("pool-x") :memory 1100))) jobs))
  (assert (= (len short) 1) short)
  (assert (.startswith (get short 0) "worker a: 置ける常駐の job の落ち着いた値の和 400 MiB(facts 100, p1 300)") short))


(deftest test-a-template-job-spread-per-machine-keeps-the-per-machine-mark
  ;; 雛形の job を機体ごとに広げた job(needs = 雛形の能力 + 機体の印 host-<機体> 1 つ)は雛形の機体ごとを継ぐ: 1 台にしか置けなくても規則 1
  ;; で赤にしない(memory には自分の機体の分を数える — memory 1100 = 落ち着き 300 + 100 + 組の job の山の上乗せ 700)。雛形の能力を持たず機体の印だけの job・機体の印を 2 つ持つ job・雛形の外の能力を足した job は機体ごとで
  ;; ない(1 台に縛った job)。失敗ケース = 広げた job を 1 台に縛った job と読んで赤にする・印だけの job を機体ごとと読んで通す。
  (val jobs #((! (resident "facts-a" #("host-a" "host-facts") :peak 5000 :settle 100))
              (! (resident "facts-b" #("host-b" "host-facts") :peak 5000 :settle 100))
              (! (resident "p1" #("pool-x") :peak 1000 :settle 300))))
  (<- out (judged #((! (host "a" #("pool-x") :memory 1100)) (! (host "b" #("pool-x") :memory 1100))) jobs))
  (assert (= out #()) out)
  (<- spread bool (per-machine? #("host-a" "host-facts") TEMPLATE))
  (<- template bool (per-machine? #("host-facts") TEMPLATE))
  (<- pinned bool (per-machine? #("host-a") TEMPLATE))
  (<- two-marks bool (per-machine? #("host-a" "host-b" "host-facts") TEMPLATE))
  (<- more bool (per-machine? #("gpu" "host-a" "host-facts") TEMPLATE))
  (<- pooled bool (per-machine? #("host-facts" "pool-x") TEMPLATE))
  (assert (= #(spread template pinned two-marks more pooled) #(True True False False False False))
          #(spread template pinned two-marks more pooled))
  (<- stranded (judged #((! (host "a" #("pool-x"))) (! (host "b" #("pool-x")))) #((! (resident "derive" #("host-a") :peak 0 :settle 0)))))
  (assert (= (len stranded) 1) stranded))


(deftest test-a-job-declaring-single-host-may-stay-on-one-worker-but-still-takes-its-room
  ;; 1 台に縛る訳を宣言した job(:single-host — card ki-2acb63aec0fd で手で持つ例外の表から job の宣言へ移した)は、置き先が 1 台でも
  ;; 規則 1 で赤にしない。訳の無い同じ job は赤。memory には数える(a の予算 1000 に縛った job の落ち着き 500 と組の job 300 + b の停止で
  ;; 移る組の job の上乗せ 300 = 1100 — 赤)。
  ;; 失敗ケース = 訳を読まずに赤にする・訳の無い 1 台の job を通す・訳の在る job を memory から外す。
  (val bound (! (resident "store" #("host-a") :peak 500 :settle 500 :single-host "機体の disk の data を持つ")))
  (val jobs #(bound (! (resident "p1" #("pool-x") :peak 600 :settle 300))))
  (<- bare (judged #((! (host "a" #("pool-x"))) (! (host "b" #("pool-x"))))
                   #((! (resident "store" #("host-a") :peak 500 :settle 500)) (! (resident "p1" #("pool-x") :peak 600 :settle 300)))))
  (assert (= (len bare) 1) bare)
  (assert (.startswith (get bare 0) "worker a を止めると、a にしか置けない job store") bare)
  (assert (in ":single-host" (get bare 0)) bare)
  (<- out (judged #((! (host "a" #("pool-x"))) (! (host "b" #("pool-x")))) jobs))
  (assert (= out #()) out)
  (<- full (judged #((! (host "a" #("pool-x") :memory 1000)) (! (host "b" #("pool-x")))) jobs))
  (assert (= (len full) 1) full)
  (assert (.startswith (get full 0) "worker a: 置ける常駐の job の落ち着いた値の和 800 MiB(store 500, p1 300)") full))


(deftest test-a-job-with-zero-replicas-is-not-placed-and-not-counted
  ;; replicas 0 の job は置かないので、1 台に縛られていても・離す組でも赤にせず、memory にも数えない(予算 0 に落ち着き 0 の組の job 1 だけ)。
  (val jobs #((! (resident "parked" #("host-a") :replicas 0)) (! (resident "p1" #("pool-x") :peak 0 :settle 0))))
  (<- out (judged #((! (host "a" #("pool-x") :memory 0)) (! (host "b" #("pool-x") :memory 0))) jobs
                  :apart #((KeptApart :first "parked" :second "p1"))))
  (assert (= out #()) out))


;; --- task の枠を読まない -------------------------------------------------------------------------------------------------

(deftest test-the-judgment-reads-no-task-room-and-no-job-count
  ;; 規則 2 を消した後(card ki-2acb63aec0fd)、この判断は task の側の枠(task-reserve・background-room)も、数の空き(job-room-of —
  ;; coordinator から消した)も読まない。読むと task の側の値で常駐の job を赤にし、coordinator の置き方(memory だけで job を数える)と
  ;; ずれる。
  (val source (.read-text (/ (. (Path __file__) parent parent) "src" "doeff_cluster" "_coordinator" "core" "failover_policy.hy")
                          :encoding "utf-8"))
  (val code (.join "\n" (gfor line (.splitlines source) (get (.split line ";") 0))))
  (assert (not (re.search r"job-room-of|HANDOFF-SURGE|load-alone|room-refusal" code)) "数の枠の判断が残っている")
  (assert (not (re.search r"\.capacity\b|task-reserve\b|task_reserve\b|background-room\b" code)) "worker の task の枠の欄を読んでいる"))
