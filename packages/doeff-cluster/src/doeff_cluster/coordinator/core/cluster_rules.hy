;;; coordinator の小さな純粋な判断 — 本文の版の検め・版の組の並べ(cluster_model から移した・#2023)・宣言の行と query の欄の読み
;;; (required-field・int-field — cluster_json から移した・#2448)。
(require doeff-hy.macros [deff val])
(val MODULE-TAGS {:context "coordinator" :role "judgment"})
(import doeff_cluster.coordinator.intent.cluster_model [ComponentVersion ACCEPTED-FORMATS])
(import doeff_cluster.shared.intent.protocol [BodyInvalid])


(defn #^ (get tuple #(ComponentVersion ...)) component-versions-of [#^ dict versions]
  "JSON の object(部品の名 → 版)→ 名の順の ComponentVersion の tuple。JSON から読む境界で使う。"
  (tuple (sorted (gfor #(component version) (.items versions) (ComponentVersion component version)))))


(defn #^ (| str None) format-version-refusal [#^ object form]  ; defk にできない: coordinator の純粋な判断(Program の外)が呼ぶ
  "本文の形の版 form が受け入れる範囲の外なら理由の文(道の型に解いた本文の欄 format を読む所 — #2445)。"
  (if (in form ACCEPTED-FORMATS)
      None
      (.format "本文の形の版 {!r} を受け入れない(受け入れる版 = {})" form (list ACCEPTED-FORMATS))))


(deff required-field [#^ dict body #^ str key]  ; defk にできない: 宣言と保存の行の読み(Program の外の純粋な判断)が呼ぶ
  {:pre [(: body dict) (: key str)] :post [(: % (| dict list str int float bool None))] :tags {:context "coordinator" :role "judgment"}}
  "宣言と保存の行(JSON)の必須の欄の値 — 欄が無ければ BodyInvalid(送り手の誤り・400)。受け口の本文は coordinator/protocol/request_bodies
   が道の型に解く(#2445)ので、ここを通るのは本文の中の宣言の行と保存の行だけ(行の型は #2447)。(get body 欄) の KeyError に頼ると、受け口は
   送り手の欠けと coordinator の中の KeyError を分けられない(#1024)。値は null でもよい(在ることだけを検める)。"
  (when (not-in key body)
    (raise (BodyInvalid (.format "本文に {} が無い" key))))
  (get body key))


(deff int-field [#^ dict fields #^ str key default]  ; defk にできない: query と保存の行の読み(Program の外の純粋な判断)が呼ぶ
  {:pre [(: fields dict) (: key str) (: default (| int None))] :post [(: % int)] :tags {:context "coordinator" :role "judgment"}}
  "query と保存の行の整数の欄(無ければ default)を int に読む(受け口の本文は道の型 — #2445) — 読めない値(数でない文字列・object など)は BodyInvalid
   (送り手の誤り・400)。読み方は int() のまま(小数は切り捨て・数字の文字列は数)。"
  (setv value (.get fields key default))
  (try
    (int value)
    (except [error [ValueError TypeError]]
      (raise (BodyInvalid (.format "{} は整数: {!r}" key value))))))


