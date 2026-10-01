;;; 実行環境の事実(ReadRuntimeFacts)の契約テスト — 同じ effect に答える本物(process-runtime-facts — この検の process を読む)と
;;; fake(given-runtime-facts — 渡した事実で答える)が、同じ deftest を通る。解釈器の組み立てと場面は runtime_facts_contract_handlers.hy。
;;;
;;;   * 答えの形: 宣言・キー・印を持つ root・印の中身・pid は場面のまま、module の置き場は問うた順に問うた module だけ
;;;     (__init__ を持つ package = その file・持たない package = dir の path に /・import できない module = 空)
;;;   * 同じ判断(check-runtime-identity)が同じ場面に同じ答え: 宣言の root で一致(キー・root・repo の commit・pid)・root の外の module の
;;;     不一致・宣言が無い・印が無い・前の commit の印 — それぞれの kind と理由の文
;;; 判断そのものの反例(読めない形式の印・渡されたキーの違い・venv の中の module など)は材料を渡して回す test_runtime_identity.hy。
(require doeff-hy.macros [defk deftest <- val])
(import json)
(import os)
(import pathlib [Path])
(import doeff_cluster)
(import doeff_cluster.shared.intent.runtime_identity_model [IdentityFailureKind ModuleOrigin ProcessFacts ReadRuntimeFacts RepoCommit RuntimeIdentity RuntimeIdentityMismatch])
(import doeff_cluster.shared.core.runtime_identity [check-runtime-identity])
(import tests.runtime_facts_contract_handlers [EnterScene FactsWorld MISSING-MODULE NAMESPACE PROBE Scene WorldSeen])


(defk expected-origin [world module]
  {:pre [(: world FactsWorld) (: module str)] :post [(: % ModuleOrigin)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "契約の世界で module が import される置き場の期待(契約の側で独りで書く)。"
  (val file (match module
              m :if (= m PROBE) (+ world.root "/app/" PROBE "/__init__.py")
              m :if (= m NAMESPACE) (+ world.root "/app/" NAMESPACE "/")
              "json" (str (.resolve (Path json.__file__)))
              "doeff_cluster" (+ (str (.resolve (Path (get (list doeff_cluster.__path__) 0)))) "/")
              _ ""))
  (ModuleOrigin :module module :file file))


(deftest test-the-facts-are-answered-in-the-shape-of-the-scene
  {:interpreters ["process-runtime-facts" "given-runtime-facts"]}
  (<- world FactsWorld (WorldSeen))
  (val modules #(MISSING-MODULE "doeff_cluster" NAMESPACE "json" PROBE))
  (<- facts ProcessFacts (ReadRuntimeFacts modules))
  (var origins #())
  (for [m modules]
    (<- origin ModuleOrigin (expected-origin world m))
    (:= origins (+ origins #(origin))))
  (assert (= facts (ProcessFacts :declared-json world.declared-json :key world.key :root world.root :marker-json world.marker-json
                                 :origins origins :pid (os.getpid)))
          facts))


(deftest test-the-declared-root-agrees
  {:interpreters ["process-runtime-facts" "given-runtime-facts"]}
  (<- world FactsWorld (WorldSeen))
  (<- verdict (check-runtime-identity #(PROBE NAMESPACE)))
  (assert (= verdict (RuntimeIdentity :key world.key :root world.root :pid (os.getpid)
                                      :commits (tuple (gfor r world.declared.repos (RepoCommit :name r.name :commit r.commit)))))
          verdict))


(deftest test-modules-outside-the-root-are-named
  {:interpreters ["process-runtime-facts" "given-runtime-facts"]}
  (<- verdict (check-runtime-identity #(PROBE "json" MISSING-MODULE)))
  (assert (= verdict (RuntimeIdentityMismatch :kind IdentityFailureKind.MODULE-OUTSIDE-ROOT :pid (os.getpid)
                                              :detail (.format "json は {}・{} は import できない"
                                                               (str (.resolve (Path json.__file__))) MISSING-MODULE)))
          verdict))


(deftest test-no-declaration-is-undeclared
  {:interpreters ["process-runtime-facts" "given-runtime-facts"]}
  (<- (EnterScene Scene.UNDECLARED))
  (<- verdict (check-runtime-identity #(PROBE)))
  (assert (= verdict (RuntimeIdentityMismatch :kind IdentityFailureKind.UNDECLARED :pid (os.getpid)
                                              :detail "実行環境の宣言(DOEFF_RUNTIME_ENV)が無い — 宣言した root の外で起きている"))
          verdict))


(deftest test-a-venv-without-a-marked-root-is-unmarked
  {:interpreters ["process-runtime-facts" "given-runtime-facts"]}
  (<- (EnterScene Scene.UNMARKED))
  (<- verdict (check-runtime-identity #(PROBE)))
  (assert (= verdict (RuntimeIdentityMismatch :kind IdentityFailureKind.ROOT-UNMARKED :pid (os.getpid)
                                              :detail "venv の上に完成の印を持つ root が無い"))
          verdict))


(deftest test-a-root-of-another-commit-is-a-marker-mismatch
  {:interpreters ["process-runtime-facts" "given-runtime-facts"]}
  (<- world FactsWorld (WorldSeen))
  (<- (EnterScene Scene.STALE))
  (<- verdict (check-runtime-identity #(PROBE)))
  (assert (= verdict (RuntimeIdentityMismatch :kind IdentityFailureKind.MARKER-MISMATCH :pid (os.getpid)
                                              :detail (.format "root {} の印のキー {} が、宣言から計算したキー {} と違う"
                                                               world.stale-root world.stale-key world.key)))
          verdict))
