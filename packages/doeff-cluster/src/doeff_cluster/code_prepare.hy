;;; 展開したコードの木の bytecode を、木の中だけに「実行時に source の hash を検める」方式で用意する。
;;;
;;; worker は版ごとに木を展開する。前の版の木から、中身の変わっていない file の .pyc を hardlink で
;;; 引き継ぎ、残りだけを焼く。検める方式(PEP 552 の checked hash)なので、引き継ぎを誤っても import が
;;; source の hash を突き合わせて焼き直す — 古い bytecode が黙って使われることはない。
;;; 共有の venv・doeff・標準 library には書かない(本番の image の焼き方 deploy/bytecode.py は木の外も歩き、
;;; 実行時に検めない方式で焼くので、中身の動く手元の環境には使えない)。
;;;
;;; 形: 判断(module 名・引き継ぐ組・焼く物)は純粋な関数、走査・hardlink・焼きは effect(handler は下の local-tree)。
;;; 経過の秒は doeff-time の GetMonotonic(main が sync-time-handler を被せる)。
;;;
;;; 完成の印: 焼いた後に木を走査し直し、焼くべき source ごとに .pyc が在ること(焼けなかった file は理由つきで
;;; 印に載せる)を検めてから、木の根に印の file(MARKER)を置く。検めが通らなければ印を置かず、0 でない終了で
;;; 終わる。worker(worker/protocol/code_store の code-host)は印の在る木だけを完成品として公開し、読む時にも印を検める。
;;;
;;; 道具は worker 自身のコードから file の path で起動する(準備する版の木から -m で起動すると、道具を持たない
;;; 古い版では道具が見つからない — 2026-09-23 に atlas の版 8d7181f が bytecode 0 のまま完成品になった原因)。
;;;
;;;   PYTHONDONTWRITEBYTECODE=1 hy <worker のコード>/doeff_cluster/code_prepare.hy <新しい木> --revision <版>
;;;       [--from <前の木> --changed <変わった path の一覧 file>] [--import-roots .,sub/dir]
;;;
;;; import の根(木の中の dir・`,` で並べる・既定 `.`)は業務の repo の形で、worker の CodeLayout(worker_model)が渡す。
;;;
;;; 焼く範囲(2026-09-26・#664 の実測): --entries <module,…> を渡すと、その module たちの import の閉包(Hy の import / require と
;;; Python の import を静的に辿る)だけを焼く。閉包の外の module は子が import した時に作られる(焼く物が減るだけで正しさは変わらない)。
;;; 並列数の既定は cgroup の CPU の上限(pod の limits)— node の CPU の数で焼くと、上限 4 の pod で 16 並列になり周期の 97% が絞られた。
(require doeff-hy.macros [defk defhandler <- val var])
(import argparse)
(import ast)
(import math)
(import dataclasses [dataclass])
(import importlib.machinery)
(import importlib.util)
(import json)
(import multiprocessing)
(import os)
(import sys)
(import concurrent.futures [ProcessPoolExecutor])
(import marshal)
(import types)
(import collections.abc [Callable])
(import pathlib [Path PurePosixPath])
(import doeff [EffectBase run])
(import doeff_time [GetMonotonic sync-time-handler])
(import doeff_core_effects.file_effects [PathKind PathStat StatPath ReadText ReadBytes WriteText WriteBytes MakeDirectory WalkTree CopyFile
                                         file-done])
(import doeff_cluster.worker.core.code_plan [MARKER cache-rel])
(import doeff_cluster.worker.intent.code_model [ScanTree LinkPycs CompileSources ImportClosure WriteMarker Note])
(import doeff_cluster.worker.core.code_prepare [prepare-tree tree-listing marker-text closure-of])


;; --- 純粋な判断 ------------------------------------------------------------------------


;; --- effect ----------------------------------------------------------------------------


;; --- Program ---------------------------------------------------------------------------


;; --- handler(実 I/O) ------------------------------------------------------------------

;; --- 本物(local-tree)と fake(files-tree)が同じく通る判断 ------------------------------------------


;; --- 焼き(本物の local-tree と模擬の files-tree が同じく通る — source を compile して PEP 552 の checked hash の .pyc を組む)--------

