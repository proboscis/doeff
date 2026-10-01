;;; 切り離した task(SubmitDetached)の純粋な判断: 送る effect を作る前の needs の検め。
;;; 型は doeff_cluster.shared.intent.detached_model、coordinator への口(送る・待つ)は doeff_cluster.shared.protocol.detached。
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import doeff [Program])
(import doeff_cluster.shared.intent.detached_model [SubmitDetached DetachedSubmitAnswer DETACHED-DEFAULT-LEASE-SECONDS
                                                    DETACHED-DEFAULT-RETAIN-SECONDS])
(import doeff_cluster.shared.intent.runtime_env_model [EnvVar])
(import doeff_cluster.shared.core.capabilities [effect-needs-problem])


(defk submit-detached-task [program key * [needs (frozenset)] [name ""] [lease-seconds DETACHED-DEFAULT-LEASE-SECONDS]
                           [retain-seconds DETACHED-DEFAULT-RETAIN-SECONDS] [environ #()]]
  {:pre [(: program Program) (: key str) (: needs (| frozenset tuple list set dict str None)) (: name str) (: lease-seconds float)
         (: retain-seconds float) (: environ (| (get tuple #(EnvVar ...)) dict))]
   :post [(: % DetachedSubmitAnswer)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "SubmitDetached の構築関数 — 作り手はここを通す。needs が能力の名の空でない frozenset でなければ(書き忘れの空・旧い Requirement の
   tuple・label の形の名)送る前に TypeError で断る(ADR-DOE-CLUSTER-001 R4b)。environ の形(EnvVar の重ならない tuple — 写像は断る)は
   型が作る時に検める。検めた SubmitDetached をその場で出し、答え(DetachedSubmitted か DetachedUnreachable)を返す(defk は作った effect を
   値として返せない — doeff-hy の _guard-performed)。needs の検めを型(intent)の外のここに置くのは、intent が core の判断を読まないため(#2564)。
   (名を submit-detached にしないのは、coordinator の判断 detached_policy.submit-detached と sim の口 local.submit-detached が同じ名を持つため。)"
  (<- problem (effect-needs-problem needs))
  (when problem (raise (TypeError (+ "SubmitDetached.needs: " problem))))
  (<- answer DetachedSubmitAnswer
      (SubmitDetached program key :needs needs :name name :lease-seconds lease-seconds :retain-seconds retain-seconds :environ environ))
  answer)
