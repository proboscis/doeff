;;; coordinator の業務の不変条件(packages/doeff-cluster/architecture.hy の defservice の :invariants が名指す判断 — 条は本番の code を
;;; 持つ package の architecture.hy に 1 か所で宣言する)。
;;;
;;; 条 C1 acknowledged-writes-survive: 返事を返した書き(盤の行)は、coordinator が止まり置き場から作り直された後も
;;; 残る。判断は記録(止める前に読めた行と、作り直した後に読めた行)を受けて破りの列を返す純関数 1 つ。記録を集めるのは検
;;; (tests/test_local.hy の coordinator の止まりの検)。
;;;
;;; 条 C15 acknowledged-values-survive・条 C16 declared-services-survive(#1976 の写しの C1 の残り): C1 は盤の行の鍵が残るかだけを見る。
;;; C15 = 書き手の止まった盤の行は、作り直した後も止める前と同じ値(置き場が古い値を読み直さない)。C16 = 受け付けた Service の宣言は、
;;; 作り直した後も在る。判断はどちらも記録(止める前と作り直した後の読み)を受けて破りの列を返す純関数 1 つ。記録を集めるのは検
;;; (tests/test_local.hy の止まりの検)。
;;;
;;; 条 C2 one-place-per-job(#2804): 入れ替え(handoff)を宣言しない job の process は、同時に 2 つ生きていない(違う worker の上でも、
;;; 同じ名の worker の新しい世代の上でも)— 担い手が途絶(処理の止まり・網の途絶)しても、途絶の間に能力の合う worker が加わっても、
;;; 宣言の needs が変わっても、置ける worker が退いても、分断の最中に k8s が同じ名の新しい世代を作っても。
;;; 守り手は 3 つ: 他へ移せる job は時間の柵(worker の fence が coordinator の移し替えより先)・他へ移せない job は「移さない」(途絶しても
;;; 動かし続けてよい印を渡した担い手から、印を持たないと知らせるか Worker が消されるまで移さない — cluster_policy の keep-marks。担い手の
;;; 沈黙が約束の期限 ClusterTiming.kept-reassign-after-ms を越えた後は時間の柵に戻る — 担い手が長い方の柵で止め切るより後に移す・条 C4 と
;;; 同じ形)・同じ名の新しい世代とは長い方の柵(印の在る job も keep-fence-ms で止める — 数の前提は ClusterTiming.keep-fence-ms の註)。
;;; 判断は記録(job の process ごとの生きていた区間)を受けて重なりの列を返す純関数 1 つ。記録を集めるのは検(tests/test_keep_when_cut_off.hy
;;; の途絶の筋書き — 模擬の世界の ProcessesOf の process の始まりと終わり)。
;;;
;;; 条 C3 stopped-generation-gets-no-new-task: 止まり始めた worker の世代(drain の頼みを通らない止め — sigterm・機体の終了・手の kill)へ、
;;; 止まり始めの後に新しい task を置かない。その世代は task を始めずに抜け、切り離した task は同じ名の新しい世代へ渡らないので、置かれた
;;; task は lease まで止まる(#2819)。名乗りの前にその世代へ置いて始まっていない task も、名乗りを吸った後はその世代に残さない(積みと
;;; 名乗りが同じ刻に届いた時に受ける順は決まっていない — #2976 の I-3 の赤 R5)。判断は記録(止めた世代の列と、止めた後・戻す前に読めた
;;; task の置き先の列)を受けて破りの列を返す純関数 1 つ。記録を集めるのは検(tests/test_detached_runners.hy の drain を頼まない止めの検と、
;;; 止める直前に置いた task の検)。
;;;
;;; 条 C5 revision-never-goes-back: GET /state の coordinator の版(revision)は、読んだ順に減らない — coordinator が止まり置き場から
;;; 作り直されても(読み直せない置き場で空から起き直すと版が 0 へ戻り、worker と使い手が古い版の答えを新しいと取り違える)。判断は記録
;;; (読んだ順の版の列)を受けて、それまでの最大より小さい読みの組の列を返す純関数 1 つ。記録を集めるのは検(tests/test_local.hy の止まりの検)。
;;;
;;; 条 C12 service-versions-never-go-back: GET /state の Service ごとの resourceVersion も、読んだ順に減らない — 作り直しの後も(版が戻ると、
;;; 版で書きの衝突を見る呼び手が古い版のまま書き戻せる)。C5 は coordinator 全体の版だけを見るので別の条にした(#1976 の
;;; 写しの C2 の残り)。判断は記録(読んだ順の Service ごとの版の列)を受けて、同じ Service のそれまでの最大より小さい読みの組の列を返す純関数 1 つ。
;;;
;;; 条 L2 alive-only-while-reachable: GET /workers/<名> が alive = true と答えるのは、その worker が lease-ms(+ 余裕)のうちに coordinator へ
;;; 届き得た時だけ — coordinator が止まり置き場から作り直された後も(最後の連絡の時刻を置き場から読み直せないと、死んだ worker を生きている
;;; と答え、置き先にも選ぶ — 本番 2026-09-25 の欠陥・L643)。判断は記録(生存の読みと、その worker が届かなくなった時刻)を受けて、届かなく
;;; なってから lease-ms + 余裕を過ぎて alive と答えた読みの列を返す純関数 1 つ。記録を集めるのは検(tests/test_local.hy の止まりの検)。
;;;
;;; 条 C6 running-within-capacity: どの瞬間も、worker の上で動いている job の process の数は、その worker が本当に置ける数(capacity)を
;;; 越えない。判断は記録(job の process ごとの生きていた区間と、worker ごとの本当の capacity)を受けて、越えた瞬間の列を返す純関数 1 つ。
;;; 記録を集めるのは検(tests/test_local.hy の容量の検)。
;;;
;;; 条 C17 jobs-stay-out-of-the-task-reserve(#3489): どの worker でも、常駐の job の置き先と並べた置き先(surge)の数の和は、capacity から
;;; task のために空けておく数(task-reserve)を引いた数を越えない — 常駐の job が枠を埋めても task の置き場が残る。判断は記録(GET /state の
;;; 読みごとの worker の常駐の job と surge の数と、worker ごとの本当の capacity と task-reserve)を受けて、越えた読みの列を返す純関数 1 つ。
;;; 記録を集めるのは検(tests/test_local.hy の予約の検)。
;;;
;;; 条 C7 placed-only-where-eligible: 置き先の worker は、job の needs を本当に提供する。判断は記録(読めた置き先・job ごとの needs・worker
;;; ごとの本当の能力)を受けて、needs を提供しない worker への置き先の列を返す純関数 1 つ。専用の能力の決まりは条 C10、drain の期限の
;;; 中の worker へ置かない事は別の条として後から足す(今の検は tests/test_drain.hy)。
;;;
;;; 条 C10 exclusive-workers-take-only-their-jobs: 専用の能力(exclusive)を本当に持つ worker には、その能力のどれかを needs に持つ job だけを
;;; 置く(専用の worker を他の job で埋めない)。判断は記録(読めた置き先・job ごとの needs・worker ごとの本当の専用の能力)を受けて、専用の
;;; worker へ置いた needs の合わない置き先の列を返す純関数 1 つ。
;;;
;;; 条 C13 ran-only-where-eligible: 子 process も、job の needs を本当に提供し、専用の能力を持つなら job がその能力を要る worker でだけ
;;; 動く(C7・C10 は coordinator が読ませる置き先 = 信念を見る。こちらは本当に動いた process = 真実を見る — #1976 の写しの C3 の残り)。
;;; 判断は記録(本当に動いた process の列・job ごとの needs・worker ごとの本当の能力と専用の能力)を受けて、資格の無い worker で動いた
;;; process の列を返す純関数 1 つ。
;;;
;;; 条 C14 runs-within-their-limit: 入れ替え(handoff)を宣言した job は同時に R + 1 まで(退いた process が上限 R = 宣言の readiness の
;;; retiredLimit か worker の WorkerPolicy.retired-limit・既定 3 — と今の process 1 つ・#4072 の D-3)、task は同時に 1 つまでしか動かない(C2 は入れ替えを宣言しない
;;; job だけを見る — #1976 の写しの C5 の残り)。判断は記録(本当に動いた process の列と、名ごとの上限)を
;;; 受けて、起きた瞬間に上限を越えていた process の列を返す純関数 1 つ。
;;;
;;; 条 C11 no-new-place-while-draining: drain を頼まれた worker には、drain の期限の内に新しい置き先を置かない(drain は worker を空けるための
;;; 頼み)。判断は記録(読めた置き先と、worker ごとの drain を頼んだ刻と期限)を受けて、drain の窓の内に置いた置き先の列を返す純関数 1 つ。
;;;
;;; 条 C8 moves-to-a-live-worker: 担い手の worker が死に、その job を本当に受けられる生きた worker が他に在るなら、その job は死から
;;; 移し替えの期限(reassign-after-ms)+ 余裕のうちに他の worker で動き始める。判断は記録(job の process の区間・死んだ worker と時刻・
;;; job を本当に受けられる他の worker)を受けて、期限のうちに他で動き始めなかった job の列を返す純関数 1 つ。
;;;
;;; 条 C9 tasks-answered-in-time: 送った task は、それを本当に走らせられる worker が在るなら、決めた時間(limit-ms)のうちに値で答えられる。
;;; 判断は記録(task ごとに送った刻・答えた刻・見ていた終わりの刻)を受けて、時間を過ぎて答えた・見ていた間に答えなかった task の列を返す
;;; 純関数 1 つ。記録を集めるのは検(tests/test_task_result_window.hy)。
;;;
;;; 条 L1 places-only-on-reachable: 新しい置き先は、lease-ms(+ 余裕)のうちに coordinator へ届き得た worker にだけ置く — coordinator が
;;; 止まり置き場から作り直された後も。判断は記録(読めた置き先の列と、worker ごとの届かなくなった時刻)を受けて、届かなくなってから
;;; lease-ms + 余裕を過ぎた後に置いた置き先の列を返す純関数 1 つ。記録を集めるのは検(tests/test_local.hy の止まりの検)。

(require doeff-hy.macros [defk val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass replace])  ; defrecord の展開が名指す


(defk acknowledged-writes-survive [before after]
  {:pre [(: before dict) (: after dict)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "条 C1: 止める前に読めた盤の行(返事を返した書き)が、作り直した後の盤に無ければ破り — 消えた行の鍵の列(空なら緑)。
   coordinator の置き場が返事の前の書きを落とさないことを、止まりの筋書きの記録から判じるため。"
  (tuple (sorted (gfor key before :if (not-in key after) key))))


(defk acknowledged-values-survive [before after]
  {:pre [(: before dict) (: after dict)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "条 C15: 書き手の止まった盤を止める前に読めた行が、作り直した後に別の値になっていれば破り — その鍵の列(空なら緑)。C1 は鍵が残るか
   だけを見るので、置き場が最後に返事を返した値でなく古い値を読み直していないことを、止まりの筋書きの記録から判じるため。作り直した
   後に無い鍵は C1 が名指すので判じない。"
  (tuple (sorted (gfor key before :if (and (in key after) (!= (get after key) (get before key))) key))))


(defk declared-services-survive [before after]
  {:pre [(: before (get tuple #(str ...))) (: after (get tuple #(str ...)))] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "条 C16: 止める前に GET /state で読めた Service の名(受け付けた宣言)が、作り直した後の読みに無ければ破り — その名の列(空なら緑)。
   coordinator の置き場が受け付けた宣言を落とさず、作り直しの後も同じ job を置き続けることを、止まりの筋書きの記録から判じるため。"
  (tuple (sorted (gfor name (set before) :if (not-in name after) name))))


(defrecord ProcessSpan
  "条 C2 の記録 1 つ = 入れ替えを宣言しない job の process 1 つが生きていた区間。worker = 走らせた worker の名(破りの名指しに使う)・
   started-ms = 起きた時刻・ended-ms = 終わった時刻(まだ動いていれば None)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ str worker)
  (#^ int started-ms)
  (#^ (| int None) ended-ms))


(defrecord SpanOverlap
  "条 C2 の破り 1 つ = 同じ job の process が 2 つ同時に生きていた組(first が先に起きた方)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ ProcessSpan first)
  (#^ ProcessSpan second))


(defk one-place-per-job [spans]
  {:pre [(: spans tuple)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "条 C2: 入れ替えを宣言しない job 1 つの process の生きていた区間の列(ProcessSpan)から、区間が重なる組(SpanOverlap)の列を返す(空なら
   緑)。担い手の途絶と、その間の置ける worker の増減・宣言の変化・同じ名の新しい世代の筋書きで、同じ job が 2 か所で走らない(書き先を
   1 つに保つ — ReadWriteOnce の置き場を 2 か所から使わない)ことを、記録から判じるため。同じ worker の上の止めてから起こす入れ替えは
   重ならない。区間は [起きた時刻, 終わった時刻) で、終わりと始まりが同じ刻なら重ならない。"
  (val ordered (tuple (sorted spans :key (fn [s] #(s.started-ms s.worker)))))
  (val ending (fn [s] (if (is s.ended-ms None) (float "inf") s.ended-ms)))
  (tuple (gfor i (range (len ordered))
               j (range (+ i 1) (len ordered))
               :setv a (get ordered i)
               :setv b (get ordered j)
               :if (and (< b.started-ms (ending a)) (< a.started-ms (ending b)))
               (SpanOverlap :first a :second b))))


(defrecord StoppedGeneration
  "条 C3 の記録: 止まり始めた worker の世代 1 つ(worker = 名・boot = その process の世代)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ str worker)
  (#^ str boot))


(defrecord TaskPlacementSeen
  "条 C3 の記録: 読めた task の置き先 1 つ(key = task の鍵・worker = 置かれた worker の名・boot = 読んだ時のその worker の世代)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ str key)
  (#^ str worker)
  (#^ str boot))


(defk stopped-generation-gets-no-new-task [stopped placed]
  {:pre [(: stopped (get tuple #(StoppedGeneration ...))) (: placed (get tuple #(TaskPlacementSeen ...)))] :post [(: % tuple)]
   :tags {:context "coordinator" :role "judgment"}}
  "条 C3: 止めた後に読めた task の置き先のうち、止まり始めた世代(同じ worker の名と世代)に置かれた物を破りとする — その task の鍵の列
   (空なら緑)。coordinator が止まる途中の世代へ task を置かず、lease まで止まる task を作らないことを、止めの筋書きの記録から判じるため。"
  (val gone (frozenset (gfor g stopped #(g.worker g.boot))))
  (tuple (sorted (gfor p placed :if (in #(p.worker p.boot) gone) p.key))))


(defrecord RevisionRead
  "条 C5 の記録 1 つ = GET /state の 1 回の読み(at-ms = 読んだ時刻・revision = coordinator の版)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ int at-ms)
  (#^ int revision))


(defrecord RevisionDrop
  "条 C5 の破り 1 つ = それまでの最大の読みより版が小さい読みの組(earlier = その最大の読み)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ RevisionRead earlier)
  (#^ RevisionRead later))


(defk revision-never-goes-back [reads]
  {:pre [(: reads (get tuple #(RevisionRead ...)))] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "条 C5: 読んだ順の版の読みの列(RevisionRead)から、それまでの最大の読みより版が小さい読みを、その最大の読みと組にした破りの列
   (RevisionDrop)を返す(空なら緑)。coordinator の作り直しが置き場から版を読み直し、版を 0 へ戻さないことを、止まりの筋書きの記録から
   判じるため。"
  (val highest (fn [i] (max (cut reads 0 (+ i 1)) :key (fn [r] r.revision))))
  (tuple (gfor i (range 1 (len reads))
               :setv prior (highest (- i 1))
               :setv read (get reads i)
               :if (< read.revision prior.revision)
               (RevisionDrop :earlier prior :later read))))


(defrecord ServiceVersionRead
  "条 C12 の記録 1 つ = GET /state の 1 回の読みの Service 1 つ(at-ms = 読んだ時刻・service = 名・version = その Service の resourceVersion)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ int at-ms)
  (#^ str service)
  (#^ int version))


(defrecord ServiceVersionDrop
  "条 C12 の破り 1 つ = 同じ Service のそれまでの最大の読みより resourceVersion が小さい読みの組(earlier = その最大の読み)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ ServiceVersionRead earlier)
  (#^ ServiceVersionRead later))


(defk service-versions-never-go-back [reads]
  {:pre [(: reads (get tuple #(ServiceVersionRead ...)))] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "条 C12: 読んだ順の Service ごとの版の読みの列(ServiceVersionRead)から、同じ Service のそれまでの最大の読みより resourceVersion が
   小さい読みを、その最大の読みと組にした破りの列(ServiceVersionDrop)を返す(空なら緑)。Service の版で書きの衝突を見る呼び手(宣言を
   PUT する道具)が、coordinator の作り直しの後に古い版で書き戻せないことを、止まりの筋書きの記録から判じるため。"
  (tuple (gfor i (range 1 (len reads))
               :setv read (get reads i)
               :setv earlier (lfor r (cut reads 0 i) :if (= r.service read.service) r)
               :if earlier
               :setv prior (max earlier :key (fn [r] r.version))
               :if (< read.version prior.version)
               (ServiceVersionDrop :earlier prior :later read))))


(defrecord WorkerProbe
  "条 L2 の記録 1 つ = GET /workers/<名> の 1 回の読み(at-ms = 読んだ時刻・worker = 名・alive = 生きていると答えたか)と、その worker が
   coordinator へ届かなくなった時刻(unreachable-since-ms — 死・網の切断。届いていれば None)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ int at-ms)
  (#^ str worker)
  (#^ bool alive)
  (#^ (| int None) unreachable-since-ms))


(defk alive-only-while-reachable [probes lease-ms slack-ms]
  {:pre [(: probes (get tuple #(WorkerProbe ...))) (: lease-ms int) (: slack-ms int)] :post [(: % tuple)]
   :tags {:context "coordinator" :role "judgment"}}
  "条 L2: 生存の読みの列から、届かなくなってから lease-ms + slack-ms を過ぎた後に alive と答えた読みを返す(空なら緑)。coordinator が
   作り直しの後も最後の連絡の時刻を読み直し、死んだ worker を生きていると答えない(置き先にも選ばない)ことを、筋書きの記録から判じるため。
   slack-ms = heartbeat の間隔と読みの拍の差の分の余裕。"
  (tuple (gfor p probes
               :if (and p.alive
                        (is-not p.unreachable-since-ms None)
                        (> p.at-ms (+ p.unreachable-since-ms lease-ms slack-ms)))
               p)))


(defrecord WorkerCapacity
  "条 C6 の記録 1 つ = worker 1 台の本当の capacity(置ける job の数)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ str worker)
  (#^ int capacity))


(defrecord OverCapacity
  "条 C6 の破り 1 つ = worker の上で capacity を越えて動いていた瞬間(at-ms = 越えた process の起きた時刻・running = その時に動いていた数)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ str worker)
  (#^ int at-ms)
  (#^ int running)
  (#^ int capacity))


(defk running-within-capacity [spans capacities]
  {:pre [(: spans (get tuple #(ProcessSpan ...))) (: capacities (get tuple #(WorkerCapacity ...)))] :post [(: % tuple)]
   :tags {:context "coordinator" :role "judgment"}}
  "条 C6: job の process の生きていた区間の列(ProcessSpan — どの job でも)と worker ごとの本当の capacity から、process が起きた瞬間に
   その worker で動いていた数が capacity を越えた所(OverCapacity)を返す(空なら緑)。coordinator が worker の置ける数より多く置かない
   ことを、筋書きの記録から判じるため。数えるのは起きた瞬間だけで足りる(数が増えるのは起きる時だけ)。区間は [起きた時刻, 終わった時刻)。"
  (val ending (fn [s] (if (is s.ended-ms None) (float "inf") s.ended-ms)))
  (tuple (gfor c capacities
               at (sorted (set (gfor s spans :if (= s.worker c.worker) s.started-ms)))
               :setv running (len (lfor o spans :if (and (= o.worker c.worker) (<= o.started-ms at) (< at (ending o))) o))
               :if (> running c.capacity)
               (OverCapacity :worker c.worker :at-ms at :running running :capacity c.capacity))))


(defrecord JobPlacesSeen
  "条 C17 の記録 1 つ = GET /state の 1 回の読みの worker 1 台(at-ms = 読んだ時刻・worker = 名・jobs = その worker の上の常駐の job の
   置き先と並べた置き先 surge の数の和)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ int at-ms)
  (#^ str worker)
  (#^ int jobs))


(defrecord WorkerReserve
  "条 C17 の記録 1 つ = worker 1 台の本当の capacity と、そのうち task のために空けておく数(task-reserve)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ str worker)
  (#^ int capacity)
  (#^ int task-reserve))


(defrecord ReserveTaken
  "条 C17 の破り 1 つ = 常駐の job と surge が task のために空けておく分に入っていた読み(seen = その読み・limit = capacity − task-reserve)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ JobPlacesSeen seen)
  (#^ int limit))


(defk jobs-stay-out-of-the-task-reserve [seen reserves]
  {:pre [(: seen (get tuple #(JobPlacesSeen ...))) (: reserves (get tuple #(WorkerReserve ...)))] :post [(: % tuple)]
   :tags {:context "coordinator" :role "judgment"}}
  "条 C17: GET /state の読みごとの worker の常駐の job と surge の数(JobPlacesSeen)と、worker ごとの本当の capacity と task-reserve から、
   数が capacity − task-reserve を越えた読み(ReserveTaken)を返す(空なら緑)。常駐の job(と入れ替えで並べた置き先)が worker の枠を
   埋め切っても、task のために空けておく分が残る(手番の task が置き場を失わない)ことを、筋書きの記録から判じるため。本当の値の記録が
   無い worker は判じない。"
  (tuple (gfor r reserves
               s seen
               :setv limit (- r.capacity r.task-reserve)
               :if (and (= s.worker r.worker) (> s.jobs limit))
               (ReserveTaken :seen s :limit limit))))


(defrecord PlacementSeen
  "条 L1 の記録 1 つ = GET /state の置き先 1 つ(job・worker・since-ms = 置いた時刻)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ str job)
  (#^ str worker)
  (#^ int since-ms))


(defrecord JobProcess
  "条 C8 の記録 1 つ = job の process 1 つの生きていた区間(job・worker・started-ms・ended-ms — まだ動いていれば None)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ str job)
  (#^ str worker)
  (#^ int started-ms)
  (#^ (| int None) ended-ms))


(defrecord StrandedJob
  "条 C8 の破り 1 つ = 担い手の死(worker・died-at-ms)から期限(due-ms)までに他の worker で動き始めなかった job。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ str job)
  (#^ str worker)
  (#^ int died-at-ms)
  (#^ int due-ms))


(defk moves-to-a-live-worker [processes deaths takers deadline-ms]
  {:pre [(: processes (get tuple #(JobProcess ...))) (: deaths (get tuple #(WorkerGone ...))) (: takers frozenset) (: deadline-ms int)]
   :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "条 C8: 死んだ worker(deaths)の上で死の瞬間に動いていた job ごとに、job を本当に受けられる生きた他の worker(takers — 真実の側の名)が
   在るのに、死から deadline-ms のうちに他の worker で process が起きていなければ破り(StrandedJob)とする(空なら緑)。coordinator が
   担い手の死の後に job を受けられる worker へ移すことを、筋書きの記録から判じるため。takers が空なら移せないので判じない。"
  (val ending (fn [p] (if (is p.ended-ms None) (float "inf") p.ended-ms)))
  (tuple (gfor d deaths
               p processes
               ;; 死の瞬間に動いていた process(死で止まった process の終わりは死の刻と同じ)。
               :if (and takers (= p.worker d.worker) (<= p.started-ms d.since-ms) (<= d.since-ms (ending p)))
               :setv due (+ d.since-ms deadline-ms)
               :if (not (any (gfor o processes (and (= o.job p.job) (!= o.worker d.worker) (in o.worker takers)
                                                     (>= o.started-ms d.since-ms) (<= o.started-ms due)))))
               (StrandedJob :job p.job :worker d.worker :died-at-ms d.since-ms :due-ms due))))


(defrecord TaskCall
  "条 C9 の記録 1 つ = 送った task 1 つ(name・sent-at-ms = 送った刻・answered-at-ms = 値で答えた刻 — 値で答えなければ None・
   refused = 値でなく失敗で答えた(走らせられない など)・observed-until-ms = 見ていた終わりの刻)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ str name)
  (#^ int sent-at-ms)
  (#^ (| int None) answered-at-ms)
  (#^ bool refused)
  (#^ int observed-until-ms))


(defk tasks-answered-in-time [calls limit-ms]
  {:pre [(: calls (get tuple #(TaskCall ...))) (: limit-ms int)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "条 C9: 送った task の記録の列から、値でなく失敗で答えた task・送ってから limit-ms を過ぎて答えた task・送ってから limit-ms を過ぎるまで
   見ていたのに答えなかった task を返す(空なら緑)。coordinator が task を走らせられる worker へ置き、値の答えを呼び手へ返すことを、
   筋書きの記録から判じるため。"
  (tuple (gfor c calls
               :setv due (+ c.sent-at-ms limit-ms)
               :if (or c.refused
                       (if (is c.answered-at-ms None) (> c.observed-until-ms due) (> c.answered-at-ms due)))
               c)))


(defrecord JobNeeds
  "条 C7 の記録 1 つ = job 1 つが要る能力(宣言の needs)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ str job)
  (#^ frozenset needs))


(defrecord WorkerAbility
  "条 C7 の記録 1 つ = worker 1 台が本当に提供する能力(provides)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ str worker)
  (#^ frozenset provides))


(defk placed-only-where-eligible [placements needs abilities]
  {:pre [(: placements (get tuple #(PlacementSeen ...))) (: needs (get tuple #(JobNeeds ...))) (: abilities (get tuple #(WorkerAbility ...)))]
   :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "条 C7: 読めた置き先の列から、置いた worker が job の needs を本当に提供していない置き先を返す(空なら緑)。coordinator が能力の合わない
   worker へ job を置かないことを、筋書きの記録から判じるため。needs か能力の記録が無い置き先は判じない。"
  (tuple (gfor p placements
               n needs
               a abilities
               :if (and (= n.job p.job) (= a.worker p.worker) (not (<= n.needs a.provides)))
               p)))


(defrecord WorkerExclusive
  "条 C10 の記録 1 つ = worker 1 台が本当に持つ専用の能力(exclusive — 空なら専用の worker ではない)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ str worker)
  (#^ frozenset exclusive))


(defk exclusive-workers-take-only-their-jobs [placements needs exclusives]
  {:pre [(: placements (get tuple #(PlacementSeen ...))) (: needs (get tuple #(JobNeeds ...))) (: exclusives (get tuple #(WorkerExclusive ...)))]
   :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "条 C10: 読めた置き先の列から、専用の能力を本当に持つ worker へ置いたのに、job の needs にその能力のどれも無い置き先を返す(空なら緑)。
   coordinator が専用の worker(例: gpu を持つ機体)を、その能力を要らない job で埋めないことを、筋書きの記録から判じるため。needs か
   専用の能力の記録が無い置き先は判じない。"
  (tuple (gfor p placements
               n needs
               x exclusives
               :if (and (= n.job p.job) (= x.worker p.worker) x.exclusive (not (& n.needs x.exclusive)))
               p)))


(defk ran-only-where-eligible [processes needs abilities exclusives]
  {:pre [(: processes (get tuple #(JobProcess ...))) (: needs (get tuple #(JobNeeds ...))) (: abilities (get tuple #(WorkerAbility ...)))
         (: exclusives (get tuple #(WorkerExclusive ...)))]
   :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "条 C13: 本当に動いた job の process の列から、走った worker が job の needs を本当に提供していない process と、専用の能力を本当に持つ
   worker でその能力を要らない job が走った process を返す(空なら緑)。C7・C10 は coordinator が読ませる置き先(信念)を判じるので、
   子 process が本当にどこで動いたか(真実)も能力に合うことを、筋書きの記録から判じるため。needs か能力の記録が無い process は判じない。"
  (tuple (gfor p processes
               n needs
               :if (= n.job p.job)
               :if (or (any (gfor a abilities (and (= a.worker p.worker) (not (<= n.needs a.provides)))))
                       (any (gfor x exclusives (and (= x.worker p.worker) x.exclusive (not (& n.needs x.exclusive))))))
               p)))


(defrecord RunLimit
  "条 C14 の記録 1 つ = 名 1 つ(job か task/<id>)の、同時に生きてよい process の数(入れ替えを宣言した job は退いた process の上限 R + 1・
   task は 1)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ str job)
  (#^ int limit))


(defk runs-within-their-limit [processes limits]
  {:pre [(: processes (get tuple #(JobProcess ...))) (: limits (get tuple #(RunLimit ...)))]
   :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "条 C14: 本当に動いた process の列から、起きた瞬間に同じ名で生きていた process の数がその名の上限を越えた process を返す(空なら緑)。
   入れ替え(handoff)の job は退いた process の上限 R に今の 1 つを足した R + 1 まで(#4072 の D-3)・task は 1 つまでしか同時に動かない
   (C2 は入れ替えを宣言しない job だけを見る)ことを、
   筋書きの記録から判じるため。上限の記録が無い名は判じない。区間は [起きた時刻, 終わった時刻)。"
  (val alive-at (fn [p t] (and (<= p.started-ms t) (or (is p.ended-ms None) (< t p.ended-ms)))))
  (tuple (gfor p processes
               r limits
               :if (= r.job p.job)
               :if (> (len (lfor q processes :if (and (= q.job p.job) (alive-at q p.started-ms)) q)) r.limit)
               p)))


(defrecord DrainWindow
  "条 C11 の記録 1 つ = worker 1 台の drain を頼んだ刻(since-ms)と期限(until-ms)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ str worker)
  (#^ int since-ms)
  (#^ int until-ms))


(defk no-new-place-while-draining [placements drains]
  {:pre [(: placements (get tuple #(PlacementSeen ...))) (: drains (get tuple #(DrainWindow ...)))] :post [(: % tuple)]
   :tags {:context "coordinator" :role "judgment"}}
  "条 C11: 読めた置き先の列から、drain を頼まれた worker へ、drain の窓([頼んだ刻, 期限])の内に置いた置き先を返す(空なら緑)。coordinator が
   空けると頼まれた worker へ新しい job を置かないことを、筋書きの記録から判じるため。drain の前からの置き先は判じない(置いた刻が窓の外)。"
  (tuple (gfor p placements
               d drains
               :if (and (= d.worker p.worker) (<= d.since-ms p.since-ms d.until-ms))
               p)))


(defrecord WorkerGone
  "条 L1 の記録 1 つ = worker が coordinator へ届かなくなった時刻(死・網の切断)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ str worker)
  (#^ int since-ms))


(defk places-only-on-reachable [placements gone lease-ms slack-ms]
  {:pre [(: placements (get tuple #(PlacementSeen ...))) (: gone (get tuple #(WorkerGone ...))) (: lease-ms int) (: slack-ms int)]
   :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "条 L1: 読めた置き先の列から、置いた worker が届かなくなって(gone)lease-ms + slack-ms を過ぎた後に置いた置き先を返す(空なら緑)。
   coordinator が作り直しの後も、死んだ worker を置き先に選ばないことを、筋書きの記録から判じるため。"
  (tuple (gfor p placements
               g gone
               :if (and (= g.worker p.worker) (> p.since-ms (+ g.since-ms lease-ms slack-ms)))
               p)))
