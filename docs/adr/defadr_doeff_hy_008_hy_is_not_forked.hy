;;; Executable ADR: Hy はフォークしない・手を入れない。Hy の振る舞いで回避が要る所は doeff の側に置く。
;;;
;;; 出自 = 利用者の決定 2026-09-29 14:0x JST(herdr w3J:pG の会話・agora-redesign #1290 の comment・逐語は :problem の fact)。
;;; doeff は Hy のフォーク(proboscis/hy・本家 1.3.1 に 10 commit)を固定して使っていた。フォークの 3 つの機能を doeff の側の
;;; 回避へ置き換える(子 #1291 テストの索引・#1292 macro の変更で古い bytecode を使わない・#1293 require の高速化)。
;;;
;;; 期限つきの例外: 3 つの回避が main に入り、Hy の指定を PyPI の版へ戻す(#1290 の手順 4)までは、今のフォークの固定を
;;; 例外として登録する(フォークだけの名前の import は #1291 が消したので、その台帳は空)。例外は期限(FORK-EXCEPTIONS-EXPIRE)を過ぎると赤になり、
;;; 原因が消えた例外が台帳に残っても赤になる(消した便が同じ commit で台帳から削る)。
;;;
;;; 戻し方: この ADR を足した commit を revert する(ADR の file 1 つが消える)。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk <- val var])
(import doeff [run])
(import ast)
(import json)
(import os)
(import re)
(import shutil)
(import subprocess)
(import sys)
(import datetime [date])
(import pathlib [Path])
(import hy)


;; ---------------------------------------------------------------------------
;; 期限つきの例外の台帳(#1290 の手順 4 まで)

;; 今のフォークの固定(doeff の pyproject.toml の [tool.uv.sources] と uv.lock)— この rev だけを例外にする。
(val FORK-PIN "adbe989a958935172f95220526362ccc103e6d47")

;; 例外の期限。3 つの回避(#1291・#1292・#1293)の着地と PyPI の Hy へ戻す変更の目安(決めた日から 2 週)。
;; 延ばす時は理由を :problem に足し、この値を同じ commit で変える。
(val FORK-EXCEPTIONS-EXPIRE (date 2026 10 13))

;; PyPI の Hy に無い名前の import の例外 — #(path module 名)。空: この ADR を書いた時の 5 つ(doeff_hy/pytest_items.py の 4 つ・
;; doeff_adr/lazy_collection.py の read_valid_records)は #1291 が main の c6783e8e2 で消した(この ADR の着地の前)。
;; 足す時は期限(FORK-EXCEPTIONS-EXPIRE)の内に消す物だけにし、理由を :problem に足す。
(val FORK-ONLY-IMPORT-EXCEPTIONS #{})

;; 照合に使う PyPI の Hy の版(フォークの元の版)。
(val PYPI-HY "hy==1.3.1")

;; 依存の宣言の中のフォークの指し方(github.com/proboscis/hy の後が区切り — proboscis/hyper などは当てない)。
(val FORK-URL-PATTERN (re.compile r"github\.com/proboscis/hy(?:\.git|[?#/\"'\s]|$)"))

;; hy.* を import しうる Hy の source の目印((import の後、括弧を跨がずに hy の名前)— reader で読む file の絞り込み。
(val HY-IMPORT-HINT (re.compile r"\(import\b[^()]*?\bhy[.\s\[\])]"))

;; 走査で降りない dir(作業木・venv・生成物)と、設計の記録(盲検の写し・再現の script を含む)。
(val SCAN-SKIP-PARTS #{".git" ".venv" ".worktrees" "node_modules" "target" "__pycache__"})
(val SCAN-SKIP-PREFIXES #("docs/design/"))



;; ---------------------------------------------------------------------------
;; 判断(純関数)

(defk dependency-violations [mentions today]
  {:pre [(: mentions list) (: today date)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "依存の宣言の中のフォークの指し方 #(path 行) のうち、例外(今の固定の rev・期限の内)でない物を挙げる — R3 の判定。"
  (lfor #(rel line) mentions
        :if (or (not-in FORK-PIN line) (> today FORK-EXCEPTIONS-EXPIRE))
        f"{rel}: {(.strip line)}"))

(defk import-violations [missing today exceptions]
  {:pre [(: missing list) (: today date) (: exceptions set)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "PyPI の Hy に無い名前の import #(path module 名) のうち、例外(台帳に在り・期限の内)でない物を挙げる — R4 の判定。"
  (lfor #(rel module name) missing
        :if (or (not-in #(rel module name) exceptions) (> today FORK-EXCEPTIONS-EXPIRE))
        f"{rel}: from {module} import {name}"))

(defk stale-exceptions [missing mentions]
  {:pre [(: missing list) (: mentions list)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "原因が消えたのに台帳に残る例外を挙げる — 消した便が同じ commit で台帳から削るため(腐った台帳は例外を黙って延ばす)。"
  (val still-missing (sfor #(rel module name) missing #(rel module name)))
  (+ (lfor entry (sorted FORK-ONLY-IMPORT-EXCEPTIONS)
           :if (not-in entry still-missing)
           f"import の例外 {entry} は、もう PyPI の Hy に無い名前を import していない — FORK-ONLY-IMPORT-EXCEPTIONS から削る")
     (if (and (not mentions) FORK-PIN)
         ["依存の宣言にフォークの固定がもう無い — FORK-PIN を空にし、期限つきの例外を閉じる"]
         [])))


;; ---------------------------------------------------------------------------
;; 土台(file と子 process)

(defk scan-relative-files [repo-root suffixes]
  {:pre [(: repo-root Path) (: suffixes tuple)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "repo の中の、拡張子が suffixes の file を #(path 相対 path) で挙げる(作業木・venv・設計の記録を除く)。"
  (lfor p (sorted (.rglob repo-root "*"))
        :setv rel (.relative-to p repo-root)
        :if (and (in p.suffix suffixes)
                 (not (& (set rel.parts) SCAN-SKIP-PARTS))
                 (not (.startswith (.as-posix rel) SCAN-SKIP-PREFIXES))
                 (.is-file p))
        #(p (.as-posix rel))))

(defk fork-mentions [repo-root]
  {:pre [(: repo-root Path)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "依存の宣言(pyproject.toml と uv.lock)の中でフォークを指す行を #(path 行) で挙げる。"
  (<- files (scan-relative-files repo-root #(".toml" ".lock")))
  (lfor #(p rel) files
        :if (in p.name #("pyproject.toml" "uv.lock"))
        line (.splitlines (.read-text p :encoding "utf-8"))
        :if (.search FORK-URL-PATTERN line)
        #(rel line)))

(defk scan-hy-imports [repo-root]
  {:pre [(: repo-root Path)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "doeff の Python と Hy の source が hy.* から import する名前を #(path module 名) で挙げる(module の import だけの文は名前 0)。"
  (<- python-files (scan-relative-files repo-root #(".py")))
  (<- hy-files (scan-relative-files repo-root #(".hy" ".hyk" ".hyp")))
  (val python-imports
    (lfor #(p rel) python-files
          :setv tree (try (ast.parse (.read-text p :encoding "utf-8" :errors "replace"))
                          (except [SyntaxError] None))
          ;; Python として読めない file(検の data)は import もできないので、hy.* の名前も使えない — 数えない。
          :if (is-not tree None)
          node (ast.walk tree)
          :if (and (isinstance node ast.ImportFrom)
                   (= node.level 0)
                   (is-not node.module None)
                   (or (= node.module "hy") (.startswith node.module "hy.")))
          alias node.names
          #(rel node.module alias.name)))
  (<- hy-imports (hy-source-imports hy-files))
  (sorted (set (+ python-imports hy-imports))))

(defk hy-source-imports [files]
  {:pre [(: files list)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "Hy の source(#(path 相対 path) の列)の (import hy.X [a b]) の名前を #(path module 名) で挙げる — Hy の reader で読むので、註と文字列の中の形は数えない。"
  (val found [])
  (var pending [])
  (var form None)
  (for [#(p rel) files
         :setv text (.read-text p :encoding "utf-8" :errors "replace")
         ;; reader で読むのは hy.* を import しうる file だけ(全 file を読むと数十秒 — 絞り込みは読む物の上位集合)。
         :if (.search HY-IMPORT-HINT text)]
    (:= pending (list (hy.read-many text :filename (str p))))
    (while pending
      (:= form (.pop pending))
      (when (isinstance form hy.models.Sequence)
        (.extend pending form))
      (when (and (isinstance form hy.models.Expression)
                 form
                 (= (get form 0) (hy.models.Symbol "import")))
        (<- names (import-form-names rel (list (cut form 1 None))))
        (.extend found names))))
  found)

(defk import-form-names [rel arguments]
  {:pre [(: rel str) (: arguments list)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "(import …) の引数から、hy.* の module の後の [...] に並ぶ名前を #(path module 名) で挙げる(:as とその別名は名前ではない)。"
  (lfor #(i module) (enumerate arguments)
        :if (and (isinstance module hy.models.Symbol)
                 (or (= (str module) "hy") (.startswith (str module) "hy."))
                 (< (+ i 1) (len arguments))
                 (isinstance (get arguments (+ i 1)) hy.models.List))
        :setv names (list (get arguments (+ i 1)))
        #(j name) (enumerate names)
        :if (and (isinstance name hy.models.Symbol)
                 (or (= j 0) (not (isinstance (get names (- j 1)) hy.models.Keyword))))
        #(rel (hy.mangle (str module)) (hy.mangle (str name)))))

(defk missing-on-pypi-hy [imports]
  {:pre [(: imports list)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "import #(path module 名) のうち PyPI の Hy に無い物を挙げる — PyPI の Hy を入れた使い捨ての環境(uv)で実際に import して確かめる。"
  (val uv (shutil.which "uv"))
  (assert (is-not uv None) "uv が PATH に無い — PyPI の Hy の使い捨ての環境を作れない")
  (val probe (+ "import importlib, json, sys\n"
                 "missing = []\n"
                 "for rel, module, name in json.load(sys.stdin):\n"
                 "    try:\n"
                 "        target = importlib.import_module(module)\n"
                 "    except ImportError:\n"
                 "        missing.append([rel, module, name]); continue\n"
                 "    if hasattr(target, name):\n"
                 "        continue\n"
                 "    try:\n"
                 "        importlib.import_module(module + '.' + name)\n"
                 "    except ImportError:\n"
                 "        missing.append([rel, module, name])\n"
                 "print(json.dumps(missing))\n"))
  (val env (dfor #(key value) (.items os.environ)
                  :if (not-in key #("VIRTUAL_ENV" "CONDA_PREFIX" "PYTHONPATH" "PYTHONPYCACHEPREFIX"))
                  key value))
  (val completed
    (subprocess.run [uv "run" "--no-project" "--isolated"
                     "--python" f"{sys.version-info.major}.{sys.version-info.minor}"
                     "--with" PYPI-HY "python" "-B" "-c" probe]
                    :input (json.dumps (lfor #(rel module name) imports [rel module name]))
                    :capture-output True :text True :env env :timeout 300 :check False))
  (assert (= completed.returncode 0) completed.stderr)
  (lfor row (json.loads completed.stdout) (tuple row)))


(defadr ADR-DOE-HY-008
  :title "Hy はフォークしない・手を入れない。Hy の振る舞いで回避が要る所は doeff の側に置く。依存の宣言に proboscis/hy を書かず、PyPI の Hy に無い名前を hy.* から import しない(今のフォークの固定は期限つきの例外)"
  :status "accepted"
  :scope ["pyproject.toml"
          "uv.lock"
          "packages"
          "docs/adr/defadr_doeff_hy_008_hy_is_not_forked.hy"]
  :problem
    [(fact
       "利用者の決定 2026-09-29 14:0x JST(逐語): \"I see, then lets introduce such bypasses and not touch hy at all.\" — 前の問い(逐語): \"so we are using hy fork but do we need to? we decided to cache the test index right?\" / \"why do we need to speed up the 'require' to even fork?\" / \"i mean if everything can be bypassed without forking hy\""
       :evidence "herdr w3J:pG の会話・agora-redesign #1290 の comment(2026-09-29)")
     (fact
       "フォークの費用(2026-09-29 の実例): doeff と agora-controllers の Hy の固定を同時に上げる必要があり、13:16〜13:45 に agora のマージが全部落ちた。早朝には bytecode の形式の変更で image が組めなくなった(#1003)。"
       :evidence "agora-redesign #1290 本文")
     (fact
       "フォークは本家 1.3.1 に 10 commit・6 file(hy/importer.py +439 行)。本家のメンテナは 8 行の変更(hylang/hy#2728)にも慎重で、取り込みは見込めない。"
       :evidence "agora-redesign #1290 本文の実測")
     (fact
       "2026-09-29 の実測: doeff の依存の宣言はフォークの rev adbe989a を指し(pyproject.toml の [tool.uv.sources] と uv.lock)、doeff の code は PyPI の Hy 1.3.1 に無い名前を 5 つ import していた(packages/doeff-hy/src/doeff_hy/pytest_items.py の 4 つ・packages/doeff-adr/src/doeff_adr/lazy_collection.py の read_valid_records)。5 つは #1291 が main の c6783e8e2(同じ日)で消した — この ADR の R5 の検査が、台帳に残った 5 つを「原因の消えた例外」として赤にして確かめた。"
       :evidence "この ADR の scan-hy-imports と missing-on-pypi-hy の出力")]
  :context
    [(interpretation
       "doeff が Hy の外から同じ正しさを出せるなら、フォークの維持(本家の更新のたびの衝突・2 repo の固定の同時の上げ)は払う理由が無い。回避は doeff の持ち物になり、doeff の検査で守れる。")
     (interpretation
       "「Hy に手を入れない」の測り方は 2 つ: 依存の宣言がフォークを指さない(入れる物が本家)・doeff の code が本家に無い名前を使わない(本家に戻しても import が落ちない)。Hy の内部の名前を読む回避(module の名前空間の _hy_macros を読む・hy.importer._could_be_hy_src を差し替える)は、本家にも在る名前だけを使う限りこの 2 つに反しない。")]
  :decision
    [(rule R1 "Hy には一切手を入れない。proboscis/hy に commit を足さない。最終形は PyPI の Hy。")
     (rule R2 "Hy の振る舞いで回避が要る所は doeff の側に置く: テストの索引(#1291)・macro の変更で古い bytecode を使わない判定(#1292 — doeff_hy_bytecode_guard が Python 標準の SourceFileLoader を包む)・require の高速化(#1293)。")
     (rule R3 "doeff と agora-controllers の依存の宣言(pyproject.toml・uv.lock)に proboscis/hy を書かない。例外は今の固定(rev adbe989a)だけで、期限は FORK-EXCEPTIONS-EXPIRE(#1290 の手順 4 で消す)。doeff の検査は doeff の木を読む — agora-controllers の宣言は agora-controllers の側の検査が守る。")
     (rule R4 "doeff の code は PyPI の Hy に無い名前を hy.* から import しない。例外の台帳(FORK-ONLY-IMPORT-EXCEPTIONS)は空(#1291 が c6783e8e2 で消した)。足すなら期限は R3 と同じ。")
     (rule R5 "例外の台帳は、原因が消えたら同じ commit で削る(フォークの固定を消す便は FORK-PIN を、import を消す便は FORK-ONLY-IMPORT-EXCEPTIONS の行を)。原因の消えた例外が台帳に残れば赤。")]
  :laws
    [(law dependency-declarations-name-no-hy-fork
       :statement "for_all line in (pyproject.toml ∪ uv.lock) of doeff and agora-controllers: names(line, github.com/proboscis/hy) => (rev(line) == FORK-PIN and today <= FORK-EXCEPTIONS-EXPIRE)"
       :counterexamples
         [(counterexample "Hy の固定をフォークの新しい rev へ上げる(2026-09-29 13:16〜13:45 に agora のマージが全部落ちた形)")
          (counterexample "期限を過ぎても pyproject.toml の [tool.uv.sources] に hy = { git = \"https://github.com/proboscis/hy.git\", rev = \"adbe989a…\" } が残る")])
     (law imports-exist-in-pypi-hy
       :statement "for_all (module, name) imported by doeff from hy.*: exists_in(PyPI hy 1.3.1, module, name) or ((path, module, name) in FORK-ONLY-IMPORT-EXCEPTIONS and today <= FORK-EXCEPTIONS-EXPIRE)"
       :counterexamples
         [(counterexample "from hy.importer import read_valid_records を新しい file に足す — PyPI の Hy に戻すと import が落ちる")
          (counterexample "(import hy.importer [add-compile-record]) を Hy の source に足す")])]
  :enforcement
    [(deftest test-adr-doe-hy-008-dependency-declarations-name-no-hy-fork
       (val repo-root (. (Path __file__) parent parent parent))
       (val mentions (run (fork-mentions repo-root)))
       (val violations (run (dependency-violations mentions (date.today))))
       (assert (= violations [])
               (+ "依存の宣言が Hy のフォークを指している(ADR-DOE-HY-008 R3 — PyPI の Hy を指す。"
                  "今の固定の例外は期限 " (str FORK-EXCEPTIONS-EXPIRE) " まで): " (str violations))))
     (deftest test-adr-doe-hy-008-imports-exist-in-pypi-hy
       (val repo-root (. (Path __file__) parent parent parent))
       (val imports (run (scan-hy-imports repo-root)))
       (val missing (run (missing-on-pypi-hy imports)))
       (val violations (run (import-violations missing (date.today) FORK-ONLY-IMPORT-EXCEPTIONS)))
       (assert (= violations [])
               (+ "PyPI の Hy に無い名前を hy.* から import している(ADR-DOE-HY-008 R4 — doeff の側で持つ): "
                  (str violations))))
     (deftest test-adr-doe-hy-008-exceptions-do-not-outlive-their-cause
       (val repo-root (. (Path __file__) parent parent parent))
       (val mentions (run (fork-mentions repo-root)))
       (val missing (run (missing-on-pypi-hy (run (scan-hy-imports repo-root)))))
       (val stale (run (stale-exceptions missing mentions)))
       (assert (= stale []) (+ "原因の消えた例外が台帳に残っている(ADR-DOE-HY-008 R5): " (str stale))))
     (deftest test-adr-doe-hy-008-judgments-reject-the-counterexamples
       ;; 反例: 別の rev・期限の後・台帳に無い import は赤、台帳に在り期限の内の import は緑。
       (val before (date 2026 9 30))
       (val after (date 2026 12 31))
       (val pin-line f"hy = {{ git = \"https://github.com/proboscis/hy.git\", rev = \"{FORK-PIN}\" }}")
       (val other-line "hy = { git = \"https://github.com/proboscis/hy.git\", rev = \"0123456789abcdef\" }")
       (assert (= (run (dependency-violations [#("pyproject.toml" pin-line)] before)) []))
       (assert (= (len (run (dependency-violations [#("pyproject.toml" other-line)] before))) 1))
       (assert (= (len (run (dependency-violations [#("pyproject.toml" pin-line)] after))) 1))
       (val registered #("packages/doeff-adr/src/doeff_adr/lazy_collection.py" "hy.importer" "read_valid_records"))
       (val unregistered #("packages/doeff-hy/src/doeff_hy/new_module.py" "hy.importer" "read_valid_records"))
       (val ledger #{registered})
       (assert (= (run (import-violations [registered] before ledger)) []))
       (assert (= (len (run (import-violations [unregistered] before ledger))) 1))
       (assert (= (len (run (import-violations [registered] after ledger))) 1))
       (assert (not (.search FORK-URL-PATTERN "https://github.com/proboscis/hyper.git")))
       (assert (.search FORK-URL-PATTERN "https://github.com/proboscis/hy.git?rev=adbe989a")))]
  :plans ["docs/design/hy-fresh-bytecode/artifacts/v1/design.md"])
