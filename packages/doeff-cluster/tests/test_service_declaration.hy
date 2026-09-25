;; service の宣言は値と関数で書く(agora-redesign #639・ADR-DOE-HY-005 R5 — doeff-cluster に macro を置かない)。
;; 関数の参照(module:attr)は Program を作る関数から導き、同じ関数へ解けること・宣言の項目が coordinator へ渡る形に残ること・
;; テスト用の main が宣言の持つ関数そのもので Program を作ることを確かめる。
(require doeff-hy.macros [defk deftest <-])
(import pytest)
(import doeff_cluster.service_model [ServiceDef System service resolve system-declaration system-main])
(import tests.fixtures.services [tally tally-program tally-system])


(deftest test-a-service-names-its-program-by-module-and-attribute
  (assert (= tally (ServiceDef "tally" "tests.fixtures.services:tally_program" "tests.fixtures.envs:plain_env"
                               #(#("kind" "k3s")) #(#("base" 1) #("step" 2)) tally-program)))
  ;; 実行先は参照を import して解く — 宣言した関数そのものに戻る。
  (assert (is (resolve tally.factory) tally-program)))


(deftest test-a-program-without-a-module-level-name-is-refused
  ;; 入れ子の関数と lambda は module:attr で引けない。実行先で初めて落ちる参照を宣言の時点で断る。
  (defk inner-program [n]
    {:pre [(: n int)] :post [(: % int)]}
    n)
  (with [raised (pytest.raises ValueError)]
    (service "inner" inner-program :env "m:e"))
  (assert (in "inner_program" (str raised.value)))
  (with [(pytest.raises ValueError)]
    (service "anonymous" (fn [] None) :env "m:e")))


(deftest test-option-keys-must-be-strings
  ;; 宣言は JSON で coordinator へ渡る。keyword の鍵({:step 2})を黙って通さず、service の名と項目を名指して断る。
  (with [raised (pytest.raises TypeError)]
    (service "tally-keyword" tally-program :env "m:e" :config {:step 2}))
  (assert (in "tally-keyword" (str raised.value)))
  (assert (in "config" (str raised.value))))


(deftest test-an-unknown-update-form-is-refused
  (with [raised (pytest.raises ValueError)]
    (service "tally-rolling" tally-program :env "m:e" :update "rolling"))
  (assert (in "rolling" (str raised.value))))


(deftest test-every-option-reaches-the-coordinator-declaration
  (setv handoff (service "tally-handoff" tally-program :env "m:e"
                         :config {"step" 1 "base" 0}
                         :readiness {"windowSeconds" 30}
                         :update "handoff"
                         :base-from {"kind" "Deployment" "namespace" "ns" "name" "app" "container" "c"}))
  (setv #(row) (system-declaration (System "handoff-system" #(handoff)) "rev1"))
  (assert (= row {"name" "tally-handoff"
                  "revision" "rev1"
                  "requires" {}
                  "run" {"kind" "service"
                         "factory" "tests.fixtures.services:tally_program"
                         "env" "m:e"
                         "config" {"step" 1 "base" 0}}
                  "readiness" {"windowSeconds" 30}
                  "update" "handoff"
                  "baseFrom" {"kind" "Deployment" "namespace" "ns" "name" "app" "container" "c"}})))


(deftest test-the-single-main-runs-the-declared-program
  ;; テスト用の main は宣言が持つ関数そのもので Program を作る(設定に上書きを重ねる)。
  (<- results list (system-main tally-system {"tally" {"step" 5}}))
  (assert (= results [6])))
