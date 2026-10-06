;;; Executable ADR: Rust の部品(doeff-vm・doeff-vm-core・doeff-linter・doeff-effect-analyzer・doeff-indexer・doeff-agentic-cli)を組む・
;;; 引く入口は 1 つ — build の口 tools/doeff_cargo_backend.py が、組む前に必ず「source の中身の hash を鍵にした wheel の保存先」を引く。
;;;
;;; 出自 = 利用者の 2026-10-07 の原文 2 通(agora-redesign #3860 の本文・dotfiles ADR-DOTFILES-027 の条 R-cf708d56 と追補 1)と、Mac の
;;; 調整役の決定(#3860 の 1・2026-10-07 — 入口は 1 つ・古い道は替える変更と同じ変更で消す)。
;;;
;;; 移す予定の入口(PENDING-ENTRIES): 鍵と保存先を自前で持つ所が 1 系統残る — commit の hook の doeff-linter の binary の置き場
;;; (linter_snapshot)。台帳に無い新しい入口は赤・入口を消した変更が台帳の行を残すと赤(消した変更が同じ commit で行を削る)。
;;; doeff-cluster の worker の native の wheel(git の tree hash の鍵・state/wheels — 実行環境の準備と起動の script)は #3860 で入口を
;;; `uv build --wheel` で通るだけの形にし、台帳の 4 行を消した(R7)。
;;;
;;; 戻し方: この ADR を足した commit を revert する(ADR の file 1 つと、build の口の保存先の形が前に戻る)。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk <- val var])
(import doeff [run])
(import ast)
(import re)
(import pathlib [Path])


;; ---------------------------------------------------------------------------
;; 台帳

;; 入口そのもの(保存先を引いてから組む口)。
(val ENTRY "tools/doeff_cargo_backend.py")

