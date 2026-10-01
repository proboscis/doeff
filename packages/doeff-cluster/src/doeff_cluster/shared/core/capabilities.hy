;;; 能力の名の検めと子の環境変数の並べ — job・task・worker・宣言が共に使う純粋な判断(coordinator の cluster_model から移した・#2023)。
(require doeff-hy.macros [defk deff val])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import re)
(import doeff_cluster.shared.intent.protocol [BodyInvalid])
(import doeff_cluster.shared.intent.runtime_env_model [EnvVar])


;; --- 実行先の能力と版(task・切り離した task・worker が共に使う) -------------------------------------
;;
;; 能力(capability — ADR-DOE-CLUSTER-001 R4b・2026-09-27): job と task は「要る能力の名」の集合(needs)を宣言し、worker は「提供する
;; 能力の名」の集合(provides)をクラスタの設定(起動の引数)で名乗る。coordinator は needs ⊆ provides の worker にだけ置く。
;; 置き場所の名(kind=k3s・role=…・機体の名)は書かない。worker の exclusive(provides の一部)は「この能力のどれかを needs に持つ
;; job / task だけを受ける」の印(以前の label `dedicated=<k>=<v>` の置き換え — 会社の機体・人の機体のように、一般の仕事を置かない担い手)。
;; 能力の名は小文字・数字・`.`・`-` だけ(`k=v` の旧い label の形を名として受けない)。needs と provides は名の順の tuple で持つ。

(val CAPABILITY-PATTERN (re.compile r"[a-z0-9][a-z0-9.-]*"))


(deff capability-refusal [name]  ; defk にできない: 宣言・heartbeat・保存の JSON を読む境界(Program の外)が呼ぶ純粋な判断
  {:pre [(: name str)] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "能力の名 1 つが名として受けられない理由(受けられれば None)— 旧い label の形(`kind=k3s`)を黙って名にしないため。"
  (cond
    (in "=" name) (.format "能力の名 {!r} は label の形(鍵=値)— 置き場所ではなく要る能力の名を書く(ADR-DOE-CLUSTER-001 R4b)" name)
    (not (CAPABILITY-PATTERN.fullmatch name)) (.format "能力の名 {!r} は小文字・数字・`.`・`-` だけで書く" name)
    True None))


(deff capabilities-of [value #^ str what]  ; defk にできない: 宣言・heartbeat・保存の JSON を読む境界(Program の外)が呼ぶ
  {:pre [(: value (| list tuple set frozenset dict str int float bool None)) (: what str)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "judgment"}}
  "JSON の能力の名の列(list・tuple・frozenset)→ 名の順の重なりの無い tuple(比べる時に順が揃う)。旧い形(label の object)や
   名として受けられない値は BodyInvalid(送り手の誤り — ValueError の子)(what = 誤りの文の欄の名)。"
  (when (isinstance value dict)
    (raise (BodyInvalid (.format "{} が label の object {!r} — 旧い requires / labels の形は受け付けない。能力の名の列で書く(ADR-DOE-CLUSTER-001 R4b)"
                                what value))))
  (when (not (isinstance value #(list tuple set frozenset)))
    (raise (BodyInvalid (.format "{} は能力の名の列: {!r}" what value))))
  (for [name value]
    (when (not (isinstance name str))
      (raise (BodyInvalid (.format "{}: 能力の名は文字列: {!r}" what name))))
    (setv problem (capability-refusal name))
    (when (is-not problem None)
      (raise (BodyInvalid (.format "{}: {}" what problem)))))
  (tuple (sorted (set value))))


(deff effect-needs-problem [needs]  ; defk にできない: effect の構成子(dataclass の __post_init__)が呼ぶ純粋な判断
  {:pre [(: needs (| frozenset tuple list set dict str None))] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "effect(RemoteJob・SubmitDetached・WarmRuntimeEnv)の needs が受けられない理由(受けられれば None)— 3 つの構成子が同じ規則で
   断るため: 能力の名の空でない frozenset(旧い Requirement の tuple・label の組・空は断る — 改訂 1 の I)。"
  (cond
    (not (isinstance needs frozenset)) (.format "needs は能力の名の frozenset: {!r}" needs)
    (not needs) "needs が空 — 要る能力の名を 1 つ以上書く"
    True (next (gfor n needs
                     :setv p (if (isinstance n str) (capability-refusal n) (.format "能力の名は文字列: {!r}" n))
                     :if p p)
               None)))


(deff environ-pairs [#^ dict environ]  ; defk にできない: coordinator の本文の読み・worker の返事の読み(Program の外)が呼ぶ純粋な判断
  {:pre [(: environ dict)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "judgment"}}
  "子の環境変数の dict → 名の順の #(名 値) の tuple(TaskRecord.environ・JobSpec.environ の形)— 行と spec の比べと指紋を
   名の順 1 つにするため。"
  (tuple (gfor k (sorted environ) #(k (get environ k)))))


;; --- 切り離した task の子の環境変数の型(SubmitDetached.environ — EnvVar の tuple・#2179) -----------------------------
;; effect は名 → 値の写像でなく EnvVar の tuple で受ける(呼び手の core が写像を組まずに済む)。名と値の規則は EnvVar を作る時に
;; 走る(runtime_env_model の EnvVar 1 つ)。組の形(EnvVar の tuple・名が重ならない)は SubmitDetached を作る時に検める。
;; coordinator への本文(wire)は名 → 値の object のままなので、handler が env-mapping で綴る。


(defk env-vars-of [environ]
  {:pre [(: environ (get dict #(str str)))] :post [(: % (get tuple #(EnvVar ...)))] :tags {:context "doeff-cluster" :role "judgment"}}
  "名 → 文字列の写像を名の順の EnvVar の tuple に写す — 宣言の :environ を読んだ境目が、effect に渡す型の組を 1 か所で作るため。"
  (tuple (gfor k (sorted environ) (EnvVar :name k :value (get environ k)))))


(defk env-mapping [env-vars]
  {:pre [(: env-vars (get tuple #(EnvVar ...)))] :post [(: % (get dict #(str str)))] :tags {:context "doeff-cluster" :role "judgment"}}
  "EnvVar の tuple を coordinator への本文の形(名 → 文字列の object)へ綴る — wire の形は変えずに effect の型だけを変えるため。"
  (dfor v env-vars v.name v.value))
