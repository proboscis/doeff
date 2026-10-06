;;; job の宣言の指紋 spec-hash — worker が起こした process の世代に載せ、coordinator が今の宣言から同じ関数で計算して比べる
;;; (worker_model から分けた・#2025 の 1 本目・#2021 の決め 1)。型 JobSpec は doeff_cluster.shared.intent.job_model。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import doeff_cluster.shared.intent.job_model [JobSpec])


(defn #^ str spec-hash [#^ JobSpec spec]
  "process を起こす形(name・entry・引数 = 設定を含む・版・once)の指紋。worker が起こした process の世代の一部として子へ渡し、
   coordinator は今の宣言から同じ関数で計算して比べる — 設定だけが変わっても指紋が変わり、前の process の報告は数えない。
   割り当ての世代(placement)・入れ替えの形(handoff・ready-instance)は含めない(比べない欄)。environ(子の環境変数)は在る時だけ
   足す。Program の job の詰めた中身(program)は含めない —
   Program の同一性は args の identity の指紋が運ぶ。公開の入口はこの 1 つ。計算は値の属性 JobSpec.fingerprint が値ごとに
   1 度だけ行い、値の中に覚える(同じ値を何度読んでも JSON の綴りと sha256 は 1 度だけ — #3774)。"
  spec.fingerprint)
