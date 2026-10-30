;; 掃除の数えを root ごとに持ち、変わっていない完成した root を数え直さない形と、上限を越えた時に余白まで消す形の検(card ki-a619d34de8c9)。
;;
;; 実例(zeus 2026-10-16・親 card ki-97da6e8be47f): 掃除は上限の際で「1 つ消す → 次に完成した root ですぐ越える」を繰り返し、20〜60 秒おきに
;; 全 root(約 50 個・1 つ約 2〜3 万 file)を lstat で数え直した(24 時間の約 36% が掃除中)。数えの IO が node の共有の disk の浅い枠
;; (nr_requests=2・queue_depth=1)を取り合い、worker の本体の thread が tick の中の同期の file の効果で block 層の待ち(D 状態)に入った。
;;   数えの使い回し  完成の時刻(完成マーカーの mtime)と最後に使った時刻(.last-used の mtime)が前の数えと同じ root は木を歩かない
;;                   (env_upkeep の kept-bytes)。完成の後に root が大きくなるのは job が import した時の .pyc だけで、job は起こす前に
;;                   .last-used を進めるので、使われた root と準備し直した root と新しい root だけを歩く
;;   余白            roots の合計が上限を越えた時は、上限の SWEEP-TARGET-RATIO まで下がるまで消す(次に完成した root 1 つで越えない)
;; 反例 = 直す前の形(掃除のたびに全 root を MeasureTree で歩く・上限ちょうどまでしか消さない)は、下の歩いた root の断言と、
;; 余白の断言が赤。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import json)
(import os)
(import pathlib [Path])
(import doeff [Program with-handlers])
(import doeff_core_effects [SlogEffect])
(import doeff_core_effects [state])
(import doeff_core_effects [scheduled])
(import doeff_core_effects [os-file-handler])
(import doeff_core_effects [subprocess-handler])
(import doeff_core_effects [MeasureTree])
(import doeff_time [Delay sync-time-handler])
(import doeff_cluster [ENV-MARKER])
(import doeff_cluster [SweepEnvs])
(import doeff_cluster [ObserveEnvs])
(import doeff_cluster._worker.protocol.code_store [PREPARE-TOOL])
(import doeff_cluster [env-host]
        doeff_cluster._worker.protocol.env_store [EnvSettings SWEEP-LOG SWEEP-DONE-LOG])
(import doeff_cluster._worker.core.env_upkeep [RootInfo kept-bytes sweep-choice sweep-target roots-bytes])

;; 同じ project の完成マーカー(worker が作った root と読まれる形 — 名は 24 字の 16 進)。
(val MARKER {"env" {"project" {"repo" "r" "path" "p"} "repos" [{"name" "r" "url" "https://example.invalid/r"}]}})
(val ROOT-A "aaaaaaaaaaaaaaaaaaaaaaaa")
(val ROOT-B "bbbbbbbbbbbbbbbbbbbbbbbb")
(val ROOT-C "cccccccccccccccccccccccc")
(val ROOT-D "dddddddddddddddddddddddd")
(val ROOT-E "eeeeeeeeeeeeeeeeeeeeeeee")
;; roots の合計の上限を掃除の消しが起きない大きさに置く(数えだけを見る — 数えは完成した root の集合が変わるたびに起きる)。
(val NO-CAP (** 2 62))
;; 掃除の終わりを待つ SweepEnvs の回数の上限と、回の間の秒(本物の時計 — 合わせて 10 秒)。
(val SWEEP-ROUNDS 1000)
(val SWEEP-PAUSE-SECONDS 0.01)
;; root ごとに置く file の大きさと、job が import した時に足される .pyc の大きさ(byte)。
(val LIB-BYTES 3000)
(val PYC-BYTES 700)


;; --- 判じ: 使い回してよい大きさ -----------------------------------------------------------------------

(val KNOWN #((RootInfo :key "env-a" :project "p" :made-ms 1000 :last-used-ms 5000 :bytes 400 :owned True)
             (RootInfo :key "env-lost" :project "p" :made-ms 1000 :last-used-ms 1000 :bytes 0 :owned True)))


