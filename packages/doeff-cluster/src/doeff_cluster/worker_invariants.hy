;;; worker の業務の不変条件(packages/doeff-cluster/architecture.hy の defservice worker の :invariants が名指す判断 — 条は本番の code を
;;; 持つ package の architecture.hy に 1 か所で宣言する)。
;;;
;;; 条 W1 handoff-keeps-a-ready-writer: 入れ替え(handoff)を宣言した Service は、入れ替えの間も書き手が居続ける — 最初の世代が Ready に
;;; なってから、どの時点でも Ready を報告した生きた process が 1 つ以上在る(旧は新が Ready になった後にだけ止める — worker_policy の
;;; handoff-actions / retired-actions)。判断は記録(世代ごとの最初の Ready の時刻と終わった時刻)を受けて空白の列を返す純関数 1 つ。
;;; 記録を集めるのは検(tests/test_local.hy の handoff の入れ替えの検)。

(require doeff-hy.macros [defk])


(defk handoff-keeps-a-ready-writer [lifetimes]
  {:pre [(: lifetimes tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "judgment"}}
  "条 W1: 世代ごとの #(最初の Ready の時刻 終わった時刻) の列(Ready を一度も報告しなかった世代は最初が None・まだ動く世代は終わりが
   None)から、最初の Ready から最後の終わりまでの間で Ready の生きた process が 1 つも無い区間 #(始め 終わり) の列を返す(空なら緑)。
   入れ替えの worker が新の準備の間に旧を止めて書き手の空白を作らないことを、筋書きの記録から判じるため。"
  (val spans (sorted (gfor #(ready ended) lifetimes :if (is-not ready None) #(ready (if (is ended None) (float "inf") ended)))))
  (tuple (gfor i (range 1 (len spans))
               :setv covered (max (gfor #(_ end) (cut spans 0 i) end))
               :setv start (get spans i 0)
               :if (< covered start)
               #(covered start))))
