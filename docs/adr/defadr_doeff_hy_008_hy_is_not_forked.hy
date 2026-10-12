;;; Executable ADR: Hy はフォークしない・手を入れない。Hy の振る舞いで回避が要る所は doeff の側に置く。
;;;
;;; 出自 = 利用者の決定 2026-09-29 14:0x JST(herdr w3J:pG の会話・agora-redesign #1290 の comment・逐語は :problem の fact)。
;;; doeff は Hy のフォーク(proboscis/hy・本家 1.3.1 に 10 commit)を固定して使っていた。フォークの 3 つの機能を doeff の側の
;;; 回避へ置き換える(子 #1291 テストの索引・#1292 macro の変更で古い bytecode を使わない・#1293 require の高速化)。
;;;
;;; 期限つきの例外: 3 つの回避が main に入り、Hy の指定を PyPI の版へ戻す(#1290 の手順 4・#1293)までは、フォークの固定を
;;; 例外として登録していた。手順 4 で固定を消したので、例外は 0(FORK-PIN = None・import の台帳は空)。例外を足すなら期限
;;; (FORK-EXCEPTIONS-EXPIRE)を過ぎると赤になり、原因が消えた例外が台帳に残っても赤になる(消した便が同じ commit で台帳から削る)。
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
(import tomllib)
(import datetime [date])
(import pathlib [Path])
(import packaging.requirements [Requirement])
(import packaging.utils [canonicalize-name])
(import hy)
(import pytest)


;; ---------------------------------------------------------------------------
;; 期限つきの例外の台帳(#1290 の手順 4 まで)

;; 例外にするフォークの固定の rev(None = 例外なし)。adbe989a958935172f95220526362ccc103e6d47 を 2026-09-29 まで例外にしていた —
;; #1293 で doeff と agora-controllers の Hy の指定を PyPI の版へ戻した時に消した。
(val FORK-PIN None)

;; 例外の期限。3 つの回避(#1291・#1292・#1293)の着地と PyPI の Hy へ戻す変更の目安(決めた日から 2 週)。
;; 延ばす時は理由を :problem に足し、この値を同じ commit で変える。
(val FORK-EXCEPTIONS-EXPIRE (date 2026 10 13))

;; PyPI の Hy に無い名前の import の例外 — #(path module 名)。空: この ADR を書いた時の 5 つ(doeff_hy/pytest_items.py の 4 つ・
;; doeff_adr/lazy_collection.py の read_valid_records)は #1291 が main の c6783e8e2 で消した(この ADR の着地の前)。
;; 足す時は期限(FORK-EXCEPTIONS-EXPIRE)の内に消す物だけにし、理由を :problem に足す。
(val FORK-ONLY-IMPORT-EXCEPTIONS #{})

;; 照合に使う PyPI の Hy の版は、uv.lock の hy の行から読む(locked-hy-version)— 写しの定数を置かない。前は "hy==1.3.1" の
;; 定数で、uv.lock の Hy を上げても照合は 1.3.1 のままだった(新しい版で消えた hy.scoping の名を見逃す — card ki-a05a752e7545)。

;; Hy の上限(R6)を置く 1 か所 — doeff-hy の依存の宣言。上限の版は uv.lock の hy の版ちょうど(`<=<版>`)で、写しの定数を置かず
;; 検が uv.lock と突き合わせる(card ki-0ff168f4eaf5)。
(val HY-CAP-DECLARATION "packages/doeff-hy/pyproject.toml")

;; 依存の宣言の中のフォークの指し方(github.com/proboscis/hy の後が区切り — proboscis/hyper などは当てない)。
(val FORK-URL-PATTERN (re.compile r"github\.com/proboscis/hy(?:\.git|[?#/\"'\s]|$)"))

;; hy.* を import しうる Hy の source の目印((import の後、括弧を跨がずに hy の名前)— reader で読む file の絞り込み。
;; hy の前は語の字でも `-` でもない(`doeff-hy.macros` は当てない — 当てると .hy の file のほぼ全部を reader で読み、検 1 本が 15 秒かかった)。
(val HY-IMPORT-HINT (re.compile r"\(import\b[^()]*?(?<![\w-])hy[.\s\[\])]"))

;; hy.* から import しうる Python の source の目印(行の頭の空白の後の `from hy` / `from hy.X`)— ast で読む file の絞り込み。
(val PY-HY-IMPORT-HINT (re.compile r"^\s*from\s+hy[.\s]" re.MULTILINE))

;; 走査で降りない dir(作業木・venv・生成物)と、設計の記録(盲検の写し・再現の script を含む)。
(val SCAN-SKIP-PARTS #{".git" ".venv" ".worktrees" "node_modules" "target" "__pycache__"})
(val SCAN-SKIP-PREFIXES #("docs/design/"))



;; ---------------------------------------------------------------------------
;; 判断(純関数)

(defk dependency-violations [mentions today pin]
  {:pre [(: mentions list) (: today date) (: pin (| str None))]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "依存の宣言の中のフォークの指し方 #(path 行) のうち、例外(固定の rev pin・期限の内)でない物を挙げる — R3 の判定(pin が None なら全部)。"
  (lfor #(rel line) mentions
        :if (or (is pin None) (not-in pin line) (> today FORK-EXCEPTIONS-EXPIRE))
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
     (if (and (not mentions) (is-not FORK-PIN None))
         ["依存の宣言にフォークの固定がもう無い — FORK-PIN を None にし、期限つきの例外を閉じる"]
         [])))

(defk hy-cap-violations [requirements hy-version]
  {:pre [(: requirements list) (: hy-version str)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "doeff-hy の依存の宣言(PEP 508 の字の列)の hy の行が、上限を uv.lock の版ちょうど(`<=<版>`)に置いていない訳を挙げる — R6 の判定。
   上限が無い・uv.lock の版と違う上限(小版の上限 `<1.4` のように、確かめていない修正版を通す物を含む)・uv.lock の版を通さない宣言・
   条件(marker)の付いた行(`; python_version < '3.15'` のように、条件の外の環境で上限が効かない — 設計検証 lt-Y7VT の盲検 B)は赤。"
  (val rows (lfor text requirements
                  :setv requirement (Requirement text)
                  :if (= (canonicalize-name requirement.name) "hy")
                  requirement))
  (val cap f"<={hy-version}")
  (if (!= (len rows) 1)
      [f"doeff-hy の依存の宣言の hy の行が 1 つでない({(len rows)} 行)— 上限を置く行を決められない"]
      (lfor #(broken message) [#((not-in cap (lfor spec (. (get rows 0) specifier) (str spec)))
                                  f"hy の上限が uv.lock の版ちょうど({cap})でない: {(get rows 0)}")
                                #((not (.contains (. (get rows 0) specifier) hy-version :prereleases True))
                                  f"hy の宣言が uv.lock の版 {hy-version} を通さない: {(get rows 0)}")
                                #((is-not (. (get rows 0) marker) None)
                                  f"hy の上限の行に条件(marker)が付いている — 条件の外の環境では上限が効かない: {(get rows 0)}")]
            :if broken
            message)))

(defk hy-caps-outside-doeff-hy [declarations]
  {:pre [(: declarations list)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "doeff-hy の外の hy の宣言 #(path PEP 508 の字) のうち、上から版を縛る物(<・<=・==・===・~=)を挙げる — R6 の「上限は doeff-hy の
   1 か所」の判定。写しの上限は、doeff-hy の上限と lock を上げる便で黙って古いまま残り、2 つ目の定義元になる(設計検証 lt-Y7VT の盲検 B)。"
  (lfor #(rel text) declarations
        :setv requirement (Requirement text)
        :if (any (gfor spec requirement.specifier (in spec.operator #("<" "<=" "==" "===" "~="))))
        f"{rel}: {text} — hy の上限は {HY-CAP-DECLARATION} の 1 か所だけに置く"))


;; ---------------------------------------------------------------------------
;; 土台(file と子 process)

(defk scan-relative-files [repo-root suffixes]
  {:pre [(: repo-root Path) (: suffixes tuple)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "repo の中の、拡張子が suffixes の file を #(path 相対 path) で挙げる(作業木・venv・設計の記録を除く)。
   降りない dir は os.walk の dirnames から外して中を歩かない(.venv・target の中まで歩くと 1 回に約 2 秒かかった)。"
  (sorted
    (lfor #(dirpath dirnames filenames) (os.walk repo-root)
          :do (setv (cut dirnames 0 None) (lfor d dirnames :if (not-in d SCAN-SKIP-PARTS) d))
          name filenames
          :setv p (/ (Path dirpath) name)
          :setv rel (.as-posix (.relative-to p repo-root))
          :if (and (in p.suffix suffixes)
                   (not-in name SCAN-SKIP-PARTS)
                   (not (.startswith rel SCAN-SKIP-PREFIXES))
                   (.is-file p))
          #(p rel))))

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
          :setv text (.read-text p :encoding "utf-8" :errors "replace")
          ;; ast で読むのは hy.* から import しうる file だけ(全 file を読むと約 3 秒 — 絞り込みは読む物の上位集合)。
          :if (.search PY-HY-IMPORT-HINT text)
          :setv tree (try (ast.parse text)
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
  "(import …) の引数から、hy.* の module の後の [...] に並ぶ名前を #(path module 名) で挙げる(:as とその別名は名前ではない)。
   Hy の reader は点の付いた名 `hy.scoping` を記号 1 つでなく式 `(. hy scoping)` に読むので、module は記号と、記号だけを
   並べた `.` の式の両方から読む — 前は記号だけを見て、Hy の source の `(import hy.X [...])` を 1 つも数えていなかった
   (macros.hy の hy.scoping の 3 つの名が照合から漏れていた・card ki-a05a752e7545)。"
  (lfor #(i module) (enumerate arguments)
        :setv parts (cond (isinstance module hy.models.Symbol)
                            [module]
                          (and (isinstance module hy.models.Expression)
                               (> (len module) 2)
                               (= (get module 0) (hy.models.Symbol "."))
                               (all (gfor part (cut module 1 None) (isinstance part hy.models.Symbol))))
                            (list (cut module 1 None))
                          True
                            [])
        :if (and parts
                 (= (str (get parts 0)) "hy")
                 (< (+ i 1) (len arguments))
                 (isinstance (get arguments (+ i 1)) hy.models.List))
        :setv dotted (.join "." (gfor part parts (hy.mangle (str part))))
        :setv names (list (get arguments (+ i 1)))
        #(j name) (enumerate names)
        :if (and (isinstance name hy.models.Symbol)
                 (or (= j 0) (not (isinstance (get names (- j 1)) hy.models.Keyword))))
        #(rel dotted (hy.mangle (str name)))))

(defk locked-hy-version [lock-text]
  {:pre [(: lock-text str)]
   :post [(: % str)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "uv.lock の本文から、doeff が入れる Hy の版を読む — R4 の照合の版の定義元は uv.lock(Hy を上げる変更が照合の版も上げる・
   card ki-a05a752e7545)。hy の行が 1 つでない・index から入れる版でない(フォークの git など)時は、照合の版を決められないので止める。"
  (val rows (lfor package (.get (tomllib.loads lock-text) "package" [])
                  :if (= (.get package "name") "hy")
                  package))
  (assert (= (len rows) 1)
          (+ "uv.lock の hy の行が 1 つでない(" (str (len rows)) " 行)— 照合する Hy の版を決められない"))
  (val source (.get (get rows 0) "source" {}))
  (assert (in "registry" source)
          (+ "uv.lock の hy が index から入れる版でない(" (str source) ")— R3 のとおりフォークを指さない"))
  (get (get rows 0) "version"))

(defk doeff-hy-requirements [repo-root]
  {:pre [(: repo-root Path)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "doeff-hy の依存の宣言([project] の dependencies — PEP 508 の字の列)を読む — R6 の上限の在りか。"
  (get (tomllib.loads (.read-text (/ repo-root HY-CAP-DECLARATION) :encoding "utf-8")) "project" "dependencies"))

(defk hy-declarations-outside-doeff-hy [repo-root]
  {:pre [(: repo-root Path)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "root と packages/*/ の pyproject.toml(doeff-hy を除く)の hy の宣言を #(path PEP 508 の字) で挙げる — [project] の dependencies・
   optional-dependencies と [dependency-groups] の字の項目を読む(群の中の {include-group …} の表は字でないので読まない)。"
  (val files (sorted (lfor p [(/ repo-root "pyproject.toml") #* (.glob (/ repo-root "packages") "*/pyproject.toml")]
                           :setv rel (.as-posix (.relative-to p repo-root))
                           :if (!= rel HY-CAP-DECLARATION)
                           #(p rel))))
  (lfor #(p rel) files
        :setv data (tomllib.loads (.read-text p :encoding "utf-8"))
        :setv project (.get data "project" {})
        group [(.get project "dependencies" [])
               #* (.values (.get project "optional-dependencies" {}))
               #* (.values (.get data "dependency-groups" {}))]
        text group
        :if (and (isinstance text str) (= (canonicalize-name (. (Requirement text) name)) "hy"))
        #(rel text)))

(defk missing-on-pypi-hy [imports hy-version]
  {:pre [(: imports list) (: hy-version str)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "import #(path module 名) のうち PyPI の Hy(版 hy-version — uv.lock の版)に無い物を挙げる — その版の Hy を入れた使い捨ての
   環境(uv)で実際に import して確かめる(作業木の venv を使わないので、lock を上げて sync する前でも新しい版で測る)。"
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
                     "--with" (+ "hy==" hy-version) "python" "-B" "-c" probe]
                    :input (json.dumps (lfor #(rel module name) imports [rel module name]))
                    :capture-output True :text True :env env :timeout 300 :check False))
  (assert (= completed.returncode 0) completed.stderr)
  (lfor row (json.loads completed.stdout) (tuple row)))


(defadr ADR-DOE-HY-008
  :title "Hy はフォークしない・手を入れない。Hy の振る舞いで回避が要る所は doeff の側に置く。依存の宣言に proboscis/hy を書かず、PyPI の Hy に無い名前を hy.* から import しない(フォークの固定は #1293 で消した)。doeff-hy の Hy の上限は doeff が確かめた uv.lock の版ちょうど"
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
       :evidence "この ADR の scan-hy-imports と missing-on-pypi-hy の出力")
     (fact
       "2026-10-12 の実測: R4 の検は Hy の source の `(import hy.X [...])` を 1 つも数えていなかった — Hy 1.3.1 の reader は `hy.scoping` を式 `(. hy scoping)` に読み、import-form-names は記号だけを見ていた。macros.hy が Hy の公開の API に無い hy.scoping から import する 3 つの名(nearest-python-scope・is-function-scope・ScopeGlobal)は照合から漏れていた。照合の版も写しの定数 \"hy==1.3.1\" で、uv.lock の Hy を上げても照合は 1.3.1 のままだった。どちらも、Hy の版上げで消えた名を検が見逃し、macros.hy の読み込みが ImportError で落ちて doeff-hy を使う全部が止まる形。"
       :evidence "card ki-a05a752e7545(依頼 lt-F5NK46AEW2SMXPY8QKS6APWHD4)— 直す前の scan-hy-imports の 70 件に hy.scoping が 0 件")
     (fact
       "2026-10-12 の実測: doeff-hy の Hy の宣言は hy>=1.2.0 で上限が無かった。doeff-hy を使う他の repo の Hy の宣言は hy>=1.0(agora-controllers の controllers/kanban ほか)・hy>=1.3(agora-controllers の根・custody の custody-scripted)・hy==1.3.0(merge-queue・code-quality)・hy>=1.2.0(agent-commons・dotfiles の agentcli)で、lock は 1.3.1(agora-controllers・custody・agent-commons)と 1.3.0(merge-queue の deploy/ledger/runtime・code-quality)。どの repo も自分の lock だけで Hy を上げられ、その版は doeff の検(R4 は doeff の uv.lock の版で測る)を通らない。Hy は 1.0.0 から semver だが、doeff-hy が依る hy.scoping などは公開の API の外で、修正版 1.3.1 も Hy の importer を直した(ZIP の中の Python の import の退行)。doeff が hy.* から import する 131 の名は 1.0.0〜1.3.1 のどの版にも在った。"
       :evidence "card ki-0ff168f4eaf5(依頼 lt-Y7VTRKY83EG2YC69W8M8EJ0BP1)— 各 repo の origin/main の pyproject.toml と uv.lock・PyPI の Hy 1.3.1 の NEWS.rst・scan-hy-imports と missing-on-pypi-hy を 1.0.0〜1.3.1 で撃った出力")]
  :context
    [(interpretation
       "doeff が Hy の外から同じ正しさを出せるなら、フォークの維持(本家の更新のたびの衝突・2 repo の固定の同時の上げ)は払う理由が無い。回避は doeff の持ち物になり、doeff の検査で守れる。")
     (interpretation
       "「Hy に手を入れない」の測り方は 2 つ: 依存の宣言がフォークを指さない(入れる物が本家)・doeff の code が本家に無い名前を使わない(本家に戻しても import が落ちない)。Hy の内部の名前を読む回避(module の名前空間の _hy_macros を読む・hy.importer._could_be_hy_src を差し替える)は、本家にも在る名前だけを使う限りこの 2 つに反しない。")
     (interpretation
       "doeff の検が確かめる Hy は uv.lock の版 1 つだけなので、doeff-hy が通してよい Hy もその版まで。上限は doeff-hy の metadata の 1 か所で、uv の解決が doeff-hy を使う全部の repo で守る。上限を置かずに他の repo の側で R4 と同じ検を撃つ形は、検の写しが repo の数だけ増え、doeff の他の検(macro の展開・bytecode の包み・ast_unparse の当て物)を通らない。上限を小版(<1.4)で置く形は確かめていない修正版を通し、== で 1 つの版に固定する形は hy==1.3.0 を宣言する merge-queue・code-quality が doeff の rev を上げた時に解けない。")]
  :decision
    [(rule R1 "Hy には一切手を入れない。proboscis/hy に commit を足さない。最終形は PyPI の Hy。")
     (rule R2 "Hy の振る舞いで回避が要る所は doeff の側に置く: テストの索引(#1291)・macro の変更で古い bytecode を使わない判定(#1292 — doeff_hy_bytecode_guard が Python 標準の SourceFileLoader を包む)・require の高速化(#1293)。")
     (rule R3 "doeff と agora-controllers の依存の宣言(pyproject.toml・uv.lock)に proboscis/hy を書かない。例外は 0(rev adbe989a の固定を 2026-09-29 まで例外にしていた — #1293 で消した)。doeff の検査は doeff の木を読む — agora-controllers の宣言は agora-controllers の側の検査が守る。")
     (rule R4 "doeff の code は PyPI の Hy(uv.lock が固定する版)に無い名前を hy.* から import しない。照合の版は検が uv.lock から読み、uv.lock の変更で `make test-changed` がこの検を選ぶ(root の pyproject.toml の [[tool.doeff.contract-tests]])— Hy を上げる変更は、新しい版に名が在る事を登記の前に確かめる。例外の台帳(FORK-ONLY-IMPORT-EXCEPTIONS)は空(#1291 が c6783e8e2 で消した)。足すなら期限は R3 と同じ。")
     (rule R5 "例外の台帳は、原因が消えたら同じ commit で削る(フォークの固定を消す便は FORK-PIN を、import を消す便は FORK-ONLY-IMPORT-EXCEPTIONS の行を)。原因の消えた例外が台帳に残れば赤。")
     (rule R6 "doeff-hy の Hy の依存の宣言の上限は、doeff の uv.lock の hy の版ちょうど(`<=<版>`)にする — doeff-hy を使う他の repo(agora-controllers・custody・merge-queue・code-quality・agent-commons・dotfiles)が自分の lock で上げられる Hy を、doeff の検が確かめた版までに留める(上限は他の repo の uv の解決にも効く)。上限は doeff-hy の 1 か所だけに置き(Hy を使う doeff の package は doeff-domain を除き doeff-hy を引き、uv は 1 つの環境に Hy を 1 つしか入れない — 他の pyproject.toml の hy の宣言は上から版を縛らない)、上限の行に条件(marker)を付けない(条件の外の環境で上限が効かなくなる)。Hy を上げる便は doeff の uv.lock と上限を同じ commit で上げ(上限の file か uv.lock の変更で `make test-changed` がこの検と R4 の検を選ぶ)、他の repo はその commit を含む doeff へ版(doeff-min-commit・git の rev)を上げてから自分の lock の Hy を上げる。")]
  :laws
    [(law dependency-declarations-name-no-hy-fork
       :statement "for_all line in (pyproject.toml ∪ uv.lock) of doeff and agora-controllers: names(line, github.com/proboscis/hy) => (FORK-PIN is not None and rev(line) == FORK-PIN and today <= FORK-EXCEPTIONS-EXPIRE)"
       :counterexamples
         [(counterexample "Hy の固定をフォークの新しい rev へ上げる(2026-09-29 13:16〜13:45 に agora のマージが全部落ちた形)")
          (counterexample "期限を過ぎても pyproject.toml の [tool.uv.sources] に hy = { git = \"https://github.com/proboscis/hy.git\", rev = \"adbe989a…\" } が残る")])
     (law imports-exist-in-pypi-hy
       :statement "for_all (module, name) imported by doeff from hy.*: exists_in(PyPI hy at version(uv.lock, hy), module, name) or ((path, module, name) in FORK-ONLY-IMPORT-EXCEPTIONS and today <= FORK-EXCEPTIONS-EXPIRE)"
       :counterexamples
         [(counterexample "from hy.importer import read_valid_records を新しい file に足す — PyPI の Hy に戻すと import が落ちる")
          (counterexample "(import hy.importer [add-compile-record]) を Hy の source に足す")
          (counterexample "uv.lock の hy を、hy.scoping の nearest-python-scope が無い版へ上げる — macros.hy の読み込みが ImportError で落ちる(card ki-a05a752e7545)")])
     (law doeff-hy-caps-hy-at-the-locked-version
       :statement "for the single hy requirement r in dependencies(packages/doeff-hy/pyproject.toml): ('<=' + version(uv.lock, hy)) in specifiers(r) and version(uv.lock, hy) satisfies r and marker(r) is None; and for_all hy requirement q in the other pyproject.toml of doeff (root and packages/*): no specifier of q has an operator in {<, <=, ==, ===, ~=}"
       :counterexamples
         [(counterexample "doeff-hy の宣言が hy>=1.2.0 のまま(上限なし)— agora-controllers が自分の lock で `uv lock --upgrade-package hy` を撃つと、doeff の検を通っていない版が入る(card ki-0ff168f4eaf5)")
          (counterexample "上限を <1.4 にする — doeff が確かめていない修正版 1.3.2 を他の repo の lock に通す(修正版 1.3.1 も Hy の importer を直した)")
          (counterexample "上限を <=1.4.0 へ上げ、uv.lock の hy を 1.3.1 のまま置く — R4 の検は 1.3.1 でしか測らず、他の repo は 1.4.0 を入れられる")
          (counterexample "上限の行を hy>=1.2.0,<=1.3.1; python_version < '3.15' にする — Python 3.15 の環境では上限が消え、消費者の宣言だけで Hy が決まる(設計検証 lt-Y7VT の盲検 B)")
          (counterexample "doeff-domain の宣言に同じ上限 hy>=1.2.0,<=1.3.1 を写す — doeff-hy の上限と lock を上げる便で写しが古いまま残り、2 つ目の定義元になる(同)")])]
  :enforcement
    [(deftest test-adr-doe-hy-008-dependency-declarations-name-no-hy-fork
       (val repo-root (. (Path __file__) parent parent parent))
       (val mentions (run (fork-mentions repo-root)))
       (val violations (run (dependency-violations mentions (date.today) FORK-PIN)))
       (assert (= violations [])
               (+ "依存の宣言が Hy のフォークを指している(ADR-DOE-HY-008 R3 — PyPI の Hy を指す。"
                  "例外の固定 = " (str FORK-PIN) "): " (str violations))))
     (deftest test-adr-doe-hy-008-imports-exist-in-pypi-hy
       (val repo-root (. (Path __file__) parent parent parent))
       (val hy-version (run (locked-hy-version (.read-text (/ repo-root "uv.lock") :encoding "utf-8"))))
       (val imports (run (scan-hy-imports repo-root)))
       (val missing (run (missing-on-pypi-hy imports hy-version)))
       (val violations (run (import-violations missing (date.today) FORK-ONLY-IMPORT-EXCEPTIONS)))
       (assert (= violations [])
               (+ "PyPI の Hy " hy-version "(uv.lock の版)に無い名前を hy.* から import している"
                  "(ADR-DOE-HY-008 R4 — doeff の側で持つ): " (str violations))))
     (deftest test-adr-doe-hy-008-exceptions-do-not-outlive-their-cause
       (val repo-root (. (Path __file__) parent parent parent))
       (val mentions (run (fork-mentions repo-root)))
       (val hy-version (run (locked-hy-version (.read-text (/ repo-root "uv.lock") :encoding "utf-8"))))
       (val missing (run (missing-on-pypi-hy (run (scan-hy-imports repo-root)) hy-version)))
       (val stale (run (stale-exceptions missing mentions)))
       (assert (= stale []) (+ "原因の消えた例外が台帳に残っている(ADR-DOE-HY-008 R5): " (str stale))))
     (deftest test-adr-doe-hy-008-doeff-hy-caps-hy-at-the-locked-version
       (val repo-root (. (Path __file__) parent parent parent))
       (val hy-version (run (locked-hy-version (.read-text (/ repo-root "uv.lock") :encoding "utf-8"))))
       (val violations (+ (run (hy-cap-violations (run (doeff-hy-requirements repo-root)) hy-version))
                          (run (hy-caps-outside-doeff-hy (run (hy-declarations-outside-doeff-hy repo-root))))))
       (assert (= violations [])
               (+ "Hy の上限が R6 に反する(ADR-DOE-HY-008 R6 — 上限は " HY-CAP-DECLARATION " の 1 か所に、条件なしで uv.lock の版 "
                  hy-version " ちょうどに置き、Hy を上げる便は上限と uv.lock を同じ commit で上げる): " (str violations))))
     (deftest test-adr-doe-hy-008-judgments-reject-the-counterexamples
       ;; 反例: 別の rev・期限の後・台帳に無い import は赤、台帳に在り期限の内の import は緑。
       (val before (date 2026 9 30))
       (val after (date 2026 12 31))
       (val pin "adbe989a958935172f95220526362ccc103e6d47")
       (val pin-line f"hy = {{ git = \"https://github.com/proboscis/hy.git\", rev = \"{pin}\" }}")
       (val other-line "hy = { git = \"https://github.com/proboscis/hy.git\", rev = \"0123456789abcdef\" }")
       (assert (= (run (dependency-violations [#("pyproject.toml" pin-line)] before pin)) []))
       (assert (= (len (run (dependency-violations [#("pyproject.toml" other-line)] before pin))) 1))
       (assert (= (len (run (dependency-violations [#("pyproject.toml" pin-line)] after pin))) 1))
       (assert (= (len (run (dependency-violations [#("pyproject.toml" pin-line)] before None))) 1))
       (val registered #("packages/doeff-adr/src/doeff_adr/lazy_collection.py" "hy.importer" "read_valid_records"))
       (val unregistered #("packages/doeff-hy/src/doeff_hy/new_module.py" "hy.importer" "read_valid_records"))
       (val ledger #{registered})
       (assert (= (run (import-violations [registered] before ledger)) []))
       (assert (= (len (run (import-violations [unregistered] before ledger))) 1))
       (assert (= (len (run (import-violations [registered] after ledger))) 1))
       (assert (not (.search FORK-URL-PATTERN "https://github.com/proboscis/hyper.git")))
       (assert (.search FORK-URL-PATTERN "https://github.com/proboscis/hy.git?rev=adbe989a"))
       ;; 反例(card ki-a05a752e7545): Hy の source の点の付いた module(reader が (. hy scoping) に読む)の名も数え、
       ;; :as の別名と hy でない module(doeff-hy.macros)は数えない。目印も doeff-hy.macros には当てない。
       (val import-form (hy.read "(import hy.scoping [nearest-python-scope is-function-scope] hy [mangle :as m] doeff-hy.macros [defk])"))
       (assert (= (run (import-form-names "macros.hy" (list (cut import-form 1 None))))
                  [#("macros.hy" "hy.scoping" "nearest_python_scope")
                   #("macros.hy" "hy.scoping" "is_function_scope")
                   #("macros.hy" "hy" "mangle")]))
       (assert (.search HY-IMPORT-HINT "(import hy.scoping [ScopeGlobal])"))
       (assert (not (.search HY-IMPORT-HINT "(import doeff-hy.macros [defk])")))
       ;; 反例: 照合の版は uv.lock の hy の行から読む(lock を上げると照合も上がる)・index から入れる版でなければ止める。
       (val lock-text (+ "version = 1\n\n[[package]]\nname = \"hy\"\nversion = \"1.4.0\"\n"
                         "source = { registry = \"https://pypi.org/simple\" }\n"))
       (assert (= (run (locked-hy-version lock-text)) "1.4.0"))
       (with [(pytest.raises AssertionError :match "index から入れる版でない")]
         (run (locked-hy-version (.replace lock-text "registry = \"https://pypi.org/simple\""
                                           "git = \"https://github.com/proboscis/hy.git?rev=adbe989a\""))))
       ;; 反例(card ki-0ff168f4eaf5): 上限なし・小版の上限(<1.4)・uv.lock と違う上限・hy の行の無い宣言は赤、uv.lock の版ちょうどの上限は緑。
       (assert (= (run (hy-cap-violations ["doeff>=0.2.0" "hy>=1.2.0,<=1.3.1"] "1.3.1")) []))
       (assert (= (len (run (hy-cap-violations ["hy>=1.2.0"] "1.3.1"))) 1))
       (assert (= (len (run (hy-cap-violations ["hy>=1.2.0,<1.4"] "1.3.1"))) 1))
       (assert (= (len (run (hy-cap-violations ["hy>=1.2.0,<=1.3.1"] "1.4.0"))) 2))
       (assert (= (len (run (hy-cap-violations ["pydantic>=2.11"] "1.3.1"))) 1))
       ;; 反例(設計検証 lt-Y7VT の盲検 B): 条件(marker)の付いた上限の行と、doeff-hy の外の上限の写しは赤。上から縛らない宣言は緑。
       (assert (= (len (run (hy-cap-violations ["hy>=1.2.0,<=1.3.1; python_version < '3.15'"] "1.3.1"))) 1))
       (assert (= (len (run (hy-caps-outside-doeff-hy [#("packages/doeff-domain/pyproject.toml" "hy>=1.2.0,<=1.3.1")]))) 1))
       (assert (= (len (run (hy-caps-outside-doeff-hy [#("packages/doeff-domain/pyproject.toml" "hy~=1.3")]))) 1))
       (assert (= (run (hy-caps-outside-doeff-hy [#("packages/doeff-domain/pyproject.toml" "hy>=1.2.0")
                                                  #("pyproject.toml" "hy>=1.2.0,!=1.3.0")])) [])))]
  :plans ["docs/design/hy-fresh-bytecode/artifacts/v1/design.md"])
