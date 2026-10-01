;;; task(RemoteJob)の純粋な判断: 送り手と受け側の版の突き合わせ・詰めた Program の置き場のキー・例外から失敗の値を作る。
;;; 型は doeff_cluster.shared.intent.remote_model、詰める・戻す(cloudpickle)は doeff_cluster.shared.protocol.program_codec。
(require doeff-hy.macros [deff val])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import hashlib)
(import traceback)
(import doeff_cluster.shared.intent.remote_model [VersionDiff TaskFailed])


;; この process の版の識別(current-versions)を読むのは io の層の process_versions.hy(#1630)。ここは突き合わせの判断だけ。
(defn #^ tuple version-diffs [#^ dict expected #^ dict actual]  ; defk にできない: 子の入口と coordinator の純粋な判断(Program の外)が呼ぶ
  "送り手の版(expected)と受け側の版(actual)の食い違った欄(VersionDiff の tuple・欄の名の順)。env のキー(envKey)は両方が名乗る
   時だけ比べて先頭に置く(送り手が env の外 — 開発の checkout — で動く時は、残りの欄と宣言の組み立ての「汚れたツリーを断る」が
   source の一致を保つ)。"
  (setv both-keyed (and (in "envKey" expected) (in "envKey" actual))
        keys (sorted (lfor k (| (set expected) (set actual)) :if (or both-keyed (!= k "envKey")) k)
                     :key (fn [k] #((!= k "envKey") k))))
  (tuple (gfor key keys :if (!= (.get expected key) (.get actual key))
               (VersionDiff key (.get expected key) (.get actual key)))))


(defn #^ str diffs-text [#^ tuple diffs]  ; defk にできない: 子の入口と coordinator の純粋な判断(Program の外)が呼ぶ
  "版の違いの列(version-diffs の答え)を 1 行で名指す。違いが在ると分かっている呼び手が使う — 答えに None を含まない(#1690)。"
  (.join "・" (gfor d diffs (.format "{}: 送り手 {} / 受け側 {}" d.field d.sender d.env))))


(defn #^ (| str None) version-mismatch [#^ dict expected #^ dict actual]  ; defk にできない: 子の入口と coordinator の純粋な判断(Program の外)が呼ぶ
  "違いを 1 行で名指す。同じなら None。"
  (setv diffs (version-diffs expected actual))
  (if diffs (diffs-text diffs) None))


(deff program-sha [#^ str blob]  ; defk にできない: 送り手(declare・task の client)・coordinator の置き場・worker の cache が Program の外で呼ぶ
  {:pre [(: blob str)] :post [(: % str) (= (len %) 64)] :tags {:context "doeff-cluster" :role "judgment"}}
  "詰めた Program の置き場のキー(中身の sha256 の 16 進 64 桁)。/programs/<sha> の鍵・宣言の行と task の本文の program・worker の
   cache の file の名はどれもこの値(定義点はここ 1 つ — ADR-DOE-CLUSTER-001 R3b・改訂 1 の F)。"
  (.hexdigest (hashlib.sha256 (.encode blob "ascii"))))


(defn #^ TaskFailed failed-from [#^ BaseException error]
  (TaskFailed (. (type error) __name__) (str error)
              (.join "" (traceback.format-exception error)) error))
