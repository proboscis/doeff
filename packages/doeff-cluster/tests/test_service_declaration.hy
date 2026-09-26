;; service の宣言は値と関数で書く(ADR-DOE-HY-005 R5 — doeff-cluster に macro を置かない)。
;; 関数の参照(module:attr)は Program を作る関数から導き、同じ関数へ解けること・宣言の項目が coordinator へ渡る形に残ること・
;; テスト用の main が宣言の持つ関数そのもので Program を作ることを確かめる。
;; 設定から本体の引数を作るのは program-arguments の 1 か所だけで、テスト用の main・実行先(job_entry)・再生(replay_main)が同じ
;; 引数を作ること、設定の鍵と本体の引数の食い違いを宣言の時点で断ることも確かめる(設計検証の盲検 A・agora-redesign#639)。
(require doeff-hy.macros [defk deftest <-])
(import json)
(import subprocess)
(import sys)
(import pytest)
(import doeff_cluster.service_model [ServiceDef System service resolve system-declaration system-main program-arguments])
(import tests.fixtures.services [tally tally-program tally-system flagged-program])


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


;; --- 設定から本体の引数を作る 1 か所(盲検 A: 組み立て側の欄 record を、テスト用の main は本体へ渡し、実行先は外していた)---

(setv RECORD {"otlp" "http://127.0.0.1:9" "chunkSeconds" 3600 "flushSeconds" 0.1})


(deftest test-program-arguments-leave-out-the-assembly-field
  ;; record は実行先の記録係の設定で、本体の引数ではない。JSON の鍵は Hy の引数名へ mangle する。
  (assert (= (program-arguments {"step" 1 "base-value" 2 "record" RECORD}) {"step" 1 "base_value" 2})))


(deftest test-the-single-main-and-the-worker-build-the-same-arguments
  ;; 宣言の :config に record を書いた service を、テスト用の main と実行先の入口(job_entry service)の両方で走らせる。
  (setv recorded (service "tally-recorded" tally-program :env "tests.fixtures.envs:plain_env"
                          :config {"step" 2 "base" 1 "record" RECORD}))
  (<- results list (system-main (System "recorded" #(recorded))))
  (assert (= results [3]))
  (setv #(row) (system-declaration (System "recorded" #(recorded)) "rev1"))
  (setv run-spec (get row "run"))
  (assert (= (get run-spec "config") {"step" 2 "base" 1 "record" RECORD}))
  (setv done (subprocess.run [sys.executable "-m" "hy" "-m" "doeff_cluster.job_entry" "service"
                              "--factory" (get run-spec "factory") "--env" (get run-spec "env")
                              "--config" (json.dumps (get run-spec "config"))]
                             :capture-output True :text True :timeout 120))
  (assert (= done.returncode 0) done.stderr)
  (assert (in "が終わった: 3" done.stderr) done.stderr))


(deftest test-a-config-key-the-program-does-not-take-is-refused
  ;; 本体の引数に無い設定の鍵は、実行先で走らせた時に初めて落ちる。宣言の時点で service の名と鍵を名指して断る。
  (with [raised (pytest.raises TypeError)]
    (service "tally-extra" tally-program :env "m:e" :config {"step" 1 "base" 0 "stride" 3}))
  (assert (in "tally-extra" (str raised.value)))
  (assert (in "stride" (str raised.value))))


(deftest test-a-program-argument-missing-from-the-config-is-refused
  (with [raised (pytest.raises TypeError)]
    (service "tally-short" tally-program :env "m:e" :config {"step" 1}))
  (assert (in "tally-short" (str raised.value)))
  (assert (in "base" (str raised.value))))


(deftest test-a-program-cannot-take-the-assembly-field-as-an-argument
  ;; 実行先は record を本体へ渡さない。record という名の引数を持つ本体は、どの道でも同じ引数を受け取れないので宣言の時点で断る。
  (with [raised (pytest.raises TypeError)]
    (service "flagged" flagged-program :env "m:e" :config {"record" True "step" 1}))
  (assert (in "flagged" (str raised.value)))
  (assert (in "record" (str raised.value))))
