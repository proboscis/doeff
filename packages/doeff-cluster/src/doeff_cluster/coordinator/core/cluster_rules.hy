;;; coordinator の小さな純粋な判断 — 本文の版の検め・版の組の並べ(cluster_model から移した・#2023)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "coordinator" :role "judgment"})
(import doeff_cluster.coordinator.intent.cluster_model [ComponentVersion ACCEPTED-FORMATS])


(defn #^ (get tuple #(ComponentVersion ...)) component-versions-of [#^ dict versions]
  "JSON の object(部品の名 → 版)→ 名の順の ComponentVersion の tuple。JSON から読む境界で使う。"
  (tuple (sorted (gfor #(component version) (.items versions) (ComponentVersion component version)))))


(defn #^ (| str None) format-version-refusal [#^ object form]  ; defk にできない: coordinator の純粋な判断(Program の外)が呼ぶ
  "本文の形の版 form が受け入れる範囲の外なら理由の文(道の型に解いた本文の欄 format を読む所 — #2445)。"
  (if (in form ACCEPTED-FORMATS)
      None
      (.format "本文の形の版 {!r} を受け入れない(受け入れる版 = {})" form (list ACCEPTED-FORMATS))))


(defn #^ (| str None) format-refusal [#^ dict body]  ; defk にできない: coordinator の純粋な判断(Program の外)が呼ぶ
  "本文の format が受け入れる範囲の外なら理由の文。"
  (setv form (.get body "format" 1))
  (if (in form ACCEPTED-FORMATS)
      None
      (.format "本文の形の版 {!r} を受け入れない(受け入れる版 = {})" form (list ACCEPTED-FORMATS))))
