;;; coordinator の小さな純粋な判断 — 本文の版の検め・版の組の並べ(cluster_model から移した・#2023)・宣言の行と query の欄の読み
;;; (required-field・int-field — cluster_json から移した・#2448)・置き場所の規則 placeable(cluster_policy から移した — task の申告の上限を
;;; 決める task_claims も同じ規則を読む・ADR-DOE-CLUSTER-005 R4)。
(require doeff-hy.macros [deff val])
(val MODULE-TAGS {:context "coordinator" :role "judgment"})
(import collections.abc [Mapping])
(import doeff_cluster._coordinator.intent.cluster_model [ComponentVersion ACCEPTED-FORMATS WorkerInfo])
(import doeff_cluster._shared.intent.protocol [BodyInvalid])


(defn #^ (get tuple #(ComponentVersion ...)) component-versions-of [#^ dict versions]
  "JSON の object(部品の名 → 版)→ 名の順の ComponentVersion の tuple。JSON から読む境界で使う。"
  (tuple (sorted (gfor #(component version) (.items versions) (ComponentVersion component version)))))


(defn #^ (| str None) format-version-refusal [#^ object form]  ; cannot be a defk: coordinator の純粋な判断(Program の外)が呼ぶ
  "本文の形の版 form が受け入れる範囲の外なら理由の文(道の型に解いた本文の欄 format を読む所 — #2445)。"
  (if (in form ACCEPTED-FORMATS)
      None
      (.format "本文の形の版 {!r} を受け入れない(受け入れる版 = {})" form (list ACCEPTED-FORMATS))))


(deff required-field [#^ (get Mapping #(str object)) body #^ str key]  ; cannot be a defk: 宣言と保存の行の読み(Program の外の純粋な判断)が呼ぶ
  {:pre [(: body Mapping) (: key str)] :post [(: % "欄の値 — JSON の値(null もある)")] :tags {:context "coordinator" :role "judgment"}}
  "宣言と保存の行(JSON)の必須の欄の値 — 欄が無ければ BodyInvalid(送り手の誤り・400)。受け口の本文は coordinator/protocol/request_bodies
   が道の型に解く(#2445)ので、ここを通るのは本文の中の宣言の行と保存の行だけ(行の型は #2447)。(get body 欄) の KeyError に頼ると、受け口は
   送り手の欠けと coordinator の中の KeyError を分けられない(#1024)。値は null でもよい(在ることだけを検める)。"
  (when (not-in key body)
    (raise (BodyInvalid (.format "本文に {} が無い" key))))
  (get body key))


(deff int-field [#^ dict fields #^ str key default]  ; cannot be a defk: query と保存の行の読み(Program の外の純粋な判断)が呼ぶ
  {:pre [(: fields dict) (: key str) (: default (| int None))] :post [(: % int)] :tags {:context "coordinator" :role "judgment"}}
  "query と保存の行の整数の欄(無ければ default)を int に読む(受け口の本文は道の型 — #2445) — 読めない値(数でない文字列・object など)は BodyInvalid
   (送り手の誤り・400)。読み方は int() のまま(小数は切り捨て・数字の文字列は数)。"
  (setv value (.get fields key default))
  (try
    (int value)
    (except [error [ValueError TypeError]]
      (raise (BodyInvalid (.format "{} は整数: {!r}" key value))))))


(deff placeable [#^ (get tuple #(str ...)) needs #^ WorkerInfo worker]  ; cannot be a defk: coordinator と模擬の置き先の選び(Program の外の純粋な判断)が呼ぶ
  {:pre [(: needs tuple) (: worker WorkerInfo)] :post [(: % bool)] :tags {:context "coordinator" :role "judgment"}}
  "needs の job / task をこの worker に置けるか — 置き場所の規則の定義点はここ 1 つ(ADR-DOE-CLUSTER-001 R4b):
   needs ⊆ provides ∪ derived(node の label から coordinator が導いた能力)、かつ worker が専用の能力(exclusive)を持てば、そのどれかを needs に持つ(一般の仕事を専用の担い手に置かない)。"
  (and (<= (set needs) (| (set worker.provides) (set worker.derived)))
       (or (not worker.exclusive) (bool (& (set needs) (set worker.exclusive))))))