(defk compiled-pyc [rel name path data]
  {:pre [(: rel str) (: name str) (: path str) (: data bytes)] :post [(: % (| bytes tuple))] :tags {:context "doeff-cluster" :role "judgment"}}
  "source の中身 1 つを、import が検める方式(PEP 552 の checked hash)の .pyc の中身にするため。焼けない時は #(相対 path 理由)
   (import の時に同じ誤りが出るので、ここでは記録だけ)。path = source の在処(Hy の source かの見分けと、誤りの文に出る名)。"
  (try
    (val loader (importlib.machinery.SourceFileLoader name path))
    (val code (.source-to-code loader data path))
    (<- pyc bytes (checked-hash-pyc code data))
    pyc
    (except [error Exception]
      #(rel (.format "{}: {}" (. (type error) __name__) (cut (str error) 0 200))))))


;; PEP 552 の hash 方式の .pyc の頭の flags: bit 0 = hash 方式・bit 1 = import の時に source の hash を検める(checked)。
(val CHECKED-HASH-FLAGS 0b11)


(defk checked-hash-pyc [code data]
  {:pre [(: code types.CodeType) (: data bytes)] :post [(: % bytes)] :tags {:context "doeff-cluster" :role "judgment"}}
  "焼いた code を、import が source の hash で検める .pyc の中身にするため(PEP 552 — 頭 = magic・flags・source の hash 8 byte、
   続けて marshal した code)。標準の私的な実装 importlib._bootstrap_external._code_to_hash_pyc と同じ並びを公開の API で組む。"
  (+ importlib.util.MAGIC-NUMBER
     (.to-bytes CHECKED-HASH-FLAGS 4 "little")
     (importlib.util.source-hash data)
     (marshal.dumps code)))


;; --- 本物の file system の答え手の部品 ------------------------------------------------------------------

(defn #^ (| tuple None) compile-one [#^ str tree #^ str rel #^ str name]
  "1 file を焼く。焼けない時は #(相対 path 理由) を返す(焼きの判断は compiled-pyc — fake と同じ関数)。"
  (setv source (/ (Path tree) rel) data (.read-bytes source))
  (setv compiled (run (compiled-pyc rel name (str source) data)))
  (when (isinstance compiled tuple) (return compiled))
  (setv cache (Path (importlib.util.cache-from-source (str source))))
  (.mkdir cache.parent :parents True :exist-ok True)
  (setv tmp (.with-suffix cache (.format ".{}.tmp" (os.getpid))))
  (.write-bytes tmp compiled)
  (os.replace tmp cache)
  None)


(defn _init-pool [#^ str tree #^ tuple roots]
  (setv sys.dont-write-bytecode True)
  (for [root (reversed roots)]
    (.insert sys.path 0 (str (/ (Path tree) root)))))


(defn _compile-task [item]
  (compile-one #* item))


(defk scan [tree]
  {:pre [(: tree str)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "foundation"}}
  "本物の木を走査して #(source の列 .pyc の列) を答えるため(隠し dir の下へは入らない — どれを数えるかの判断は tree-listing)。"
  (val root (Path tree))
  (val rels [])
  (for [#(dirpath dirnames filenames) (os.walk root)]
    (val kept (lfor d dirnames :if (not (.startswith d ".")) d))
    ;; os.walk は dirnames の list そのものを見て降りる先を決めるので、中身を入れ替える。
    (.clear dirnames)
    (.extend dirnames kept)
    (val rel-dir (.as-posix (.relative-to (Path dirpath) root)))
    (for [name filenames]
      (.append rels (if (= rel-dir ".") name (+ rel-dir "/" name)))))
  (<- listed tuple (tree-listing rels))
  listed)


(defn #^ int cpu-limit-of [#^ (| str None) cpu-max #^ int available]
  "cgroup v2 の cpu.max の中身(\"<quota> <period>\" か \"max <period>\")と使える CPU の数 → 焼きの並列数(pod の上限を越えないため)。"
  (setv parts (if cpu-max (.split cpu-max) []))
  (if (and (= (len parts) 2) (!= (get parts 0) "max"))
      (max 1 (min available (math.ceil (/ (int (get parts 0)) (int (get parts 1))))))
      (max 1 available)))


(defn #^ int usable-cpus []
  "この process が使える CPU の数(affinity と cgroup の上限の小さい方)— 焼きの並列数の既定。"
  (setv available (if (hasattr os "sched_getaffinity") (len (os.sched-getaffinity 0)) (or (os.cpu-count) 1))
        path (Path "/sys/fs/cgroup/cpu.max"))
  (cpu-limit-of (if (.is-file path) (.read-text path) None) available))


(defk import-closure [tree sources entries roots]
  {:pre [(: tree str) (: sources (| list tuple)) (: entries tuple) (: roots tuple)] :post [(: % frozenset)]
   :tags {:context "doeff-cluster" :role "foundation"}}
  "本物の木の file を読んで、entries の import の閉包に入る source の相対 path を求めるため(辿り方の判断は closure-of)。"
  (<- closure frozenset (closure-of sources entries roots (fn [rel] (.read-text (/ (Path tree) rel) :encoding "utf-8" :errors "replace"))))
  closure)


(defn #^ int link-pycs [#^ str old #^ str new #^ tuple pycs]
  (setv count 0)
  (for [rel pycs]
    (setv target (/ (Path new) rel))
    (.mkdir target.parent :parents True :exist-ok True)
    (when (not (.exists target))
      ;; import が焼き直す時は別 file へ書いて置き換えるので、共有しても壊れない。
      (os.link (/ (Path old) rel) target)
      (+= count 1)))
  count)


(defn #^ list compile-sources [#^ str tree #^ tuple items #^ int jobs #^ tuple roots]
  (setv work (lfor #(rel name) items #(tree rel name)))
  (setv results
    (if (or (<= jobs 1) (<= (len work) 1))
        (lfor item work (_compile-task item))
        ;; 焼きは 1 file ずつ独立で CPU だけを使うので、process に分ける(Hy の macro 展開が大半)。
        ;; fork にする: spawn では子が Hy の module を import し直す前に関数を解けない。
        (with [pool (ProcessPoolExecutor :max-workers jobs :mp-context (multiprocessing.get-context "fork")
                                         :initializer _init-pool :initargs #(tree roots))]
          (list (.map pool _compile-task work :chunksize 4)))))
  (lfor r results :if (is-not r None) r))


(defk write-marker [tree content]
  {:pre [(: tree str) (: content dict)] :post [(: % None)] :tags {:context "doeff-cluster" :role "foundation"}}
  "完成の印を本物の木の根へ置くため(別の file へ書いて置き換える — 書きかけの印を読ませない)。"
  (val target (/ (Path tree) MARKER))
  (val tmp (/ (Path tree) (+ MARKER ".tmp")))
  (<- text str (marker-text content))
  (.write-text tmp text :encoding "utf-8")
  (os.replace tmp target)
  None)


(defhandler local-tree []
  ;; 本物: 木の走査・hardlink・焼きを os と process の pool で行う(本番の入口 = 下の main)。
  (ScanTree [tree]
    (<- found tuple (scan tree))
    (resume found))
  (LinkPycs [old new pycs] (resume (link-pycs old new pycs)))
  (ImportClosure [tree sources entries roots]
    (<- closure frozenset (import-closure tree sources entries roots))
    (resume closure))
  (CompileSources [tree items jobs roots] (resume (compile-sources tree items jobs roots)))
  (WriteMarker [tree content]
    (<- (write-marker tree content))
    (resume None))
  (Note [line] (print line :file sys.stderr :flush True) (resume None)))


;; --- fake: file system の effect の上の木(files-tree)--------------------------------------------------
;; 木の効果に、汎用の file system の effect(doeff_core_effects.file_effects)で答える。模擬の世界では memory-file-handler を外側に
;; 被せて、I/O なしで焼きの Program(prepare-tree)を走らせる。走査・閉包・焼き・印の中身の判断は本物と同じ関数(tree-listing・
;; closure-of・compiled-pyc・marker-text)を通る。本物との違い: hardlink の代わりに写す(中身は同じ)・焼きは並列にしない
;; (jobs を読まない)・焼きの間の import の路を足さない(焼く source の macro が木の中の別の module を require する時は本物だけが解ける)・
;; Note は捨てる(模擬の世界に stderr は無い)・Hy の source は焼けない(doeff-hy の _could_be_hy_src が os.path.isfile で Hy の source かを
;; 見るので、disk に無い source は Python として読まれ SyntaxError の失敗になる — 契約テスト test_tree_contract.hy の頭の註)。

(defk tree-file-rels [tree]
  {:pre [(: tree str)] :post [(: % list)] :tags {:context "doeff-cluster" :role "foundation"}}
  "木の下の file の相対 path を並べるため(無い木は空 — 本物の os.walk が無い dir で何も出さないのと同じ)。"
  (<- walked (WalkTree tree))
  (if (isinstance walked tuple)
      (lfor entry walked :if (= entry.kind PathKind.FILE) entry.name)
      []))


(defk copy-pycs [old new pycs]
  {:pre [(: old str) (: new str) (: pycs tuple)] :post [(: % int)] :tags {:context "doeff-cluster" :role "foundation"}}
  "前の木の .pyc を新しい木の同じ相対 path へ写し、写した数を返すため(写し先が在れば写さない — 本物の hardlink の代わり)。"
  (var count 0)
  (for [rel pycs]
    (val target (os.path.join new rel))
    (<- (file-done (MakeDirectory (os.path.dirname target))))
    (<- found (file-done (StatPath target)))
    (match found
      (PathStat :kind PathKind.MISSING) (do (<- (file-done (CopyFile (os.path.join old rel) target)))
                                            (:= count (+ count 1)))
      _ None))
  count)


(defk source-texts [tree sources]
  {:pre [(: tree str) (: sources (| list tuple))] :post [(: % dict)] :tags {:context "doeff-cluster" :role "foundation"}}
  "木の source の相対 path → text を読むため(閉包の辿りに渡す)。"
  (val texts {})
  (for [rel sources]
    (<- text (file-done (ReadText (os.path.join tree rel))))
    (.update texts {rel text}))
  texts)


(defk place-compiled [tree rel name data]
  {:pre [(: tree str) (: rel str) (: name str) (: data bytes)] :post [(: % (| tuple None))] :tags {:context "doeff-cluster" :role "foundation"}}
  "source 1 つの中身を compiled-pyc で焼いて __pycache__ へ置くため(焼けなければ置かずに #(相対 path 理由) を返す)。"
  (<- compiled (compiled-pyc rel name (os.path.join tree rel) data))
  (match compiled
    (bytes) (do (val cache (os.path.join tree (cache-rel rel)))
                (<- (file-done (MakeDirectory (os.path.dirname cache))))
                (<- (file-done (WriteBytes cache compiled :replace True)))
                None)
    failure failure))


(defk compile-in-files [tree items]
  {:pre [(: tree str) (: items tuple)] :post [(: % list)] :tags {:context "doeff-cluster" :role "foundation"}}
  "焼く物を読んで place-compiled で焼き、焼けなかった物の #(相対 path 理由) の list を返すため。"
  (val failures [])
  (for [#(rel name) items]
    (<- data (file-done (ReadBytes (os.path.join tree rel))))
    (match data
      (bytes) (do (<- failure (place-compiled tree rel name data))
                  (when (is-not failure None)
                    (.append failures failure)))
      other (raise (TypeError (.format "ReadBytes の答えが bytes でない: {!r}" other)))))
  failures)


(defhandler files-tree
  ;; fake(上の註): 木の効果を file system の effect へ出し直す。
  (ScanTree [tree]
    (<- rels list (tree-file-rels tree))
    (<- listed tuple (tree-listing rels))
    (resume listed))
  (LinkPycs [old new pycs]
    (<- copied int (copy-pycs old new pycs))
    (resume copied))
  (ImportClosure [tree sources entries roots]
    (<- texts dict (source-texts tree sources))
    (<- closure frozenset (closure-of sources entries roots (fn [rel] (get texts rel))))
    (resume closure))
  (CompileSources [tree items jobs roots]
    (<- failures list (compile-in-files tree items))
    (resume failures))
  (WriteMarker [tree content]
    (<- text str (marker-text content))
    (<- (file-done (WriteText (os.path.join tree MARKER) text :replace True)))
    (resume None))
  (Note [line]
    (resume None)))


(defn #^ None main []
  (setv parser (argparse.ArgumentParser))
  (.add-argument parser "tree")
  (.add-argument parser "--revision" :required True)
  (.add-argument parser "--from" :dest "old")
  (.add-argument parser "--changed")
  (.add-argument parser "--jobs" :type int :default (usable-cpus) :help "焼きの並列数(既定 = cgroup の CPU の上限)")
  (.add-argument parser "--entries" :default "" :help "焼く範囲の入口の module(`,` で並べる・空 = 根の下を全部)")
  (.add-argument parser "--import-roots" :default "." :help "木の中の import の根(`,` で並べる・前が先)")
  (setv args (.parse-args parser))
  (setv roots (tuple (gfor r (.split args.import-roots ",") :if r r)))
  (setv tree (str (.resolve (Path args.tree))))
  ;; 焼く途中の import(Hy の require 等)が timestamp 方式の .pyc を書かないようにする。
  (setv sys.dont-write-bytecode True)
  ;; file の path で起動すると、道具の dir(worker 自身のコードの doeff_cluster)が sys.path の先頭に入る。
  ;; 焼く木の module 名がそこで解けてしまわないよう外す。
  (setv here (. (.resolve (Path __file__)) parent))
  (setv (cut sys.path) (lfor p sys.path :if (not (and p (= (.resolve (Path p)) here))) p))
  (_init-pool tree roots)
  (setv changed (frozenset (if args.changed (.split (.read-text (Path args.changed))) [])))
  (setv old (if args.old (str (.resolve (Path args.old))) None))
  (setv entries (tuple (gfor e (.split args.entries ",") :if e e)))
  (setv summary (run ((sync-time-handler) ((local-tree) (prepare-tree tree args.revision old changed args.jobs roots entries)))))
  (when (is-not (get summary "problem") None)
    (print (+ "準備に失敗: " (get summary "problem")) :file sys.stderr :flush True)
    (sys.exit 1)))


(when (= __name__ "__main__")
  (main))
