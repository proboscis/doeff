;; Pod の job の label の綴りの契約(contracts/pod-job-labels.json)の検。
;;
;;   worker は置かれた常駐の job を、自分の Pod の label <prefix><job の名>=<value> に付け外しする(綴りの定義は
;;   _worker/core/pod_label_rules の JOB-LABEL-PREFIX・JOB-LABEL-VALUE)。その綴りは、job の置き先の Pod を Service の selector で選ぶ外の
;;   系へ約束した値で、外の系は隠した区画の file の path を読まず、この契約の file を読む(区画の中を並べ替えても外の検が赤にならない)。
;;   契約の file と pod_label_rules の値が揃うこと、片方だけ替えると赤になること(反例)を確かめる。
(require doeff-hy.macros [deftest defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import pathlib [Path])
(import doeff_cluster._worker.core.pod_label_rules [JOB-LABEL-PREFIX JOB-LABEL-VALUE])

(val ROOT (. (Path __file__) (resolve) parent parent))
(val CONTRACT-FILE (/ ROOT "contracts" "pod-job-labels.json"))
(val CONTRACT-KEYS ["prefix" "value"])
;; 契約の file の中身。
(val CONTRACT (json.loads (.read-text CONTRACT-FILE :encoding "utf-8")))


(defk contract-drift [contract prefix value]
  {:pre [(: contract dict) (: prefix str) (: value str)] :post [(: % tuple)]}
  "契約の file の中身 contract と pod_label_rules の綴り prefix・value の食い違いを 1 件 1 行で返す(空 = 揃っている)。"
  (val keys-line (if (= (sorted contract) CONTRACT-KEYS) [] [(.format "契約の鍵が {} でない: {}" CONTRACT-KEYS (sorted contract))]))
  (val value-lines (lfor [key want] [["prefix" prefix] ["value" value]] :if (and (in key contract) (!= (get contract key) want))
                     (.format "契約の {} が {!r} で、pod_label_rules の値 {!r} と食い違う" key (get contract key) want)))
  (tuple (+ keys-line value-lines)))


(deftest test-the-contract-file-matches-the-pod-label-rules
  (<- found tuple (contract-drift CONTRACT JOB-LABEL-PREFIX JOB-LABEL-VALUE))
  (assert (= found #()) found))


(deftest test-a-contract-changed-alone-is-red
  (<- prefix-moved tuple (contract-drift (| CONTRACT {"prefix" "placed.doeff.dev/"}) JOB-LABEL-PREFIX JOB-LABEL-VALUE))
  (assert (and (= (len prefix-moved) 1) (in "契約の prefix" (get prefix-moved 0))) prefix-moved)
  (<- value-moved tuple (contract-drift (| CONTRACT {"value" "true"}) JOB-LABEL-PREFIX JOB-LABEL-VALUE))
  (assert (and (= (len value-moved) 1) (in "契約の value" (get value-moved 0))) value-moved))


(deftest test-the-rules-changed-alone-are-red
  (<- prefix-moved tuple (contract-drift CONTRACT "placed.doeff.dev/" JOB-LABEL-VALUE))
  (assert (and (= (len prefix-moved) 1) (in "契約の prefix" (get prefix-moved 0))) prefix-moved)
  (<- value-moved tuple (contract-drift CONTRACT JOB-LABEL-PREFIX "true"))
  (assert (and (= (len value-moved) 1) (in "契約の value" (get value-moved 0))) value-moved))


(deftest test-a-contract-missing-or-adding-a-key-is-red
  (<- missing tuple (contract-drift {"prefix" JOB-LABEL-PREFIX} JOB-LABEL-PREFIX JOB-LABEL-VALUE))
  (assert (and (= (len missing) 1) (in "契約の鍵が" (get missing 0))) missing)
  (<- added tuple (contract-drift (| CONTRACT {"node" "k3s-3"}) JOB-LABEL-PREFIX JOB-LABEL-VALUE))
  (assert (and (= (len added) 1) (in "契約の鍵が" (get added 0))) added))
