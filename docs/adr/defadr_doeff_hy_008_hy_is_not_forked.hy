;;; Executable ADR: Hy はフォークしない・手を入れない。Hy の振る舞いで回避が要る所は doeff の側に置く。
;;;
;;; 出自 = 利用者の決定 2026-09-29(agora-redesign #1290 の comment・決めた事は :problem の fact)。
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
(import doeff-hy [light-stack])
(import ast)
(import builtins)
(import collections)
(import importlib.util)
(import inspect)
(import json)
(import os)
(import re)
(import shutil)
(import subprocess)
(import sys)
(import types)
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
;; 読みは doeff で uv.lock の版を読む 1 か所(scripts/semgrep_locked.py の locked_version — commit の hook の semgrep・pre-commit と
;; packages/doeff-hy/tests/test_bytecode_guard.py も読む)に任せ、ここに写しの読みを置かない(card ki-33d2441b7e28)。
(val LOCK-READER (/ (. (Path __file__) parent parent parent) "scripts" "semgrep_locked.py"))

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

;; R7 — import でない形の Hy の内部への依り(card ki-2fa4c02604b1)。数えるのは source の形からで、写しの台帳を置かない。
;; hy.* の名へ代入しうる Python の source の目印(行の頭の `hy.X = …`)— ast で読む file の絞り込み。
(val PY-HY-ASSIGN-HINT (re.compile r"^\s*hy(?:\.\w+)+\s*=(?!=)" re.MULTILINE))

;; Hy が macro へ compiler を渡す約束の名(第 1 引数を `_hy-compiler` にした macro に、Hy 1.3.1 の hy.macros.macroexpand が
;; compiler を渡す)を書いた Hy の source の目印 — reader で読む file の絞り込み(2026-10-12 は macros.hy と handle.hy の 2 つ)。
(val HY-COMPILER-HINT (re.compile r"(?<![\w-])_hy[-_]compiler(?![\w-])"))

;; macro が受けた compiler を指す記号の名。macros.hy の補助の関数は受けた compiler を `compiler` の名で受ける。
(val COMPILER-SYMBOLS #{"compiler" "_hy-compiler" "_hy_compiler"})



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

(defk attribute-parts [node]
  {:pre [(: node ast.AST)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "名から始まる属性の連なり(`hy.importer._could_be_hy_src`)を名の列にする — 名から始まる連なりでなければ []。"
  (var parts [])
  (var current node)
  (while (isinstance current ast.Attribute)
    (:= parts (+ [current.attr] parts))
    (:= current current.value))
  (if (isinstance current ast.Name) (+ [current.id] parts) []))

(defk python-assignment-reliances [rel tree]
  {:pre [(: rel str) (: tree ast.Module)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "Python の source の木から、hy.* の名への代入(差し替え — `hy.importer._could_be_hy_src = …`)を
   #(path \"replaced\" module 名) で挙げる — R7 の数え方。条件を確かめてから差し替える形(ast_unparse の install)も数える:
   差し替える名が新しい版で消えたら、当て物ごと消す合図。"
  (val found [])
  (for [node (ast.walk tree)
        :if (isinstance node #(ast.Assign ast.AugAssign ast.AnnAssign))
        target (if (isinstance node ast.Assign) node.targets [node.target])]
    (<- parts (attribute-parts target))
    (when (and (> (len parts) 1) (= (get parts 0) "hy"))
      (.append found #(rel "replaced" (.join "." (cut parts 0 -1)) (get parts -1)))))
  found)

(defk hy-form-reliances [rel forms]
  {:pre [(: rel str) (: forms list)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "Hy の source の式の列から、macro が受ける compiler への依りを #(path 種類 対象 名) で挙げる — R7 の数え方:
   第 1 引数を `_hy-compiler` にした defmacro(Hy が compiler を渡す約束 — compiler-parameter)と、compiler の属性の読み
   `(. compiler scope)`(`compiler.filename` も reader が同じ式に読む)と method 呼び `(._syntax-error compiler …)`
   (reader が `((. None _syntax-error) compiler …)` に読む — 前は数えず、盲検 B の失敗例が検を緑のまま通した)— compiler-attribute。"
  (val found [])
  (var pending (list forms))
  (var form None)
  (while pending
    (:= form (.pop pending))
    (when (isinstance form hy.models.Sequence)
      (.extend pending form))
    (when (and (isinstance form hy.models.Expression)
               (> (len form) 2)
               (= (get form 0) (hy.models.Symbol "defmacro"))
               (isinstance (get form 2) hy.models.List)
               (get form 2)
               (in (str (get (get form 2) 0)) COMPILER-SYMBOLS))
      (.append found #(rel "compiler-parameter" "hy.macros.macroexpand" "_hy_compiler")))
    (when (and (isinstance form hy.models.Expression)
               (> (len form) 2)
               (= (get form 0) (hy.models.Symbol "."))
               (isinstance (get form 1) hy.models.Symbol)
               (in (str (get form 1)) COMPILER-SYMBOLS)
               (isinstance (get form 2) hy.models.Symbol))
      (.append found #(rel "compiler-attribute" "_hy_compiler" (hy.mangle (str (get form 2))))))
    (when (and (isinstance form hy.models.Expression)
               (> (len form) 1)
               (isinstance (get form 0) hy.models.Expression)
               (= (len (get form 0)) 3)
               (= (get (get form 0) 0) (hy.models.Symbol "."))
               (= (get (get form 0) 1) (hy.models.Symbol "None"))
               (isinstance (get (get form 0) 2) hy.models.Symbol)
               (isinstance (get form 1) hy.models.Symbol)
               (in (str (get form 1)) COMPILER-SYMBOLS))
      (.append found #(rel "compiler-attribute" "_hy_compiler" (hy.mangle (str (get (get form 0) 2)))))))
  (sorted (set found)))

(defk row-read-gives-frame [use parents row]
  {:pre [(: use ast.Attribute) (: parents dict) (: row tuple)]
   :post [(: % bool)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "`inspect.stack` の使い 1 つ `inspect.stack(…)[n]<読み>` の読み(`[k]` か `.属性`)を light_stack が返す行 row に当てると frame が
   返るか。行がどの読みに応えるかは light_stack の持ち物なので、判定は読みの字面(`[0]` など)を写しで持たず、実物の行に当てる
   (前は `[n][0]` だけを通し、light_stack を `.frame` にも応える行へ直しても赤のままだった — 盲検 A の失敗例)。"
  (val call (.get parents (id use)))
  (val index (.get parents (id call)))
  (val reading (.get parents (id index)))
  (val read
    (cond (not (and (isinstance call ast.Call) (is call.func use)
                    (isinstance index ast.Subscript) (is index.value call)))
            None
          (and (isinstance reading ast.Subscript) (is reading.value index)
               (isinstance reading.slice ast.Constant) (isinstance reading.slice.value int))
            (try (get row reading.slice.value)
                 (except [#(IndexError KeyError TypeError)] None))
          (and (isinstance reading ast.Attribute) (is reading.value index))
            (if (hasattr row reading.attr) (getattr row reading.attr) None)
          True
            None))
  (isinstance read types.FrameType))

(defk stack-call-violations [rel module source row]
  {:pre [(: rel str) (: module str) (: source (| str None)) (: row tuple)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "light_stack が軽い版を返す Hy の module(source は新しい版の物・None = module が無い)の `inspect.stack` の使い方を確かめる —
   R7 の判定。row は light_stack がその module からの呼びに返す行の 1 つ(light-stack-sample-row)。どの使いも
   `inspect.stack(…)[n]` の読みが row から frame を返し、使いが 1 つ以上在る事。読みが frame を返さなければ light_stack の下で
   require が落ち、使いが 0 なら当て物が当たらない。Python として読めない source(Hy の言葉の module)は形を測れないので赤。"
  (val parsed (if (is source None)
                  None
                  (try (ast.parse source)
                       (except [SyntaxError] None))))
  (cond
    (is source None)
      [f"{rel}: light_stack が当てる module {module} が無い — _HY_CALLER_MODULES を見直す"]
    (is parsed None)
      [f"{rel}: light_stack が当てる module {module} の source を Python として読めない(Hy の言葉の module か)— inspect.stack の読み方を測れない"]
    True
      (do
        (val tree parsed)
        (val parents (dfor node (ast.walk tree) child (ast.iter-child-nodes node) (id child) node))
        (val uses (lfor node (ast.walk tree)
                        :if (and (isinstance node ast.Attribute)
                                 (= node.attr "stack")
                                 (isinstance node.value ast.Name)
                                 (= node.value.id "inspect"))
                        node))
        (val lines (.splitlines source))
        (val found (if uses
                       []
                       [f"{rel}: {module} が inspect.stack を使わない — light_stack の当て物が当たらない(_HY_CALLER_MODULES を見直す)"]))
        (for [use uses]
          (<- gives-frame (row-read-gives-frame use parents row))
          (when (not gives-frame)
            (.append found f"{rel}: {module} の {use.lineno} 行の `{(.strip (get lines (- use.lineno 1)))}` の読みは、light_stack が返す行({(. (type row) __name__)})から frame を返さない")))
        found)))

(defk reliance-violations [missing hy-version compiler-failure]
  {:pre [(: missing list) (: hy-version str) (: compiler-failure (| str None))]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "PyPI の Hy(版 hy-version)に無かった依り #(path 種類 対象 名) を、名を挙げた文にする — R7 の判定。compiler-failure は、
   第 1 引数を _hy-compiler にした macro をその版で定義・展開できなかった時の例外の要約(できたなら None)。"
  (val reason (if (is compiler-failure None) "" f"(macro を定義・展開できない: {compiler-failure})"))
  (lfor #(rel kind target name) missing
        (cond (= kind "replaced")
                f"{rel}: 差し替える {target}.{name} が Hy {hy-version} に無い"
              (= kind "compiler-parameter")
                f"{rel}: Hy {hy-version} の {target} が、第 1 引数 {name} の macro に compiler を渡さない{reason}"
              (= kind "compiler-attribute")
                f"{rel}: Hy {hy-version} の macro が受ける compiler({target})に属性 {name} が無い"
              True
                (raise (ValueError f"知らない依りの種類 {kind}({rel})")))))

(defk hy-caps-outside-doeff-hy [declarations]
  {:pre [(: declarations list)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "doeff-hy の外の hy の宣言 #(path PEP 508 の字)(呼び手が doeff-hy を引かない配布物の宣言を除いて渡す — そちらは hy-mirror-violations
   が判じる)のうち、上から版を縛る物(<・<=・==・===・~=)を挙げる — R6 の「上限は doeff-hy の 1 か所」の判定。doeff-hy を引く配布物の
   上限の写しは、doeff-hy の上限と lock を上げる便で黙って古いまま残り、2 つ目の定義元になる(設計検証 lt-Y7VT の盲検 B)。"
  (lfor #(rel text) declarations
        :setv requirement (Requirement text)
        :if (any (gfor spec requirement.specifier (in spec.operator #("<" "<=" "==" "===" "~="))))
        f"{rel}: {text} — hy の上限は {HY-CAP-DECLARATION} の 1 か所だけに置く"))

(defk hy-mirror-violations [mirrors requirements]
  {:pre [(: mirrors list) (: requirements list)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "doeff-hy を引かずに Hy を宣言する配布物の hy の宣言 #(path PEP 508 の字) のうち、doeff-hy の依存の宣言 requirements の hy の行と
   版の指定(specifier の集合 — 字の順は問わない)が違う物・条件(marker)の付いた物を挙げる — R6 の「doeff-hy を引かない配布物にも
   上限を効かせる」の判定(card ki-96ca6c49fffd)。doeff-hy の hy の行が 1 つでない時は hy-cap-violations が名指すので何も挙げない。"
  (val rows (lfor text requirements
                  :setv requirement (Requirement text)
                  :if (= (canonicalize-name requirement.name) "hy")
                  requirement))
  (if (!= (len rows) 1)
      []
      (lfor #(rel text) mirrors
            :setv requirement (Requirement text)
            :if (or (!= requirement.specifier (. (get rows 0) specifier)) (is-not requirement.marker None))
            f"{rel}: {text} — doeff-hy を引かない配布物の hy の宣言は {HY-CAP-DECLARATION} の hy の行({(get rows 0)})と同じ版の指定にし、条件を付けない")))


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

(defk locked-hy-version [repo-root]
  {:pre [(: repo-root Path)]
   :post [(: % str)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "repo-root の uv.lock が決める、doeff が入れる Hy の版 — R4・R6・R7 の照合の版の定義元は uv.lock(Hy を上げる変更が照合の版も
   上げる・card ki-a05a752e7545)。読みは LOCK-READER の locked_version: hy の行が 1 つでない・index から入れる版でない(フォークの
   git など — R3)時は、照合の版を決められないので理由を名指して止まる(SystemExit)。"
  (val spec (importlib.util.spec-from-file-location "semgrep_locked" LOCK-READER))
  (val reader (importlib.util.module-from-spec spec))
  (.exec-module spec.loader reader)
  (.locked-version reader repo-root "hy"))

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

(defk doeff-hy-free-hy-declarations [repo-root]
  {:pre [(: repo-root Path)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "root と packages/*/ の pyproject.toml のうち、[project] の dependencies を workspace の配布物の間で辿っても doeff-hy に届かない
   配布物(doeff-hy を除く)の、[project] の dependencies の hy の宣言を #(path PEP 508 の字) で挙げる — R6 で doeff-hy の宣言と同じ
   版の指定を求める宣言(card ki-96ca6c49fffd — 今は doeff-domain・ADR-DOE-DOMAIN-001 R1 が doeff-hy への依存を禁じる)。extra と
   dependency-group は辿らない(入れるかは消費者が決めるので、上限は必ず入る依存の側で効かせる)。数えるのは依存の形からで、写しの台帳を置かない。"
  (val files (lfor p [(/ repo-root "pyproject.toml") #* (sorted (.glob (/ repo-root "packages") "*/pyproject.toml"))]
                   #((.as-posix (.relative-to p repo-root)) (.get (tomllib.loads (.read-text p :encoding "utf-8")) "project" {}))))
  (val needs (dfor #(rel project) files
                   :if (in "name" project)
                   (canonicalize-name (get project "name"))
                   (sfor text (.get project "dependencies" []) (canonicalize-name (. (Requirement text) name)))))
  ;; doeff-hy に届く配布物の閉包 — 1 回の掃きで少なくとも 1 段ずつ広がるので、配布物の数だけ掃けば足りる。
  (val reaching #{"doeff-hy"})
  (for [_ (range (len needs))]
    (.update reaching (sfor #(name deps) (.items needs) :if (& deps reaching) name)))
  (lfor #(rel project) files
        :if (and (!= rel HY-CAP-DECLARATION)
                 (not-in (canonicalize-name (.get project "name" "")) reaching))
        text (.get project "dependencies" [])
        :if (= (canonicalize-name (. (Requirement text) name)) "hy")
        #(rel text)))

(defk missing-on-pypi-hy [imports hy-version]
  {:pre [(: imports list) (: hy-version str)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "import #(path module 名) のうち PyPI の Hy(版 hy-version — uv.lock の版)に無い物を挙げる — その版の Hy を入れた使い捨ての
   環境(uv)で実際に import して確かめる(作業木の venv を使わないので、lock を上げて sync する前でも新しい版で測る)。"
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
  (<- missing (pypi-hy-run hy-version probe (lfor #(rel module name) imports [rel module name])))
  (lfor row missing (tuple row)))

(defk pypi-hy-run [hy-version probe payload]
  {:pre [(: hy-version str) (: probe str) (: payload list)]
   :post [(: % (| list dict))]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "PyPI の Hy(版 hy-version)だけを入れた使い捨ての環境(uv)で Python の probe を走らせ、payload を JSON で stdin に渡し、
   stdout の JSON を返す — R4 と R7 の照合の土台(作業木の venv を使わないので、lock を上げて sync する前でも新しい版で測る)。"
  (val uv (shutil.which "uv"))
  (assert (is-not uv None) "uv が PATH に無い — PyPI の Hy の使い捨ての環境を作れない")
  (val env (dfor #(key value) (.items os.environ)
                  :if (not-in key #("VIRTUAL_ENV" "CONDA_PREFIX" "PYTHONPATH" "PYTHONPYCACHEPREFIX"))
                  key value))
  (val completed
    (subprocess.run [uv "run" "--no-project" "--isolated"
                     "--python" f"{sys.version-info.major}.{sys.version-info.minor}"
                     "--with" (+ "hy==" hy-version) "python" "-B" "-c" probe]
                    :input (json.dumps payload)
                    :capture-output True :text True :env env :timeout 300 :check False))
  (assert (= completed.returncode 0) completed.stderr)
  (json.loads completed.stdout))

(defk scan-hy-reliances [repo-root]
  {:pre [(: repo-root Path)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "doeff の source が import でない形で依る Hy の内部を #(path 種類 対象 名) で挙げる — R7 の母集団: hy.* の名への代入
   (Python の source)・macro が受ける compiler とその属性(Hy の source)・light_stack が軽くする module(_HY_CALLER_MODULES)。"
  (val found [])
  (<- python-files (scan-relative-files repo-root #(".py")))
  (for [#(p rel) python-files
        :setv text (.read-text p :encoding "utf-8" :errors "replace")
        ;; ast で読むのは hy.* の名へ代入しうる file だけ(絞り込みは読む物の上位集合)。
        :if (.search PY-HY-ASSIGN-HINT text)
        :setv tree (try (ast.parse text)
                        (except [SyntaxError] None))
        ;; Python として読めない file(検の data)は実行されないので、差し替えもしない — 数えない。
        :if (is-not tree None)]
    (<- rows (python-assignment-reliances rel tree))
    (.extend found rows))
  (<- hy-files (scan-relative-files repo-root #(".hy" ".hyk" ".hyp")))
  (for [#(p rel) hy-files
        :setv text (.read-text p :encoding "utf-8" :errors "replace")
        ;; reader で読むのは compiler を受ける約束の名を書いた file だけ(全 file を読むと数十秒)。
        :if (.search HY-COMPILER-HINT text)]
    (<- rows (hy-form-reliances rel (list (hy.read-many text :filename (str p)))))
    (.extend found rows))
  (val light-stack-rel (.as-posix (.relative-to (.resolve (Path light-stack.__file__)) (.resolve repo-root))))
  (.extend found (lfor module (sorted light-stack._HY_CALLER_MODULES)
                       #(light-stack-rel "light-stack" module "inspect.stack")))
  (sorted (set found)))

(defk light-stack-sample-row [module]
  {:pre [(: module str)]
   :post [(: % tuple)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "doeff-hy の light_stack が、module からの inspect.stack の呼びに返す行の 1 つ目 — R7 の判定が Hy の読みを当てる実物
   (行の形は light_stack の持ち物で、判定は写しを持たない)。light_stack は呼び手の大域の __name__ で軽い版へ渡すので、
   __name__ を module にした大域で inspect.stack(0) を呼ぶ。差し替えを入れてから呼ぶ(doeff_hy の import が入れる物と同じ・2 度目は何もしない)。"
  (light-stack.install)
  (get (builtins.eval "inspect.stack(0)" {"__name__" module "inspect" inspect}) 0))

(defk probe-reliances [reliances hy-version]
  {:pre [(: reliances list) (: hy-version str)]
   :post [(: % dict)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "依り #(path 種類 対象 名) を PyPI の Hy(版 hy-version — uv.lock の版)で測る — {\"missing\" 無かった依りの列
   \"sources\" {light_stack が当てる module の名 その source(module が無ければ None)}}。compiler は、第 1 引数を
   _hy-compiler にした macro を公開の defmacro で定義して展開し、Hy が渡した物を読む(Hy の内部の名を probe 自身は使わない)。"
  (val probe (+ "import importlib, inspect, json, sys, types\n"
                 "import hy\n"
                 "module = types.ModuleType('doeff_hy_reliance_probe')\n"
                 "module.received = []\n"
                 "failure = None\n"
                 "try:\n"
                 "    hy.eval(hy.read('(defmacro doeff-hy-reliance-probe [[_hy-compiler None]] (.append received _hy-compiler) 0)'), module=module)\n"
                 "    hy.eval(hy.read('(doeff-hy-reliance-probe)'), module=module)\n"
                 "except Exception as error:\n"
                 "    lines = [line for line in str(error).splitlines() if line.strip()]\n"
                 "    failure = type(error).__name__ + (': ' + lines[-1].strip() if lines else '')\n"
                 "compiler = module.received[0] if module.received else None\n"
                 "passed = compiler is not None and not isinstance(compiler, hy.models.Object)\n"
                 "missing = []\n"
                 "sources = {}\n"
                 "for rel, kind, target, name in json.load(sys.stdin):\n"
                 "    if kind == 'light-stack':\n"
                 "        try:\n"
                 "            sources[target] = inspect.getsource(importlib.import_module(target))\n"
                 "        except ImportError:\n"
                 "            sources[target] = None\n"
                 "        continue\n"
                 "    if kind == 'replaced':\n"
                 "        try:\n"
                 "            present = hasattr(importlib.import_module(target), name)\n"
                 "        except ImportError:\n"
                 "            present = False\n"
                 "    elif kind == 'compiler-parameter':\n"
                 "        present = passed\n"
                 "    elif kind == 'compiler-attribute':\n"
                 "        present = passed and hasattr(compiler, name)\n"
                 "    else:\n"
                 "        raise ValueError(kind)\n"
                 "    if not present:\n"
                 "        missing.append([rel, kind, target, name])\n"
                 "print(json.dumps({'missing': missing, 'sources': sources, 'compiler_failure': failure}))\n"))
  (<- probed (pypi-hy-run hy-version probe (lfor #(rel kind target name) reliances [rel kind target name])))
  {"missing" (lfor row (get probed "missing") (tuple row))
   "sources" (get probed "sources")
   "compiler-failure" (get probed "compiler_failure")})


(defadr ADR-DOE-HY-008
  :title "Hy はフォークしない・手を入れない。Hy の振る舞いで回避が要る所は doeff の側に置く。依存の宣言に proboscis/hy を書かず、PyPI の Hy に無い名前を hy.* から import しない(フォークの固定は #1293 で消した)。doeff-hy の Hy の上限は doeff が確かめた uv.lock の版ちょうど"
  :status "accepted"
  :scope ["pyproject.toml"
          "uv.lock"
          "packages"
          "docs/adr/defadr_doeff_hy_008_hy_is_not_forked.hy"]
  :problem
    [(fact
       "利用者の決定 2026-09-29: Hy を fork せずに済む迂回を入れ、Hy には一切手を入れない。前の問い: test の索引は cache すると決めたのに、Hy の fork は本当に要るのか。require を速くするために fork までする必要は無く、すべて Hy を fork せずに迂回できるのではないか。"
       :evidence "agora-redesign #1290 の comment(2026-09-29)")
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
       :evidence "card ki-0ff168f4eaf5(依頼 lt-Y7VTRKY83EG2YC69W8M8EJ0BP1)— 各 repo の origin/main の pyproject.toml と uv.lock・PyPI の Hy 1.3.1 の NEWS.rst・scan-hy-imports と missing-on-pypi-hy を 1.0.0〜1.3.1 で撃った出力")
     (fact
       "2026-10-12 の実測: doeff-hy は import でない形でも Hy の内部に依る — doeff_hy/__init__.py の hy.importer._could_be_hy_src の差し替え(名が替われば代入は新しい属性を作るだけで、.hyk / .hyp を Python として読む)・macro の第 1 引数 _hy-compiler で受けた compiler の scope・this・filename の読み(macros.hy・handle.hy — Hy 1.3.1 の hy.macros.macroexpand は第 1 引数の名が _hy_compiler の macro にだけ compiler を渡す)・light_stack.py が hy.compiler と hy.macros の inspect.stack()[n][0] の 4 箇所へ frame 1 つの tuple を返す当て物(Hy の側が [n].frame を読む形に変われば require が AttributeError で落ちる)。R4 の検は import の名だけを照合し、これらは登記の前の検に入らず、マージの後の日次の検でしか測られなかった。"
       :evidence "card ki-2fa4c02604b1(依頼 lt-WPF4K5X5R2R5KSJ94PG76JHCWC)— Hy 1.3.1 の importer.py:118-126・compiler.py:55・345-357・macros.py:153・179・203・405")
     (fact
       "2026-10-12 の実測(doeff d406b2891・Python 3.14.3t): doeff-hy の検(packages/doeff-hy/tests・packages/doeff-hy-pytest/tests・ADR-DOE-HY-001〜008)を Hy の 3 つの版で走らせた — 1.3.1(lock)は 842 通過・2 失敗、1.3.0(lock を 1.3.0 にした作業木)は 841 通過・3 失敗、1.2.0(lock は 1.3.1 のまま環境の Hy だけを替えた — doeff の workspace は doeff-claude-code などの hy>=1.3 のため 1.2.0 では lock が解けない)は 832 通過・12 失敗。3 つに共通の 2 本は Hy と関わらない main の赤で、1.3.0 の 3 本目は lock の版と上限の違いを R6 の検が名指した物。1.2.0 だけの 10 本は 1 つの訳 — Hy 1.2.0 の hy.macros.require が、module の無い名前空間(hy.eval に dict を渡す形)では target の module を None のまま find_spec へ渡して AttributeError で落ちる(1.3.0 の derive_target_module が名前空間の __name__ を使う形に直した)。R4・R7 の照合(import の 142 本・内部への依り 9 本)は 3 つの版のどれでも緑で、この違いは名の照合では見えず、検を走らせて初めて見えた。doeff-hy の宣言の下限は 1.2.0 で、doeff-domain の宣言(hy>=1.2.0)は doeff-hy を引かないので R6 の上限が効かなかった。"
       :evidence "card ki-96ca6c49fffd(依頼 lt-RBXNG8M7AW6QDW1JE0S2G72B5S)— 3 つの作業木の pytest の要約と、1.2.0・1.3.0 の site-packages の hy/macros.py の差")
     (fact
       "2026-10-12 の実測: bytecode の包み(doeff_hy_bytecode_guard — R2)も import でない形で Hy の内部に依る — module の macro の表の名 _hy_macros・_hy_reader_macros、Hy 1.3.1 の hy.macros.macroexpand がその表を `名 in 表` と `表[名]` で引く事(引いた名を覚える表が使った macro を拾う)、gensym の名 `_hy_gensym_<base>_<数え>` と let が改めた束縛の名 `_hy_let_<元の名>_<数>` の形(compile の口の正準化が読む)。R7 の検はこれらを数えない。Hy 1.3.1 の 1 か所だけを替えた失敗ケースで測ると、表を .get で引く版と _hy_macros の名を替えた版では包みの検(packages/doeff-hy/tests/test_bytecode_guard.py — uv.lock の変更で選ばれる)の 9 本が「macro を変えても使う側が古い展開のまま」で赤だったが、_hy_reader_macros の名を替えた版・gensym の名の形を替えた版・get_anon_var の名の形を替えた版では 20 本とも緑だった(reader macro の変更で古い展開を黙って使う・code が compile の順で揺れる)。"
       :evidence "card ki-eafa369600cd(依頼 lt-JYZ9N64ZE70GCBXPP33QDG3WF7)— docs/design-checks/lt-JYZ9N64ZE70GCBXPP33QDG3WF7/evidence")
     (fact
       "2026-10-12 の実測: bytecode の包みの外にも Hy の内部の名への依りが 3 か所在り、uv.lock の版で測る検に入っていなかった — doeff_effect_analyzer.result_cache が module の名前空間の表の名 _hy_macros・_hy_reader_macros を字で読む(解析の鍵に macro の提供元を入れるため)・doeff_hy.static_baseline が Hy の一時の名 _hy_anon_<番号>・_hy_let_first_<番号> の形を先頭を分けた regex で持つ・macro_use._exported が Hy の export の名の表 _hy_export_macros の字を持つ(名の定義元が records の外)。doeff_hy.static_stub も先頭 _hy_ の字を持っていた。3 か所とも名は Hy を上げて替わっても検が緑のままで、鍵が macro の提供元を落とす・基点の鍵が番号で揺れる・全部の require を数え損ねる形。"
       :evidence "card ki-ffe72a1fb7d7(依頼 lt-AGZXCF8NKEHJCFNWCG0JZZCBS0)— card ki-eafa369600cd の担い手の所見(報告 lt-TCEJ)と、origin/main fb4dd0f3b の result_cache.py:395・static_baseline.py の _HY_GENERATED_NUMBER・macro_use.py:384・static_stub.py:51")]
  :context
    [(interpretation
       "doeff が Hy の外から同じ正しさを出せるなら、フォークの維持(本家の更新のたびの衝突・2 repo の固定の同時の上げ)は払う理由が無い。回避は doeff の持ち物になり、doeff の検査で守れる。")
     (interpretation
       "「Hy に手を入れない」の測り方は 2 つ: 依存の宣言がフォークを指さない(入れる物が本家)・doeff の code が本家に無い名前を使わない(本家に戻しても import が落ちない)。Hy の内部の名前を読む回避(module の名前空間の _hy_macros を読む・hy.importer._could_be_hy_src を差し替える)は、本家にも在る名前だけを使う限りこの 2 つに反しない。")
     (interpretation
       "doeff の検が確かめる Hy は uv.lock の版 1 つだけなので、doeff-hy が通してよい Hy もその版まで。上限は doeff-hy の metadata の 1 か所で、uv の解決が doeff-hy を使う全部の repo で守る。上限を置かずに他の repo の側で R4 と同じ検を撃つ形は、検の写しが repo の数だけ増え、doeff の他の検(macro の展開・bytecode の包み・ast_unparse の当て物)を通らない。上限を小版(<1.4)で置く形は確かめていない修正版を通し、== で 1 つの版に固定する形は hy==1.3.0 を宣言する merge-queue・code-quality が doeff の rev を上げた時に解けない。下限も同じ論法で、doeff-hy の検を走らせて緑だった版より古い版を通さない(1.2.0 では検 10 本が赤 — 下限は merge-queue・code-quality の lock の版 1.3.0)。上限の定義は doeff-hy の宣言の 1 か所で、doeff-hy を引かない配布物(doeff-domain — ADR-DOE-DOMAIN-001 R1 が doeff-hy への依存を禁じる)だけは同じ版の指定を写して置く — 写しは検が doeff-hy の宣言と突き合わせるので、上限を上げる便で古いまま残れば赤になり、2 つ目の定義元にならない。写さない形は、doeff-domain だけを引く環境で上限が効かない。")
     (interpretation
       "import でない依りは、写しの台帳でなく source の形から数える(R7)— 台帳は依りを足す変更が書き忘れると黙って腐る。数える形は 3 つ: hy.* の名への代入(`hy.X.Y = …`)・第 1 引数を _hy-compiler にした defmacro と compiler の属性の読み(`(. compiler 名)`・`compiler.名`・method 呼び `(.名 compiler …)`)・light_stack の _HY_CALLER_MODULES。light_stack の当て物は、Hy の inspect.stack の読みを light_stack が返す実物の行に当てて測る — 行がどの読みに応えるかは light_stack の持ち物で、判定に写しを置かない(盲検 A・B の失敗例で直した — docs/design-checks/lt-WPF4K5X5R2R5KSJ94PG76JHCWC)。別名で受けた module への代入や getattr の文字列の名は数えない。R6 の上限が他の repo の Hy を uv.lock の版までに留めるので、R7 が uv.lock の版で測れば他の repo の Hy にも効く。")
     (interpretation
       "bytecode の包みの依り(表の名と引き方・export の名の表・gensym と let の名の形・関数の中の局所の macro の表を開く口)は、R7 の母集団へ名や形の写しとして足さず、包みの検が uv.lock の版の Hy で包みそのものを動かして測る — 依りは名が在るかではなく、本物の展開の下で包みの表が使った macro と表に無かった名を覚え、compile の口が名を振り直すかという振る舞いで、名を照合する probe は表を .get で引く版を見分けられない。包みの検は R4・R7 と同じく uv.lock の版を読み、uv.lock の変更で選ばれる(card ki-33d2441b7e28・ki-eafa369600cd)。同じ file の同じ process の検は venv の Hy を測るので、file の全部の検が venv の Hy の版と uv.lock の版の一致を先に確かめる — PyPI の版の中身は版ごとに変わらないので、一致すれば uv.lock の版を測る。Hy を上げて sync していない作業木では赤になり、sync を名指す(設計検証 lt-JYZ9N64ZE70GCBXPP33QDG3WF7 の盲検 A・B)。")
     (interpretation
       "Hy の内部の名(module の macro の表の名・reader macro の表の名・export の名の表の名・Hy が一時の名に振る先頭と形)の定義元は doeff_hy_bytecode_guard.records の 1 か所(定数 MACRO_TABLE・READER_TABLE・EXPORT_NAMES・GENERATED_NAME_PREFIX・ANON_VAR_NAME と、関数 macro_tables・exported_macro_names・fold_anon_var_numbers)に置き、包みの外の使い手(doeff_effect_analyzer.result_cache の解析の鍵・doeff_hy.static_baseline の基点の鍵・macro_use の全部の require の判定・doeff_hy.static_stub の隠す名)はそれを引く。名の字が 2 つ目の定義元になると、Hy が名を替えた時に直す所が 2 つ以上になり、測る検は records の読みしか見ないので写しは測られないまま古くなる。測りは records の読みを実物の Hy の module に当てる検(test_bytecode_guard.py の test_records_reads_… と test_the_temporaries_hy_names_… — 表と export の読みは Hy 自身の require の振る舞いと一致し、一時の名は fold_anon_var_numbers が畳める形のまま)と、鍵が両方の表に従う事を測る analyzer の 2 本で、uv.lock の変更で選ばれる。字が records の外に在る事は packages/doeff-hy/tests/test_hy_internal_names_have_one_home.py の走査が赤にする(綴られた名だけを数える — 別の書き方は数えず、依りそのものは上の検が測る)。")]
  :decision
    [(rule R1 "Hy には一切手を入れない。proboscis/hy に commit を足さない。最終形は PyPI の Hy。")
     (rule R2 "Hy の振る舞いで回避が要る所は doeff の側に置く: テストの索引(#1291)・macro の変更で古い bytecode を使わない判定(#1292 — doeff_hy_bytecode_guard が Python 標準の SourceFileLoader を包む)・require の高速化(#1293)。")
     (rule R3 "doeff と agora-controllers の依存の宣言(pyproject.toml・uv.lock)に proboscis/hy を書かない。例外は 0(rev adbe989a の固定を 2026-09-29 まで例外にしていた — #1293 で消した)。doeff の検査は doeff の木を読む — agora-controllers の宣言は agora-controllers の側の検査が守る。")
     (rule R4 "doeff の code は PyPI の Hy(uv.lock が固定する版)に無い名前を hy.* から import しない。照合の版は検が uv.lock から読み、uv.lock の変更で `make test-changed` がこの検を選ぶ(root の pyproject.toml の [[tool.doeff.contract-tests]])— Hy を上げる変更は、新しい版に名が在る事を登記の前に確かめる。例外の台帳(FORK-ONLY-IMPORT-EXCEPTIONS)は空(#1291 が c6783e8e2 で消した)。足すなら期限は R3 と同じ。")
     (rule R5 "例外の台帳は、原因が消えたら同じ commit で削る(フォークの固定を消す便は FORK-PIN を、import を消す便は FORK-ONLY-IMPORT-EXCEPTIONS の行を)。原因の消えた例外が台帳に残れば赤。")
     (rule R6 "doeff-hy の Hy の依存の宣言の上限は、doeff の uv.lock の hy の版ちょうど(`<=<版>`)にする — doeff-hy を使う他の repo(agora-controllers・custody・merge-queue・code-quality・agent-commons・dotfiles)が自分の lock で上げられる Hy を、doeff の検が確かめた版までに留める(上限は他の repo の uv の解決にも効く)。上限の定義は doeff-hy の 1 か所に置き(Hy を使う doeff の package は doeff-domain を除き doeff-hy を引き、uv は 1 つの環境に Hy を 1 つしか入れない — doeff-hy を引く配布物の hy の宣言は上から版を縛らない)、上限の行に条件(marker)を付けない(条件の外の環境で上限が効かなくなる)。[project] の dependencies を辿っても doeff-hy に届かない配布物(今は doeff-domain)の hy の宣言は、doeff-hy の hy の行と同じ版の指定を条件なしで置き、検が突き合わせる(card ki-96ca6c49fffd)。下限は doeff-hy の検を走らせて緑だった版にする(今は 1.3.0 — 2026-10-12 の実測)。Hy を上げる便は doeff の uv.lock と上限を同じ commit で上げ(上限の file か uv.lock の変更で `make test-changed` がこの検と R4 の検を選ぶ)、他の repo はその commit を含む doeff へ版(doeff-min-commit・git の rev)を上げてから自分の lock の Hy を上げる。")
     (rule R7 "doeff の code が import でない形で依る Hy の内部 — 差し替える hy.* の名(hy.importer._could_be_hy_src)・macro の第 1 引数 _hy-compiler に Hy が compiler を渡す約束とその属性(scope・this・filename)・light_stack が軽い版を返す呼び出し(_HY_CALLER_MODULES の module の inspect.stack(…)[n] の読みが、light_stack の返す行から frame を返す)— も、PyPI の Hy(uv.lock が固定する版)に在る事を検が確かめる。R4 と同じく照合の版は uv.lock から読み、uv.lock の変更で `make test-changed` がこの検を選ぶ。依りは source の形から数え、写しの台帳を置かない。bytecode の包み(R2)の依り — module の macro の表の名(doeff_hy_bytecode_guard.records の MACRO_TABLE・READER_TABLE)と展開が表を `名 in 表` と `表[名]` で引く事・Hy の export が書く公開の macro の名の表・gensym と let が改めた束縛の名の形・関数の中の require の macro が入る局所の表(compiler が局所の状態を開く口 new_local_state と、その状態の macro の表の鍵 records.LOCAL_MACRO_TABLE)— は、包みの検(packages/doeff-hy/tests/test_bytecode_guard.py)が uv.lock の版の Hy で包みを動かして測る(使い捨ての環境の検はその版の PyPI の Hy を入れ、同じ process の検は venv の Hy がその版である事を先に確かめる・uv.lock の変更で選ばれる)。これらの名の定義元は records の 1 か所で、包みの外の使い手(doeff_effect_analyzer.result_cache・doeff_hy.static_baseline・macro_use の全部の require の判定・doeff_hy.static_stub)も records の定数と関数で引き、字を持たない(card ki-ffe72a1fb7d7 — records の読みを実物の Hy に当てる検が同じ file に在り、字の写しは test_hy_internal_names_have_one_home.py が数える)。")]
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
       :statement "for the single hy requirement r in dependencies(packages/doeff-hy/pyproject.toml): ('<=' + version(uv.lock, hy)) in specifiers(r) and version(uv.lock, hy) satisfies r and marker(r) is None; and for_all hy requirement m in [project].dependencies of a doeff distribution d != doeff-hy whose [project].dependencies do not reach doeff-hy: specifiers(m) == specifiers(r) and marker(m) is None; and for_all other hy requirement q in the other pyproject.toml of doeff (root and packages/*): no specifier of q has an operator in {<, <=, ==, ===, ~=}"
       :counterexamples
         [(counterexample "doeff-hy の宣言が hy>=1.2.0 のまま(上限なし)— agora-controllers が自分の lock で `uv lock --upgrade-package hy` を撃つと、doeff の検を通っていない版が入る(card ki-0ff168f4eaf5)")
          (counterexample "上限を <1.4 にする — doeff が確かめていない修正版 1.3.2 を他の repo の lock に通す(修正版 1.3.1 も Hy の importer を直した)")
          (counterexample "上限を <=1.4.0 へ上げ、uv.lock の hy を 1.3.1 のまま置く — R4 の検は 1.3.1 でしか測らず、他の repo は 1.4.0 を入れられる")
          (counterexample "上限の行を hy>=1.2.0,<=1.3.1; python_version < '3.15' にする — Python 3.15 の環境では上限が消え、消費者の宣言だけで Hy が決まる(設計検証 lt-Y7VT の盲検 B)")
          (counterexample "doeff-adr(doeff-hy を引く)の宣言に上限 hy>=1.2.0,<=1.3.1 を写す — doeff-hy の上限と lock を上げる便で写しが古いまま残り、2 つ目の定義元になる(同)")
          (counterexample "doeff-domain(doeff-hy を引かない)の宣言を hy>=1.2.0 のまま置く — doeff-domain だけを引く環境では上限が効かず、doeff が測っていない Hy が入る(card ki-96ca6c49fffd)")
          (counterexample "doeff-hy の上限と lock を 1.3.2 へ上げ、doeff-domain の宣言を hy>=1.3.0,<=1.3.1 のまま置く — doeff-domain の写しが古いまま残る(同)")])
     (law internal-reliances-exist-in-pypi-hy
       :statement "for_all reliance in assignments(doeff, hy.*) ∪ compiler_reads(doeff) ∪ light_stack._HY_CALLER_MODULES: holds(PyPI hy at version(uv.lock, hy), reliance)"
       :counterexamples
         [(counterexample "uv.lock の hy を、hy.importer._could_be_hy_src の名を替えた版へ上げる — doeff_hy の代入は新しい属性を作るだけで、.hyk / .hyp を Python として読み SyntaxError(card ki-2fa4c02604b1)")
          (counterexample "uv.lock の hy を、compiler の scope が別の名になった版へ上げる — defk などの _in-function-body? が展開の時に AttributeError")
          (counterexample "uv.lock の hy を、hy.macros が inspect.stack()[1].frame を読む版へ上げる — light_stack の 1 要素の tuple に .frame が無く、require が AttributeError(light_stack の行を .frame にも応える形へ直せば緑 — 判定は実物の行に当てる)")
          (counterexample "macro の補助が compiler の非公開の method を (._syntax-error compiler form message) で呼ぶ — method 呼びの形も compiler の属性として数えないと、Hy の版上げで名が消えても検は緑のまま(盲検 B)")])]
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
       (val hy-version (run (locked-hy-version repo-root)))
       (val imports (run (scan-hy-imports repo-root)))
       (val missing (run (missing-on-pypi-hy imports hy-version)))
       (val violations (run (import-violations missing (date.today) FORK-ONLY-IMPORT-EXCEPTIONS)))
       (assert (= violations [])
               (+ "PyPI の Hy " hy-version "(uv.lock の版)に無い名前を hy.* から import している"
                  "(ADR-DOE-HY-008 R4 — doeff の側で持つ): " (str violations))))
     (deftest test-adr-doe-hy-008-internal-reliances-exist-in-pypi-hy
       (val repo-root (. (Path __file__) parent parent parent))
       (val hy-version (run (locked-hy-version repo-root)))
       (val reliances (run (scan-hy-reliances repo-root)))
       (val probed (run (probe-reliances reliances hy-version)))
       (val violations
         (+ (run (reliance-violations (get probed "missing") hy-version (get probed "compiler-failure")))
            (sum (lfor #(rel kind module name) reliances
                       :if (= kind "light-stack")
                       (run (stack-call-violations rel module (get (get probed "sources") module)
                                                   (run (light-stack-sample-row module)))))
                 [])))
       (assert (= violations [])
               (+ "PyPI の Hy " hy-version "(uv.lock の版)に、doeff が import でない形で依る Hy の内部が無い"
                  "(ADR-DOE-HY-008 R7 — 当て物を新しい版に合わせるか、Hy の版を戻す): " (str violations))))
     (deftest test-adr-doe-hy-008-reliance-probe-names-what-pypi-hy-lacks
       ;; 反例(card ki-2fa4c02604b1): 在らない名・属性・module を、uv.lock の版の PyPI の Hy で測ると名を挙げて赤になる。
       (val repo-root (. (Path __file__) parent parent parent))
       (val hy-version (run (locked-hy-version repo-root)))
       (val rows [#("a.py" "replaced" "hy.importer" "_could_be_hy_src")
                  #("a.py" "replaced" "hy.importer" "_doeff_no_such_name")
                  #("m.hy" "compiler-parameter" "hy.macros.macroexpand" "_hy_compiler")
                  #("m.hy" "compiler-attribute" "_hy_compiler" "scope")
                  #("m.hy" "compiler-attribute" "_hy_compiler" "doeff_no_such_attribute")
                  #("l.py" "light-stack" "hy.macros" "inspect.stack")
                  #("l.py" "light-stack" "hy.doeff_no_such_module" "inspect.stack")])
       (val probed (run (probe-reliances rows hy-version)))
       (assert (= (get probed "missing")
                  [#("a.py" "replaced" "hy.importer" "_doeff_no_such_name")
                   #("m.hy" "compiler-attribute" "_hy_compiler" "doeff_no_such_attribute")]))
       (assert (is (get probed "compiler-failure") None))
       (val messages (run (reliance-violations (get probed "missing") hy-version None)))
       (assert (in "hy.importer._doeff_no_such_name" (get messages 0)) messages)
       (assert (in "doeff_no_such_attribute" (get messages 1)) messages)
       (assert (= (run (stack-call-violations "l.py" "hy.macros" (get (get probed "sources") "hy.macros")
                                              (run (light-stack-sample-row "hy.macros"))))
                  []))
       (assert (is (get (get probed "sources") "hy.doeff_no_such_module") None)))
     (deftest test-adr-doe-hy-008-exceptions-do-not-outlive-their-cause
       (val repo-root (. (Path __file__) parent parent parent))
       (val mentions (run (fork-mentions repo-root)))
       (val hy-version (run (locked-hy-version repo-root)))
       (val missing (run (missing-on-pypi-hy (run (scan-hy-imports repo-root)) hy-version)))
       (val stale (run (stale-exceptions missing mentions)))
       (assert (= stale []) (+ "原因の消えた例外が台帳に残っている(ADR-DOE-HY-008 R5): " (str stale))))
     (deftest test-adr-doe-hy-008-doeff-hy-caps-hy-at-the-locked-version
       (val repo-root (. (Path __file__) parent parent parent))
       (val hy-version (run (locked-hy-version repo-root)))
       (val requirements (run (doeff-hy-requirements repo-root)))
       (val mirrors (run (doeff-hy-free-hy-declarations repo-root)))
       (val violations (+ (run (hy-cap-violations requirements hy-version))
                          (run (hy-mirror-violations mirrors requirements))
                          (run (hy-caps-outside-doeff-hy (lfor declaration (run (hy-declarations-outside-doeff-hy repo-root))
                                                               :if (not-in declaration mirrors)
                                                               declaration)))))
       (assert (= violations [])
               (+ "Hy の上限が R6 に反する(ADR-DOE-HY-008 R6 — 上限は " HY-CAP-DECLARATION " に、条件なしで uv.lock の版 "
                  hy-version " ちょうどに置き、doeff-hy を引かない配布物は同じ版の指定を置き、Hy を上げる便は上限と uv.lock を"
                  "同じ commit で上げる): " (str violations))))
     (deftest test-adr-doe-hy-008-judgments-reject-the-counterexamples [tmp-path]
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
       ;; 反例: 照合の版は uv.lock の hy の行から読む(lock を上げると照合も上がる)・hy の行が 1 つでない・index から入れる版でなければ
       ;; 止める(読みは LOCK-READER の 1 か所 — card ki-33d2441b7e28)。
       (val lock-text (+ "version = 1\n\n[[package]]\nname = \"hy\"\nversion = \"1.4.0\"\n"
                         "source = { registry = \"https://pypi.org/simple\" }\n"))
       (val lock (/ tmp-path "uv.lock"))
       (.write-text lock lock-text :encoding "utf-8")
       (assert (= (run (locked-hy-version tmp-path)) "1.4.0"))
       (.write-text lock (.replace lock-text "registry = \"https://pypi.org/simple\""
                                   "git = \"https://github.com/proboscis/hy.git?rev=adbe989a\"")
                    :encoding "utf-8")
       (with [(pytest.raises SystemExit :match "index から入れる版でない")]
         (run (locked-hy-version tmp-path)))
       (.write-text lock (.replace lock-text "name = \"hy\"" "name = \"hyrule\"") :encoding "utf-8")
       (with [(pytest.raises SystemExit :match "1 つに決まらない")]
         (run (locked-hy-version tmp-path)))
       ;; 反例(card ki-0ff168f4eaf5): 上限なし・小版の上限(<1.4)・uv.lock と違う上限・hy の行の無い宣言は赤、uv.lock の版ちょうどの上限は緑。
       (assert (= (run (hy-cap-violations ["doeff>=0.2.0" "hy>=1.2.0,<=1.3.1"] "1.3.1")) []))
       (assert (= (len (run (hy-cap-violations ["hy>=1.2.0"] "1.3.1"))) 1))
       (assert (= (len (run (hy-cap-violations ["hy>=1.2.0,<1.4"] "1.3.1"))) 1))
       (assert (= (len (run (hy-cap-violations ["hy>=1.2.0,<=1.3.1"] "1.4.0"))) 2))
       (assert (= (len (run (hy-cap-violations ["pydantic>=2.11"] "1.3.1"))) 1))
       ;; 反例(card ki-2fa4c02604b1): R7 の数え方 — hy.* の名への代入は数え、hy でない物・hy そのものへの代入は数えない。
       (assert (= (run (python-assignment-reliances
                         "a.py" (ast.parse "import hy.importer\nhy.importer._could_be_hy_src = f\nx.y = 1\nhy = 2\ndef g():\n    hy.compat.rewriting_unparse = h\n")))
                  [#("a.py" "replaced" "hy.importer" "_could_be_hy_src")
                   #("a.py" "replaced" "hy.compat" "rewriting_unparse")]))
       ;; compiler を受ける defmacro と compiler の属性の読み(点の付いた記号も reader が (. compiler 名) に読む)・method 呼び
       ;; (._syntax-error compiler …)(盲検 B の失敗例)は数え、compiler でない物の属性と method 呼び・第 1 引数が compiler でない
       ;; defmacro は数えない。
       (assert (= (run (hy-form-reliances
                         "m.hy" (list (hy.read-many (+ "(defmacro m [_hy-compiler x] (. _hy-compiler this))\n"
                                                       "(defn f [compiler] compiler.filename (isinstance (. compiler scope) X) (. other scope))\n"
                                                       "(defn g [compiler form] (raise (._syntax-error compiler form \"m\")) (.append other compiler))\n"
                                                       "(defmacro n [x] x.y)\n")))))
                  [#("m.hy" "compiler-attribute" "_hy_compiler" "_syntax_error")
                   #("m.hy" "compiler-attribute" "_hy_compiler" "filename")
                   #("m.hy" "compiler-attribute" "_hy_compiler" "scope")
                   #("m.hy" "compiler-attribute" "_hy_compiler" "this")
                   #("m.hy" "compiler-parameter" "hy.macros.macroexpand" "_hy_compiler")]))
       (assert (.search HY-COMPILER-HINT "(defmacro val [_hy-compiler #* args]"))
       (assert (not (.search HY-COMPILER-HINT "(defn f [my_hy_compiler_x])")))
       (assert (.search PY-HY-ASSIGN-HINT "hy.importer._could_be_hy_src = _could_be_hy_src"))
       (assert (not (.search PY-HY-ASSIGN-HINT "hy.importer._could_be_hy_src == f")))
       ;; light_stack の当て物: Hy の読みを light_stack が返す行に当てて frame が返れば緑。行の形は light_stack の持ち物 —
       ;; frame 1 つの tuple の行なら [n][0] だけが緑で [n].frame・[n][3] は赤、.frame にも応える行なら [n].frame も緑
       ;; (盲検 A の失敗例: 前は [n][0] を写しで持ち、行を直しても赤のままだった)。使い 0・module 無し・Hy の言葉の source は赤。
       (val frame (sys._getframe))
       (val tuple-row #(frame))
       (val named-row ((collections.namedtuple "NamedRow" ["frame"]) frame))
       (val frame-only "import inspect\ndef f(n):\n    return inspect.stack(0)[n + 1][0]\n")
       (val attribute-source (.replace frame-only "[n + 1][0]" "[n + 1].frame"))
       (assert (= (run (stack-call-violations "l.py" "hy.macros" frame-only tuple-row)) []))
       (val attribute-read (run (stack-call-violations "l.py" "hy.macros" attribute-source tuple-row)))
       (assert (and (= (len attribute-read) 1) (in "inspect.stack(0)[n + 1].frame" (get attribute-read 0))) attribute-read)
       (assert (= (len (run (stack-call-violations "l.py" "hy.macros" (.replace frame-only "[n + 1][0]" "[n + 1][3]") tuple-row))) 1))
       (assert (= (run (stack-call-violations "l.py" "hy.macros" attribute-source named-row)) []))
       (assert (= (run (stack-call-violations "l.py" "hy.macros" frame-only named-row)) []))
       (assert (= (len (run (stack-call-violations "l.py" "hy.macros" "import inspect\nrows = inspect.stack()\n" tuple-row))) 1))
       (assert (in "inspect.stack を使わない" (get (run (stack-call-violations "l.py" "hy.macros" "def f():\n    pass\n" tuple-row)) 0)))
       (assert (in "hy.macros が無い" (get (run (stack-call-violations "l.py" "hy.macros" None tuple-row)) 0)))
       (assert (in "Python として読めない" (get (run (stack-call-violations "l.py" "hy.core.util" "(get (.stack inspect) (+ n 1) 0)" tuple-row)) 0)))
       ;; 判定に渡す行は doeff-hy の light_stack の実物(hy.compiler からの呼びに返す行)で、今の light_stack の行は [0] で frame を返す。
       (assert (isinstance (get (run (light-stack-sample-row "hy.compiler")) 0) types.FrameType))
       (assert (in "scope" (get (run (reliance-violations [#("m.hy" "compiler-attribute" "_hy_compiler" "scope")] "9.9.9" None)) 0)))
       (val not-passed (run (reliance-violations [#("m.hy" "compiler-parameter" "hy.macros.macroexpand" "_hy_compiler")]
                                                 "0.27.0" "HyEvalError: 'NoneType' object has no attribute '__dict__'")))
       (assert (and (in "_hy_compiler" (get not-passed 0)) (in "HyEvalError" (get not-passed 0))) not-passed)
       ;; 反例(設計検証 lt-Y7VT の盲検 B): 条件(marker)の付いた上限の行と、doeff-hy を引く配布物の上限の写しは赤。上から縛らない宣言は緑。
       (assert (= (len (run (hy-cap-violations ["hy>=1.2.0,<=1.3.1; python_version < '3.15'"] "1.3.1"))) 1))
       (assert (= (len (run (hy-caps-outside-doeff-hy [#("packages/doeff-adr/pyproject.toml" "hy>=1.2.0,<=1.3.1")]))) 1))
       (assert (= (len (run (hy-caps-outside-doeff-hy [#("packages/doeff-adr/pyproject.toml" "hy~=1.3")]))) 1))
       (assert (= (run (hy-caps-outside-doeff-hy [#("packages/doeff-adr/pyproject.toml" "hy>=1.2.0")
                                                  #("pyproject.toml" "hy>=1.2.0,!=1.3.0")])) []))
       ;; 反例(card ki-96ca6c49fffd): doeff-hy を引かない配布物(doeff-domain)の hy の宣言は、doeff-hy の hy の行と同じ版の指定で
       ;; なければ赤 — 上限なし・違う上限・条件つき。字の順だけの違いは同じ指定で緑。
       (val cap-rows ["doeff>=0.2.0" "hy>=1.3.0,<=1.3.1"])
       (val domain "packages/doeff-domain/pyproject.toml")
       (assert (= (run (hy-mirror-violations [#(domain "hy<=1.3.1,>=1.3.0")] cap-rows)) []))
       (assert (= (len (run (hy-mirror-violations [#(domain "hy>=1.2.0")] cap-rows))) 1))
       (assert (= (len (run (hy-mirror-violations [#(domain "hy>=1.3.0,<=1.3.0")] cap-rows))) 1))
       (assert (= (len (run (hy-mirror-violations [#(domain "hy>=1.3.0,<=1.3.1; python_version < '3.15'")] cap-rows))) 1))
       ;; doeff-hy に届かない配布物の数え方: [project] の dependencies で届く物(2 段目で届く物を含む)の hy は数えず、extra だけで
       ;; 届く物と、届かない物の hy は数える。doeff-hy 自身は数えない。
       (val repo (/ tmp-path "repo"))
       (for [#(rel text) [#("pyproject.toml" "[project]\nname = \"root\"\ndependencies = [\"hy>=1.0\"]\n[dependency-groups]\ndev = [\"doeff-hy\"]\n")
                          #("packages/doeff-hy/pyproject.toml" "[project]\nname = \"doeff-hy\"\ndependencies = [\"hy>=1.3.0,<=1.3.1\"]\n")
                          #("packages/near/pyproject.toml" "[project]\nname = \"near\"\ndependencies = [\"hy>=1.0\", \"doeff_hy\"]\n")
                          #("packages/far/pyproject.toml" "[project]\nname = \"far\"\ndependencies = [\"hy>=1.0\", \"near[x]>=0.1\"]\n")
                          #("packages/extra/pyproject.toml" "[project]\nname = \"extra\"\ndependencies = [\"hy>=1.0\"]\n[project.optional-dependencies]\nx = [\"doeff-hy\"]\n")]]
         (.mkdir (. (/ repo rel) parent) :parents True :exist-ok True)
         (.write-text (/ repo rel) text :encoding "utf-8"))
       (assert (= (run (doeff-hy-free-hy-declarations repo))
                  [#("pyproject.toml" "hy>=1.0") #("packages/extra/pyproject.toml" "hy>=1.0")])))]
  :plans ["docs/design/hy-fresh-bytecode/artifacts/v1/design.md"])