;; 入口へ「無い時の組み方」を渡すだけの口(build の hook は入口の wheel_from_store・editable_from_store を呼ぶ)。
(val COMPILERS #{"packages/doeff-indexer/doeff_indexer_build_backend.py"})

;; 移す予定の入口 — path → 理由(消した変更が同じ commit で行を削る)。
(val PENDING-ENTRIES
  {"packages/doeff-linter/scripts/linter_snapshot.py"
   "commit の hook の doeff-linter の binary の置き場(sha と入力の鍵・cargo build --release)— 別の変更で入口の保存先へ移す"})

;; 走査で降りない dir(作業木・venv・生成物)と、検と文書の置き場(検は入口を呼んで確かめる側・文書は手順の写し)。
(val SCAN-SKIP-PARTS #{".git" ".venv" ".worktrees" "node_modules" "target" "__pycache__" "tests" "conformance"})
(val SCAN-SKIP-PREFIXES #("docs/" "specs/" "notes/" ".github/"))

;; Rust を組む・鍵を作る形の目印。`uv build --wheel` は入口(build の口)を通る呼びなので数えない(#3860 — worker と起動の script は
;; これだけで wheel を用意する)。
;;   - Python: maturin の build の hook を呼ぶ(maturin.build_wheel・maturin.build_editable)・命令の列に "build" と "--release" が並ぶ
;;     (cargo build --release)・名に native_key を持つ関数の定義。
;;   - Hy: 註でない行の "build" "--release" の並び・(defk native-key の定義。
(val MATURIN-HOOKS #{"build_wheel" "build_editable"})
(val BUILD-FLAGS #{"--release"})
(val HY-BUILD-ARGV (re.compile r"\"build\"\s+\"--release\""))
(val HY-KEY-DEFINITION (re.compile r"\(defk\s+native-key\b"))


;; ---------------------------------------------------------------------------
;; 判断(純関数)

(defk python-builds-rust [text]
  {:pre [(: text str)] :post [(: % bool)] :tags {:context "doeff-build-adr" :role "judgment"}}
  "Python の source が Rust を組む・鍵を作る形を持つか(註と docstring は数えない — ast で読む)。"
  (val tree (try (ast.parse text) (except [SyntaxError] None)))
  (when (is tree None)
    (return False))
  (for [node (ast.walk tree)]
    (match node
      (ast.Attribute :value (ast.Name :id "maturin") :attr attr) :if (in attr MATURIN-HOOKS)
        (return True)
      (| (ast.List :elts elts) (ast.Tuple :elts elts))
        (let [words (sfor e elts :if (and (isinstance e ast.Constant) (isinstance e.value str)) e.value)]
          (when (and (in "build" words) (& words BUILD-FLAGS))
            (return True)))
      (ast.FunctionDef :name name) :if (in "native_key" name)
        (return True)
      _ None))
  False)

(defk hy-builds-rust [text]
  {:pre [(: text str)] :post [(: % bool)] :tags {:context "doeff-build-adr" :role "judgment"}}
  "Hy の source が Rust を組む・鍵を作る形を持つか(; で始まる註の行は数えない)。"
  (val code (.join "\n" (gfor line (.splitlines text) :if (not (.startswith (.lstrip line) ";")) line)))
  (bool (or (.search HY-BUILD-ARGV code) (.search HY-KEY-DEFINITION code))))

(defk entry-violations [found]
  {:pre [(: found frozenset)] :post [(: % list)] :tags {:context "doeff-build-adr" :role "judgment"}}
  "Rust を組む・鍵を作る file の集合 found のうち、入口・組み方の口・移す予定の台帳のどれでもない物(2 つ目の入口)と、入口が
   見つからない事を挙げる。"
  (+ (lfor rel (sorted found)
           :if (and (!= rel ENTRY) (not-in rel COMPILERS) (not-in rel PENDING-ENTRIES))
           f"{rel}: Rust を組む・鍵を作る 2 つ目の入口 — 組み方を {ENTRY} の wheel_from_store・editable_from_store に渡す")
     (if (in ENTRY found) [] [f"{ENTRY}: 入口が Rust を組む形を持たない(入口が消えた)"])))

(defk stale-pending [found]
  {:pre [(: found frozenset)] :post [(: % list)] :tags {:context "doeff-build-adr" :role "judgment"}}
  "移す予定の台帳に在るのに、もう Rust を組む・鍵を作る形を持たない file(入口へ移した・消した)を挙げる — 移した変更が同じ commit で
   台帳から削るため。"
  (lfor rel (sorted PENDING-ENTRIES)
        :if (not-in rel found)
        f"{rel}: もう自前で組まない — PENDING-ENTRIES から削る"))

(defk compiler-hooks-use-the-store [text]
  {:pre [(: text str)] :post [(: % list)] :tags {:context "doeff-build-adr" :role "judgment"}}
  "組み方の口の build の hook(build_wheel・build_editable)が入口の保存先(wheel_from_store・editable_from_store)を呼ぶか。答え =
   呼ばない hook の名。"
  (val wanted {"build_wheel" "wheel_from_store" "build_editable" "editable_from_store"})
  (lfor node (. (ast.parse text) body)
        :if (and (isinstance node ast.FunctionDef) (in node.name wanted))
        :if (not (any (gfor inner (ast.walk node)
                            (and (isinstance inner ast.Call) (isinstance inner.func ast.Name)
                                 (= inner.func.id (get wanted node.name))))))
        node.name))


;; ---------------------------------------------------------------------------
;; 土台(file を読む)

(defk rust-building-files [repo-root]
  {:pre [(: repo-root Path)] :post [(: % frozenset)] :tags {:context "doeff-build-adr" :role "foundation"}}
  "repo の中で Rust を組む・鍵を作る形を持つ Python と Hy の file の相対 path を集める(検・文書・作業木・venv を除く)。"
  (var found #{})
  (for [p (sorted (.rglob repo-root "*"))
        :setv rel (.relative-to p repo-root)
        :if (and (in p.suffix #(".py" ".hy"))
                 (not (& (set rel.parts) SCAN-SKIP-PARTS))
                 (not (.startswith (.as-posix rel) SCAN-SKIP-PREFIXES))
                 (not (.is-symlink p))
                 (.is-file p))]
    (<- hit bool (match p.suffix
                   ".py" (python-builds-rust (.read-text p :encoding "utf-8" :errors "replace"))
                   _ (hy-builds-rust (.read-text p :encoding "utf-8" :errors "replace"))))
    (when hit
      (:= found (| found #{(.as-posix rel)}))))
  (frozenset found))


(defadr ADR-DOE-BUILD-001
  :title "Rust の部品を組む・引く入口は build の口 tools/doeff_cargo_backend.py の 1 つ。口は組む前に必ず source の中身の hash を鍵にした wheel の保存先を引き、wheel の hook も editable の hook も同じ保存先の同じ wheel から答える。自前の鍵や保存先を持つ 2 つ目の入口を作らない"
  :status "accepted"
  :scope ["tools/doeff_cargo_backend.py"
          "packages/doeff-indexer/doeff_indexer_build_backend.py"
          "packages/doeff-vm/pyproject.toml"
          "packages/doeff-cluster/src/doeff_cluster/shared/core/native_wheel.py"
          "packages/doeff-cluster/src/doeff_cluster/worker/entry/boot_wheel.py"
          "docs/adr/defadr_doeff_build_001_one_rust_wheel_entry.hy"]
  :problem
    [(fact
       "利用者の原文 2026-10-07 01:5x(音声入力・ドウェーフ = doeff・ラスト = Rust): 「そもそもドウェーフの一部のパッケージを更新するたびに、ドウェーフ全体をビルドしてイメージに焼き込むっていう作業が必要になっちゃってるっていうのがおかしくて、今触ってるドウェーフクラスターとかドウェーフエージェンツは、ドウェーフのラストのVMをビルドする必要はほとんどないはずなんだよね。」"
       :evidence "agora-redesign #3860 の本文・dotfiles docs/adr/defadr_dotfiles_027_operator_feedback.hy の条 R-cf708d56")
     (fact
       "利用者の原文 2026-10-07 02:1x: 「imageを作るにしてもちゃんとMakefileやキャッシュレイヤーをつかって無駄にしないようにして」"
       :evidence "agora-redesign #3860 の本文・R-cf708d56 の追補 1")
     (fact
       "2026-10-06 の数(cc1-w38 の調べ・log の行で確かめた分): 日次の全体検証は task ごとに新しい HOME を作り wheel の保存先を子へ継がないので、doeff-vm を約 100 度・doeff-linter を 14 度組み直した(最初の依存の入れ 6m15s〜9m30s)。build の口の保存先は wheel の hook だけが引き、editable の hook(agora-controllers の doeff-vm・doeff の workspace の一員)は毎回 maturin を撃っていた。doeff-indexer の口は保存先を通らずに毎回 CLI の binary と拡張 module を組んでいた。"
       :evidence "agora-redesign #3860 の調べの表・tools/doeff_cargo_backend.py の 2026-10-06 の版の頭の註「editable と sdist は引かない」")
     (fact
       "鍵と保存先を自前で持つ所が 3 系統あった: build の口(中身の hash・$XDG_CACHE_HOME/doeff-cargo-wheels/<package>/<hash>/)・doeff-cluster の worker(doeff-vm と doeff-vm-core の git の tree hash・state/wheels/doeff-vm-<鍵>/)・commit の hook の linter(sha と入力の object id・doeff-linter-snapshots)。worker の鍵は Python の版と機体だけを足し、rustc・maturin の版と組みを変える環境変数と作業木の未 commit の変更を見ない。"
       :evidence "packages/doeff-cluster/src/doeff_cluster/shared/core/native_wheel.py・packages/doeff-linter/scripts/linter_snapshot.py")]
  :context
    [(interpretation
       "Rust の source が変わらない版上げで Rust を組み直さない事は、組む所が何か所あっても全部が同じ鍵の同じ保存先を先に引く時だけ保証できる。入口が 2 つあると、片方の鍵が見ない入力(rustc の版・未 commit の変更)で古い wheel を引くか、片方だけが保存先を持たずに毎回組む。build の口は uv sync・uv build・worker の uv build --wheel・起動の script のどれもが必ず通る所なので、入口をここに置けば、呼び手を数えずに全部へ効く。")
     (interpretation
       "鍵は build の口の物を残す(source の中身・道具の版・機体・Python の ABI・組みを変える環境変数・build の設定)。中身の hash は git の object を要らず、未 commit の変更も拾う。tool.uv.cache-keys の宣言は「uv が口を呼ぶか」と「保存先の鍵」の 1 つの定義になる — だから wheel に入る Python の source(doeff-vm の doeff_vm/)も宣言に並べる。")
     (interpretation
       "保存先の並びは worker と同じ <package>-<鍵>/<wheel> と使った印 .used にし、日次の検証は worker の /work/state/wheels を置き場に渡す。worker の掃除(7 日使われない dir を消す)がそのまま効き、保存先の掃除を 2 つ持たない。cargo の target は組む時だけの一時の dir のまま(共有の target は中身の違う作業木の build を取り違え、その wheel が鍵の下に置かれると同じ鍵の build が全部それを引く)。")]
  :decision
    [(rule R1 "Rust の部品を組む・引く入口は tools/doeff_cargo_backend.py の stored_wheel 1 つ。build_wheel・build_editable はどちらも保存先を先に引き、在れば cargo も maturin も撃たない。無い時・壊れている時だけ組んで置いてから使う。")
     (rule R2 "editable の hook は保存先の wheel から答える: 組み立ての成果物(native の拡張 module と maturin の include で wheel に入れる物)を source の木に置かず、editable の wheel の中身として venv の site-packages の __editable__.<名>.native/ に入れ(uv の RECORD が持つ)、python-source を路に足し finder を起こす .pth と、package の探し先を 成果物の dir → 作業木の dir にする finder と、dist-info の wheel を返す。訳: 木の git の名簿に無い file を消す写し(dotfiles の remote_check)が source の木の成果物を消しても uv の記録はその有無を知らず、次の uv sync が組み直さず import が落ちた(2026-10-07 04:00 の日次・agora-redesign #3860)。python-source を持たない package は保存先の wheel をそのまま返す。")
     (rule R3 "組み方が maturin だけでない package(doeff-indexer)は、無い時の組み方(Compile)を wheel_from_store・editable_from_store に渡す。自前で保存先・鍵を持たず、build の hook から maturin を直に撃たない。")
     (rule R4 "鍵 = tool.uv.cache-keys の file(宣言が無ければ既定の glob)の中身と相対 path・rustc と maturin の版・機体・組む Python の ABI・組みを変える環境変数・build の設定。wheel に入る file はどれも宣言に並べる(doeff-vm は doeff_vm/ の .py・.pyi・py.typed も)。")
     (rule R5 "保存先 = env DOEFF_WHEEL_CACHE(無ければ $XDG_CACHE_HOME/doeff-cargo-wheels)の <package>-<鍵>/<wheel> と .used。使う前に RECORD の hash で確かめ、壊れていれば名指しの 1 行を出してその wheel を除き組み直す。")
     (rule R6 "移す予定の入口(commit の hook の linter の binary)は台帳 PENDING-ENTRIES に理由つきで置く。台帳に無い新しい入口は赤。入口へ移した・消した変更は同じ commit で台帳の行を削る(残すと赤)。")
     (rule R7 "doeff-cluster の worker(実行環境の準備の EnsureNativeWheel)と起動の script(worker/entry/boot_wheel)は、native の wheel を `uv build --wheel` で入口へ渡すだけにし、自前の鍵(git の tree hash)と置き場を持たない。保存先は入口の DOEFF_WHEEL_CACHE(worker の state/wheels)。入れる wheel は uv build が root の下の --out-dir(native_wheel.wheel_out_dir)に出した file で、どの版の入口でも出る(宣言の古い doeff の root も同じ形で通る)。入口が env DOEFF_WHEEL_REPORT の file に書く 1 行(組んだか)は観測だけで、書かない版の入口では由来を閉じた型の UNREPORTED にし、準備を止めず、組んだ・使ったのどちらにも埋めない — 読みの定義点は native_wheel.reported_built(#3860 の 1 の続き)。")]
  :laws
    [(law rust-builds-pass-through-one-entry
       :statement "for_all file f in doeff (excluding tests and docs): builds_or_keys_rust(f) => f == ENTRY or f in COMPILERS or f in PENDING-ENTRIES; and builds_or_keys_rust(ENTRY)"
       :counterexamples
         [(counterexample "package の build の口が tools/doeff_cargo_backend.py を通さず maturin.build_editable を直に呼ぶ(2026-10-06 までの editable の形 — 日次の検証で doeff-vm を 1 日 約 100 度組んだ)")
          (counterexample "新しい script が自前の鍵(native_key — git の tree hash)で wheel の置き場を作る(#3860 の前の worker の native_wheel と同じ形)")
          (counterexample "script が cargo build --release を直に撃つ(入口の保存先を通らない)")])
     (law compiler-hooks-read-the-store
       :statement "for_all hook h in {build_wheel, build_editable} of every file in COMPILERS: calls(h, the matching store function of ENTRY)"
       :counterexamples
         [(counterexample "doeff-indexer の build_editable が with cargo_target_dir(): maturin.build_editable(...) のまま(保存先を通らず毎回組む)")])
     (law pending-entries-do-not-outlive-their-cause
       :statement "for_all f in PENDING-ENTRIES: builds_or_keys_rust(f)"
       :counterexamples
         [(counterexample "worker の native_wheel を入口へ移した変更が PENDING-ENTRIES の行を残す(台帳が黙って次の入口を許す)")])]
  :enforcement
    [(deftest test-adr-doe-build-001-rust-builds-pass-through-one-entry
       (val repo-root (. (Path __file__) parent parent parent))
       (val found (run (rust-building-files repo-root)))
       (val violations (run (entry-violations found)))
       (assert (= violations []) (+ "Rust を組む・鍵を作る 2 つ目の入口(ADR-DOE-BUILD-001 R1・R6): " (str violations))))
     (deftest test-adr-doe-build-001-compiler-hooks-read-the-store
       (val repo-root (. (Path __file__) parent parent parent))
       (val missing (lfor rel (sorted COMPILERS)
                          hook (run (compiler-hooks-use-the-store (.read-text (/ repo-root rel) :encoding "utf-8")))
                          f"{rel}: {hook}"))
       (assert (= missing []) (+ "組み方の口の build の hook が保存先を通らない(ADR-DOE-BUILD-001 R3): " (str missing))))
     (deftest test-adr-doe-build-001-pending-entries-do-not-outlive-their-cause
       (val repo-root (. (Path __file__) parent parent parent))
       (val stale (run (stale-pending (run (rust-building-files repo-root)))))
       (assert (= stale []) (+ "移した入口が台帳に残っている(ADR-DOE-BUILD-001 R6): " (str stale))))
     (deftest test-adr-doe-build-001-judgments-reject-the-counterexamples
       ;; 反例: 入口を 2 つにした形(maturin の hook を直に呼ぶ口・cargo build --release を撃つ script・自前の鍵 native_key・Hy の
       ;; native-key)は 2 つ目の入口として赤。uv build --wheel は入口を通る呼びなので数えない。註と docstring の中の綴りは数えない。
       ;; 台帳の行は原因が消えると赤。
       (assert (run (python-builds-rust "import maturin\ndef build_editable(d, c=None, m=None):\n    return maturin.build_editable(d, c, m)\n")))
       (assert (run (python-builds-rust "import subprocess\nsubprocess.run(['cargo', 'build', '--release'])\n")))
       (assert (not (run (python-builds-rust "import subprocess\nsubprocess.run(['uv', 'build', '--wheel', '--out-dir', 'x', 'pkg'])\n"))))
       (assert (run (python-builds-rust "def native_key(package, trees):\n    return package\n")))
       (assert (not (run (python-builds-rust "\"\"\"cargo build --release の註\"\"\"\n# maturin.build_wheel を呼ばない\nx = ['build', 'docs']\n"))))
       (assert (run (hy-builds-rust "(defk native-key [wheel] wheel)\n")))
       (assert (run (hy-builds-rust "(RunProcess :argv #(cargo \"build\" \"--release\"))\n")))
       (assert (not (run (hy-builds-rust "(RunProcess :argv #(uv \"build\" \"--wheel\" \"--out-dir\" tmp src))\n"))))
       (assert (not (run (hy-builds-rust ";; uv \"build\" \"--wheel\" を撃たない\n"))))
       (val second "packages/doeff-new/build_backend.py")
       (assert (= (len (run (entry-violations (frozenset #{ENTRY second})))) 1))
       (assert (= (run (entry-violations (frozenset (| #{ENTRY} COMPILERS (set PENDING-ENTRIES))))) []))
       (assert (= (len (run (entry-violations (frozenset #{second})))) 2))
       (assert (= (len (run (stale-pending (frozenset #{ENTRY})))) (len PENDING-ENTRIES)))
       (assert (= (run (compiler-hooks-use-the-store "def build_editable(d, c=None, m=None):\n    with cargo_target_dir():\n        return maturin.build_editable(d, c, m)\n"))
                  ["build_editable"]))
       (assert (= (run (compiler-hooks-use-the-store "def build_editable(d, c=None, m=None):\n    return editable_from_store(d, c, compile_it)\n")) [])))]
  :plans [])
