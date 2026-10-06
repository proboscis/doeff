;;; 展開したコードの木の bytecode を、木の中だけに「実行時に source の hash を検める」方式で用意する道具。1 回の起動で幾つもの木を用意する。
;;;
;;; worker は版ごとに木を展開する。.pyc は source の中身で引く保存先(doeff-hy の code_store — 入口は 1 つ・版・木・root をまたいで中身の同じ
;;; source の code を引く)から書き、無い物・今の macro に合わない物だけを焼いて保存先へ足す(#3858 — 前は前の版の木から変わっていない file の
;;; .pyc を hardlink で引き継いだので、引き継ぎ元の root が無い時 — 新しい worker・空の /work・テストの 1 台 — は全部を焼き直した)。
;;; 検める方式(PEP 552 の checked hash)なので、保存先の code を誤って使っても import が source の hash を突き合わせて焼き直す — 古い
;;; bytecode が黙って使われることはない。Hy の source の code は、さらに展開が依った macro の記録を今の環境の macro の file と照らしてから使う。
;;; 共有の venv・doeff・標準 library には書かない(本番の image の焼き方 deploy/bytecode.py は木の外も歩き、実行時に検めない方式で焼くので、
;;; 中身の動く手元の環境には使えない)。
;;;
;;;   PYTHONDONTWRITEBYTECODE=1 hy <worker のコード>/doeff_cluster/worker/entry/code_prepare.hy --revision <版> [--jobs N]
;;;       [--entries <module,…>] --tree <木> --roots <根,…> [--tree … --roots … …]
;;;
;;; 木ごとの引数の揃え方(1 つだけ): --tree・--roots は --tree の順に並べる。--roots は木ごとに必ず 1 つ(import の根 — 木の中の dir・`,` で
;;; 並べる・前が先)。数が揃わなければ使い方の誤り(終わり 2)。保存先の dir は環境変数 DOEFF_HY_CODE_STORE(code_store の store-dir —
;;; worker の起動の script の既定は $WORK_DIR/state/doeff-hy-code-store・`off` なら保存先を使わずに全部を焼く)。
;;;
;;; 手順(全部の木を 1 回の process で — 木ごとに順に起こすと、木 1 つの終わりを待つ間ほかの core が遊ぶ):
;;;   1 走査      木ごとに ScanTree
;;;   2 閉包      --entries を渡すと、その module から import を静的に辿り(Hy の import / require と Python の import)、どの木の
;;;               module にも解ける所まで辿った閉包だけを用意する(ある木の module が import する別の木の module も入る)。木の外の module
;;;               (標準・第三者)は辿らない。閉包の外の module は子が import した時に作られる(用意する物が減るだけで正しさは変わらない)。
;;;               --entries が無ければ全部の木の根の下を全部用意する。
;;;               import の名は構文の読み(Hy の read-many・Python の ast.parse — 閉包の秒の大半)で求め、保存先に source の中身の鍵で
;;;               置く(種類 .imports — 形は bake_plan)。次の準備は中身の同じ source の entry を使い、中身の変わった file だけを読む。
;;;   3 用意      用意する物を全部の木から集め、source の大きい順に 1 つの process の pool へ 1 つずつ渡す(BakeSources — 答え手は焼きの
;;;               道具 foundation/bytecode_pool.hy を子 process で起こす)— 1 file の秒の偏りが大きい(大半は Hy の macro の展開)ので、
;;;               名の順・束で渡すと最後に遅い file を 1 core で待つ。並列数の既定は cgroup の CPU の上限(pod の limits)。道具は 1 つずつ
;;;               保存先の code を引き(stored)、無ければ焼いて足す(rebuilt)。木に既に在る .pyc が今の source と macro に合う物は
;;;               焼かずに残す(reused — 同じ commit の木を hardlink で写した root)。
;;;   4 検めと印   木ごとに用意した後を走査し直し、用意するべき source ごとに .pyc が在ること(焼けなかった file は理由つきで印に載せる)を
;;;               検めてから、木の根に完成の印(MARKER)を置く。検めが通らない木には印を置かない。
;;;
;;; 報告(stderr の slog の行): 木ごとに `tree=<--tree の綴り> stored=N rebuilt=N reused=N failed=N problem=<文|->`、最後に全体の
;;; `stored=N rebuilt=N reused=N failed=N compile_s=… closure_s=… scan_s=…`(用意する計画の file のうち stored = 保存先の code から書いた・
;;; rebuilt = 焼いた・reused = 焼かずに残した・failed = 焼けなかった — 4 つの和が計画の数・scan_s = 木の走査・closure_s = 閉包の歩み・
;;; compile_s = 保存先の引きと焼き)。--entries の在る時は閉包の歩みの後に `closure_modules=N closure_reread=N`(閉包の source の数・そのうち
;;; 構文を読み直した数)。検めの通らない木が 1 つでも在れば終わり 1(ほかの木の印は置く)。
;;; 実行環境の準備(worker/protocol/env_translation)は木ごとの行を読み、版ごとのコードの木の準備(worker/core/code_rules の script)は印の
;;; 有無を確かめる。
;;;
;;; 形: 純粋な判断と record(引数の揃え・閉包の歩み・import の名の entry の形・焼く順・報告の行)は worker/core/bake_plan.hy、走査と印は木の
;;; 効果(worker/protocol/tree_files の tree-files が汎用の file の効果へ出し直す — #2468)、保存先の entry の読み書きはこの file の効果
;;; ReadStoreEntry・WriteStoreEntry・DiscardStoreEntry(答え手 store-entries が code_store を呼ぶ)、焼きはこの file の効果 BakeSources(答え手
;;; pool-tool-baker が焼きの道具を汎用の子 process の効果 RunProcess で起こす — 生の process の pool は foundation の層だけが持つ)。
;;; main が本物の os-file-handler と subprocess-handler を被せる。経過の秒は doeff-time の GetMonotonic(main が sync-time-handler を被せる)。
;;;
;;; 版に依らず効く形: この file は worker の版の file だが、準備する root の venv の python と import の路で走る(import する doeff は
;;; root の版)。だから手順はこの file に置き、root の側から import するのは、cluster で動く job の doeff の版から変わっていない部品の
;;; 名前と引数の形だけ(code_plan の compile-plan・marker-content・tree-problem と bake_plan が読む module-name・imported-names、code_model の
;;; ScanTree・WriteMarker・Note、tree_files の tree-files、file_effects・process_effects の効果と file-done、os_file・os_process・handlers・
;;; doeff-time の答え手)。判断の module bake_plan と焼きの道具 bytecode_pool も worker の版の file なので、package の import でなく、この file の
;;; 位置から求めた path で読む・起こす(package の名 doeff_cluster.worker.core.bake_plan で引くと root の版の doeff の物になり、この module を
;;; 持たない古い版の root で落ちる)。保存先の入口 code_store も、root の版の doeff-hy は持たない版でありうるので、worker の版の doeff-hy の
;;; file を同じ checkout の中の位置から求めた path で読む(worker のコードは doeff の checkout の packages/doeff-cluster/src に在る)。
;;; 古い入口: root の側の worker/core/code_prepare の prepare-tree、code_plan の carry-pairs、code_model の LinkPycs と python_bytecode の
;;; compile-python-sources・prepare-compile-path は、版を上げていない worker の古い入口が呼ぶので名前と引数の形を変えない(消すのは全 worker の
;;; 版上げの後の別の変更)。
;;;
;;; 道具は worker 自身のコードから file の path で起動する(準備する版の木から -m で起動すると、道具を持たない
;;; 古い版では道具が見つからない — 2026-09-23 に atlas の版 8d7181f が bytecode 0 のまま完成品になった原因)。
(require doeff-hy.macros [defk defhandler val var <-])
(val MODULE-TAGS {:context "worker" :role "main"})
(import argparse)
(import hy)
(import importlib.machinery)
(import importlib.util)
(import os)
(import posixpath)
(import sys)
(import dataclasses [dataclass])  ; dataclass は効果の型が使う
(import pathlib [Path])
(import types [ModuleType])
(import doeff [EffectBase run with-handlers])
(import doeff_time [GetMonotonic sync-time-handler])
(import doeff_core_effects.handlers [slog-handler])
(import doeff_core_effects.file_effects [FileFailed PathStat ReadText StatPath file-done])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.process_effects [InterpreterFacts ProcessOutcome ReadInterpreter RunProcess])
(import doeff_cluster.worker.core.code_plan [compile-plan marker-content tree-problem])
(import doeff_cluster.worker.intent.code_model [ScanTree WriteMarker Note])
(import doeff_cluster.worker.protocol.tree_files [tree-files])


