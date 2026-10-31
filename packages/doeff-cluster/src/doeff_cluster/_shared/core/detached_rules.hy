;;; 切り離した task(SubmitDetached)の純粋な判断: 送る effect を作る前の needs の検めと、置き方の名乗り(優先・同時数の上限の組)の
;;; 形の検めと本文の綴り(送り手と coordinator の受け口が同じ規則を読む — 2026-10-11)。系が宣言する組の上限(GroupLimit)の形の検めと
;;; 綴りも同じ所に置く(系の構成子・coordinator の資源の口・保存の読み直しが同じ規則を読む — 2026-10-12)。
;;; 型は doeff_cluster._shared.intent.detached_model、coordinator への口(送る・待つ)は doeff_cluster._shared.protocol.detached。
(require doeff-hy.macros [defk deff <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import doeff [Program])
(import doeff_cluster._shared.intent.detached_model [SubmitDetached DetachedSubmitAnswer DETACHED-DEFAULT-LEASE-SECONDS
                                                    DETACHED-DEFAULT-RETAIN-SECONDS TaskLimit GroupLimit TASK-LIMIT-SCOPES])
(import doeff_cluster._shared.intent.runtime_env_model [EnvVar])
(import doeff_cluster._shared.core.capabilities [effect-needs-problem])


(deff task-priority-refusal [#^ (| dict list tuple str int float bool None) value]  ; defk にできない: coordinator の本文の読み(Program の外)が呼ぶ純粋な判断
  {:pre [(: value (| dict list tuple str int float bool None))] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "task の本文の欄 priority(置く順の優先)が受けられない理由(None = 欄が無い・整数)。真偽値は整数として受けない。"
  (if (or (is value None) (and (isinstance value int) (not (isinstance value bool))))
      None
      (.format "priority は整数(大きいほど先に置く・負 = 背景の仕事): {!r}" value)))


;; 知らせ先(SubmitDetached の notify — 出口の行の欄)の長さの上限(key と同じ — detached_policy.DETACHED-MAX-KEY-LENGTH)。
(val TASK-NOTIFY-MAX-LENGTH 200)


(deff task-notify-refusal [#^ (| dict list tuple str int float bool None) value]  ; defk にできない: coordinator の本文の読み(Program の外)と構築関数が呼ぶ純粋な判断
  {:pre [(: value (| dict list tuple str int float bool None))] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "task の本文の欄 notify(終わりの知らせ先)が受けられない理由(None = 欄が無いか、空白を持たない
   TASK-NOTIFY-MAX-LENGTH 字までの文字列)。読み手は語の頭で自分の行を選ぶので、空白で語を割らせない。"
  (if (or (is value None)
          (and (isinstance value str) (<= (len value) TASK-NOTIFY-MAX-LENGTH) (not (any (gfor c value (.isspace c))))))
      None
      (.format "notify は空白を持たない {} 字までの文字列(終わりの知らせ先): {!r}" TASK-NOTIFY-MAX-LENGTH value)))


(deff task-notify-json [#^ str notify]  ; defk にできない: 本文の綴り(handler と模擬の宿 — Program の外)が呼ぶ純粋な綴り
  {:pre [(: notify str)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "judgment"}}
  "知らせ先の本文の欄を綴るため(空 = 知らせない は欄を置かない — 旧い coordinator は知らない欄を捨てる)。"
  (if notify {"notify" notify} {}))


(val TASK-LIMIT-KEYS (frozenset #("group" "part")))


(deff task-limit-refusal [#^ (| dict list tuple str int float bool None) value]  ; defk にできない: coordinator の本文の読みと構築関数が呼ぶ純粋な判断
  {:pre [(: value (| dict list tuple str int float bool None))] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "同時数の上限の組の名乗り 1 つ(本文の JSON の object {group part?})が受けられない理由(受けられれば None)。上限の値と範囲の欄
   (limit・scope — 2026-10-12 より前の送り手の形)は受けない — 値と範囲は系の宣言の GroupLimit が持つ。"
  (cond
    (not (isinstance value dict)) (.format "limits の 1 つは {{group part?}} の object: {!r}" value)
    (- (set value) TASK-LIMIT-KEYS)
      (.format "limits の 1 つは組の名 group(と区分 part)だけを名乗る — 上限の値と範囲は系の宣言(:task-limits)が持つ。受けない欄: {}"
               (sorted (- (set value) TASK-LIMIT-KEYS)))
    (not (and (isinstance (.get value "group") str) (.get value "group")))
      (.format "limits の group は空でない文字列: {!r}" (.get value "group"))
    (and (in "part" value) (not (and (isinstance (get value "part") str) (get value "part"))))
      (.format "limits の part は空でない文字列(区分を名乗らない時は欄を置かない): {!r}" (get value "part"))
    True None))


(deff task-limits-refusal [#^ (| dict list tuple str int float bool None) value]  ; defk にできない: coordinator の本文の読み(Program の外)が呼ぶ純粋な判断
  {:pre [(: value (| dict list tuple str int float bool None))] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "task の本文の欄 limits(同時数の上限の組の列)が受けられない理由(None = 欄が無い・どれも受けられる)。同じ組を 2 度名乗る列は断る。"
  (cond
    (is value None) None
    (not (isinstance value list)) (.format "limits は {{group part?}} の object の列: {!r}" value)
    True (or (next (gfor item value :setv problem (task-limit-refusal item) :if problem problem) None)
             (let [groups (lfor item value (get item "group"))
                   twice (sorted (sfor g groups :if (> (.count groups g) 1) g))]
               (if twice (.format "limits に同じ組が 2 度ある: {}" twice) None)))))


(deff task-limits-of [#^ (| dict list tuple str int float bool None) value]  ; defk にできない: coordinator の本文と保存の行の読み(Program の外)が呼ぶ純粋な読み
  {:pre [(: value (| dict list tuple str int float bool None))] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "judgment"}}
  "受けられる limits の JSON(task-limits-refusal が None の値)→ TaskLimit の tuple(欄が無ければ空)。"
  (tuple (gfor item (or value []) (TaskLimit :group (get item "group") :part (.get item "part" "")))))


(deff task-limit-json [#^ TaskLimit limit]  ; defk にできない: 本文の綴り(handler と模擬の宿)と保存の綴り(Program の外)が呼ぶ純粋な綴り
  {:pre [(: limit TaskLimit)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "judgment"}}
  "組の名乗り 1 つ → 本文と保存の JSON の object を綴るため(区分を名乗らない組は part の欄を置かない)。"
  (| {"group" limit.group} (if limit.part {"part" limit.part} {})))


(deff task-limits-json [#^ tuple limits]  ; defk にできない: 本文の綴り(handler と模擬の宿)と保存の綴り(Program の外)が呼ぶ純粋な綴り
  {:pre [(: limits tuple)] :post [(: % list)] :tags {:context "doeff-cluster" :role "judgment"}}
  "TaskLimit の tuple → 本文と保存の JSON の形(task-limits-of の逆)。"
  (lfor l limits (task-limit-json l)))


(val GROUP-LIMIT-KEYS (frozenset #("scope" "limit" "perPart" "system")))


(deff group-limit-refusal [#^ (| dict list tuple str int float bool None) name #^ (| dict list tuple str int float bool None) spec]  ; defk にできない: 系の構成子(module の読み込みの時)・coordinator の受け口と保存の読み直し(Program の外)が呼ぶ純粋な判断
  {:pre [(: name (| dict list tuple str int float bool None)) (: spec (| dict list tuple str int float bool None))] :post [(: % (| str None))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "系が宣言する組の上限 1 つ(name = 組の名・spec = JSON の object {scope limit perPart? system?})が受けられない理由(受けられれば None)。
   系の構成子(service_build.system-of)・coordinator の資源の口(GroupLimit の作り・書き直し)・保存の読み直しが同じ規則で読む。
   system = 組を宣言した系の名(資源の口が別の系の書き直しを断る鍵)。無い組は持ち主の無い組(系の構成子は必ず付ける — 無いのは
   74ebb50b6 の宣言の道具が書いた組と、運用者が資源の口に直に作った組)。"
  (cond
    (not (and (isinstance name str) name (not-in "/" name))) (.format "組の名は「/」を含まない空でない文字列: {!r}" name)
    (not (isinstance spec dict)) (.format "組 {} の上限は {{scope limit perPart? system?}} の object: {!r}" name spec)
    (- (set spec) GROUP-LIMIT-KEYS) (.format "組 {} の上限の知らない欄: {}" name (sorted (- (set spec) GROUP-LIMIT-KEYS)))
    (not-in (.get spec "scope") TASK-LIMIT-SCOPES)
      (.format "組 {} の scope は {} のどれか: {!r}" name (sorted TASK-LIMIT-SCOPES) (.get spec "scope"))
    (not (and (isinstance (.get spec "limit") int) (not (isinstance (.get spec "limit") bool)) (>= (.get spec "limit") 1)))
      (.format "組 {} の limit は 1 以上の整数: {!r}" name (.get spec "limit"))
    (not (isinstance (.get spec "perPart" False) bool)) (.format "組 {} の perPart は真偽値: {!r}" name (.get spec "perPart"))
    (and (in "system" spec) (not (and (isinstance (get spec "system") str) (get spec "system"))))
      (.format "組 {} の system(組を宣言した系の名)は空でない文字列(持ち主の無い組は欄を置かない): {!r}" name (get spec "system"))
    True None))


(deff group-limit-of [#^ str name #^ dict spec]  ; defk にできない: 系の構成子と coordinator の受け口・保存の読み直し(Program の外)が呼ぶ純粋な読み
  {:pre [(: name str) (: spec dict)] :post [(: % GroupLimit)] :tags {:context "doeff-cluster" :role "judgment"}}
  "受けられる組の上限(group-limit-refusal が None の名と spec)→ GroupLimit。"
  (GroupLimit :group name :scope (get spec "scope") :limit (get spec "limit") :per-part (.get spec "perPart" False) :system (.get spec "system" "")))


(deff group-limit-spec [#^ GroupLimit limit]  ; defk にできない: 宣言の行の組み立て(module の外の CLI)と coordinator の資源の行・保存の綴り(Program の外)が呼ぶ純粋な綴り
  {:pre [(: limit GroupLimit)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "judgment"}}
  "GroupLimit → 資源 GroupLimit の spec の JSON の形(group-limit-of の逆 — 区分ごとに数えない組は perPart の欄を、持ち主の無い組は
   system の欄を置かない)。"
  (| {"scope" limit.scope "limit" limit.limit} (if limit.per-part {"perPart" True} {}) (if limit.system {"system" limit.system} {})))


(deff task-placing-json [#^ int priority #^ tuple limits]  ; defk にできない: 本文の綴り(handler と模擬の宿 — Program の外)が呼ぶ純粋な綴り
  {:pre [(: priority int) (: limits tuple)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "judgment"}}
  "置き方の名乗りの本文の欄(既定の値 = 優先 0・上限なしの欄は置かない — 旧い coordinator は知らない欄を捨てる)。"
  (| (if priority {"priority" priority} {})
     (if limits {"limits" (task-limits-json limits)} {})))


(defk submit-detached-task [program key * [needs (frozenset)] [name ""] [lease-seconds DETACHED-DEFAULT-LEASE-SECONDS]
                           [retain-seconds DETACHED-DEFAULT-RETAIN-SECONDS] [environ #()] [priority 0] [limits #()] [notify ""]]
  {:pre [(: program Program) (: key str) (: needs (| frozenset tuple list set dict str None)) (: name str) (: lease-seconds float)
         (: retain-seconds float) (: environ (| (get tuple #(EnvVar ...)) dict)) (: priority int) (: limits tuple) (: notify str)]
   :post [(: % DetachedSubmitAnswer)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "SubmitDetached の構築関数 — 作り手はここを通す。needs が能力の名の空でない frozenset でなければ(書き忘れの空・旧い Requirement の
   tuple・label の形の名)送る前に TypeError で断る(ADR-DOE-CLUSTER-001 R4b)。environ の形(EnvVar の重ならない tuple — 写像は断る)は
   型が作る時に検める。置き方の名乗り(priority = 置く順の優先・limits = 同時数の上限 TaskLimit の tuple)と知らせ先 notify(終わりの
   出口へ積む先 — 空 = 知らせない)は受け口と同じ規則で検める。検めた SubmitDetached をその場で出し、答え(DetachedSubmitted か DetachedUnreachable)を返す(defk は作った effect を
   値として返せない — doeff-hy の _guard-performed)。needs の検めを型(intent)の外のここに置くのは、intent が core の判断を読まないため(#2564)。
   (名を submit-detached にしないのは、coordinator の判断 detached_policy.submit-detached と sim の口 local.submit-detached が同じ名を持つため。)"
  (<- problem (effect-needs-problem needs))
  (when problem (raise (TypeError (+ "SubmitDetached.needs: " problem))))
  ;; 置き方の名乗り(優先・同時数の上限)は coordinator の受け口と同じ規則で、送る前に断る(2026-10-11)。
  (val placing (or (task-priority-refusal priority)
                   (if (all (gfor l limits (isinstance l TaskLimit)))
                       (task-limits-refusal (task-limits-json limits))
                       (.format "limits は TaskLimit の tuple: {!r}" limits))
                   ;; 知らせ先も受け口と同じ規則で、送る前に断る。
                   (task-notify-refusal notify)))
  (when placing (raise (TypeError (+ "SubmitDetached: " placing))))
  (<- answer DetachedSubmitAnswer
      (SubmitDetached program key :needs needs :name name :lease-seconds lease-seconds :retain-seconds retain-seconds :environ environ
                      :priority priority :limits limits :notify notify))
  answer)
