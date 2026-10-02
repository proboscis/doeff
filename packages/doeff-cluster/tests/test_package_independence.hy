;; この package が業務の code なしで立つこと(設計 worker-runtime-env.md 節 3.4)を、import と依存と準備の code の字面で確かめる。
;;
;;   import の許可表: source(src/doeff_cluster)が import / require してよい最上位の名 = 標準 library・doeff・doeff_*・hy・httpx・
;;                    cloudpickle(と相対の import)。pyproject.toml の依存も同じ許可表で読む。
;;   package 名を書かない: 実行環境の準備の code(宣言・準備・handler)は package の名を文字列で持たない — どの package を wheel に
;;                    するかは宣言の native だけが言う。
;; どちらも反例の fixture(tests/fixtures/independence)で走査が拾うことを確かめる。
(require doeff-hy.macros [deftest defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import re)
(import sys)
(import tomllib)
(import pathlib [Path])

(setv ROOT (. (Path __file__) (resolve) parent parent))
(setv SOURCE (/ ROOT "src" "doeff_cluster"))
(setv FIXTURES (/ ROOT "tests" "fixtures" "independence"))
;; 標準 library 以外で許す最上位の名(doeff_ で始まる物は別に許す)。
(setv ALLOWED-THIRD-PARTY (frozenset #("doeff" "hy" "httpx" "cloudpickle")))
;; package 名を書かない検査の対象 = 実行環境の宣言と準備の code(src/doeff_cluster からの path — 移した時はここも直す。
;; 無い path は検の赤にする: 移しで path が古くなると、検が黙ってその file を読まなくなるため)。
(setv ENV-MODULES #("shared/intent/runtime_env_model.hy" "shared/intent/env_marker_model.hy" "shared/core/runtime_env.hy"
                    "shared/core/runtime_env_rules.hy"
                    "worker/intent/env_prepare_model.hy" "worker/core/env_prepare.hy" "sim/env_world.hy"
                    "worker/protocol/env_translation.hy" "worker/entry/env_tool.hy" "env_handlers.hy"))

(setv HY-IMPORT (re.compile r"\((?:import|require)\s+([A-Za-z_.][A-Za-z0-9_.\-]*)"))
(setv HY-IMPORT-LIST (re.compile r"\(import\s+\[([^\]]*)\]"))
(setv PY-IMPORT (re.compile r"^\s*(?:from\s+([A-Za-z_.][A-Za-z0-9_.]*)\s+import|import\s+([A-Za-z_][A-Za-z0-9_.]*))" re.M))
(setv QUOTED (re.compile r"\"([A-Za-z0-9_.\-]+)\""))


(defk top-level [name]
  {:pre [(: name str)] :post [(: % str)]}
  "import の名 → 最上位の名(相対の import は空・Hy の綴りの - は _)。"
  (if (.startswith name ".") "" (.replace (get (.split name ".") 0) "-" "_")))


(defk allowed? [top]
  {:pre [(: top str)] :post [(: % bool)]}
  "最上位の名が許可表の中か。"
  (or (= top "") (= top "__future__") (in top sys.stdlib-module-names) (in top ALLOWED-THIRD-PARTY) (.startswith top "doeff_")))


(defk imported-names [text suffix]
  {:pre [(: text str) (: suffix str)] :post [(: % tuple)]}
  "file の中身から import / require する名を拾う(Hy と Python の両方の書き方)。"
  ;; 文字列と註の中の「(import …)」は import ではないので、先に外す。
  (val code (re.sub (if (= suffix ".py") r"#[^\n]*" r";[^\n]*") ""
                    (re.sub r"\"\"\"[\s\S]*?\"\"\"|\"(?:\\.|[^\"\\])*\"" "\"\"" text)))
  (var names [])
  (if (= suffix ".py")
      (for [m (.finditer PY-IMPORT code)]
        (.append names (or (.group m 1) (.group m 2))))
      (do (for [m (.finditer HY-IMPORT code)] (.append names (.group m 1)))
          (for [m (.finditer HY-IMPORT-LIST code)] (.extend names (.split (.group m 1))))))
  (tuple names))


(defk outside-imports [path]
  {:pre [(: path Path)] :post [(: % tuple)]}
  "file の中の、許可表の外の import(\"<file>: <名>\")。"
  (<- names tuple (imported-names (.read-text path :encoding "utf-8") path.suffix))
  (var out [])
  (for [n names]
    (<- top str (top-level n))
    (<- ok bool (allowed? top))
    (when (not ok) (.append out (.format "{}: {}" path.name n))))
  (tuple out))


(defk sim-imports [path]
  {:pre [(: path Path)] :post [(: % (get tuple str))]}
  "file の中の、模擬の環境(doeff_cluster.sim — architecture.hy の :verification-environment)の import(\"<file>: <名>\")。"
  (<- names tuple (imported-names (.read-text path :encoding "utf-8") path.suffix))
  (tuple (lfor n names :if (or (= n "doeff_cluster.sim") (.startswith n "doeff_cluster.sim."))
               (.format "{}: {}" path.name n))))


(defk outside-dependencies [path]
  {:pre [(: path Path)] :post [(: % tuple)]}
  "pyproject.toml の依存のうち許可表の外の物。"
  (val data (tomllib.loads (.read-text path :encoding "utf-8")))
  (var out [])
  (for [spec (get (get data "project") "dependencies")]
    (val name (.replace (.lower (get (re.split r"[<>=!~\[; ]" spec 1) 0)) "-" "_"))
    (when (not (or (in name ALLOWED-THIRD-PARTY) (.startswith name "doeff_")))
      (.append out name)))
  (tuple out))


(defk package-names []
  {:pre [] :post [(: % frozenset)]}
  "この workspace の package の名(- と _ の両方の綴り)。準備の code がこれを文字列で持てば赤。"
  (val names (lfor p (.iterdir (. ROOT parent)) :if (/ p "pyproject.toml") :if (.is-file (/ p "pyproject.toml")) p.name))
  (frozenset (+ names (lfor n names (.replace n "-" "_")))))


(defk named-packages [path names]
  {:pre [(: path Path) (: names frozenset)] :post [(: % tuple)]}
  "file の中の文字列のうち、package の名に等しい物(doeff-linter の文脈の宣言 MODULE-TAGS の :context は package の名ではないので外して読む)。"
  (val text (re.sub r"\(val MODULE-TAGS \{[^}]*\}\)" "" (.read-text path :encoding "utf-8")))
  (tuple (lfor m (.finditer QUOTED text) :if (in (.group m 1) names)
               (.format "{}: {}" path.name (.group m 1)))))


(deftest test-the-scans-catch-their-counterexamples
  ;; 反例: 許可表の外の import・依存と、準備の code の package 名の文字列を、走査が拾う。
  (<- imports tuple (outside-imports (/ FIXTURES "outside_import.hy")))
  (assert (= imports #("outside_import.hy: numpy")) imports)
  (<- deps tuple (outside-dependencies (/ FIXTURES "outside_dependency.toml")))
  (assert (= deps #("numpy")) deps)
  (<- names frozenset (package-names))
  (<- named tuple (named-packages (/ FIXTURES "package_name.hy") names))
  (assert (= named #("package_name.hy: doeff-vm")) named)
  (<- sims tuple (sim-imports (/ FIXTURES "imports_sim.hy")))
  (assert (= sims #("imports_sim.hy: doeff_cluster.sim.local")) sims))


(deftest test-production-does-not-import-the-verification-environment
  ;; 模擬の環境(sim/)は本番の coordinator と worker を読むが、本番の code は sim を読まない。
  (val files (sorted (lfor f (.rglob SOURCE "*.hy") :if (not (in "sim" (. (.relative-to f SOURCE) parts))) f)))
  (assert (> (len files) 30) "走査の母集団が空で緑にならない")
  (var found [])
  (for [f files]
    (<- hits tuple (sim-imports f))
    (.extend found hits))
  (assert (not found) (.join "\n" found)))


(deftest test-the-source-imports-only-allowed-names
  ;; 層の dir(shared/<層>・foundation・service/<層>・sim)の下まで読む — 直下だけでは、移しのたびに母集団が黙って減る。
  (val files (sorted (+ (list (.rglob SOURCE "*.hy")) (list (.rglob SOURCE "*.py")))))
  (assert (> (len files) 30) "走査の母集団が空で緑にならない")
  (var outside [])
  (for [f files]
    (<- found tuple (outside-imports f))
    (.extend outside found))
  (assert (not outside) (.join "\n" outside)))


(deftest test-the-dependencies-are-allowed-names
  (<- deps tuple (outside-dependencies (/ ROOT "pyproject.toml")))
  (assert (not deps) deps))


(deftest test-the-environment-code-names-no-package
  (<- names frozenset (package-names))
  (assert (in "doeff-vm" names))
  (val missing (lfor module ENV-MODULES :if (not (.is-file (/ SOURCE module))) module))
  (assert (not missing) missing)
  (var named [])
  (for [module ENV-MODULES]
    (<- found tuple (named-packages (/ SOURCE module) names))
    (.extend named found))
  (assert (not named) (.join "\n" named)))
