;;; 土台だけの image の約束(設計 worker-runtime-env.md 節 3.5・E13)を Dockerfile の字面で検める。
;;;
;;;   hy -m doeff_cluster.image_contract <Dockerfile> …   違反があれば 1 行ずつ出して終了コード 1
;;;
;;; 約束: image を作り直す理由は (1) 土台の道具の版を変える時 (2) 業務の Python の package が新しい OS の library を要する時 の
;;; 2 種類だけ。だから Dockerfile は
;;;   - 頭の註に「作り直す理由」と 2 種類を書く
;;;   - OS の package は頭の註の表(`#   <名>  (1|2)  <何に使うか>`)に理由の種類つきで載せた物だけを入れる(python の package は載せられない)
;;;   - Python の package・venv・interpreter を入れない(pip・uv の sync / pip / tool / venv / python・venv や site-packages の COPY・
;;;     PYTHONPATH / VIRTUAL_ENV の ENV)
;;;   - npm の道具は版を固定して入れる(`名@x.y.z`)
;;; 業務の code と依存は宣言(RuntimeEnv)から worker が root に用意するので、image には載らない。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import re)
(import sys)
(import pathlib [Path])
(import doeff [run])

(val REASONS-TITLE "作り直す理由")
(val REASON-TOOLS "(1) 土台の道具")
(val REASON-LIBRARY "(2) 業務の Python の package が新しい OS の library を要する時")
(val PACKAGE-ROW (re.compile r"^#\s+([a-z0-9][a-z0-9.+\-]*)\s+\((1|2)\)\s+\S"))
(val PYTHON-PACKAGE (re.compile r"^(python|python3|pypy|pip)(\b|[.\-0-9])"))
(val INSTALLERS (re.compile (+ r"(\bpip3?\s|\bpipx\b|\bpoetry\b|\bconda\b|\beasy_install\b|python3?\s+-m\s+(pip|venv|ensurepip)"
                               r"|\buv\s+(pip|sync|tool|add|venv|python|run)\b|\buvx\s)")))
(val COPIED-ENV (re.compile r"(\.venv|site-packages|dist-packages|/\.local\b|\.whl\b)"))
(val FORBIDDEN-ENV (re.compile r"\b(PYTHONPATH|VIRTUAL_ENV|PYTHONHOME)\s*="))
(val NPM-INSTALL (re.compile r"\bnpm\s+(install|i)\s+([^&;|]*)"))
(val PINNED-NPM (re.compile r"^\"?@?[a-z0-9@/._\-]+@\$?\{?[A-Za-z0-9_.\-]+\}?\"?$"))


