;;; coordinator の業務の不変条件(packages/doeff-cluster/architecture.hy の defservice の :invariants が名指す判断 — 条は本番の code を
;;; 持つ package の architecture.hy に 1 か所で宣言する)。
;;;
;;; 条 C1 acknowledged-writes-survive: 返事を返した書き(盤の行)は、coordinator が止まり置き場から作り直された後も
;;; 残る。判断は記録(止める前に読めた行と、作り直した後に読めた行)を受けて破りの列を返す純関数 1 つ。記録を集めるのは検
;;; (tests/test_local.hy の coordinator の止まりの検)。
;;;
;;; 条 C2 one-place-per-job(#2804): 入れ替え(handoff)を宣言しない job の process は、同時に 2 つ生きていない(違う worker の上でも、
;;; 同じ名の worker の新しい世代の上でも)— 担い手が途絶(処理の止まり・網の途絶)しても、途絶の間に能力の合う worker が加わっても、
;;; 宣言の needs が変わっても、置ける worker が退いても、分断の最中に k8s が同じ名の新しい世代を作っても。
;;; 守り手は 3 つ: 他へ移せる job は時間の柵(worker の fence が coordinator の移し替えより先)・他へ移せない job は「移さない」(途絶しても
;;; 動かし続けてよい印を渡した担い手から、印を持たないと知らせるか Worker が消されるまで移さない — cluster_policy の keep-marks)・同じ名の
;;; 新しい世代とは長い方の柵(印の在る job も keep-fence-ms で止める — 数の前提は ClusterTiming.keep-fence-ms の註)。
;;; 判断は記録(job の process ごとの生きていた区間)を受けて重なりの列を返す純関数 1 つ。記録を集めるのは検(tests/test_keep_when_cut_off.hy
;;; の途絶の筋書き — 模擬の世界の ProcessesOf の process の始まりと終わり)。
;;;
;;; 条 C3 stopped-generation-gets-no-new-task: 止まり始めた worker の世代(drain の頼みを通らない止め — sigterm・機体の終了・手の kill)へ、
;;; 止まり始めの後に新しい task を置かない。その世代は task を始めずに抜け、切り離した task は同じ名の新しい世代へ渡らないので、置かれた
;;; task は lease まで止まる(#2819)。判断は記録(止めた世代の列と、止めた後・戻す前に読めた task の置き先の列)を受けて破りの列を返す
;;; 純関数 1 つ。記録を集めるのは検(tests/test_detached_runners.hy の drain を頼まない止めの検)。
;;;
;;; 条 C5 revision-never-goes-back: GET /state の coordinator の版(revision)は、読んだ順に減らない — coordinator が止まり置き場から
;;; 作り直されても(読み直せない置き場で空から起き直すと版が 0 へ戻り、worker と使い手が古い版の答えを新しいと取り違える)。判断は記録
;;; (読んだ順の版の列)を受けて、それまでの最大より小さい読みの組の列を返す純関数 1 つ。記録を集めるのは検(tests/test_local.hy の止まりの検)。
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
;;; 条 C7 placed-only-where-eligible: 置き先の worker は、job の needs を本当に提供する。判断は記録(読めた置き先・job ごとの needs・worker
;;; ごとの本当の能力)を受けて、needs を提供しない worker への置き先の列を返す純関数 1 つ。専用の能力(exclusive)の決まりと、drain の
;;; 期限の中の worker へ置かない事は、別の条として後から足す(今の検は tests/test_local.hy の gpu-only と tests/test_drain.hy)。
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


(defrecord PlacementSeen
  "条 L1 の記録 1 つ = GET /state の置き先 1 つ(job・worker・since-ms = 置いた時刻)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ str job)
  (#^ str worker)
  (#^ int since-ms))


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
