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
