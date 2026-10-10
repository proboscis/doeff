;;; job が要る値を、worker の中の file の path でなく cluster の ConfigMap・Secret の参照で受ける時の型(関数は持たない — 値を得る部品は
;;; doeff_cluster.shared.protocol.supplied_values)。
;;;
;;;   SuppliedKind              参照の種類(CONFIGMAP・SECRET)
;;;   SuppliedRef               値 1 つの参照: 種類・namespace・名・キー
;;;   SuppliedObjectRef         物 1 つ丸ごとの参照: 種類・namespace・名
;;;   SuppliedValueUnavailable  値を得られなかった・書き出せなかった(参照の綴りと理由を文に持つ — 値と token は載せない)
;;;   WorkerFactName            job が worker に問える機体の事実の名(NODE_NAME・SYSTEMD_ROOT・WORK_ROOT — 名と環境変数の対応は
;;;                             protocol の FACT-ENVIRON-NAMES の 1 か所)
;;;   WorkerFactMissing         事実に答える環境変数が無い・空(事実の名と環境変数の名を文に持つ — 既定の値で埋めない)
;;;
;;; 機体の事実を名で問う訳: job の宣言の :environ に worker の中の path を書くと、job が worker の作り(mount の位置)を知る事に成る。機体ごとに
;;; 違う値は、job が事実の名で問い、静的な worker の宣言が 1 度だけ置いた値で答える。
(require doeff-hy.macros [val])
(require doeff-hy.record [defenum defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import enum [StrEnum])  ; defenum の展開が使う

(defenum SuppliedKind CONFIGMAP SECRET)


(defrecord SuppliedObjectRef
  "cluster が持つ物 1 つの参照: kind = 種類・namespace・name = ConfigMap か Secret の名。"
  {:check [(> (len namespace) 0) (> (len name) 0)]}
  (#^ SuppliedKind kind)
  (#^ str namespace)
  (#^ str name))


(defrecord SuppliedRef
  "cluster が持つ値 1 つの参照: kind = 種類・namespace・name = ConfigMap か Secret の名・key = その data のキー。"
  {:check [(> (len namespace) 0) (> (len name) 0) (> (len key) 0)]}
  (#^ SuppliedKind kind)
  (#^ str namespace)
  (#^ str name)
  (#^ str key))


(defclass SuppliedValueUnavailable [RuntimeError]
  "参照の値を得られなかった・書き出せなかった(参照の綴りと理由を文に持つ — 値と token は載せない・既定の値で埋めない)。")


(defenum WorkerFactName NODE_NAME SYSTEMD_ROOT WORK_ROOT)


(defclass WorkerFactMissing [RuntimeError]
  "機体の事実に答える環境変数が無い・空(事実の名と環境変数の名を文に持つ — 既定の値で埋めない)。")
