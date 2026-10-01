;;; 実行環境の root の完成の印(env の準備が最後に書く file の名と形の版)と、file の中身の指紋を問う effect FileSha256 —
;;; worker の準備(worker/core/env_prepare)と、送り手の宣言の組み立て・入口の検め(shared/core/runtime_env・runtime_identity・
;;; shared/protocol/checkout_reads・runtime_facts)が同じ約束を読む共有の部品(#2025 の 3 本目で env_prepare から分けた)。
(require doeff-hy.macros [defeffect val])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})


(val ENV-MARKER ".doeff-env-ready.json")
(val ENV-MARKER-FORMAT 1)


(defeffect FileSha256
  "file の中身の sha256(16 進)。答え = str か None(file が無い)。準備(処理ステージ 4)と送り手の宣言の組み立て(runtime_env)が出す。"
  {:fields [(: path str)]
   :answer (| str None)
   :tags {:context "runtime-env" :role "intent"}})
