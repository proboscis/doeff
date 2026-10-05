;;; bytecode の準備の道具(worker/entry/code_prepare.hy)が全部の木を 1 回で焼く段取りの純粋な判断と、その record — 命令の木ごとの引数の
;;; 揃え・焼きの並列数・木をまたいだ import の閉包の歩み・import の名の表(閉包の読みの使い回し)・前の木から引き継ぐ .pyc(頭の検めの方式と
;;; magic)・焼く順(大きい順)・焼きの道具への受け渡しの行・報告の行。I/O と効果は持たない。
;;;
;;; 読まれ方: 入口はこの file を package の import でなく、入口の file の位置から求めた path で読む(module 名 doeff_cluster_worker_bake_plan
;;; — 入口は準備する root の venv の python で走るので、package で引くと root の版の doeff の物になり、この file を持たない古い版の root で
;;; 落ちる)。だからこの file が import してよいのも、cluster で動く job の doeff の版から変わっていない部品(code_plan の module-name・
;;; imported-names・carry-pairs と doeff-hy の macro)と標準 library だけ。検は package の名で普通に import して判断を検める。
(require doeff-hy.macros [defk val <-])
(require doeff-hy.record [defrecord defenum])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import bisect)
(import hashlib)
(import json)
(import math)
(import posixpath)
(import dataclasses [dataclass])  ; dataclass は defrecord の展開が使う
(import enum [StrEnum])  ; StrEnum は defenum の展開が使う
(import doeff_cluster.worker.core.code_plan [carry-pairs imported-names module-name])


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


