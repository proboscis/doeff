;;; 待ちの子(#3646)の小さな判断 — どの job が待ちの子から分かれるか・分かれ元の root のキー・準備完了の印の読み・起動で読み込む module の
;;; 名・待ちの子の置き場(socket・印・exit の file)と起動の argv。型は worker/intent/worker_model。
;;;
;;; 条の守りの置き場(architecture.hy の worker の :invariants・判じる関数は worker/core/invariants):
;;;   WC1(古い root から分かれない)= task の分かれ元のキーを決めるのは warm-key-of の 1 か所(task 自身の env のキー)。
;;;   WC3(分かれる前に VM を起こさない)= 準備済みに数える印の形を判じるのは warm-mark-clean? の 1 か所。
(require doeff-hy.macros [defk val var <-])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import json)
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])
(import doeff_cluster.shared.core.runtime_env_rules [runtime-env-of-json])
(import doeff_cluster.worker.intent.worker_model [WorldView WarmChildMark WarmChildView])
(import doeff_cluster.worker.core.worker_rules [code-key])


(defn #^ bool forks-from-warm-child [#^ JobSpec spec]  ; defk にできない: 純粋な判断の start-on-ready-tree(Program の外の関数)が呼ぶ
  "待ちの子から分かれて走る job = 実行環境を宣言した task(once)。service は今までどおり入れ物 shim で起こす(種類で道が決まる)。"
  (and spec.once (is-not spec.runtime-env None)))


(defn #^ str warm-key-of [#^ JobSpec spec]  ; defk にできない: 純粋な判断の start-on-ready-tree(Program の外の関数)が呼ぶ
  "task の分かれ元の待ちの子の root のキー = その task 自身の env の root のキー(条 WC1 — 定義点はここ 1 つ)。"
  (code-key spec))


(defn #^ bool warm-mark-clean [#^ WarmChildMark mark]  ; defk にできない: 純粋な判断の warm-child-ready(Program の外の関数)が呼ぶ
  "準備完了の印が「分かれる前に thread も VM も無い」形か(条 WC3 — 判じるのはここ 1 か所): threads が 1・vm-live が全部 0。"
  (and (= mark.threads 1) (all (gfor n mark.vm-live (= n 0)))))


(defn #^ (| WarmChildView None) warm-child-of [#^ WorldView world #^ str key]  ; defk にできない: 純粋な判断の phase-of が呼ぶ
  "root のキーの待ちの子の観測(起こしていなければ None)。"
  (next (gfor view world.warm-children :if (= view.key key) view) None))


(defn #^ bool warm-child-ready [#^ (| WarmChildView None) view]  ; defk にできない: 純粋な判断の start-on-ready-tree が呼ぶ
  "分けてよい待ちの子か: 走っていて(終わりを観測していない・止め始めていない)、準備完了の印が分かれる前の形。"
  (and (is-not view None) (is view.exit-code None) (is view.stop None)
       (is-not view.mark None) (warm-mark-clean view.mark)))


(defn #^ str mark-refusal [#^ WarmChildMark mark]  ; defk にできない: 純粋な判断の warm-child-actions の列の中で呼ぶ
  "分かれる前の形でない印を止める訳(観測の detail に残る)。"
  (.format "準備完了の印が分かれる前の形でない(threads={} vmLive={})" mark.threads (list mark.vm-live)))


(defk warm-preload [key specs warm]
  {:pre [(: key str) (: specs tuple) (: warm tuple)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "root のキーの待ちの子が起動で読み込む module の名(名の順・重なりは 1 つ)を決めるため: その root の task の宣言と、温める表の行の
   bytecodeEntries を合わせる(宣言の読みは定義点 runtime-env-of-json)。名は root のキーに入らないので、同じ root の宣言どうしで違えば
   合わせた物を読む(読まなかった module は分かれた task が自分で読む — 遅くなるだけで落ちない)。"
  (val texts (+ (tuple (gfor spec specs :if (and (forks-from-warm-child spec) (= (warm-key-of spec) key)) spec.runtime-env))
                (tuple (gfor w warm :if (= w.key key) w.runtime-env))))
  (var names (frozenset))
  (for [text texts]
    (<- env RuntimeEnv (runtime-env-of-json (json.loads text)))
    (:= names (| names (frozenset env.bytecode-entries))))
  (tuple (sorted names)))
