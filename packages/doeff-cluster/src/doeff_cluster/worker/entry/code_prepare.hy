;;; 展開したコードの木の bytecode を、木の中だけに「実行時に source の hash を検める」方式で用意する道具。1 回の起動で幾つもの木を焼く。
;;;
;;; worker は版ごとに木を展開する。前の版の木から、中身の変わっていない file の .pyc を hardlink で
;;; 引き継ぎ、残りだけを焼く。検める方式(PEP 552 の checked hash)なので、引き継ぎを誤っても import が
;;; source の hash を突き合わせて焼き直す — 古い bytecode が黙って使われることはない。
;;; 共有の venv・doeff・標準 library には書かない(本番の image の焼き方 deploy/bytecode.py は木の外も歩き、
;;; 実行時に検めない方式で焼くので、中身の動く手元の環境には使えない)。
;;;
;;;   PYTHONDONTWRITEBYTECODE=1 hy <worker のコード>/doeff_cluster/worker/entry/code_prepare.hy --revision <版> [--jobs N]
;;;       [--entries <module,…>] --tree <木> --roots <根,…> [--from <前の木>] [--changed <変わった path の一覧 file>] [--tree … --roots … …]
;;;
;;; 木ごとの引数の揃え方(1 つだけ): --tree・--roots・--from・--changed はどれも --tree の順に並べる。--roots は木ごとに必ず 1 つ
;;; (import の根 — 木の中の dir・`,` で並べる・前が先)。--from と --changed は、どの木にも無ければ書かず、書くなら木ごとに 1 つずつ
;;; (その木に無ければ空文字 "")。数が揃わなければ使い方の誤り(終わり 2)。
;;;
;;; 段取り(全部の木を 1 回の process で — 木ごとに順に起こすと、木 1 つの終わりを待つ間ほかの core が遊ぶ):
;;;   1 走査      木ごとに ScanTree
;;;   2 閉包      --entries を渡すと、その module から import を静的に辿り(Hy の import / require と Python の import)、どの木の
;;;               module にも解ける所まで辿った閉包だけを焼く(ある木の module が import する別の木の module も入る)。木の外の module
;;;               (標準・第三者)は辿らない。閉包の外の module は子が import した時に作られる(焼く物が減るだけで正しさは変わらない)。
;;;               --entries が無ければ全部の木の根の下を全部焼く。
;;;               import の名は構文の読み(Hy の read-many・Python の ast.parse — 閉包の秒の大半)で求める。歩みの後に木ごとの根へ
;;;               import の名の表(bake_plan の IMPORT-TABLE — source の相対 path → その source の sha256 と import の名)を残し、次の版は
;;;               引き継ぎ元(--from)の表のうち --changed に無く sha256 が今の source と合う行を使い回して、変わった file だけを読む
;;;               (#3694)。表の無い引き継ぎ元・形の違う表・表に無い file は今どおり読む。
;;;   3 引き継ぎ   木ごとに前の木から .pyc を hardlink する(LinkPycs)。前の木の .pyc ごとに頭の 16 byte(PEP 552 の magic と flags)を
;;;               読み(汎用の file の効果 ReadBytes)、import が新しい木でそのまま使う物 — checked hash の方式で magic が今の Python と同じ
;;;               物 — だけを引き継ぐ(#3727 — 子が import の時に書いた timestamp の方式の物や古い Python の物は、新しい木では import の
;;;               時に compile される。引き継がずに焼く一覧に残し、焼いた数 rebuilt に入る)。
;;;   4 焼き      焼く物を全部の木から集め、source の大きい順に 1 つの process の pool へ 1 つずつ渡す(BakeSources — 答え手は焼きの
;;;               道具 foundation/bytecode_pool.hy を子 process で起こす)— 1 file の秒の偏りが大きい(大半は Hy の macro の展開)ので、
;;;               名の順・束で渡すと最後に遅い file を 1 core で待つ。並列数の既定は cgroup の CPU の上限(pod の limits)。
;;;               焼く物のうち、引き継いだ .pyc が今の source の hash と今の環境の macro の記録に合う物は、道具が pool へ送らずに残す
;;;               (reused — #3675)。
;;;   5 検めと印   木ごとに焼いた後を走査し直し、焼くべき source ごとに .pyc が在ること(焼けなかった file は理由つきで印に載せる)を
;;;               検めてから、木の根に完成の印(MARKER)を置く。検めが通らない木には印を置かない。
;;;
;;; 報告(stderr の slog の行): 木ごとに `tree=<--tree の綴り> carried=N rebuilt=N reused=N failed=N problem=<文|->`、最後に全体の
;;; `carried=N rebuilt=N reused=N failed=N carry_s=… compile_s=… closure_s=… scan_s=…`(carried = 前の木から hardlink した .pyc・焼く計画の
;;; file のうち rebuilt = 焼いた・reused = 焼かずに残した・failed = 焼けなかった — 3 つの和が焼く計画の数・scan_s = 木の走査・closure_s =
;;; 閉包の歩み)。--entries の在る時は閉包の歩みの後に `closure_modules=N closure_reread=N`(閉包の source の数・そのうち構文を読み
;;; 直した数)。検めの通らない木が 1 つでも在れば終わり 1(ほかの木の印は置く)。
;;; 実行環境の準備(worker/protocol/env_translation)は木ごとの行を読み、版ごとのコードの木の準備(worker/core/code_rules の script)は印の
;;; 有無を確かめる。
;;;
;;; 形: 純粋な判断と record(引数の揃え・閉包の歩み・引き継ぐ .pyc・焼く順・報告の行)は worker/core/bake_plan.hy、走査・hardlink・印は木の効果
;;; (worker/protocol/tree_files の tree-files が汎用の file の効果へ出し直す — #2468)、焼きはこの file の効果 BakeSources(答え手
;;; pool-tool-baker が焼きの道具を汎用の子 process の効果 RunProcess で起こす — 生の process の pool は foundation の層だけが持つ)。
;;; main が本物の os-file-handler と subprocess-handler を被せる。経過の秒は doeff-time の GetMonotonic(main が sync-time-handler を被せる)。
;;;
;;; 版に依らず効く形: この file は worker の版の file だが、準備する root の venv の python と import の路で走る(import する doeff は
;;; root の版)。だから段取りはこの file に置き、root の側から import するのは、cluster で動く job の doeff の版から変わっていない部品の
;;; 名前と引数の形だけ(code_plan の compile-plan・marker-content・tree-problem と bake_plan が読む carry-pairs、code_model の ScanTree・
;;; LinkPycs・WriteMarker・Note、tree_files の tree-files、file_effects・process_effects の効果と file-done、os_file・os_process・handlers・
;;; doeff-time の答え手)— 引き継ぐ .pyc の頭の判断(#3727)も、root の側の carry-pairs と ScanTree の答えの形を変えずに足した(頭は
;;; この file が ReadBytes で読み、bake_plan の carried-pycs が頭で絞ってから carry-pairs へ渡す)。判断の module bake_plan と焼きの道具
;;; bytecode_pool も worker の版の file なので、package の import でなく、この file の位置から求めた path で読む・起こす(package の名
;;; doeff_cluster.worker.core.bake_plan で引くと root の版の doeff の物になり、この module を持たない古い版の root で落ちる)。
;;; 古い入口: root の側の worker/core/code_prepare の prepare-tree と python_bytecode の compile-python-sources・prepare-compile-path は、
;;; 版を上げていない worker の古い entry が呼ぶので名前と引数の形を変えない(消すのは全 worker の版上げの後の別の変更)。
;;;
;;; 道具は worker 自身のコードから file の path で起動する(準備する版の木から -m で起動すると、道具を持たない
;;; 古い版では道具が見つからない — 2026-09-23 に atlas の版 8d7181f が bytecode 0 のまま完成品になった原因)。
(require doeff-hy.macros [defk defhandler val var <-])
(val MODULE-TAGS {:context "worker" :role "main"})
(import argparse)
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
(import doeff_core_effects.file_effects [FileFailed PathStat ReadBytes ReadText StatPath WriteText file-done])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.process_effects [InterpreterFacts ProcessOutcome ReadInterpreter RunProcess])
(import doeff_cluster.worker.core.code_plan [compile-plan marker-content tree-problem])
(import doeff_cluster.worker.intent.code_model [ScanTree LinkPycs WriteMarker Note])
(import doeff_cluster.worker.protocol.tree_files [tree-files])


;; --- worker の版の file の置き場 ---------------------------------------------------------------

;; worker 自身のコードの doeff_cluster の dir(この file は doeff_cluster/worker/entry/ に在る)。
(val WORKER-CODE (. (Path __file__) (resolve) parent parent parent))
;; 判断の module と、その module 名(package の名と重ならない名 — package の名で引くと root の版の物になる)。
(val BAKE-PLAN (str (/ WORKER-CODE "worker" "core" "bake_plan.hy")))
(val BAKE-PLAN-MODULE "doeff_cluster_worker_bake_plan")
;; 焼きの道具(生の process の pool を持つ foundation の console の道具)。
(val POOL-TOOL (str (/ WORKER-CODE "foundation" "bytecode_pool.hy")))


(defk module-at [name path]
  {:pre [(: name str) (: path str)] :post [(: % ModuleType)] :tags {:context "worker" :role "main"}}
  "worker 自身のコードの Hy の file を、package の import でなく path で module として読むため(頭の註の「版に依らず効く形」)。name は
   sys.modules に置く名(record の dataclass が自分の module を引くため)。"
  (val loader (importlib.machinery.SourceFileLoader name path))
  (match (importlib.util.spec-from-file-location name path :loader loader)
    None (raise (ImportError (.format "{} を module として読めない" path)))
    spec (do (val module (importlib.util.module-from-spec spec))
             (setv (get sys.modules name) module)
             (.exec-module loader module)
             module)))


;; 純粋な判断と record(worker/core/bake_plan.hy — 名は plan.<名> で引く)。
(val plan (run (module-at BAKE-PLAN-MODULE BAKE-PLAN)))


;; --- 焼きの並列数と変わった path ---------------------------------------------------------------

(defk usable-cpus []
  {:pre [] :post [(: % int)] :tags {:context "worker" :role "main"}}
  "この process が使える CPU の数(affinity と cgroup の上限の小さい方)— 焼きの並列数の既定。cgroup の cpu.max は file の効果で読む
   (答え手 = 入口の os-file-handler・無い / 読めない = 上限なし)。"
  (val available (if (hasattr os "sched_getaffinity") (len (os.sched-getaffinity 0)) (or (os.cpu-count) 1)))
  (<- cpu-max (ReadText "/sys/fs/cgroup/cpu.max"))
  (<- limit int (plan.cpu-limit-of (if (isinstance cpu-max str) cpu-max None) available))
  limit)


(defk changed-paths [path]
  {:pre [(: path str)] :post [(: % frozenset)] :tags {:context "worker" :role "main"}}
  "前の版の木から変わった path の一覧(--changed の file・空白で区切る)を読むため — 引き継がない file を決める材料。読めなければ OSError。"
  (<- text (file-done (ReadText path)))
  (frozenset (.split text)))


;; --- 焼きの効果 --------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] BakeSources [EffectBase]
  "焼く物を 1 つの process の pool で焼く。items = #(木の path 相対 path module 名) の列(この順に pool へ渡す)・jobs = 並列数・
   paths = 焼く process の import の路の先頭に足す dir(前が先)。答え = bake_plan の BakeAnswer(焼けなかった物と、在る .pyc が今の source
   と macro に合うので焼かずに残した物)。"
  (#^ tuple items)
  (#^ int jobs)
  (#^ tuple paths))


;; --- Program -----------------------------------------------------------------------------

(defk bake-trees [shaped]
  {:pre [(: shaped tuple)] :post [(: % tuple)] :tags {:context "worker" :role "main"}}
  "揃えた木の組(TreeArgs の列)を焼く木(BakeTree の列)にするため: 木と前の木の path を symlink を辿った絶対 path にし、変わった path の
   一覧の file を読む(読めなければ OSError)。"
  (var trees #())
  (for [given shaped]
    (<- seen PathStat (file-done (StatPath given.named)))
    (var old None)
    (when (is-not given.old None)
      (<- old-seen PathStat (file-done (StatPath given.old)))
      (:= old old-seen.real-path))
    (var changed (frozenset))
    (when (is-not given.changed None)
      (<- listed frozenset (changed-paths given.changed))
      (:= changed listed))
    (:= trees (+ trees #((plan.BakeTree :named given.named :path seen.real-path :roots given.roots :old old :changed changed)))))
  trees)


(defk old-import-table [old]
  {:pre [(: old (| str None))] :post [(: % plan.ImportTable)] :tags {:context "worker" :role "main"}}
  "引き継ぎ元の木(old — 無ければ None)の根に残る import の名の表を読むため(#3694)。木が無い・表が無い・読めない時は行 0 で理由つき
   (閉包の source を全部読む)。"
  (var text None)
  (when (is-not old None)
    (<- read (| str FileFailed) (ReadText (posixpath.join old plan.IMPORT-TABLE)))
    (match read
      (FileFailed) None
      found (:= text found)))
  (<- table plan.ImportTable (plan.import-table-of text))
  table)


(defk old-pyc-heads [old pycs]
  {:pre [(: old str) (: pycs tuple)] :post [(: % tuple)] :tags {:context "worker" :role "main"}}
  "引き継ぎ元の木(old)の .pyc(pycs — 走査の相対 path の列)ごとに頭の 16 byte を読み、PycHead の列(pycs の順)にするため — 引き継ぐ
   物を検めの方式と magic で選ぶ材料(#3727)。読めない .pyc は方式 UNREADABLE(引き継がない)。"
  (var heads #())
  (for [rel pycs]
    (<- read (| bytes FileFailed) (ReadBytes (posixpath.join old rel) :limit plan.PYC-HEAD-BYTES))
    (<- head plan.PycHead (plan.pyc-head-of rel (match read (FileFailed) None found found)))
    (:= heads (+ heads #(head))))
  heads)


(defk closure-of-trees [trees sources entries]
  {:pre [(: trees tuple) (: sources tuple) (: entries tuple)] :post [(: % tuple)] :tags {:context "worker" :role "main"}}
  "木ごとの焼く範囲(trees と同じ順の、相対 path の frozenset か None = 根の下を全部)を求めるため。entries が在れば、entries から import を
   静的に辿った、木をまたぐ閉包(source は module ごとに 1 度だけ読む — 読めない source は何も import しない物として扱う)。
   import の名は、引き継ぎ元の木の表の行のうち --changed に無く sha256 が今の source と合う物を使い回し、ほかは構文を読む(#3694)。
   歩みの後に、木ごとに今の閉包の source の行を表として木の根に残す(書けなければ Note — 次の版が全部を読み直すだけ)。"
  (if (not entries)
      (tuple (gfor _ trees None))
      (do (<- index (plan.module-index trees sources))
          (var tables #())
          (for [tree trees]
            (<- table plan.ImportTable (old-import-table tree.old))
            (when (and (is-not tree.old None) (is-not table.problem None))
              (<- (Note (.format "import の名の表を使わない(閉包の source を全部読む): {}: {}" tree.named table.problem))))
            (:= tables (+ tables #(table))))
          (var seen (frozenset))
          (var frontier (frozenset entries))
          (var rows #())
          (var reread 0)
          (while frontier
            (<- found tuple (plan.closure-step index frontier seen))
            (:= seen (| seen (frozenset found)))
            (var imports #())
            (for [m found]
              (<- place tuple (plan.module-place index m))
              (<- read (| str FileFailed) (ReadText (posixpath.join (. (get trees (get place 0)) path) (get place 1))))
              (match read
                (FileFailed) (:= imports (+ imports #(#())))
                text (do (<- digest str (plan.source-digest text))
                         (<- usable (| plan.ImportRow None)
                             (plan.usable-row (get tables (get place 0)) (get place 1) digest (. (get trees (get place 0)) changed)))
                         (var listed #())
                         (match usable
                           (plan.ImportRow :imports carried) (:= listed carried)
                           None (do (:= listed (plan.imported-names (get place 1) text))
                                    (:= reread (+ reread 1))))
                         (:= imports (+ imports #(listed)))
                         (:= rows (+ rows #(#((get place 0) (plan.ImportRow :rel (get place 1) :digest digest :imports listed))))))))
            (<- named frozenset (plan.imported-modules index found imports))
            (:= frontier named))
          (for [#(i tree) (enumerate trees)]
            (<- text str (plan.import-table-text (tuple (gfor r rows :if (= (get r 0) i) (get r 1)))))
            (<- wrote (| FileFailed None) (WriteText (posixpath.join tree.path plan.IMPORT-TABLE) text :replace True))
            (match wrote
              (FileFailed :reason reason) (<- (Note (.format "import の名の表を書けない: {}: {}" tree.named reason)))
              _ None))
          (<- (Note (.format "closure_modules={} closure_reread={}" (len rows) reread)))
          (<- scopes tuple (plan.closure-scopes index seen (len trees)))
          scopes)))


(defk prepare-trees [trees revision jobs entries]
  {:pre [(: trees tuple) (: revision str) (: jobs int) (: entries tuple)] :post [(: % tuple)] :tags {:context "worker" :role "main"}}
  "全部の木を 1 回で準備し、木ごとの結果(TreeOutcome の列・trees の順)を返す(頭の註の段取り 1〜5)。検めが通った木には完成の印を置き、
   通らない木は印を置かずに理由を結果に載せる。経過の秒は全体の報告の行にだけ載せる。"
  (<- started float (GetMonotonic))
  (var scans #())
  (for [tree trees]
    (<- scanned tuple (ScanTree tree.path))
    (:= scans (+ scans #(scanned))))
  (<- scanned-at float (GetMonotonic))
  (<- scopes tuple (closure-of-trees trees (tuple (gfor s scans (tuple (get s 0)))) entries))
  (<- closed float (GetMonotonic))
  ;; 引き継ぎと、木ごとの焼く物の計画。
  (var linked #())
  (var plans #())
  (for [#(tree scanned scope) (zip trees scans scopes)]
    (<- sources list (plan.scoped-sources (get scanned 0) scope))
    (var pycs (list (get scanned 1)))
    (var carried 0)
    (when (is-not tree.old None)
      (<- old-scan tuple (ScanTree tree.old))
      (<- heads tuple (old-pyc-heads tree.old (tuple (get old-scan 1))))
      (<- pairs list (plan.carried-pycs heads importlib.util.MAGIC-NUMBER (frozenset (get old-scan 0)) (frozenset sources) (frozenset pycs)
                                        tree.changed))
      (<- hardlinked int (LinkPycs tree.old tree.path (tuple pairs)))
      (:= carried hardlinked)
      (:= pycs (+ pycs pairs)))
    (<- tree-plan list (compile-plan sources (frozenset pycs) tree.roots))
    (:= linked (+ linked #(carried)))
    (:= plans (+ plans #(tree-plan))))
  (<- carried-at float (GetMonotonic))
  ;; 焼く物の大きさを読み、全部の木の焼く物を大きい順に 1 つの pool で焼く(読めない source は大きさ 0 — 焼きが理由つきで断る)。
  (var items #())
  (for [#(tree tree-plan) (zip trees plans)]
    (for [#(rel name) tree-plan]
      (<- stat (| PathStat FileFailed) (StatPath (posixpath.join tree.path rel)))
      (:= items (+ items #((plan.BakeItem :tree tree.path :rel rel :name name :size (match stat (PathStat :size size) size _ 0)))))))
  (<- order tuple (plan.bake-order items))
  (<- paths tuple (plan.trees-import-path trees))
  (<- answer plan.BakeAnswer (BakeSources order jobs paths))
  (val failures answer.failed)
  (<- baked float (GetMonotonic))
  (for [#(path rel reason) (cut failures 0 20)]
    (<- (Note f"  焼けない: {path}/{rel}: {reason}")))
  ;; 検め: 焼いた結果を木から読み直す(焼きの答えを信じず、置かれた物を数える)。
  (var outcomes #())
  (for [#(tree scope tree-carried tree-plan) (zip trees scopes linked plans)]
    (<- failed list (plan.tree-failures failures tree.path))
    (<- after tuple (ScanTree tree.path))
    (<- after-sources list (plan.scoped-sources (get after 0) scope))
    (val after-pycs (frozenset (get after 1)))
    (<- problem (| str None) (tree-problem after-sources after-pycs (frozenset (gfor f failed (get f 0))) tree.roots))
    (if (is problem None)
        (do (<- marker dict (marker-content revision True after-sources after-pycs failed tree.roots))
            (<- (WriteMarker tree.path marker)))
        (<- (Note (.format "検めが通らないので完成の印を置きません: {}: {}" tree.named problem))))
    (<- reused int (plan.tree-reused answer.reused tree.path))
    (val outcome (plan.TreeOutcome :named tree.named :carried tree-carried :rebuilt (- (len tree-plan) (len failed) reused)
                                   :reused reused :failed (len failed) :problem problem))
    (<- line str (plan.tree-line outcome))
    (<- (Note line))
    (:= outcomes (+ outcomes #(outcome))))
  (val summary (plan.BakeSummary :trees outcomes :scan-s (- scanned-at started) :closure-s (- closed scanned-at)
                                 :carry-s (- carried-at closed) :compile-s (- baked carried-at)))
  (<- total str (plan.total-line summary))
  (<- (Note total))
  outcomes)


;; --- 焼きの効果の訳し --------------------------------------------------------------------
;; 焼きの道具 foundation/bytecode_pool.hy はこの file と同じく準備する root の venv の python で起こす(import は root の側の compile-one
;; だけ)。焼く物を標準入力で渡し、焼けなかった物を標準出力から読む。

(defhandler pool-tool-baker
  ;; 焼きの効果を、焼きの道具の子 process(この process と同じ interpreter)に訳す(main が被せる・子 process の効果は外側の
  ;; subprocess-handler が答える)。道具が 0 でない終わりで止まれば、焼きの失敗でなく道具の失敗として落ちる。
  (BakeSources [items jobs paths]
    ;; interpreter は venv の prefix から求める(hy の起動は sys.executable を hy の入口に差し替えるので、sys.executable では起こせない)。
    (<- facts InterpreterFacts (ReadInterpreter))
    (<- argv tuple (plan.bake-argv (posixpath.join facts.prefix "bin" "python") POOL-TOOL jobs paths))
    (<- text str (plan.bake-input items))
    (<- outcome ProcessOutcome (RunProcess :argv argv :stdin text))
    (when (!= outcome.exit-code 0)
      (raise (RuntimeError (.format "焼きの道具が終わり {} で止まった: {}" outcome.exit-code
                                    (cut (+ outcome.stderr outcome.start-error) -2000 None)))))
    (<- answer plan.BakeAnswer (plan.bake-answer outcome.stdout))
    (resume answer)))


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
  (.add-argument parser "--from" :action "append" :dest "old" :help "引き継ぎ元の前の木(書くなら木ごとに 1 つ・無い木は \"\")")
  (.add-argument parser "--changed" :action "append" :help "前の木から変わった path の一覧の file(書くなら木ごとに 1 つ・無い木は \"\")")
  (setv args (.parse-args parser))
  (setv shaped (run (plan.tree-arguments (tuple args.tree) (tuple (or args.roots [])) (tuple (or args.old []))
                                         (tuple (or args.changed [])))))
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
  (setv outcomes (run (with-handlers [(sync-time-handler) slog-handler os-file-handler subprocess-handler tree-files pool-tool-baker]
                                     (prepare-trees trees args.revision args.jobs entries))))
  (setv failed (tuple (gfor t outcomes :if (is-not t.problem None) t)))
  (for [t failed]
    (print (.format "準備に失敗: {}: {}" t.named t.problem) :file sys.stderr :flush True))
  (when failed
    (sys.exit 1)))


(when (= __name__ "__main__")
  (main))