;; --- worker の版の file の置き場 ---------------------------------------------------------------

;; worker 自身のコードの doeff_cluster の dir(この file は doeff_cluster/worker/entry/ に在る)。
(val WORKER-CODE (. (Path __file__) (resolve) parent parent parent))
;; 判断の module と、その module 名(package の名と重ならない名 — package の名で引くと root の版の物になる)。
(val BAKE-PLAN (str (/ WORKER-CODE "worker" "core" "bake_plan.hy")))
(val BAKE-PLAN-MODULE "doeff_cluster_worker_bake_plan")
;; 焼きの道具(生の process の pool を持つ foundation の console の道具)。
(val POOL-TOOL (str (/ WORKER-CODE "foundation" "bytecode_pool.hy")))
;; 保存先の入口(worker の版の doeff-hy の code_store — worker のコード doeff_cluster は doeff の checkout の packages/doeff-cluster/src/ に在る)と、
;; その module 名(root の版の doeff_hy_bytecode_guard.code_store と重ならない名)。
(val CODE-STORE (str (/ (. WORKER-CODE parent parent parent) "doeff-hy" "src" "doeff_hy_bytecode_guard" "code_store.py")))
(val CODE-STORE-MODULE "doeff_cluster_worker_code_store")


(defk module-at [name path]
  {:pre [(: name str) (: path str)] :post [(: % ModuleType)] :tags {:context "worker" :role "main"}}
  "worker 自身のコード(と同じ checkout の doeff-hy)の file を、package の import でなく path で module として読むため(頭の註の「版に依らず
   効く形」)。name は sys.modules に置く名(record の dataclass が自分の module を引くため)。"
  (val loader (importlib.machinery.SourceFileLoader name path))
  (match (importlib.util.spec-from-file-location name path :loader loader)
    None (raise (ImportError (.format "{} を module として読めない" path)))
    spec (do (val module (importlib.util.module-from-spec spec))
             (setv (get sys.modules name) module)
             (.exec-module loader module)
             module)))


