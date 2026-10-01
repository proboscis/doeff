;;; 宣言してよいかの検め(declare の頭の註の 2 つ — 系の関数の checkout が宣言の版そのものか・土台の :needs が job の :needs の一部か)の
;;; 判断(declare から分けた・#2346)。checkout の読みは effect で、答えるのは doeff_cluster.shared.protocol.checkout_reads。
;;; ⚠ 系の関数の module の file の置き場(読み込んだ module の __file__ と、相対 path を絶対にする時の作業 dir)は、まだこの判断の中で
;;; process の状態から直に読む(品質検査の登録簿の層は declare の時と同じ runtime)。module の import 先を解く汎用の効果(#2347)が入った後に、
;;; その効果を出して読む形へ直す(#2346 の comment)。
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import collections.abc [Callable])
(import os)
(import sys)
(import doeff_cluster.shared.core.runtime_env [checked-declaring-checkout])
(import doeff_cluster.shared.intent.runtime_env_model [RepoCheckout RuntimeEnvInvalid])
(import doeff_cluster.shared.intent.service_model [foundation-needs-refusal System])


(defk declaring-refusal [build foundation system revision]
  {:pre [(: build Callable) (: foundation Callable) (: system System) (: revision str)] :post [(: % (| str None))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "宣言してよいかを検めて、断る理由の文を返すため(よければ None — 頭の註の 2 つ)。build = 系の関数(その module の file の在る
   checkout を読む)・foundation = 土台の関数・system = build に foundation を渡した系。"
  (<- needs (| str None) (foundation-needs-refusal system foundation))
  ;; build は呼べる関数なので、その module は読み込み済み — 名から import し直さず sys.modules から引く(#1692)。
  (val source (getattr (.get sys.modules build.__module__) "__file__" None))
  (match #(needs source)
    #(None None) (.format "系の関数 {}:{} の module に file が無い(宣言の版と同じ code かを確かめられない)" build.__module__ build.__qualname__)
    #(None file) (try
                   (<- _ RepoCheckout (checked-declaring-checkout (os.path.dirname (os.path.abspath file)) revision))
                   None
                   (except [error RuntimeEnvInvalid] (str error)))
    #(reason _) reason))
