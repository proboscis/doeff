;;; coordinator の業務の不変条件(packages/doeff-cluster/architecture.hy の defservice の :invariants が名指す判断 — 条は本番の code を
;;; 持つ package の architecture.hy に 1 か所で宣言する)。
;;;
;;; 条 C1 acknowledged-writes-survive: 返事を返した書き(盤の行)は、coordinator が止まり置き場から作り直された後も
;;; 残る。判断は記録(止める前に読めた行と、作り直した後に読めた行)を受けて破りの列を返す純関数 1 つ。記録を集めるのは検
;;; (tests/test_local.hy の coordinator の止まりの検)。

(require doeff-hy.macros [defk])


(defk acknowledged-writes-survive [before after]
  {:pre [(: before dict) (: after dict)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "judgment"}}
  "条 C1: 止める前に読めた盤の行(返事を返した書き)が、作り直した後の盤に無ければ破り — 消えた行の鍵の列(空なら緑)。
   coordinator の置き場が返事の前の書きを落とさないことを、止まりの筋書きの記録から判じるため。"
  (tuple (sorted (gfor key before :if (not-in key after) key))))
