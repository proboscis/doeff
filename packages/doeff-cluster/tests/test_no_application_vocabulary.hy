;; この package は業務を知らない(2026-09-25 — 業務の repo から切り出した時の決まり)。業務の系の語が source・検・配備の材料・文書に
;; 混ざっていないことを、file の中身と名の両方で確かめる。業務の事情(名・資格・語)は引数や宣言(ClusterNaming・CodeLayout・
;; effect_codec.register)で業務の側から渡す。
;;
;; 語の一覧は、切り出す前のこの code に混ざっていた業務の系の語(系の名・業務の資源の名・業務の repo の配置)。大文字小文字を区別しない。
(require doeff-hy.macros [deftest])
(import re)
(import pathlib [Path])

(setv ROOT (. (Path __file__) (resolve) parent parent))
(setv THIS (. (Path __file__) (resolve)))
;; 語 → 何の語か(赤の時の文に出す)。
(setv FORBIDDEN {"agora" "業務の系の名"
                 "kanban" "業務の盤の名"
                 "conversation" "業務の資源の名"
                 "budget" "業務の書き手の名"
                 "artifact" "業務の書き手の名"
                 "herdr" "業務の系の名"
                 "hypha" "業務の系の名"
                 "mediagen" "業務の系の名"
                 "sporocarp" "業務の系の名"
                 "acp" "業務の系の名(制御面)"
                 "controllers/" "業務の repo の配置"
                 "controllers." "業務の repo の module 名"
                 "clients/hy" "業務の repo の配置"})
(setv PATTERN (re.compile (+ "(?i)(?<![a-z])(" (.join "|" (gfor w FORBIDDEN (re.escape w))) ")")))
(setv SUFFIXES #(".hy" ".py" ".md" ".sh" ".toml" ".yaml" ".yml" ".json" ".sql" ".xml"))


(defn #^ list scanned-files []
  "検める file: この package の下の文字の file(この検そのものと、生成物の dir を除く)。"
  (sorted (gfor p (.rglob ROOT "*")
                :if (and (.is-file p)
                         (or (in p.suffix SUFFIXES) (= p.name "Dockerfile"))
                         (!= (.resolve p) THIS)
                         (not (any (gfor part (. (.relative-to p ROOT) parts)
                                         (or (= part "__pycache__") (.startswith part "."))))))
                p)))


(defn #^ list hits-in [#^ str text]
  "text の中の業務の語の一致(語・行の番号)。"
  (lfor #(n line) (enumerate (.splitlines text) 1)
        m (.finditer PATTERN line)
        #((.lower (.group m 1)) n)))


(deftest test-the-scan-finds-application-words-and-ignores-generic-ones
  ;; 反例: 業務の語は大文字小文字を問わず拾う。語の一部として含む一般の語(例: replace の中の "acp" ではない綴り)は拾わない。
  (assert (= (lfor #(w _) (hits-in "(import controllers.budget.effects)") w) ["controllers." "budget"]))
  (assert (= (lfor #(w _) (hits-in "io.Agora.revision / ACP_TOKEN / Kanban") w) ["agora" "acp" "kanban"]))
  (assert (= (hits-in "(replace state :tasks tasks) ;; placement / capacity") [])))


(deftest test-the-scan-covers-the-package
  ;; 検める母集団が空で緑にならない: source・検・配備の材料・文書が入っている。
  (setv names (sfor p (scanned-files) (.as-posix (.relative-to p ROOT))))
  (for [want ["src/doeff_cluster/coordinator.hy" "src/doeff_cluster/effect_codec.hy" "src/doeff_cluster/shim.py"
              "tests/test_coordinator.hy" "deploy/boot.sh" "deploy/Dockerfile" "README.md" "pyproject.toml"]]
    (assert (in want names) want)))


(deftest test-the-package-has-no-application-vocabulary
  (setv found [])
  (for [p (scanned-files)]
    (setv rel (.as-posix (.relative-to p ROOT)))
    (for [#(word _) (hits-in rel)]
      (.append found (.format "{}(file の名): {} — {}" rel word (get FORBIDDEN word))))
    (for [#(word n) (hits-in (.read-text p :encoding "utf-8"))]
      (.append found (.format "{}:{}: {} — {}" rel n word (get FORBIDDEN word)))))
  (assert (= found []) (.join "\n" found)))
