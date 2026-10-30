;;; 分かれた子の環境変数を、頼みの env で置き換える process の境界(#3646)。待ちの子(worker/entry/warm_child.py)から分かれた子 A だけが、
;;; 入口の Program を走らせる前に呼ぶ — 今の task の子に Popen が env を渡す所と同じ役目(生の環境変数に触ってよいのは foundation の層)。
;;; 待ちの子そのものでは呼ばない(待ちの子は env の値を持たない — tests/test_warm_child.py の test_the_warm_child_keeps_no_request_env が
;;; 契約の検)。値は log・答え・断りの文に出さない。
(require doeff-hy.macros [defk val])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import os)


(defk replace-environ [env]
  {:pre [(: env tuple)] :post [(: % int)] :tags {:context "doeff-cluster" :role "foundation" :spells "env"}}
  "この process の環境変数を、名と値の組(#(名 値))の列で置き換えるため。答え = 置いた環境変数の数。"
  (.clear os.environ)
  (.update os.environ (dict env))
  (len os.environ))
