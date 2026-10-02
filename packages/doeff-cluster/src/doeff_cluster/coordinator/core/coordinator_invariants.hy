;;; coordinator の業務の不変条件(packages/doeff-cluster/architecture.hy の defservice の :invariants が名指す判断 — 条は本番の code を
;;; 持つ package の architecture.hy に 1 か所で宣言する)。
;;;
;;; 条 C1 acknowledged-writes-survive: 返事を返した書き(盤の行)は、coordinator が止まり置き場から作り直された後も
;;; 残る。判断は記録(止める前に読めた行と、作り直した後に読めた行)を受けて破りの列を返す純関数 1 つ。記録を集めるのは検
;;; (tests/test_local.hy の coordinator の止まりの検)。
;;;
;;; 条 C2 one-place-per-job(#2804): 入れ替え(handoff)を宣言しない job の process は、同時に 2 つの worker の上で生きていない — 担い手が
;;; 途絶(処理の止まり・網の途絶)しても、途絶の間に能力の合う worker が加わっても、宣言の needs が変わっても、置ける worker が退いても。
;;; 守り手は 2 つ: 他へ移せる job は時間の柵(worker の fence が coordinator の移し替えより先)・他へ移せない job は「移さない」(途絶しても
;;; 動かし続けてよい印を渡した担い手から、印を持たないと知らせるか Worker が消されるまで移さない — cluster_policy の keep-marks)。
;;; 判断は記録(job の process ごとの生きていた区間)を受けて重なりの列を返す純関数 1 つ。記録を集めるのは検(tests/test_keep_when_cut_off.hy
;;; の途絶の筋書き — 模擬の世界の ProcessesOf の process の始まりと終わり)。

(require doeff-hy.macros [defk val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass replace])  ; defrecord の展開が名指す


(defk acknowledged-writes-survive [before after]
  {:pre [(: before dict) (: after dict)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "条 C1: 止める前に読めた盤の行(返事を返した書き)が、作り直した後の盤に無ければ破り — 消えた行の鍵の列(空なら緑)。
   coordinator の置き場が返事の前の書きを落とさないことを、止まりの筋書きの記録から判じるため。"
  (tuple (sorted (gfor key before :if (not-in key after) key))))


(defrecord ProcessSpan
  "条 C2 の記録 1 つ = 入れ替えを宣言しない job の process 1 つが生きていた区間。worker = 走らせた worker の名・started-ms = 起きた時刻・
   ended-ms = 終わった時刻(まだ動いていれば None)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ str worker)
  (#^ int started-ms)
  (#^ (| int None) ended-ms))


(defrecord SpanOverlap
  "条 C2 の破り 1 つ = 同じ job の process が違う 2 つの worker の上で同時に生きていた組(first が先に起きた方)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ ProcessSpan first)
  (#^ ProcessSpan second))


(defk one-place-per-job [spans]
  {:pre [(: spans tuple)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "条 C2: 入れ替えを宣言しない job 1 つの process の生きていた区間の列(ProcessSpan)から、違う worker の上で区間が重なる組(SpanOverlap)の
   列を返す(空なら緑)。担い手の途絶と、その間の置ける worker の増減・宣言の変化の筋書きで、同じ job が 2 か所で走らない(書き先を 1 つに
   保つ — ReadWriteOnce の置き場を 2 か所から使わない)ことを、記録から判じるため。区間は [起きた時刻, 終わった時刻) で、終わりと始まりが
   同じ刻なら重ならない。"
  (val ordered (tuple (sorted spans :key (fn [s] #(s.started-ms s.worker)))))
  (val ending (fn [s] (if (is s.ended-ms None) (float "inf") s.ended-ms)))
  (tuple (gfor i (range (len ordered))
               j (range (+ i 1) (len ordered))
               :setv a (get ordered i)
               :setv b (get ordered j)
               :if (and (!= a.worker b.worker) (< b.started-ms (ending a)) (< a.started-ms (ending b)))
               (SpanOverlap :first a :second b))))