(deftest test-a-root-whose-times-are-unchanged-keeps-its-size
  (assert (= (! (kept-bytes KNOWN "env-a" 1000 5000)) 400) "完成の時刻も使った時刻も同じ root は前の大きさを使い回す")
  (assert (is (! (kept-bytes KNOWN "env-a" 1000 6000)) None) "使われた root(job が .pyc を足す)は数え直す")
  (assert (is (! (kept-bytes KNOWN "env-a" 2000 5000)) None) "準備し直した root(完成の時刻が変わる)は数え直す")
  (assert (is (! (kept-bytes KNOWN "env-new" 1000 1000)) None) "前の数えに無い root は数える")
  (assert (is (! (kept-bytes KNOWN "env-lost" 1000 1000)) None) "前の数えで測れなかった root(大きさ 0)は数え直す")
  (assert (is (! (kept-bytes #() "env-a" 1000 5000)) None) "まだ数えていない(起き直した worker)なら全部数える")
  (assert (is (! (kept-bytes KNOWN "env-a" 0 5000)) None) "完成の時刻を読めなかった root は使い回さない"))


;; --- 判じ: 上限を越えた時の余白 -------------------------------------------------------------------------
;; 数は zeus の本番(上限 20 GiB・root 1 つ約 0.4 GB — 2026-10-16 の実測 333〜508 MB)。

(val GIB (** 2 30))
(val ZEUS-CAP (* 20 GIB))
(val ZEUS-ROOT-BYTES (* 400 (** 10 6)))
(val ROOMY-FREE (* 100 GIB))


(defk zeus-roots [n]
  {:pre [(: n int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "同じ project の root n 個(i 番目は完成と最後に使った時刻が i — 番号の小さいほど古い)を返すため。"
  (tuple (gfor i (range n)
               (RootInfo :key (.format "env-{:03d}" i) :project "p" :made-ms i :last-used-ms i :bytes ZEUS-ROOT-BYTES :owned True))))


(deftest test-a-sweep-over-the-cap-goes-down-to-the-margin-and-the-next-root-does-not-cross
  ;; 54 個の合計 21.6 GB は上限 21.47 GB を越える。直す前の選びは 1 つ消して 21.2 GB(上限の際)に止め、次に完成した root 1 つで
  ;; また越えた(往復)。上限の 95%(20.4 GB)まで下げる選びは古い 3 つを 1 回で消し、次に完成した root 1 つでは越えない。
  (<- roots tuple (zeus-roots 54))
  (<- total int (roots-bytes roots))
  (assert (> total ZEUS-CAP) "前提: 合計は上限を越える")
  (<- chosen tuple (sweep-choice roots (frozenset) ZEUS-CAP ROOMY-FREE 0))
  (assert (= chosen #("env-000" "env-001" "env-002")) chosen)
  (val kept (tuple (gfor r roots :if (not-in r.key chosen) r)))
  (<- left int (roots-bytes kept))
  (<- target int (sweep-target ZEUS-CAP))
  (assert (<= left target) (.format "1 回の消しで下げ先 {} まで下がる(残り {})" target left))
  ;; 次に完成した root 1 つ(最も新しい)では上限を越えず、掃除は何も選ばない。
  (val next-roots (+ kept #((RootInfo :key "env-054" :project "p" :made-ms 54 :last-used-ms 54 :bytes ZEUS-ROOT-BYTES :owned True))))
  (<- again tuple (sweep-choice next-roots (frozenset) ZEUS-CAP ROOMY-FREE 0))
  (assert (= again #()) again))


(deftest test-a-low-free-disk-within-the-cap-is-not-pushed-down-to-the-margin
  ;; 合計が上限の内で空きだけが最低を割った時は、空きが戻る分だけ選ぶ(下げ先までは消さない — #4051 の選びのまま)。
  (<- roots tuple (zeus-roots 50))
  (assert (<= (! (roots-bytes roots)) ZEUS-CAP) "前提: 合計は上限の内")
  (val min-free (* 25 GIB))
  (<- chosen tuple (sweep-choice roots (frozenset) ZEUS-CAP (- min-free 1) min-free))
  (assert (= chosen #("env-000")) chosen))


;; --- env-host: 数えで木を歩く root -------------------------------------------------------------------

(defrecord SweepsSeen
  "ここまでの掃除の見え方: walked = MeasureTree を受けた path(出た順)・lines = 掃除の選びの行の #(walked roots-bytes)(出た順)・
   done = 掃除の終わりの行の数。"
  (#^ (get tuple #(str ...)) walked)
  (#^ (get tuple #(tuple ...)) lines)
  (#^ int done))


(defeffect NotedSweepsSeen
  "ここまでの掃除の見え方を読む — 検だけの問い(sweeps-seen が答える)。"
  {:fields [] :answer SweepsSeen :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler sweeps-seen
  "env-host のすぐ外に立つ係: MeasureTree の path を覚えて外の os-file-handler へ渡し、掃除の選びの行の欄(walked・roots-bytes)と
   終わりの行を覚え、他の log の行は受け流す。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var walked #())
  (session var lines #())
  (session var done 0)
  (MeasureTree [path]
    (:= walked (+ walked #(path)))
    (<- answer effect)
    (resume answer))
  (SlogEffect []
    (when (= effect.msg SWEEP-DONE-LOG)
      (:= done (+ done 1)))
    (when (= effect.msg SWEEP-LOG)
      (:= lines (+ lines #(#((get effect.kwargs "walked") (get effect.kwargs "roots_bytes"))))))
    (resume None))
  (NotedSweepsSeen []
    (resume (SweepsSeen :walked walked :lines lines :done done))))


(defk placed-root [base name made-s used-s]
  {:pre [(: base Path) (: name str) (: made-s int) (: used-s int)] :post [(: % Path)] :tags {:context "doeff-cluster-test" :role "program"}}
  "完成した root を置き場に置くため(LIB-BYTES の file 1 つ・完成マーカーの時刻 = made-s・.last-used の時刻 = used-s)。"
  (val root (/ base "state" "roots" name))
  (.mkdir root :parents True)
  (.write-bytes (/ root "lib.py") (* b"x" LIB-BYTES))
  (.write-text (/ root ENV-MARKER) (json.dumps MARKER))
  (os.utime (/ root ENV-MARKER) #(made-s made-s))
  (.write-text (/ root ".last-used") "")
  (os.utime (/ root ".last-used") #(used-s used-s))
  root)


(defk tree-bytes [root]
  {:pre [(: root Path)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "root の下の file の大きさの和を、掃除の数え(MeasureTree)と同じ物差しで返すため(使い回した大きさが本物と合うかの比べ)。"
  (sum (gfor #(top _ files) (os.walk root) name files (. (os.lstat (os.path.join top name)) st-size))))


(defk swept-to [count]
  {:pre [(: count int)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "root の観測を新しくしてから(完成した root の集合の変わりで掃除が起きる)、掃除の終わりの行が count 本になるまで SweepEnvs を撃つため。"
  (<- (ObserveEnvs))
  (for [_ (range SWEEP-ROUNDS)]
    (<- (SweepEnvs (frozenset)))
    (<- seen SweepsSeen (NotedSweepsSeen))
    (when (>= seen.done count)
      (break))
    (<- (Delay SWEEP-PAUSE-SECONDS)))
  (<- noted SweepsSeen (NotedSweepsSeen))
  (assert (= noted.done count) noted)
  None)


(defrecord ThreeSweeps
  "3 回の掃除の見え方: seen = 終わりの掃除の見え方・sizes = 各回の終わりの本物の roots の合計(tree-bytes の和)・roots = 置いた root の path
   (名 → path)。"
  (#^ SweepsSeen seen)
  (#^ (get tuple #(int ...)) sizes)
  (#^ dict roots))


(defk three-sweeps [base]
  {:pre [(: base Path)] :post [(: % ThreeSweeps)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 1 回目は root a・b・c を数える。2 回目の前に新しい root d が完成する。3 回目の前に、b で job が走り(.last-used を進め .pyc を
   足す)、a を準備し直し(完成の時刻が変わる)、新しい root e が完成する。各回の終わりの本物の roots の合計を覚える。"
  (var roots {})
  (for [#(name made) #(#(ROOT-A 1000) #(ROOT-B 2000) #(ROOT-C 3000))]
    (<- root Path (placed-root base name made made))
    (:= roots (| roots {name root})))
  (<- (swept-to 1))
  (var sizes #())
  (<- first-size int (sum-of-trees roots))
  (:= sizes (+ sizes #(first-size)))
  (<- d Path (placed-root base ROOT-D 4000 4000))
  (:= roots (| roots {ROOT-D d}))
  (<- (swept-to 2))
  (<- second-size int (sum-of-trees roots))
  (:= sizes (+ sizes #(second-size)))
  ;; b で job が走った: 起こす前に .last-used を進め、import で .pyc を足す。
  (val b (get roots ROOT-B))
  (.mkdir (/ b "__pycache__"))
  (.write-bytes (/ b "__pycache__" "lib.cpython-314.pyc") (* b"y" PYC-BYTES))
  (os.utime (/ b ".last-used") #(6000 6000))
  ;; a を準備し直した(完成マーカーを書き直す — 時刻が変わる)。
  (os.utime (/ (get roots ROOT-A) ENV-MARKER) #(7000 7000))
  (<- e Path (placed-root base ROOT-E 8000 8000))
  (:= roots (| roots {ROOT-E e}))
  (<- (swept-to 3))
  (<- third-size int (sum-of-trees roots))
  (:= sizes (+ sizes #(third-size)))
  (<- seen SweepsSeen (NotedSweepsSeen))
  (ThreeSweeps :seen seen :sizes sizes :roots roots))


(defk sum-of-trees [roots]
  {:pre [(: roots dict)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "置いた root の本物の大きさの和を返すため(roots = 名 → path)。"
  (var total 0)
  (for [root (.values roots)]
    (<- size int (tree-bytes root))
    (:= total (+ total size)))
  total)


(deftest test-a-sweep-walks-only-new-used-or-reprepared-roots [tmp-path]
  ;; 1 回目は a・b・c を全部歩く。2 回目は新しい d だけを歩き(a・b・c は数え直さない)、3 回目は使われた b・準備し直した a・新しい e だけを
  ;; 歩く(c と d は数え直さない)。どの回も、掃除の行の roots の合計は本物の合計と同じ(使い回した大きさが合っている — b に足された
  ;; .pyc も 3 回目の合計に入る)。直す前の形は 2 回目に 4 つ・3 回目に 5 つ歩く — 赤。
  (val settings (EnvSettings :state (str (/ tmp-path "state")) :uv-cache (str (/ tmp-path "state" "uv-cache")) :hy-command "hy" :platform "test"
                             :code-prepare PREPARE-TOOL :uv "true" :roots-cap-bytes NO-CAP))
  (<- got ThreeSweeps (scheduled (with-handlers [(state) (sync-time-handler) os-file-handler subprocess-handler sweeps-seen (env-host settings)]
                                   (three-sweeps tmp-path))))
  (val path-of (fn [name] (str (get got.roots name))))
  (val walked got.seen.walked)
  (assert (= (sorted (cut walked 0 3)) (sorted (map path-of #(ROOT-A ROOT-B ROOT-C)))) walked)
  (assert (= (cut walked 3 4) #((path-of ROOT-D))) walked)
  (assert (= (sorted (cut walked 4 None)) (sorted (map path-of #(ROOT-A ROOT-B ROOT-E)))) walked)
  (assert (= (tuple (gfor line got.seen.lines (get line 0))) #(3 1 3)) got.seen.lines)
  (assert (= (tuple (gfor line got.seen.lines (get line 1))) got.sizes) #(got.seen.lines got.sizes)))