(defrecord Violation
  "約束を破る 1 か所。line = Dockerfile の行(命令の頭)・rule = 破った約束の名・text = その字面。"
  (#^ int line)
  (#^ str rule)
  (#^ str text))


(defrecord Instruction
  "継続行を 1 行につないだ命令。line = 頭の行・keyword = 命令の語(大文字)・body = 語の後ろ。"
  (#^ int line)
  (#^ str keyword)
  (#^ str body))


(defk header-of [lines]
  {:pre [(: lines tuple)] :post [(: % tuple)]}
  "最初の命令より前の註の行。"
  (var out [])
  (for [line lines]
    (cond
      (.startswith (.strip line) "#") (.append out (.rstrip line))
      (= (.strip line) "") None
      True (break)))
  (tuple out))


(defk declared-packages [header]
  {:pre [(: header tuple)] :post [(: % dict)]}
  "頭の註の OS の package の表 → {名: 理由の種類}。"
  (dfor line header
        :setv m (.match PACKAGE-ROW line)
        :if m
        (.group m 1) (.group m 2)))


(defk instructions-of [lines]
  {:pre [(: lines tuple)] :post [(: % tuple)]}
  "Dockerfile の命令(註と空行を除き、行末の \\ の継続をつなぐ)。"
  (var out [])
  (var start 0)
  (var buffer [])
  (for [#(index raw) (enumerate lines 1)]
    (val stripped (.strip raw))
    (when (and (not buffer) (or (= stripped "") (.startswith stripped "#")))
      (continue))
    (when (and buffer (.startswith stripped "#"))
      (continue))
    (when (not buffer) (:= start index))
    (.append buffer (.rstrip stripped "\\"))
    (when (not (.endswith stripped "\\"))
      (val joined (.join " " buffer))
      (val parts (.split joined None 1))
      (.append out (Instruction :line start :keyword (.upper (get parts 0))
                                :body (if (> (len parts) 1) (get parts 1) "")))
      (:= buffer [])))
  (tuple out))


(defk apt-packages [body]
  {:pre [(: body str)] :post [(: % tuple)]}
  "RUN の中の apt-get / apt install が入れる package の名。"
  (var out [])
  (for [segment (re.split r"&&|;|\|\|" body)]
    (val words (.split segment))
    (when (and (>= (len words) 2) (in (get words 0) #("apt-get" "apt")) (in "install" words))
      (val after (cut words (+ (.index words "install") 1) None))
      (.extend out (lfor w after :if (not (.startswith w "-")) (get (.split w "=") 0)))))
  (tuple out))


(defk npm-unpinned [body]
  {:pre [(: body str)] :post [(: % tuple)]}
  "RUN の中の npm install が版を固定せずに入れる package。"
  (var out [])
  (for [m (.finditer NPM-INSTALL body)]
    (for [word (.split (.group m 2))]
      (when (and (not (.startswith word "-")) (not (.match PINNED-NPM word)))
        (.append out word))))
  (tuple out))


(defk image-contract-violations [text]
  {:pre [(: text str)] :post [(: % tuple)]}
  "Dockerfile の中身の、土台だけの image の約束への違反(無ければ空)。"
  (val lines (tuple (.splitlines text)))
  (<- header tuple (header-of lines))
  (val header-text (.join "\n" header))
  (<- declared dict (declared-packages header))
  (<- instructions tuple (instructions-of lines))
  (var out [])
  (for [#(needle rule) #(#(REASONS-TITLE "reasons-missing") #(REASON-TOOLS "reason-tools-missing")
                         #(REASON-LIBRARY "reason-library-missing"))]
    (when (not-in needle header-text)
      (.append out (Violation :line 1 :rule rule :text needle))))
  (for [name declared]
    (when (.match PYTHON-PACKAGE name)
      (.append out (Violation :line 1 :rule "python-package-declared" :text name))))
  (for [ins instructions]
    (cond
      (= ins.keyword "RUN")
      (do (<- installed tuple (apt-packages ins.body))
          (for [name installed]
            (cond
              (.match PYTHON-PACKAGE name)
              (.append out (Violation :line ins.line :rule "python-os-package" :text name))
              (not-in name declared)
              (.append out (Violation :line ins.line :rule "os-package-undeclared" :text name))))
          (val found (.search INSTALLERS ins.body))
          (when found
            (.append out (Violation :line ins.line :rule "python-install" :text (.strip (.group found 0)))))
          (<- loose tuple (npm-unpinned ins.body))
          (for [word loose]
            (.append out (Violation :line ins.line :rule "npm-unpinned" :text word))))
      (in ins.keyword #("COPY" "ADD"))
      (do (when (= ins.keyword "ADD")
            (.append out (Violation :line ins.line :rule "add-instruction" :text ins.body)))
          (val copied (.search COPIED-ENV ins.body))
          (when copied
            (.append out (Violation :line ins.line :rule "python-env-copied" :text (.group copied 0)))))
      (= ins.keyword "ENV")
      (do (val env-found (.search FORBIDDEN-ENV ins.body))
          (when env-found
            (.append out (Violation :line ins.line :rule "python-env-variable" :text (.group env-found 1)))))
      True None))
  (tuple out))


(defn main []
  "image を build する前に Dockerfile を検め、約束を破る image を作らせないための入口(違反が在れば終了コード 1)。"
  (setv paths (cut sys.argv 1 None))
  (when (not paths)
    (print "使い方: hy -m doeff_cluster.image_contract <Dockerfile> …" :file sys.stderr)
    (sys.exit 2))
  (setv failed False)
  (for [p paths]
    (setv found (run (image-contract-violations (.read-text (Path p) :encoding "utf-8"))))
    (for [v found]
      (print (.format "{}:{}: {} — {}" p v.line v.rule v.text))
      (setv failed True)))
  (sys.exit (if failed 1 0)))


(when (= __name__ "__main__")
  (main))