(defrecord ImportRow
  "import の名の表の行 1 つ(#3694): rel = 木の中の source の相対 path・digest = source の sha256(16 進)・imports = その source が
   import する名(imported-names の答えの形 #(#(点の数 名 取り出す名の tuple) …))。"
  (#^ str rel)
  (#^ str digest)
  (#^ tuple imports))


(defrecord ImportTable
  "引き継ぎ元の木の import の名の表を読んだ結果(#3694): rows = ImportRow の列(rel の名の順・同じ rel は 1 つ — usable-row が二分探索で
   引く)・problem = 表を使えない理由(行 0 — 全部を読み直す)か None。"
  (#^ tuple rows)
  (#^ (| str None) problem))


(defenum PycScheme CHECKED-HASH UNCHECKED-HASH TIMESTAMP UNREADABLE)
;; .pyc の検めの方式(PEP 552 の頭の flags — 0 = timestamp・0b01 = unchecked hash・0b11 = checked hash)。UNREADABLE = 頭の 16 byte を
;; 読めない・足りない・flags がどれでもない .pyc(#3727)。


(defrecord PycHead
  "引き継ぎ元の木の .pyc 1 つの頭を読んだ結果(#3727): path = 木の中の相対 path・scheme = 検めの方式・magic = 頭の magic 4 byte(頭を
   読めない・16 byte に足りなければ空)。"
  (#^ str path)
  (#^ PycScheme scheme)
  (#^ bytes magic))


(defrecord BakeItem
  "焼く物 1 つ: tree = 木の path・rel = 木の中の相対 path・name = module 名・size = source の大きさ(byte — 焼く順を決める)。"
  (#^ str tree)
  (#^ str rel)
  (#^ str name)
  (#^ int size))


(defrecord TreeOutcome
  "木 1 つの結果(報告の行): carried = 前の木から hardlink で引き継いだ .pyc の数・焼く計画の file のうち rebuilt = 焼いた数・reused = 在った
   .pyc が今の source と macro に合い焼かずに残した数・failed = 焼けなかった数(rebuilt + reused + failed = 焼く計画の数 — #3675)・
   problem = 検めが通らない理由(印を置いていない)か None。"
  (#^ str named)
  (#^ int carried)
  (#^ int rebuilt)
  (#^ int reused)
  (#^ int failed)
  (#^ (| str None) problem))


(defrecord BakeAnswer
  "焼きの道具の答え: failed = 焼けなかった物の #(木の path 相対 path 理由) の列・reused = 在った .pyc が今の source と今の環境の macro に
   合うので pool へ送らなかった物の #(木の path 相対 path) の列(#3675)。"
  (#^ tuple failed)
  (#^ tuple reused))


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


(defk imported-modules [index found imports]
  {:pre [(: index ModuleIndex) (: found tuple) (: imports tuple)] :post [(: % frozenset)] :tags {:context "worker" :role "judgment"}}
  "訪ねた module(found — 索引に在る名)の source が import する名(imports — 同じ順の、imported-names の答えの形 #(#(点の数 名
   取り出す名の tuple) …) の列)を module 名に解くため。相対の名は module の package から解き、from a import x の a.x も入れる(x が
   module でなければどの木にも解けず、次の歩みで落ちる)。"
  (frozenset (gfor #(m named) (zip found imports)
                   :setv rel (get (get index.places (bisect.bisect-left index.names m)) 1)
                   :setv package (.split (if (.endswith rel #("__init__.py" "__init__.hy")) m (.join "." (cut (.split m ".") 0 -1))) ".")
                   #(dots target names) named
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


;; --- import の名の表(閉包の歩みの読みの使い回し・#3694)-------------------------------------------
;; 木の根に残す file。閉包の歩みが訪ねた source ごとに、その source の sha256 と import の名(imported-names の答え)を持つ。次の版の
;; 準備は、引き継ぎ元の木の表のうち --changed に無く sha256 が今の source と合う行を使い回し、構文の読み(Hy の read-many と Python の
;; ast.parse — 閉包の歩みの秒の大半)を変わった file だけにする。表は今の閉包の source の行だけを持つ(消えた file・閉包から外れた
;; file の行は次の表に残らない)。隠し file なので木の走査の source には混ざらない。

(val IMPORT-TABLE ".doeff-import-names.json")
(val IMPORT-TABLE-FORMAT 1)


(defk source-digest [text]
  {:pre [(: text str)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "source の中身の sha256(16 進)を求めるため — 表の行を使い回してよいかの鍵(--changed の漏れでも古い行を使わない)。"
  (.hexdigest (hashlib.sha256 (.encode text "utf-8"))))


(defk import-row-of [rel value]
  {:pre [(: rel str) (: value (| dict list str int float bool None))] :post [(: % (| ImportRow None))] :tags {:context "worker" :role "judgment"}}
  "表の JSON の行 1 つ({\"sha256\" <16 進> \"imports\" [[点の数 名 [取り出す名 …]] …]})を ImportRow に読むため。形が違えば None
   (その file は読み直す)。"
  (var entries #())
  (var whole True)
  (match value
    {"sha256" (str) "imports" (list)}
      (for [e (get value "imports")]
        (match e
          [(int) (str) (list)] :if (all (gfor x (get e 2) (isinstance x str)))
            (:= entries (+ entries #(#((get e 0) (get e 1) (tuple (get e 2))))))
          _ (:= whole False)))
    _ (:= whole False))
  (if whole (ImportRow :rel rel :digest (get value "sha256") :imports entries) None))


(defk import-table-of [text]
  {:pre [(: text (| str None))] :post [(: % ImportTable)] :tags {:context "worker" :role "judgment"}}
  "引き継ぎ元の木の表の file の中身(無い・読めなければ None)を ImportTable に読むため(JSON の境界はここ 1 か所)。無い・JSON で
   ない・形の版が違う表は行 0 で理由つき(全部を読み直す — 安全側)。形の違う行は落とす(その file は読み直す)。"
  (val parsed (if (is text None)
                  None
                  (try (json.loads text) (except [ValueError] None))))
  (var rows #())
  (var problem None)
  (cond
    (is text None) (:= problem "引き継ぎ元の木に import の名の表が無い(表を残す前の形の木か、読めない)")
    (is parsed None) (:= problem "引き継ぎ元の木の import の名の表が JSON でない")
    True
      (match parsed
        {"format" form "modules" (dict)} :if (= form IMPORT-TABLE-FORMAT)
          (for [#(rel value) (.items (get parsed "modules"))]
            (<- row (| ImportRow None) (import-row-of rel value))
            (when (is-not row None)
              (:= rows (+ rows #(row)))))
        _ (:= problem "引き継ぎ元の木の import の名の表の形の版が違う")))
  (ImportTable :rows (tuple (sorted rows :key (fn [r] r.rel))) :problem problem))


(defk usable-row [table rel digest changed]
  {:pre [(: table ImportTable) (: rel str) (: digest str) (: changed frozenset)] :post [(: % (| ImportRow None))]
   :tags {:context "worker" :role "judgment"}}
  "閉包の歩みが訪ねた source 1 つについて、引き継ぎ元の表の行を使い回してよいかを決めるため: 行が在り、--changed に無く(足した・消した・
   名を替えた file も一覧に載る)、sha256 が今の source(digest)と合う時だけ行を返す。None なら呼び手が構文を読む(imported-names)—
   一覧の漏れ(手で直した木など)でも sha256 が違えば古い行を使わない。"
  (val at (bisect.bisect-left table.rows rel :key (fn [r] r.rel)))
  (val row (if (and (< at (len table.rows)) (= (. (get table.rows at) rel) rel)) (get table.rows at) None))
  (if (and (is-not row None) (not-in rel changed) (= row.digest digest)) row None))


(defk import-table-json [rows]
  {:pre [(: rows tuple)] :post [(: % dict)] :tags {:context "worker" :role "judgment"}}
  "木の閉包の source の行(ImportRow の列)を、木の根に残す表の JSON の値に綴るため(JSON の境界の 1 点・相対 path の順)。"
  {"format" IMPORT-TABLE-FORMAT
   "modules" (dfor row (sorted rows :key (fn [r] r.rel))
                   row.rel {"sha256" row.digest
                            "imports" (lfor #(dots name names) row.imports [dots name (list names)])})})


(defk import-table-text [rows]
  {:pre [(: rows tuple)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "木の閉包の source の行(ImportRow の列)を、木の根に残す表の file の中身にするため。"
  (<- value dict (import-table-json rows))
  (json.dumps value :ensure-ascii False))


(defk scoped-sources [sources scope]
  {:pre [(: sources (| list tuple)) (: scope (| frozenset None))] :post [(: % list)] :tags {:context "worker" :role "judgment"}}
  "木の走査の source の列(sources)を、その木の焼く範囲(scope — 閉包の相対 path・None = 根の下を全部)に絞るため(焼く物と検めの対象)。"
  (if (is scope None) (list sources) (lfor s sources :if (in s scope) s)))


(defk tree-failures [failures tree]
  {:pre [(: failures tuple) (: tree str)] :post [(: % list)] :tags {:context "worker" :role "judgment"}}
  "1 回の焼きの焼けなかった物(#(木の path 相対 path 理由) の列)から、木 1 つの物を #(相対 path 理由) の列で取り出すため(検めと印に載せる)。"
  (lfor f failures :if (= (get f 0) tree) #((get f 1) (get f 2))))


(defk tree-reused [reused tree]
  {:pre [(: reused tuple) (: tree str)] :post [(: % int)] :tags {:context "worker" :role "judgment"}}
  "1 回の焼きの焼かずに残した物(#(木の path 相対 path) の列)のうち、木 1 つの物の数を求めるため(報告の行の reused)。"
  (sum (gfor r reused :if (= (get r 0) tree) 1)))


;; --- 前の木から引き継ぐ .pyc(#3727)---------------------------------------------------------------
;; 引き継ぎ元の木の .pyc には、道具が焼いた checked hash の物と、閉包の外の module を子が import した時に Python が書いた timestamp の物
;; (と、古い Python が焼いた物)が混ざる。新しい木は展開し直すので source の mtime が違い、timestamp の .pyc は import の時に古いと判じられ
;; その場で compile される。magic の違う .pyc も同じ。だから引き継ぐのは、import が新しい木でそのまま使う checked hash で magic が今の
;; Python と同じ物だけにし、ほかは焼く一覧に残す(焼いた数 rebuilt に入る)。

(val PYC-HEAD-BYTES 16)


(defk pyc-head-of [path head]
  {:pre [(: path str) (: head (| bytes None))] :post [(: % PycHead)] :tags {:context "worker" :role "judgment"}}
  "引き継ぎ元の木の .pyc 1 つの頭(head — 先頭の PYC-HEAD-BYTES byte・読めなければ None)を PycHead にするため(PEP 552 — magic 4 byte・
   flags 4 byte・残りは timestamp と大きさか source の hash)。足りない頭と、flags がどの方式でもない頭は UNREADABLE。"
  (match head
    (bytes) :if (>= (len head) PYC-HEAD-BYTES)
      (PycHead :path path
               :scheme (match (int.from-bytes (cut head 4 8) "little")
                         0 PycScheme.TIMESTAMP
                         0b01 PycScheme.UNCHECKED-HASH
                         0b11 PycScheme.CHECKED-HASH
                         _ PycScheme.UNREADABLE)
               :magic (cut head 0 4))
    _ (PycHead :path path :scheme PycScheme.UNREADABLE :magic b"")))


(defk carried-pycs [heads magic old-sources new-sources new-pycs changed]
  {:pre [(: heads tuple) (: magic bytes) (: old-sources frozenset) (: new-sources frozenset) (: new-pycs frozenset) (: changed frozenset)]
   :post [(: % list)] :tags {:context "worker" :role "judgment"}}
  "前の木から hardlink で引き継ぐ .pyc(相対 path の列)を決めるため: 引き継ぎ元の木の .pyc の頭(heads — PycHead の列)のうち checked hash
   の方式で magic が今の Python(magic)と同じ物から、code_plan の carry-pairs が source で選ぶ物(source が変わっておらず新しい木にも在り、
   新しい木にまだ .pyc の無い物)。"
  (<- pairs list (carry-pairs (lfor head heads :if (and (= head.scheme PycScheme.CHECKED-HASH) (= head.magic magic)) head.path)
                              old-sources new-sources new-pycs changed))
  pairs)


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


(defk bake-answer [text]
  {:pre [(: text str)] :post [(: % BakeAnswer)] :tags {:context "worker" :role "judgment"}}
  "焼きの道具の標準出力(1 つ 1 行 — 焼けなかった物は `failed\\t<木の path>\\t<相対 path>\\t<理由>`・焼かずに残した物は
   `reused\\t<木の path>\\t<相対 path>`)を BakeAnswer にするため。"
  (val rows (tuple (gfor line (.splitlines text) (.split line "\t" 3))))
  (BakeAnswer :failed (tuple (gfor r rows :if (and (= (len r) 4) (= (get r 0) "failed")) (tuple (cut r 1 None))))
              :reused (tuple (gfor r rows :if (and (= (len r) 3) (= (get r 0) "reused")) (tuple (cut r 1 None))))))


;; --- 報告の行 ------------------------------------------------------------------------------

(defk tree-line [outcome]
  {:pre [(: outcome TreeOutcome)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "木 1 つの報告の行を作るため(呼び手が木ごとの問題を読む形 — 問題の文の改行は空白にして 1 行に収める)。"
  (.format "tree={} carried={} rebuilt={} reused={} failed={} problem={}" outcome.named outcome.carried outcome.rebuilt outcome.reused
           outcome.failed
           (if (is outcome.problem None) "-" (.replace outcome.problem "\n" " "))))


(defk total-line [summary]
  {:pre [(: summary BakeSummary)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "全体の報告の行を作るため(頭は carried=… rebuilt=… reused=… — 呼び手はこの行で合計を読む・秒は処理ごとに分けて名乗る)。"
  (.format "carried={} rebuilt={} reused={} failed={} carry_s={} compile_s={} closure_s={} scan_s={}"
           (sum (gfor t summary.trees t.carried)) (sum (gfor t summary.trees t.rebuilt)) (sum (gfor t summary.trees t.reused))
           (sum (gfor t summary.trees t.failed))
           (round summary.carry-s 2) (round summary.compile-s 2) (round summary.closure-s 2) (round summary.scan-s 2)))
