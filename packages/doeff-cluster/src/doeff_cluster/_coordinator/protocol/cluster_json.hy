;;; coordinator の保存の行と受け口の本文(JSON)を型へ読み・型から書く純粋な関数(cluster_model から移した・#2023 → 保存の綴りとして protocol へ移した・#2448)。
(require doeff-hy.macros [defk deff val])
(val MODULE-TAGS {:context "coordinator" :role "protocol"})
(import dataclasses [asdict fields])
(import doeff_cluster._coordinator.core.cluster_rules [component-versions-of])
(import doeff_cluster._coordinator.intent.cluster_model [HandoffPhase HandoffWatch ClusterNaming TaskRecord ENDED-PHASES])
(import doeff_cluster._shared.intent.detached_model [DetachedEnded OUTBOX-ENDED])
(import doeff_cluster._shared.core.capabilities [POOL-PREFIX capabilities-of environ-pairs])
(import doeff_cluster._shared.core.detached_rules [task-limits-refusal task-limits-of task-limits-json])


(defn #^ HandoffWatch handoff-watch-from-json [#^ dict data]  ; defk にできない: 保存の読み(coordinator の起動の純粋な関数)が呼ぶ
  "保存の形 → HandoffWatch(HandoffWatch.to-json の逆)。知らない段は読めない(ValueError — 黙って待ちに戻さない)。"
  (HandoffWatch :declaration (get data "declaration") :since-ms (get data "sinceMs")
                :phase (HandoffPhase (get data "phase"))
                :abandoned-ms (.get data "abandonedMs")
                :reason (.get data "reason" "")
                :last-report (.get data "lastReport")))


;; image の版を追う係(base-follow)が読んでいた naming の欄(image の LABEL の名)。係は消した(Program の job は宣言した commit でだけ
;; 解く — 計画 2.2 の E)ので、書かれていれば黙って捨てず、理由つきで断る(coordinator は起動しない)。
(val RETIRED-NAMING-FIELDS (frozenset #("revisionLabel" "versionLabels")))


(defn #^ ClusterNaming naming-from-json [#^ str text]
  "coordinator の引数(JSON)→ ClusterNaming。欄は ownerAnnotation・ownerScope・nodeCapabilities([{\"label\" \"value\" \"capability\"} …])。
   書かなかった欄は既定のまま。消した欄(RETIRED-NAMING-FIELDS)と知らない欄は断る。"
  (import json)
  (setv data (json.loads text))
  (when (not (isinstance data dict))
    (raise (ValueError "naming は JSON の object")))
  (when (& (set data) RETIRED-NAMING-FIELDS)
    (raise (ValueError (.format "naming の {} は受け付けない — image の版を追う係は消した(Program の job は宣言した commit でだけ解く)"
                                (sorted (& (set data) RETIRED-NAMING-FIELDS))))))
  (setv known #{"ownerAnnotation" "ownerScope" "nodeCapabilities"})
  (setv unknown (sorted (gfor k data :if (not-in k known) k)))
  (when unknown
    (raise (ValueError (+ "naming の知らない欄: " (.join ", " unknown)))))
  (setv base (ClusterNaming))
  (setv derived (if (in "nodeCapabilities" data)
                    (tuple (gfor row (get data "nodeCapabilities")
                                 #((get row "label") (get row "value") (get row "capability"))))
                    base.node-capabilities))
  ;; pool- で始まる名は worker が枠の file で名乗る置ける組の印。導く能力と名が重なると、枠の file の印が
  ;; node の label の境界を名乗れてしまう(coordinator は導く能力の名を worker の名乗りから外す)ので、起動で断る。
  (setv pooled (sorted (gfor row derived :if (.startswith (get row 2) POOL-PREFIX) (get row 2))))
  (when pooled
    (raise (ValueError (.format "naming の nodeCapabilities の能力 {} は {} で始まる — {} は worker の枠の file の置ける組の印に取ってある"
                                pooled POOL-PREFIX POOL-PREFIX))))
  (ClusterNaming :owner-annotation (.get data "ownerAnnotation" base.owner-annotation)
                 :owner-scope (.get data "ownerScope" base.owner-scope)
                 :node-capabilities derived))


(deff task-record-to-json [#^ TaskRecord task]  ; defk にできない: 保存の綴り(state_json・durable_kv — Program の外)が呼ぶ純粋な綴り
  {:pre [(: task TaskRecord)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "TaskRecord → 保存の JSON の形(版は名 → 値の object・needs は名の list)。保存の 2 つの形(state file と durable の KV)はここだけを使う。
   知らせ先の無い行は欄 notify を置かない(欄を知らない前の版の coordinator へ戻しても、知らせ先の無い開いた行を
   旧い形と読んで failed にしない)。"
  (setv row (| (asdict task) {"versions" (dict task.versions) "needs" (list task.needs) "environ" (dict task.environ)
                              "limits" (task-limits-json task.limits)}))
  ;; 待ちの知らせの印も、積んでいない行は欄を置かない(欄を知らない前の版へ戻しても、待っている行を旧い形と
  ;; 読んで failed にしない — 印を積んだ行だけが戻した版で failed になる)。
  (setv unset (| (if task.notify #{} #{"notify"}) (if (is task.queued-noticed-ms None) #{"queued_noticed_ms"} #{})))
  (dfor #(k v) (.items row) :if (not-in k unset) k v))


(defk task-record-from-json [data]
  {:pre [(: data (get dict #(str object)))] :post [(: % TaskRecord)] :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "保存の JSON の形 → TaskRecord(task-record-to-json の逆)。
   旧い形の行は読み直しで coordinator を落とさず、まだ終わっていない行を failed(理由つき)にする — 旧い形は受け付けない。
  旧い形 = TaskRecord に無い欄を持つ行(今の TaskRecord の欄の集合 1 つで判じる — 消した欄を 1 つずつ数えると、
   数え漏れた欄 1 つで読み直しが TypeError になり coordinator が起きない。実弾 2026-09-28 の予行: 3880944e の行の env)。
   無い欄は捨てて読む(終わった行は Program 無し = program None で読む)。空の requires は needs 無しと同じ。"
  (val known (sfor f (fields TaskRecord) f.name))
  (val extra (sorted (gfor k data :if (not-in k known) k)))
  (val phase (stored-str data "phase" "queued"))
  (val unended (not-in phase ENDED-PHASES))
  ;; 上限の値と範囲を名乗る limits(2026-10-12 より前の形)も旧い形(読み分けて値を捨てて数えない)。
  (val old-limits (old-limits-reason (list (stored-items data "limits"))))
  (val reason (or (old-task-row-reason (.get data "requires") extra) old-limits))
  ;; failure = まだ終わっていない旧い形の行を failed にする理由(None = そのまま読む)
  (val failure (if unended reason None))
  ;; 欄ごとに型を確かめて読む(#** で辞書を渡すと、型の違う保存の値が黙って欄に入る — #1662)。
  (TaskRecord :id (stored-str data "id")
              :name (stored-str data "name")
              :program (stored-optional-str data "program")
              :revision (stored-str data "revision")
              :versions (component-versions-of (get data "versions"))
              :needs (capabilities-of (.get data "needs" []) "task の needs")
              :lease-ms (stored-int data "lease_ms")
              :lease-until-ms (stored-int data "lease_until_ms")
              :submitted-ms (stored-int data "submitted_ms")
              :phase (if (is failure None) phase "failed")
              :worker (stored-optional-str data "worker")
              :result (stored-optional-str data "result")
              :detail (if (is failure None) (stored-str data "detail" "") failure)
              :started-ms (stored-optional-int data "started_ms")
              :finished-ms (stored-optional-int data "finished_ms")
              :detached (stored-bool data "detached" False)
              :key (stored-optional-str data "key")
              :boot (stored-optional-str data "boot")
              :retain-ms (stored-int data "retain_ms" 0)
              :reported (stored-bool data "reported" False)
              :runtime-env (stored-optional-dict data "runtime_env")
              :env-attempts (stored-int data "env_attempts" 0)
              :avoid (stored-items data "avoid")
              :failure-kind (stored-str data "failure_kind" "")
              :retryable (stored-bool data "retryable" False)
              ;; 子の環境変数の欄の無い旧い行は空(欄が無いだけで旧い形とは数えない — 足した欄)。
              :environ (environ-pairs (.get data "environ" {}))
              ;; 置き方の名乗り(2026-10-11)の欄の無い旧い行は優先 0・上限なし(足した欄)。旧い形の limits の行は組を名乗らない
              ;; (まだ終わっていなければ上の failure で failed — 置かない行は数えない)。
              :priority (stored-int data "priority" 0)
              :limits (if old-limits #() (stored-limits data))
              ;; 終わりの知らせ先の欄の無い行は知らせない(足した欄)。
              :notify (stored-str data "notify" "")
              ;; 待ちの知らせの印の欄の無い行は、まだ積んでいない(足した欄)。
              :queued-noticed-ms (stored-optional-int data "queued_noticed_ms")))


(deff ended-to-json [#^ DetachedEnded ended]  ; defk にできない: 保存の綴り(durable_kv — Program の外)と返事の綴り(replies)が呼ぶ純粋な綴り
  {:pre [(: ended DetachedEnded)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "終わりの出口の行 → 保存と GET /outbox の返事の JSON の形(欄の名は camel — 読み手の DetachedEndedWire と同じ)。"
  {"id" ended.id "key" ended.key "notify" ended.notify "phase" ended.phase "detail" ended.detail "failureKind" ended.failure-kind
   "finishedMs" ended.finished-ms "kind" ended.kind})


(defk ended-from-json [data]
  {:pre [(: data (get dict #(str object)))] :post [(: % DetachedEnded)] :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "保存の JSON の形 → 終わりの出口の行(ended-to-json の逆)。型の違う欄は名乗る ValueError(保存の行の壊れ)。"
  (DetachedEnded :id (stored-str data "id") :key (stored-str data "key") :notify (stored-str data "notify") :phase (stored-str data "phase")
                 :detail (stored-str data "detail" "") :failure-kind (stored-str data "failureKind" "") :finished-ms (stored-int data "finishedMs")
                 ;; 種類の欄の無い保存の行(種類の前の版が積んだ)は終わりの行。
                 :kind (stored-str data "kind" OUTBOX-ENDED)))


;; 保存の行の欄の読み(task-record-from-json)。型の違う値は、どの欄がどう違うかを名乗る ValueError にする(保存の行の壊れ — 送り手の誤りの
;; BodyInvalid とは別)。無い欄は既定値で読む(既定値が None の欄は必須)。

(deff stored-str [#^ dict data #^ str key #^ (| str None) [default None]]  ; defk にできない: 保存の読み直し(Program の外)が呼ぶ純粋な読み
  {:pre [(: data dict) (: key str) (: default (| str None))] :post [(: % str)] :tags {:context "coordinator" :role "protocol"}}
  "保存の行の文字列の欄を str として読むため(無ければ default・default が None なら必須)。"
  (when (and (not-in key data) (is default None))
    (raise (ValueError (.format "保存の task の行に {} が無い" key))))
  (setv value (.get data key default))
  (when (not (isinstance value str))
    (raise (ValueError (.format "保存の task の行の {} は文字列: {!r}" key value))))
  value)


(deff stored-optional-str [#^ dict data #^ str key]  ; defk にできない: 保存の読み直し(Program の外)が呼ぶ純粋な読み
  {:pre [(: data dict) (: key str)] :post [(: % (| str None))] :tags {:context "coordinator" :role "protocol"}}
  "保存の行の、無くてよい文字列の欄を str か None として読むため。"
  (setv value (.get data key None))
  (when (not (isinstance value #(str (type None))))
    (raise (ValueError (.format "保存の task の行の {} は文字列か null: {!r}" key value))))
  value)


(deff stored-int [#^ dict data #^ str key #^ (| int None) [default None]]  ; defk にできない: 保存の読み直し(Program の外)が呼ぶ純粋な読み
  {:pre [(: data dict) (: key str) (: default (| int None))] :post [(: % int)] :tags {:context "coordinator" :role "protocol"}}
  "保存の行の整数の欄を int として読むため(無ければ default・default が None なら必須。真偽値は整数と数えない)。"
  (when (and (not-in key data) (is default None))
    (raise (ValueError (.format "保存の task の行に {} が無い" key))))
  (setv value (.get data key default))
  (when (or (not (isinstance value int)) (isinstance value bool))
    (raise (ValueError (.format "保存の task の行の {} は整数: {!r}" key value))))
  value)


(deff stored-optional-int [#^ dict data #^ str key]  ; defk にできない: 保存の読み直し(Program の外)が呼ぶ純粋な読み
  {:pre [(: data dict) (: key str)] :post [(: % (| int None))] :tags {:context "coordinator" :role "protocol"}}
  "保存の行の、無くてよい整数の欄を int か None として読むため。"
  (setv value (.get data key None))
  (when (or (isinstance value bool) (not (isinstance value #(int (type None)))))
    (raise (ValueError (.format "保存の task の行の {} は整数か null: {!r}" key value))))
  value)


(deff stored-bool [#^ dict data #^ str key #^ bool default]  ; defk にできない: 保存の読み直し(Program の外)が呼ぶ純粋な読み
  {:pre [(: data dict) (: key str) (: default bool)] :post [(: % bool)] :tags {:context "coordinator" :role "protocol"}}
  "保存の行の真偽値の欄を bool として読むため。"
  (setv value (.get data key default))
  (when (not (isinstance value bool))
    (raise (ValueError (.format "保存の task の行の {} は真偽値: {!r}" key value))))
  value)


(deff stored-optional-dict [#^ dict data #^ str key]  ; defk にできない: 保存の読み直し(Program の外)が呼ぶ純粋な読み
  {:pre [(: data dict) (: key str)] :post [(: % (| dict None))] :tags {:context "coordinator" :role "protocol"}}
  "保存の行の、無くてよい object の欄を dict か None として読むため。"
  (setv value (.get data key None))
  (when (not (isinstance value #(dict (type None))))
    (raise (ValueError (.format "保存の task の行の {} は object か null: {!r}" key value))))
  value)


(deff stored-items [#^ dict data #^ str key]  ; defk にできない: 保存の読み直し(Program の外)が呼ぶ純粋な読み
  {:pre [(: data dict) (: key str)] :post [(: % tuple)] :tags {:context "coordinator" :role "protocol"}}
  "保存の行の配列の欄を tuple として読むため(無ければ空)。JSON を通った行は list、JSON を通らずに渡る行(asdict のまま)は tuple で来る。"
  (setv value (.get data key #()))
  (when (not (isinstance value #(list tuple)))
    (raise (ValueError (.format "保存の task の行の {} は配列: {!r}" key value))))
  (tuple value))


(deff stored-limits [#^ dict data]  ; defk にできない: 保存の読み直し(Program の外)が呼ぶ純粋な読み
  {:pre [(: data dict)] :post [(: % tuple)] :tags {:context "coordinator" :role "protocol"}}
  "保存の行の欄 limits(同時数の上限の列)を TaskLimit の tuple として読むため(無ければ空)。形の違う値は受け口と同じ規則の理由で断る。"
  (setv value (list (stored-items data "limits"))
        problem (task-limits-refusal value))
  (when problem
    (raise (ValueError (.format "保存の task の行の limits が誤っている: {}" problem))))
  (task-limits-of value))


(deff old-limits-reason [#^ list limits]  ; defk にできない: 保存の読み直し(Program の外)が呼ぶ純粋な判断
  {:pre [(: limits list)] :post [(: % (| str None))] :tags {:context "coordinator" :role "protocol"}}
  "保存の task の行の limits が旧い形(上限の値 limit と範囲 scope を task が名乗る — 2026-10-12 より前)なら、まだ終わっていない行を
   failed にする理由の文(新しい形なら None)。値の持ち主は系の宣言 1 つになったので、旧い行の値を読んで数えない。"
  (if (any (gfor item limits (and (isinstance item dict) (or (in "limit" item) (in "scope" item)))))
      "旧い形の task(上限の値と範囲を名乗る limits)は受け付けない — 上限の値と範囲は系の宣言(:task-limits)が持つ。組の名だけで送り直す"
      None))


(deff old-task-row-reason [#^ (| dict list None) old #^ list extra]  ; defk にできない: 保存の読み直し(Program の外)が呼ぶ純粋な判断
  {:pre [(: old (| dict list None)) (: extra list)] :post [(: % (| str None))] :tags {:context "coordinator" :role "protocol"}}
  "保存の task の行が旧い形なら、まだ終わっていない行を failed にする理由の文(新しい形なら None)。old = 行の requires の値・
   extra = 今の TaskRecord に無い欄の名(requires・blob・env ほか)。"
  (cond
    old (.format "旧い形の task(requires {})は受け付けない — 新しい形(needs)で送り直す" old)
    (in "blob" extra) "旧い形の task(詰めた Program を行に持つ blob)は受け付けない — Program を /programs に置き、その sha で送り直す"
    (in "env" extra) "旧い形の task(handler の組の import path env)は受け付けない — task の Program が自分の土台で本体を包み、needs で送り直す"
    extra (.format "旧い形の task(今の形に無い欄 {})は受け付けない — 新しい形で送り直す" extra)
    True None))
