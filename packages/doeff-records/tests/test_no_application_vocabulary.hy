;; この package は業務を知らない(agora-redesign #623 の受入「package に業務の語が 0 件」・doeff-claude-code の同名の検と同じ形)。業務の系の語が source・検・配備の材料・文書に
;; 混ざっていないことを、file の中身と名の両方で確かめる。業務の事情(表の名・書き手の名・欄)は宣言(RecordsSchema)と
;; handler を組む時の引数で業務の側から渡す。
;;
;; 語の一覧は doeff-cluster と同じ(業務の系の名・業務の資源の名・業務の repo の配置)。大文字小文字を区別しない。
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
;; 宣言の欄の名 size-budget(設計の綴り・行の byte の上限)は業務の書き手の名 budget ではない — 語を調べる前に外す。
(setv ALLOWED (re.compile "(?i)size[-_]budget"))
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
        m (.finditer PATTERN (.sub ALLOWED "" line))
        #((.lower (.group m 1)) n)))


(deftest test-the-scan-finds-application-words-and-ignores-generic-ones
  ;; 反例: 業務の語は大文字小文字を問わず拾う。語の一部として含む一般の語(例: replace の中の "acp" ではない綴り)は拾わない。
  (assert (= (lfor #(w _) (hits-in "(import controllers.budget.effects)") w) ["controllers." "budget"]))
  (assert (= (lfor #(w _) (hits-in "io.Agora.revision / ACP_TOKEN / Kanban") w) ["agora" "acp" "kanban"]))
  (assert (= (hits-in "(replace state :tasks tasks) ;; placement / capacity") []))
  ;; 宣言の欄の名 size-budget は拾わず、単独の budget は拾う。
  (assert (= (hits-in ":size-budget 400 decl.size_budget") []))
  (assert (= (lfor #(w _) (hits-in "size-budget budget") w) ["budget"])))


(deftest test-the-scan-covers-the-package
  ;; 検める母集団が空で緑にならない: source・検・配備の材料・文書が入っている。
  (setv names (sfor p (scanned-files) (.as-posix (.relative-to p ROOT))))
  (for [want ["src/doeff_records/effects.hy" "src/doeff_records/values.hy" "src/doeff_records/memory.hy"
              "src/doeff_records/pg.hy" "src/doeff_records/pg_sql.hy" "src/doeff_records/laws.hy"
              "tests/test_laws.hy" "tests/conftest.py" "README.md" "pyproject.toml"]]
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
