;;; bytecode の準備の道具(worker/entry/code_prepare.hy)が全部の木を 1 回で焼く段取りの純粋な判断と、その record — 命令の木ごとの引数の
;;; 揃え・焼きの並列数・木をまたいだ import の閉包の歩み・焼く順(大きい順)・焼きの道具への受け渡しの行・報告の行。I/O と効果は持たない。
;;;
;;; 読まれ方: 入口はこの file を package の import でなく、入口の file の位置から求めた path で読む(module 名 doeff_cluster_worker_bake_plan
;;; — 入口は準備する root の venv の python で走るので、package で引くと root の版の doeff の物になり、この file を持たない古い版の root で
;;; 落ちる)。だからこの file が import してよいのも、cluster で動く job の doeff の版から変わっていない部品(code_plan の module-name・
;;; imported-names と doeff-hy の macro)と標準 library だけ。検は package の名で普通に import して判断を検める。
(require doeff-hy.macros [defk val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import bisect)
(import math)
(import posixpath)
(import dataclasses [dataclass])  ; dataclass は defrecord の展開が使う
(import doeff_cluster.worker.core.code_plan [imported-names module-name])


;; --- record ------------------------------------------------------------------------------

(defrecord TreeArgs
  "命令の木 1 つの引数(揃えた後): named = --tree の綴り・roots = --roots の根(前が先)・old = --from(無ければ None)・
   changed = --changed の file(無ければ None)。"
  (#^ str named)
  (#^ tuple roots)
  (#^ (| str None) old)
  (#^ (| str None) changed))


(defrecord BakeTree
  "焼く木 1 つ。named = 命令に書かれた木の綴り(報告の行の名 — 呼び手はこの綴りで木を引き当てる)・path = symlink を辿った絶対 path・
   roots = 木の中の import の根(前が先)・old = 引き継ぎ元の前の木の絶対 path か None・changed = 前の木から変わった相対 path。"
  (#^ str named)
  (#^ str path)
  (#^ tuple roots)
  (#^ (| str None) old)
  (#^ frozenset changed))


(defrecord ModuleIndex
  "木をまたいだ module の索引: names = module 名(名の順)・places = 同じ順の #(木の番号 相対 path)。同じ名が幾つもの source に在れば、
   import の路の前の木(番号の小さい方)・同じ木の中では相対 path の名の順の先を採る。"
  (#^ tuple names)
  (#^ tuple places))


(defrecord BakeItem
  "焼く物 1 つ: tree = 木の path・rel = 木の中の相対 path・name = module 名・size = source の大きさ(byte — 焼く順を決める)。"
  (#^ str tree)
  (#^ str rel)
  (#^ str name)
  (#^ int size))


(defrecord TreeOutcome
  "木 1 つの結果(報告の行): carried = 引き継いだ .pyc の数・compiled = 焼けた数・failed = 焼けなかった数・problem = 検めが通らない理由
   (印を置いていない)か None。"
  (#^ str named)
  (#^ int carried)
  (#^ int compiled)
  (#^ int failed)
  (#^ (| str None) problem))


(defrecord BakeSummary
  "1 回の準備の結果: trees = 木ごとの TreeOutcome(命令の順)・経過の秒(scan-s = 木の走査・closure-s = 閉包の歩み・carry-s = 引き継ぎ・
   compile-s = 焼き)。"
  (#^ tuple trees)
  (#^ float scan-s)
  (#^ float closure-s)
  (#^ float carry-s)
  (#^ float compile-s))


;; --- 命令の引数と並列数 ----------------------------------------------------------------------

(defk cpu-limit-of [cpu-max available]
  {:pre [(: cpu-max (| str None)) (: available int)] :post [(: % int)] :tags {:context "worker" :role "judgment"}}
  "cgroup v2 の cpu.max の中身(\"<quota> <period>\" か \"max <period>\")と使える CPU の数から、焼きの並列数を決めるため(pod の上限を
   越えないため — node の CPU の数で焼くと、上限 4 の pod で 16 並列になり周期の 97% が絞られた)。"
  (val parts (if cpu-max (.split cpu-max) []))
  (if (and (= (len parts) 2) (!= (get parts 0) "max"))
      (max 1 (min available (math.ceil (/ (int (get parts 0)) (int (get parts 1))))))
      (max 1 available)))


(defk tree-arguments [trees roots olds changes]
  {:pre [(: trees tuple) (: roots tuple) (: olds tuple) (: changes tuple)] :post [(: % (| tuple str))]
   :tags {:context "worker" :role "judgment"}}
  "木ごとに並べた命令の引数(--tree・--roots・--from・--changed の値の列)を木の組(TreeArgs の列)に揃えるため(揃え方は入口の頭の註)。
   揃わなければ使い方の誤りの文。"
  (cond
    (!= (len roots) (len trees))
      (.format "--roots は --tree ごとに 1 つ書く(木 {} に --roots {})" (len trees) (len roots))
    (any (gfor r roots (not (.strip r ","))))
      "--roots の値が空(import の根を 1 つ以上 `,` で並べる)"
    (not-in (len olds) #(0 (len trees)))
      (.format "--from は書かないか --tree ごとに 1 つ書く(無い木は \"\" — 木 {} に --from {})" (len trees) (len olds))
    (not-in (len changes) #(0 (len trees)))
      (.format "--changed は書かないか --tree ごとに 1 つ書く(無い木は \"\" — 木 {} に --changed {})" (len trees) (len changes))
    True
      (tuple (gfor #(i tree) (enumerate trees)
                   (TreeArgs :named tree
                             :roots (tuple (gfor r (.split (get roots i) ",") :if r r))
                             :old (if (and olds (get olds i)) (get olds i) None)
                             :changed (if (and changes (get changes i)) (get changes i) None))))))


(defk trees-import-path [trees]
  {:pre [(: trees tuple)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "焼く process の import の路の先頭に足す dir(木の順・木の中は根の順)を求めるため — 焼く source の macro が同じ木や別の木の module を
   require するので、全部の木の根を足す。"
  (tuple (gfor tree trees root tree.roots (posixpath.normpath (posixpath.join tree.path root)))))


;; --- 木をまたいだ import の閉包 ------------------------------------------------------------------

(defk module-index [trees sources]
  {:pre [(: trees tuple) (: sources tuple)] :post [(: % ModuleIndex)] :tags {:context "worker" :role "judgment"}}
  "木ごとの source の相対 path の列(sources — trees と同じ順)から、木をまたいだ module の索引を作るため(import の根の外の source は
   module 名を持たないので入らない)。"
  (val named (sorted (gfor #(i #(tree rels)) (enumerate (zip trees sources))
                           rel rels
                           :setv name (module-name rel tree.roots)
                           :if (is-not name None)
                           #(name i rel))))
  (val firsts (tuple (gfor #(k entry) (enumerate named)
                           :if (or (= k 0) (!= (get (get named (- k 1)) 0) (get entry 0)))
                           entry)))
  (ModuleIndex :names (tuple (gfor e firsts (get e 0))) :places (tuple (gfor e firsts #((get e 1) (get e 2))))))


(defk closure-step [index frontier seen]
  {:pre [(: index ModuleIndex) (: frontier frozenset) (: seen frozenset)] :post [(: % tuple)]
   :tags {:context "worker" :role "judgment"}}
  "閉包の 1 歩: frontier(import された名)と、その上の package の名(import a.b.c は a と a.b の __init__ も走らせる)のうち、どれかの
   木の module に解け、まだ訪ねていない名を求めるため(名の順)。"
  (tuple (sorted (sfor name frontier
                       :setv parts (.split name ".")
                       n (range 1 (+ (len parts) 1))
                       :setv m (.join "." (cut parts 0 n))
                       :setv at (bisect.bisect-left index.names m)
                       :if (and (not-in m seen) (< at (len index.names)) (= (get index.names at) m))
                       m))))


(defk module-place [index name]
  {:pre [(: index ModuleIndex) (: name str)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "索引に在る module 名の置き場 #(木の番号 相対 path) を引くため(閉包の歩みが読む source を決める)。"
  (get index.places (bisect.bisect-left index.names name)))


(defk imported-modules [index found texts]
  {:pre [(: index ModuleIndex) (: found tuple) (: texts tuple)] :post [(: % frozenset)] :tags {:context "worker" :role "judgment"}}
  "訪ねた module(found — 索引に在る名)の source の text(texts — 同じ順)が import する名を集めるため。相対の名は module の package から
   解き、from a import x の a.x も入れる(x が module でなければどの木にも解けず、次の歩みで落ちる)。"
  (frozenset (gfor #(m text) (zip found texts)
                   :setv rel (get (get index.places (bisect.bisect-left index.names m)) 1)
                   :setv package (.split (if (.endswith rel #("__init__.py" "__init__.hy")) m (.join "." (cut (.split m ".") 0 -1))) ".")
                   #(dots target names) (imported-names rel text)
                   :setv base (if (> dots 0)
                                  (.join "." (+ (cut package 0 (max 0 (- (len package) (- dots 1)))) (if target [target] [])))
                                  target)
                   :if base
                   name (+ #(base) (tuple (gfor x names (+ base "." x))))
                   name)))


(defk closure-scopes [index seen count]
  {:pre [(: index ModuleIndex) (: seen frozenset) (: count int)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "閉包に入った module 名(seen)を、木ごとの相対 path の frozenset(木の番号の順・count 個)に分けるため。"
  (val places (tuple (gfor m seen (get index.places (bisect.bisect-left index.names m)))))
  (tuple (gfor i (range count) (frozenset (gfor p places :if (= (get p 0) i) (get p 1))))))


(defk scoped-sources [sources scope]
  {:pre [(: sources (| list tuple)) (: scope (| frozenset None))] :post [(: % list)] :tags {:context "worker" :role "judgment"}}
  "木の走査の source の列(sources)を、その木の焼く範囲(scope — 閉包の相対 path・None = 根の下を全部)に絞るため(焼く物と検めの対象)。"
  (if (is scope None) (list sources) (lfor s sources :if (in s scope) s)))


(defk tree-failures [failures tree]
  {:pre [(: failures tuple) (: tree str)] :post [(: % list)] :tags {:context "worker" :role "judgment"}}
  "1 回の焼きの焼けなかった物(#(木の path 相対 path 理由) の列)から、木 1 つの物を #(相対 path 理由) の列で取り出すため(検めと印に載せる)。"
  (lfor f failures :if (= (get f 0) tree) #((get f 1) (get f 2))))


;; --- 焼く順と焼きの道具への受け渡し --------------------------------------------------------------

(defk bake-order [items]
  {:pre [(: items tuple)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "焼く物(BakeItem の列)を、pool へ渡す順の #(木の path 相対 path module 名) の列にするため: source の大きい順(同じ大きさは木の path と
   相対 path の順)。1 file の秒の偏りが大きく(中央値 0.17 秒・最大 6〜25 秒 — 大半は Hy の macro の展開で、大きい source ほど長い)、
   遅い物を先に配ると、最後に 1 core だけが長い file を焼いて残りが遊ぶ時間が減る。"
  (tuple (gfor item (sorted items :key (fn [i] #((- i.size) i.tree i.rel))) #(item.tree item.rel item.name))))


(defk bake-argv [python tool jobs paths]
  {:pre [(: python str) (: tool str) (: jobs int) (: paths tuple)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "焼きの道具(tool = worker 自身のコードの foundation/bytecode_pool.hy)を起こす命令を組むため(python = 入口と同じ venv の interpreter —
   準備する root の venv・jobs = 並列数・paths = 焼く process の import の路の先頭に足す dir)。"
  (+ #(python "-m" "hy" tool "--jobs" (str jobs)) (tuple (gfor p paths a #("--path" p) a))))


(defk bake-input [items]
  {:pre [(: items tuple)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "焼く物(#(木の path 相対 path module 名) の列)を焼きの道具の標準入力(1 つ 1 行・tab で区切る・この順に焼く)にするため。"
  (.join "" (gfor #(tree rel name) items (.format "{}\t{}\t{}\n" tree rel name))))


(defk bake-failures [text]
  {:pre [(: text str)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "焼きの道具の標準出力(焼けなかった物 1 つ 1 行の `<木の path>\\t<相対 path>\\t<理由>`)を #(木の path 相対 path 理由) の列にするため。"
  (tuple (gfor line (.splitlines text) :setv parts (.split line "\t" 2) :if (= (len parts) 3) (tuple parts))))


;; --- 報告の行 ------------------------------------------------------------------------------

(defk tree-line [outcome]
  {:pre [(: outcome TreeOutcome)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "木 1 つの報告の行を作るため(呼び手が木ごとの問題を読む形 — 問題の文の改行は空白にして 1 行に収める)。"
  (.format "tree={} carried={} compiled={} failed={} problem={}" outcome.named outcome.carried outcome.compiled outcome.failed
           (if (is outcome.problem None) "-" (.replace outcome.problem "\n" " "))))


(defk total-line [summary]
  {:pre [(: summary BakeSummary)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "全体の報告の行を作るため(頭は carried=… compiled=… — 呼び手はこの行で合計を読む・秒は処理ごとに分けて名乗る)。"
  (.format "carried={} compiled={} failed={} carry_s={} compile_s={} closure_s={} scan_s={}"
           (sum (gfor t summary.trees t.carried)) (sum (gfor t summary.trees t.compiled)) (sum (gfor t summary.trees t.failed))
           (round summary.carry-s 2) (round summary.compile-s 2) (round summary.closure-s 2) (round summary.scan-s 2)))