;; 純粋な判断と record(worker/core/bake_plan.hy — 名は plan.<名> で引く)と、保存先の入口(code_store — 名は store.<名> で引く)。
(val plan (run (module-at BAKE-PLAN-MODULE BAKE-PLAN)))
(val store (run (module-at CODE-STORE-MODULE CODE-STORE)))


;; --- 焼きの並列数 -----------------------------------------------------------------------------

(defk usable-cpus []
  {:pre [] :post [(: % int)] :tags {:context "worker" :role "main"}}
  "この process が使える CPU の数(affinity と cgroup の上限の小さい方)— 焼きの並列数の既定。cgroup の cpu.max は file の効果で読む
   (答え手 = 入口の os-file-handler・無い / 読めない = 上限なし)。"
  (val available (if (hasattr os "sched_getaffinity") (len (os.sched-getaffinity 0)) (or (os.cpu-count) 1)))
  (<- cpu-max (ReadText "/sys/fs/cgroup/cpu.max"))
  (<- limit int (plan.cpu-limit-of (if (isinstance cpu-max str) cpu-max None) available))
  limit)


;; --- 焼きの効果 --------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] BakeSources [EffectBase]
  "用意する物を 1 つの process の pool で用意する。items = #(木の path 相対 path module 名) の列(この順に pool へ渡す)・jobs = 並列数・
   paths = 焼く process の import の路の先頭に足す dir(前が先)・code-store = 保存先の dir(None = 保存先を使わない)。答え = bake_plan の
   BakeAnswer(焼けなかった物・保存先の code から書いた物・在る .pyc が今の source と macro に合うので焼かずに残した物・保存先へ書けなかった
   理由)。"
  (#^ tuple items)
  (#^ int jobs)
  (#^ tuple paths)
  (#^ (| str None) code-store))


;; --- 保存先の entry の効果(閉包の import の名 — 答え手 store-entries が code_store を呼ぶ)----------------------------------

(defclass [(dataclass :frozen True)] ReadStoreEntry [EffectBase]
  "保存先の entry の中身を読む(当たれば entry の時刻を進める)。答え = bytes か None(無い・読めない)。"
  (#^ str path))


(defclass [(dataclass :frozen True)] WriteStoreEntry [EffectBase]
  "保存先へ entry を書く(一時の file に書いてから置き換える)。答え = 書けなかった理由か None。"
  (#^ str path)
  (#^ bytes data))


(defclass [(dataclass :frozen True)] DiscardStoreEntry [EffectBase]
  "壊れた entry を名指しの 1 行で除く(呼び手は作り直す)。答え = None。"
  (#^ str path)
  (#^ str problem))


;; --- Program -----------------------------------------------------------------------------

(defk bake-trees [shaped]
  {:pre [(: shaped tuple)] :post [(: % tuple)] :tags {:context "worker" :role "main"}}
  "揃えた木の組(TreeArgs の列)を焼く木(BakeTree の列)にするため: 木の path を symlink を辿った絶対 path にする(読めなければ OSError)。"
  (var trees #())
  (for [given shaped]
    (<- seen PathStat (file-done (StatPath given.named)))
    (:= trees (+ trees #((plan.BakeTree :named given.named :path seen.real-path :roots given.roots)))))
  trees)


(defk stored-imports [code-store rel text hy-version]
  {:pre [(: code-store (| str None)) (: rel str) (: text str) (: hy-version str)] :post [(: % (| tuple None))] :tags {:context "worker" :role "main"}}
  "source 1 つの import の名を保存先から引くため(source の中身の鍵 — 無い・保存先を使わない時は None)。形の違う entry は名指して除き
   None(呼び手は構文を読み直して書き直す)。"
  (if (is code-store None)
      None
      (do (<- parts tuple (plan.imports-key-parts rel hy-version))
          (val path (store.entry-path code-store (store.content-key parts (.encode text "utf-8")) store.IMPORTS-SUFFIX))
          (<- data (| bytes None) (ReadStoreEntry path))
          (match data
            None None
            _ (do (<- read (| tuple str) (plan.imports-of-entry data))
                  (match read
                    (tuple) read
                    problem (do (<- (DiscardStoreEntry path problem))
                                None)))))))


(defk store-imports [code-store rel text hy-version imports]
  {:pre [(: code-store (| str None)) (: rel str) (: text str) (: hy-version str) (: imports tuple)] :post [(: % (| str None))]
   :tags {:context "worker" :role "main"}}
  "読み直した source 1 つの import の名を保存先へ足すため(source の中身の鍵)。答え = 書けなかった理由か None(保存先を使わない時も None)。"
  (if (is code-store None)
      None
      (do (<- parts tuple (plan.imports-key-parts rel hy-version))
          (val path (store.entry-path code-store (store.content-key parts (.encode text "utf-8")) store.IMPORTS-SUFFIX))
          (<- data bytes (plan.imports-entry imports))
          (<- problem (| str None) (WriteStoreEntry path data))
          problem)))


(defk closure-of-trees [trees sources entries code-store hy-version]
  {:pre [(: trees tuple) (: sources tuple) (: entries tuple) (: code-store (| str None)) (: hy-version str)] :post [(: % tuple)]
   :tags {:context "worker" :role "main"}}
  "木ごとの焼く範囲(trees と同じ順の、相対 path の frozenset か None = 根の下を全部)を求めるため。entries が在れば、entries から import を
   静的に辿った、木をまたぐ閉包(source は module ごとに 1 度だけ読む — 読めない source は何も import しない物として扱う)。
   import の名は、保存先に中身の同じ source の entry が在ればそれを使い、無ければ構文を読んで保存先へ足す(頭の註の手順 2・#3858)。
   保存先へ書けなかった理由は Note に 1 度ずつ出す(次の準備が読み直すだけ)。"
  (if (not entries)
      (tuple (gfor _ trees None))
      (do (<- index (plan.module-index trees sources))
          (var seen (frozenset))
          (var frontier (frozenset entries))
          (var modules 0)
          (var reread 0)
          (var unstored #())
          (while frontier
            (<- found tuple (plan.closure-step index frontier seen))
            (:= seen (| seen (frozenset found)))
            (var imports #())
            (for [m found]
              (<- place tuple (plan.module-place index m))
              (val rel (get place 1))
              (<- read (| str FileFailed) (ReadText (posixpath.join (. (get trees (get place 0)) path) rel)))
              (match read
                (FileFailed) (:= imports (+ imports #(#())))
                text (do (:= modules (+ modules 1))
                         (<- stored (| tuple None) (stored-imports code-store rel text hy-version))
                         (var listed #())
                         (match stored
                           None (do (:= listed (plan.imported-names rel text))
                                    (:= reread (+ reread 1))
                                    (<- problem (| str None) (store-imports code-store rel text hy-version listed))
                                    (when (and (is-not problem None) (not-in problem unstored))
                                      (:= unstored (+ unstored #(problem)))))
                           _ (:= listed stored))
                         (:= imports (+ imports #(listed))))))
            (<- named frozenset (plan.imported-modules index found imports))
            (:= frontier named))
          (for [problem unstored]
            (<- (Note (.format "import の名を保存先へ書けない(次の準備が読み直す): {}" problem))))
          (<- (Note (.format "closure_modules={} closure_reread={}" modules reread)))
          (<- scopes tuple (plan.closure-scopes index seen (len trees)))
          scopes)))


(defk prepare-trees [trees revision jobs entries code-store hy-version]
  {:pre [(: trees tuple) (: revision str) (: jobs int) (: entries tuple) (: code-store (| str None)) (: hy-version str)] :post [(: % tuple)]
   :tags {:context "worker" :role "main"}}
  "全部の木を 1 回で準備し、木ごとの結果(TreeOutcome の列・trees の順)を返す(頭の註の手順 1〜4)。検めが通った木には完成の印を置き、
   通らない木は印を置かずに理由を結果に載せる。経過の秒は全体の報告の行にだけ載せる。code-store = 保存先の dir(None = 使わない)・
   hy-version = この root の Hy の版(import の名の entry の鍵)。"
  (<- started float (GetMonotonic))
  (var scans #())
  (for [tree trees]
    (<- scanned tuple (ScanTree tree.path))
    (:= scans (+ scans #(scanned))))
  (<- scanned-at float (GetMonotonic))
  (<- scopes tuple (closure-of-trees trees (tuple (gfor s scans (tuple (get s 0)))) entries code-store hy-version))
  (<- closed float (GetMonotonic))
  ;; 木ごとの用意する物の計画と、その大きさ(読めない source は大きさ 0 — 焼きが理由つきで断る)。全部の木の物を大きい順に 1 つの pool で用意する。
  (var plans #())
  (var items #())
  (for [#(tree scanned scope) (zip trees scans scopes)]
    (<- sources list (plan.scoped-sources (get scanned 0) scope))
    (<- tree-plan list (compile-plan sources (frozenset (get scanned 1)) tree.roots))
    (:= plans (+ plans #(tree-plan)))
    (for [#(rel name) tree-plan]
      (<- stat (| PathStat FileFailed) (StatPath (posixpath.join tree.path rel)))
      (:= items (+ items #((plan.BakeItem :tree tree.path :rel rel :name name :size (match stat (PathStat :size size) size _ 0)))))))
  (<- order tuple (plan.bake-order items))
  (<- paths tuple (plan.trees-import-path trees))
  (<- answer plan.BakeAnswer (BakeSources order jobs paths code-store))
  (val failures answer.failed)
  (<- baked float (GetMonotonic))
  (for [#(path rel reason) (cut failures 0 20)]
    (<- (Note f"  焼けない: {path}/{rel}: {reason}")))
  (for [reason answer.unstored]
    (<- (Note (.format "焼いた code を保存先へ書けない(次の準備が焼き直す): {}" reason))))
  ;; 検め: 用意した結果を木から読み直す(焼きの答えを信じず、置かれた物を数える)。
  (var outcomes #())
  (for [#(tree scope tree-plan) (zip trees scopes plans)]
    (<- failed list (plan.tree-failures failures tree.path))
    (<- after tuple (ScanTree tree.path))
    (<- after-sources list (plan.scoped-sources (get after 0) scope))
    (val after-pycs (frozenset (get after 1)))
    (<- problem (| str None) (tree-problem after-sources after-pycs (frozenset (gfor f failed (get f 0))) tree.roots))
    (if (is problem None)
        (do (<- marker dict (marker-content revision True after-sources after-pycs failed tree.roots))
            (<- (WriteMarker tree.path marker)))
        (<- (Note (.format "検めが通らないので完成の印を置きません: {}: {}" tree.named problem))))
    (<- stored int (plan.tree-count answer.stored tree.path))
    (<- reused int (plan.tree-count answer.reused tree.path))
    (val outcome (plan.TreeOutcome :named tree.named :stored stored :rebuilt (- (len tree-plan) (len failed) reused stored)
                                   :reused reused :failed (len failed) :problem problem))
    (<- line str (plan.tree-line outcome))
    (<- (Note line))
    (:= outcomes (+ outcomes #(outcome))))
  (val summary (plan.BakeSummary :trees outcomes :scan-s (- scanned-at started) :closure-s (- closed scanned-at)
                                 :compile-s (- baked closed)))
  (<- total str (plan.total-line summary))
  (<- (Note total))
  outcomes)


;; --- 焼きの効果の訳し --------------------------------------------------------------------
;; 焼きの道具 foundation/bytecode_pool.hy はこの file と同じく準備する root の venv の python で起こす(root の側から import するのは
;; python_bytecode と doeff-hy の bytecode-is-current だけ・保存先の入口は worker の版の code_store)。用意する物を標準入力で渡し、
;; 結果の行を標準出力から読む。

(defhandler pool-tool-baker
  ;; 焼きの効果を、焼きの道具の子 process(この process と同じ interpreter)に訳す(main が被せる・子 process の効果は外側の
  ;; subprocess-handler が答える)。道具が 0 でない終わりで止まれば、焼きの失敗でなく道具の失敗として落ちる。
  (BakeSources [items jobs paths code-store]
    ;; interpreter は venv の prefix から求める(hy の起動は sys.executable を hy の入口に差し替えるので、sys.executable では起こせない)。
    (<- facts InterpreterFacts (ReadInterpreter))
    (<- argv tuple (plan.bake-argv (posixpath.join facts.prefix "bin" "python") POOL-TOOL jobs paths CODE-STORE code-store))
    (<- text str (plan.bake-input items))
    (<- outcome ProcessOutcome (RunProcess :argv argv :stdin text))
    (when (!= outcome.exit-code 0)
      (raise (RuntimeError (.format "焼きの道具が終わり {} で止まった: {}" outcome.exit-code
                                    (cut (+ outcome.stderr outcome.start-error) -2000 None)))))
    ;; 道具の stderr の行(保存先の壊れた entry の名指しなど — code_store が pool の子で出す)は、この道具の報告の行と並べて出す。
    (for [line (.splitlines outcome.stderr)]
      (when (.strip line)
        (<- (Note line))))
    (<- answer plan.BakeAnswer (plan.bake-answer outcome.stdout))
    (resume answer)))


;; --- 保存先の entry の効果の訳し ------------------------------------------------------------------
;; 保存先の entry の読み書きは保存先の入口 code_store の関数そのもの(使った印・一時の file からの置き換え・壊れた entry の名指しの定義点)。

(defhandler store-entries
  ;; 保存先の entry の効果を、worker の版の code_store の関数で答える(main が被せる)。
  (ReadStoreEntry [path]
    (resume (store.read-entry path)))
  (WriteStoreEntry [path data]
    (resume (store.write-entry path data)))
  (DiscardStoreEntry [path problem]
    (resume (store.discard-entry path problem))))


;; --- 入口 --------------------------------------------------------------------------------

(defn #^ None main []  ; defk にできない: console の入口(素の関数として呼ばれる)
  ;; file の読み(cgroup の上限・変わった path の一覧・木の path の解き)は本物の file の答え手(os-file-handler)の下で。
  (setv default-jobs (run (with-handlers [os-file-handler] (usable-cpus))))
  (setv parser (argparse.ArgumentParser))
  (.add-argument parser "--revision" :required True)
  (.add-argument parser "--jobs" :type int :default default-jobs :help "焼きの並列数(既定 = cgroup の CPU の上限)")
  (.add-argument parser "--entries" :default "" :help "焼く範囲の入口の module(`,` で並べる・空 = 全部の木の根の下を全部)")
  (.add-argument parser "--tree" :action "append" :required True :help "焼く木(幾つでも — 木ごとの引数はこの順に並べる)")
  (.add-argument parser "--roots" :action "append" :help "木の中の import の根(`,` で並べる・前が先 — 木ごとに 1 つ)")
  (setv args (.parse-args parser))
  (setv shaped (run (plan.tree-arguments (tuple args.tree) (tuple (or args.roots [])))))
  (when (isinstance shaped str)
    (.error parser shaped))
  ;; 焼く途中の import(Hy の require 等)が timestamp 方式の .pyc を書かないようにする。
  (setv sys.dont-write-bytecode True)
  ;; file の path で起動すると、道具の dir(worker 自身のコードの doeff_cluster/worker/entry)が sys.path の先頭に入る。
  ;; 焼く木の module 名がそこで解けてしまわないよう外す。
  (setv here (. (.resolve (Path __file__)) parent))
  (setv (cut sys.path) (lfor p sys.path :if (not (and p (= (.resolve (Path p)) here))) p))
  (setv trees (run (with-handlers [os-file-handler] (bake-trees shaped))))
  (setv entries (tuple (gfor e (.split args.entries ",") :if e e)))
  ;; 木の効果は言い換え tree-files(worker/protocol/tree_files)が汎用の file の効果へ出し直し、本物の os-file-handler が答える(#2468)。
  ;; Note の行は slog-handler が stderr へ出す(worker が木ごとの問題と合計を読む)。
  ;; 保存先の dir は環境変数で決まる(code_store の store-dir — import の読みの口と同じ 1 つの解き方)。import の名の entry の鍵は、この root の
  ;; venv の Hy の版で読みが変わりうるので Hy の版を入れる。
  (setv code-store (store.store-dir))
  (setv outcomes (run (with-handlers [(sync-time-handler) slog-handler os-file-handler subprocess-handler tree-files pool-tool-baker
                                      store-entries]
                                     (prepare-trees trees args.revision args.jobs entries code-store hy.__version__))))
  (setv failed (tuple (gfor t outcomes :if (is-not t.problem None) t)))
  (for [t failed]
    (print (.format "準備に失敗: {}: {}" t.named t.problem) :file sys.stderr :flush True))
  (when failed
    (sys.exit 1)))


(when (= __name__ "__main__")
  (main))
