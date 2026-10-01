;;; worker の job の判断の小さな関数 — 木の鍵(code-key)・READY の木の path(ready-path)・退いた process の名(retired-name)・
;;; 入口の検めの対象と断り(probed-job・probe-args・probe-refusal)。型は worker/intent/worker_model(#2025 で分けた)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.worker.intent.worker_model [CodeView])


(setv ENV-KEY-PREFIX "env-")

(defn #^ str code-key [#^ JobSpec spec]
  "展開する木の鍵(cache の dir の名前・完成の印の版)。revision そのもの(1 つの commit の木)。
   実行環境の job は \"env-<キー>\"(worker が宣言から計算した env-key)が root の鍵。"
  (if spec.runtime-env
      (+ ENV-KEY-PREFIX (or spec.env-key (raise (ValueError (+ "実行環境の job に env-key が無い: " spec.name)))))
      spec.revision))


(defn #^ (| str None) ready-path [#^ (| CodeView None) code]  ; defk にできない: 純粋な判断の start-actions(Program の外の関数)が呼ぶ
  "木が READY ならその path、観測が無い・READY でなければ None(CodeView が READY ⇔ path の在る事を作る時に確かめる)。"
  (if (is code None) None code.path))


(setv RETIRED-MARK "#retired-")

(defn #^ str retired-name [#^ str name #^ str instance]
  (+ name RETIRED-MARK instance))


(defn #^ bool probed-job [#^ JobSpec spec]
  "入口の検めの対象: service の job(args の先頭が \"service\" — job_entry の service 入口)。task(once)と素の entry は対象外。"
  (and (not spec.once) (> (len spec.args) 0) (= (get spec.args 0) "service")))


(defn #^ tuple probe-args [#^ JobSpec spec]
  "検めの対象の job の入口を検める引数(spec.entry の probe 口へ渡す)。Program の job(2026-09-27)は入口の module を import できるか
   だけを検める — 詰めた Program の版と復元は起こした子が検め、理由つきで落ちる(job_entry.read-program)。"
  #("probe"))

;; 旧い service の spec の引数(2026-09-27 より前の job_entry service の形 — 関数の参照 + handler の組の import path + 設定)。
(setv OLD-SERVICE-FLAGS #("--factory" "--env" "--config"))

(defn #^ (| str None) probe-refusal [#^ JobSpec spec]
  "検めの対象の spec を検める前に断る理由(断らなければ None)。Program の job の service は詰めた Program の置き場のキー(spec.program)を
   持ち、旧い引数(--factory・--env・--config)を持たない。旧い coordinator の返事の spec は入口の module の import だけなら通ってしまい、
   子の job_entry が argparse で落ちて起こし直しを繰り返すので、検めの段で理由つきに止める(計画 2.8 の入口 15)。"
  (setv old (lfor flag OLD-SERVICE-FLAGS :if (in flag spec.args) flag))
  (cond
    (not (probed-job spec)) None
    old (.format "旧い service の spec の引数 {} は受け付けない — Program の job(service --identity と詰めた Program)で宣言し直す"
                 (.join "・" old))
    (not spec.program) "service の spec に詰めた Program の置き場のキー(program)が無い — Program の job で宣言し直す"
    True None))
