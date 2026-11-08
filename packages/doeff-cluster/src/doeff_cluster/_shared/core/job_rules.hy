;;; job の宣言の指紋 spec-hash — worker が起こした process の世代に載せ、coordinator が今の宣言から同じ関数で計算して比べる
;;; (worker_model から分けた・#2025 の 1 本目・#2021 の決め 1)。型 JobSpec は doeff_cluster._shared.intent.job_model。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import dataclasses [replace])
(import math)
(import re)
(import typing [TypeGuard])
(import doeff_cluster._shared.intent.job_model [IdleBudget JobSpec])


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


;; 常駐の job の暇な時の予算 idleBudget(任意の欄 — 書かない job には無い・型は job_model.IdleBudget): 暇な時の上限 cpuCores(core の数)・
;; pssMB(MB の整数)と、上限を出した元の暇な時の値 fromCpuCores・fromPssMB(同じ単位・上限以下)。doeff-cluster は運ぶだけ。
(val IDLE-BUDGET-KEYS #("cpuCores" "pssMB" "fromCpuCores" "fromPssMB"))


(defn #^ (get TypeGuard (| int float)) core-count? [#^ object value] ; defk にできない: idle-budget-read の型を絞る判定(TypeGuard)
  "暇な時の予算の cpu の値(0 以上の有限の数 — 真偽は数と読まない)か。"
  (and (isinstance value #(int float)) (not (isinstance value bool)) (math.isfinite value) (>= value 0)))


(defn #^ (get TypeGuard int) megabytes? [#^ object value] ; defk にできない: idle-budget-read の型を絞る判定(TypeGuard)
  "暇な時の予算の pss の値(0 以上の整数の MB — 真偽は整数と読まない)か。"
  (and (isinstance value int) (not (isinstance value bool)) (>= value 0)))


(defn #^ (| IdleBudget str) idle-budget-read [#^ object value]  ; defk にできない: 宣言の値を作る時(defsystem の展開と構成子)と coordinator の宣言の読み(Program の外の純粋な判断)が呼ぶ
  "宣言の行の idleBudget の object を、送り手と coordinator が同じ 1 つの規則で読むため: 受けられれば IdleBudget、受けられなければ理由の
   文。鍵は IDLE-BUDGET-KEYS のちょうど 4 つ・cpu は 0 以上の有限の数・pss は 0 以上の整数・元の値は上限以下。"
  (when (not (and (isinstance value dict) (= (set value) (set IDLE-BUDGET-KEYS))))
    (return (.format "idleBudget は鍵 {} の object か null: {!r}" (.join "・" IDLE-BUDGET-KEYS) value)))
  (setv cpu (get value "cpuCores") pss (get value "pssMB") from-cpu (get value "fromCpuCores") from-pss (get value "fromPssMB"))
  (if (and (core-count? cpu) (core-count? from-cpu) (megabytes? pss) (megabytes? from-pss))
      (cond
        (> from-cpu cpu) (.format "idleBudget の fromCpuCores {} が cpuCores {} を越える — 元の暇な時の値は上限以下" from-cpu cpu)
        (> from-pss pss) (.format "idleBudget の fromPssMB {} が pssMB {} を越える — 元の暇な時の値は上限以下" from-pss pss)
        True (IdleBudget :cpu-cores (float cpu) :pss-mb pss :from-cpu-cores (float from-cpu) :from-pss-mb from-pss))
      (.format "idleBudget の {} は 0 以上の数(cpu は core の数・pss は MB の整数): {!r}"
               (.join "・" (+ (lfor #(f v) #(#("cpuCores" cpu) #("fromCpuCores" from-cpu)) :if (not (core-count? v)) f)
                              (lfor #(f v) #(#("pssMB" pss) #("fromPssMB" from-pss)) :if (not (megabytes? v)) f)))
               value)))


(defn #^ (| str None) idle-budget-refusal [#^ object value]  ; defk にできない: 宣言の値を作る時と coordinator の宣言の読み(Program の外の純粋な判断)が呼ぶ
  "job の暇な時の予算 idleBudget(無い = None)を受けられない理由(受けられれば None — 規則は idle-budget-read)。"
  (if (is value None)
      None
      (let [read (idle-budget-read value)]
        (if (isinstance read str) read None))))


(defn #^ (get dict #(str (| int float))) idle-budget-json [#^ IdleBudget ceiling]  ; defk にできない: 宣言の行の組み立てと coordinator の spec の綴り(Program の外の純粋な変換)が呼ぶ
  "IdleBudget → 宣言の行と Service の spec の idleBudget の object(idle-budget-read の逆)。"
  {"cpuCores" ceiling.cpu-cores "pssMB" ceiling.pss-mb "fromCpuCores" ceiling.from-cpu-cores "fromPssMB" ceiling.from-pss-mb})


(defn #^ bool spec-hash-matches [#^ JobSpec spec #^ (| str None) reported]  ; defk にできない: coordinator の純粋な判断(Program の外 — running-process・new-generation-live)が spec-hash と並べて呼ぶ
  "worker が報告した process の指紋 reported が、今の宣言 spec の process の物か(coordinator の readiness・入れ替えの見張りが読む)。
   中身の鍵(JobSpec.content)の在る宣言は、鍵で計った指紋に加えて、鍵を外した版の指紋(版と commit で計る今までの
   指紋)も受ける: 鍵の欄を読まない古い worker は版で計った指紋を報告し、その指紋が合うのは宣言の版そのもので起こした process だけ。"
  (or (= reported (spec-hash spec))
      (and (is-not spec.content None) (= reported (spec-hash (replace spec :content None))))))
