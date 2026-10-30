;;; record-store(effect の記録の置き場)の業務の不変条件(packages/doeff-cluster/architecture.hy の defservice record-store の :invariants が
;;; 名指す判断 — 条は本番の code を持つ package の architecture.hy に 1 か所で宣言する)。
;;;
;;; 条 R1 prune-keeps-runs-whole: 保持の期限で消すのは run を丸ごとで、run の途中だけを残さない(再生は run の始まりから走らせるので、
;;; 頭の区切りの欠けた run は再生できない)。判断は記録(消す前に読めた run ごとの text と、消した後に読めた text)を受けて、一部だけ
;;; 残った run の列を返す純関数 1 つ。記録を集めるのは検(tests/test_record_files_contract.hy の保持の検)。

(require doeff-hy.macros [defk])


(defk prune-keeps-runs-whole [before after]
  {:pre [(: before dict) (: after dict)] :post [(: % tuple)] :tags {:context "record-store" :role "judgment"}}
  "条 R1: 消す前の run の鍵 → 記録の text と、消した後の同じ鍵 → text(消えた run は None か鍵が無い)から、丸ごと残ってもおらず丸ごと
   消えてもいない run の鍵の列を返す(空なら緑)。保持の handler が run の一部の区切りだけを消して再生できない run を残さないことを、
   保持の筋書きの記録から判じるため。"
  (tuple (sorted (gfor #(key text) (.items before)
                       :setv kept (.get after key)
                       :if (not (or (is kept None) (= kept text)))
                       key))))
