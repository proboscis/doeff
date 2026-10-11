;;; 切り離した task(SubmitDetached)の純粋な判断: 送る effect を作る前の needs の検めと、置き方の名乗り(優先・同時数の上限)の
;;; 形の検めと本文の綴り(送り手と coordinator の受け口が同じ規則を読む — 2026-10-11)。
;;; 型は doeff_cluster.shared.intent.detached_model、coordinator への口(送る・待つ)は doeff_cluster.shared.protocol.detached。
(require doeff-hy.macros [defk deff <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import doeff [Program])
(import doeff_cluster.shared.intent.detached_model [SubmitDetached DetachedSubmitAnswer DETACHED-DEFAULT-LEASE-SECONDS
                                                    DETACHED-DEFAULT-RETAIN-SECONDS TaskLimit TASK-LIMIT-SCOPES])
(import doeff_cluster.shared.intent.runtime_env_model [EnvVar])
(import doeff_cluster.shared.core.capabilities [effect-needs-problem])


(deff task-priority-refusal [#^ (| dict list tuple str int float bool None) value]  ; defk にできない: coordinator の本文の読み(Program の外)が呼ぶ純粋な判断
  {:pre [(: value (| dict list tuple str int float bool None))] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "task の本文の欄 priority(置く順の優先)が受けられない理由(None = 欄が無い・整数)。真偽値は整数として受けない。"
  (if (or (is value None) (and (isinstance value int) (not (isinstance value bool))))
      None
      (.format "priority は整数(大きいほど先に置く・負 = 背景の仕事): {!r}" value)))


(deff task-limit-refusal [#^ (| dict list tuple str int float bool None) value]  ; defk にできない: coordinator の本文の読みと構築関数が呼ぶ純粋な判断
  {:pre [(: value (| dict list tuple str int float bool None))] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "同時数の上限 1 つ(本文の JSON の object {group limit scope})が受けられない理由(受けられれば None)。"
  (cond
    (not (isinstance value dict)) (.format "limits の 1 つは {{group limit scope}} の object: {!r}" value)
    (not (and (isinstance (.get value "group") str) (.get value "group")))
      (.format "limits の group は空でない文字列: {!r}" (.get value "group"))
    (not (and (isinstance (.get value "limit") int) (not (isinstance (.get value "limit") bool)) (>= (.get value "limit") 1)))
      (.format "limits の limit は 1 以上の整数: {!r}" (.get value "limit"))
    (not-in (.get value "scope") TASK-LIMIT-SCOPES)
      (.format "limits の scope は {} のどれか: {!r}" (sorted TASK-LIMIT-SCOPES) (.get value "scope"))
    True None))


(deff task-limits-refusal [#^ (| dict list tuple str int float bool None) value]  ; defk にできない: coordinator の本文の読み(Program の外)が呼ぶ純粋な判断
  {:pre [(: value (| dict list tuple str int float bool None))] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "task の本文の欄 limits(同時数の上限の列)が受けられない理由(None = 欄が無い・どれも受けられる)。"
  (cond
    (is value None) None
    (not (isinstance value list)) (.format "limits は {{group limit scope}} の object の列: {!r}" value)
    True (next (gfor item value :setv problem (task-limit-refusal item) :if problem problem) None)))


(deff task-limits-of [#^ (| dict list tuple str int float bool None) value]  ; defk にできない: coordinator の本文と保存の行の読み(Program の外)が呼ぶ純粋な読み
  {:pre [(: value (| dict list tuple str int float bool None))] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "judgment"}}
  "受けられる limits の JSON(task-limits-refusal が None の値)→ TaskLimit の tuple(欄が無ければ空)。"
  (tuple (gfor item (or value []) (TaskLimit :group (get item "group") :limit (get item "limit") :scope (get item "scope")))))


(deff task-limits-json [#^ tuple limits]  ; defk にできない: 本文の綴り(handler と模擬の宿)と保存の綴り(Program の外)が呼ぶ純粋な綴り
  {:pre [(: limits tuple)] :post [(: % list)] :tags {:context "doeff-cluster" :role "judgment"}}
  "TaskLimit の tuple → 本文と保存の JSON の形(task-limits-of の逆)。"
  (lfor l limits {"group" l.group "limit" l.limit "scope" l.scope}))


(deff task-placing-json [#^ int priority #^ tuple limits]  ; defk にできない: 本文の綴り(handler と模擬の宿 — Program の外)が呼ぶ純粋な綴り
  {:pre [(: priority int) (: limits tuple)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "judgment"}}
  "置き方の名乗りの本文の欄(既定の値 = 優先 0・上限なしの欄は置かない — 旧い coordinator は知らない欄を捨てる)。"
  (| (if priority {"priority" priority} {})
     (if limits {"limits" (task-limits-json limits)} {})))


(defk submit-detached-task [program key * [needs (frozenset)] [name ""] [lease-seconds DETACHED-DEFAULT-LEASE-SECONDS]
                           [retain-seconds DETACHED-DEFAULT-RETAIN-SECONDS] [environ #()] [priority 0] [limits #()]]
  {:pre [(: program Program) (: key str) (: needs (| frozenset tuple list set dict str None)) (: name str) (: lease-seconds float)
         (: retain-seconds float) (: environ (| (get tuple #(EnvVar ...)) dict)) (: priority int) (: limits tuple)]
   :post [(: % DetachedSubmitAnswer)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "SubmitDetached の構築関数 — 作り手はここを通す。needs が能力の名の空でない frozenset でなければ(書き忘れの空・旧い Requirement の
   tuple・label の形の名)送る前に TypeError で断る(ADR-DOE-CLUSTER-001 R4b)。environ の形(EnvVar の重ならない tuple — 写像は断る)は
   型が作る時に検める。置き方の名乗り(priority = 置く順の優先・limits = 同時数の上限 TaskLimit の tuple)は受け口と同じ規則で検める。検めた SubmitDetached をその場で出し、答え(DetachedSubmitted か DetachedUnreachable)を返す(defk は作った effect を
   値として返せない — doeff-hy の _guard-performed)。needs の検めを型(intent)の外のここに置くのは、intent が core の判断を読まないため(#2564)。
   (名を submit-detached にしないのは、coordinator の判断 detached_policy.submit-detached と sim の口 local.submit-detached が同じ名を持つため。)"
  (<- problem (effect-needs-problem needs))
  (when problem (raise (TypeError (+ "SubmitDetached.needs: " problem))))
  ;; 置き方の名乗り(優先・同時数の上限)は coordinator の受け口と同じ規則で、送る前に断る(2026-10-11)。
  (val placing (or (task-priority-refusal priority)
                   (if (all (gfor l limits (isinstance l TaskLimit)))
                       (task-limits-refusal (task-limits-json limits))
                       (.format "limits は TaskLimit の tuple: {!r}" limits))))
  (when placing (raise (TypeError (+ "SubmitDetached: " placing))))
  (<- answer DetachedSubmitAnswer
      (SubmitDetached program key :needs needs :name name :lease-seconds lease-seconds :retain-seconds retain-seconds :environ environ
                      :priority priority :limits limits))
  answer)
