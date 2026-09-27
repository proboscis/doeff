;;; テストの土台(foundation)— handler の組を返す module の最上位の関数(ADR-DOE-CLUSTER-001 R3b)。
;;;
;;; 以前はここに env の関数(`(config ctx) → handler の list` — job_entry の --env に import path で渡した)を置いていた。job は Program の
;;; 値 1 つになり、実行先は handler を足さない(R2)ので、handler の組は Program が本体の with-handlers で自分で並べる。ここに残るのは、
;;; その Program が引数に受けて呼ぶ土台の関数だけ(宣言には関数の参照 {"ref" "module:qualname"} で載り、handler の値は詰めない)。
(require doeff-hy.macros [defk])
(import doeff_core_effects.handlers [reader])


(defk plain-foundation []
  {:pre [] :post [(: % list)] :needs #{"cluster-net"} :tags {:context "doeff-cluster-test" :role "foundation"}}
  "見本の土台: 子の名と基準の値を Ask で答える reader 1 つ。"
  [(reader {"worker" "child" "base" 100})])


(defk greeting-foundation []
  {:pre [] :post [(: % list)] :needs #{"cluster-net"} :tags {:context "doeff-cluster-test" :role "foundation"}}
  "見本の土台: 挨拶の語を Ask で答える reader 1 つ。"
  [(reader {"greeting" "hi"})])
