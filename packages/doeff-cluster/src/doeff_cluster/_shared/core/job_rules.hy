;;; job の宣言の指紋 spec-hash — worker が起こした process の世代に載せ、coordinator が今の宣言から同じ関数で計算して比べる
;;; (worker_model から分けた・#2025 の 1 本目・#2021 の決め 1)。型 JobSpec は doeff_cluster._shared.intent.job_model。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import dataclasses [replace])
(import re)
(import doeff_cluster._shared.intent.job_model [JobSpec])


(defn #^ str spec-hash [#^ JobSpec spec]
  "process を起こす形(name・entry・引数 = 設定を含む・版・once)の指紋。worker が起こした process の世代の一部として子へ渡し、
   coordinator は今の宣言から同じ関数で計算して比べる — 設定だけが変わっても指紋が変わり、前の process の報告は数えない。
   割り当ての世代(placement)・入れ替えの形(handoff・ready-instance)は含めない(比べない欄)。environ(子の環境変数)は在る時だけ
   足す。Program の job の詰めた中身(program)は含めない —
   Program の同一性は args の identity の指紋が運ぶ。公開の入口はこの 1 つ。計算は値の属性 JobSpec.fingerprint が値ごとに
   1 度だけ行い、値の中に覚える(同じ値を何度読んでも JSON の綴りと sha256 は 1 度だけ — #3774)。"
  spec.fingerprint)


;; 中身の鍵の綴り: 英数字と . _ : - の 1〜128 字(送り手の計った hash に名の頭を付けた物 — 例 "sha256:<64 桁>")。
(val #^ (get re.Pattern str) CONTENT-KEY (re.compile r"[0-9A-Za-z._:-]{1,128}"))


(defn #^ (| str None) content-refusal [#^ object value]  ; defk にできない: 宣言の組み立て(system-declaration)と coordinator の宣言の読み(Program の外の純粋な判断)が呼ぶ
  "宣言の行の中身の鍵 content(無い = None)を受けられない理由を、送り手と coordinator が同じ 1 つの規則で言うため(受けられれば None)。"
  (if (or (is value None) (and (isinstance value str) (.fullmatch CONTENT-KEY value)))
      None
      (.format "content は中身の鍵(英数字と . _ : - の 1〜128 字)か null: {!r}" value)))


;; 常駐の job の memory: 起動の山 peakMiB と落ち着いた値 settleMiB(MiB の整数・0 以上・落ち着きは山以下)。
;; 値の持ち主は job の宣言(defsystem の :peak-mib・:settle-mib)で、既定の値で埋めない。
(defn #^ (| str None) memory-refusal [#^ object peak #^ object settle]  ; defk にできない: 宣言の値を作る時(defsystem の構成子)と coordinator の宣言の読み(Program の外の純粋な判断)が呼ぶ
  "job の memory の山 peak・落ち着いた値 settle(MiB)を受けられない理由を、送り手と coordinator が同じ 1 つの規則で言うため(受けられれば
   None)。真偽は整数と読まない。"
  (setv wrong (lfor #(field value) #(#("peakMiB" peak) #("settleMiB" settle))
                    :if (not (and (isinstance value int) (not (isinstance value bool)) (>= value 0)))
                    field))
  (cond
    wrong (.format "{} は 0 以上の整数(MiB)で必ず書く: peakMiB {!r}・settleMiB {!r}" (.join "・" wrong) peak settle)
    (> settle peak) (.format "settleMiB {} が peakMiB {} を越える — 落ち着いた値は起動の山以下" settle peak)
    True None))


(defn #^ (| str None) single-host-refusal [#^ object value]  ; defk にできない: 宣言の値を作る時と coordinator の宣言の読み(Program の外の純粋な判断)が呼ぶ
  "job を 1 台の worker に縛ってよい訳 singleHost(無い = None)を受けられない理由(受けられれば None)。訳は空白だけでない文字列。"
  (if (or (is value None) (and (isinstance value str) (.strip value)))
      None
      (.format "singleHost は 1 台に縛る訳の空でない文字列か null: {!r}" value)))


(defn #^ bool spec-hash-matches [#^ JobSpec spec #^ (| str None) reported]  ; defk にできない: coordinator の純粋な判断(Program の外 — running-process・new-generation-live)が spec-hash と並べて呼ぶ
  "worker が報告した process の指紋 reported が、今の宣言 spec の process の物か(coordinator の readiness・入れ替えの見張りが読む)。
   中身の鍵(JobSpec.content)の在る宣言は、鍵で計った指紋に加えて、鍵を外した版の指紋(版と commit で計る今までの
   指紋)も受ける: 鍵の欄を読まない古い worker は版で計った指紋を報告し、その指紋が合うのは宣言の版そのもので起こした process だけ。"
  (or (= reported (spec-hash spec))
      (and (is-not spec.content None) (= reported (spec-hash (replace spec :content None))))))
