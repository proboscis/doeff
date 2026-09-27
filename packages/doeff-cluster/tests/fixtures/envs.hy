;;; テストの土台(foundation)— 本体の Program を受け、自分の handler(と scheduler)の下で走らせて答えを返す module の最上位の関数
;;; (ADR-DOE-CLUSTER-001 R3b・計画 10.1)。job の Program は (foundation 本体) で本体を包み、自分で scheduled を包まない。
;;;
;;; 本番の形の土台(plain-foundation など)は scheduler を含む — 実行先(job_entry)は handler を 1 つも足さないので、scheduler も土台が
;;; 並べる。sim の土台(sim-foundation など)は scheduler と時計を含まない — sim-cluster の外側の scheduler と仮想の時計が答える
;;; (含めると service の中に 2 つ目の scheduler ができ、Delay が外の scheduler を塞ぐ)。宣言には関数の参照 {"ref" "module:qualname"}
;;; で載り、handler の値は詰めない。
(require doeff-hy.macros [defk <-])
(import doeff [with-handlers DoExpr EffectBase Program])
(import doeff_core_effects.handlers [reader state])
(import doeff_core_effects.scheduler [scheduled])


(defk plain-foundation [body]
  {:pre [(: body (| Program EffectBase))] :post [(: % "body の答え")] :needs #{"cluster-net"}
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "見本の本番の形の土台: scheduler と、子の名と基準の値を Ask で答える reader 1 つの下で本体を走らせる。"
  (<- answer (scheduled (with-handlers [(reader {"worker" "child" "base" 100})] body)))
  answer)


(defk greeting-foundation [body]
  {:pre [(: body (| Program EffectBase))] :post [(: % "body の答え")] :needs #{"cluster-net"}
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "見本の本番の形の土台: scheduler と、挨拶の語を Ask で答える reader 1 つの下で本体を走らせる。"
  (<- answer (scheduled (with-handlers [(reader {"greeting" "hi"})] body)))
  answer)


(defk scheduler-foundation [body]
  {:pre [(: body (| Program EffectBase))] :post [(: % "body の答え")] :needs #{"cluster-net"}
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "見本の本番の形の土台: scheduler だけ(本体が自分の handler を全部並べる task の Program 用)。"
  (<- answer (scheduled body))
  answer)


(defk sim-foundation [body]
  {:pre [(: body (| Program EffectBase))] :post [(: % "body の答え")] :needs #{"cluster-net"}
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "見本の sim の土台: session の値の置き場(state)だけ。scheduler と時計は sim-cluster の外側、宿の契約とクラスタの約束は sim の宿が
   答える。"
  (<- answer (with-handlers [(state)] body))
  answer)


(defk sim-plain-foundation [body]
  {:pre [(: body (| Program EffectBase))] :post [(: % "body の答え")] :needs #{"cluster-net"}
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "見本の sim の土台: plain-foundation と同じ reader(scheduler を除く)。"
  (<- answer (with-handlers [(state) (reader {"worker" "child" "base" 100})] body))
  answer)
