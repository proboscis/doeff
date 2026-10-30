;;; 機体の memory の見張りの判断(card ki-7b934e2538f9)— 機体と container の memory の読みの綴りを数にし、job ごとの memory を process の木で
;;; 数え、機体の memory が尽きかけた時に先に止める木を選ぶ。I/O を持たない(読みは worker/protocol/memory_watch が効果で集める)。
;;;
;;; 何のためか: atlas と zeus で、1 つの job が memory を食うと機体の全体が reclaim の thrash で数十秒〜数十分止まり、worker も coordinator も
;;; 応えなくなった(4 回・OOM kill は起きない)。誰が食ったかは 10 分ごとの測りの間に落ちて読めなかった。見張りは 5 秒ごとに job ごとの
;;; memory を数えて最大を残し、機体の空きが閾を割ったら job の木の中のいちばん大きい単位を、kernel より先に止める。
;;;
;;; 止める単位(kill-units — 根 = shim か待ちの子 A は止めない):
;;;   task(名が task/ で始まる job)  根の下の全部で 1 つ(TASK — task は 1 回の仕事なので丸ごと止める)。
;;;   常駐の job  根から、いちばん大きい子の木が今の木の DOMINANT-SHARE 以上で、今の process 自身の rss 以上の間は、その子へ下りる
;;;               (包みの uv・job の本体・memory を食った子へ)。着いた所を集まりの点とする。単位 = 下りなかった兄弟の木(BRANCH — shim が
;;;               引き取った孤児も)と、集まりの点の子ごとの木(BRANCH — 子を並べて走らせる job なら子の木 1 本ずつ)と、集まりの点の
;;;               process だけ(子が在れば BODY — job の本体・子が無ければ LEAF — 食った道具そのもの)。
;;; 止める順(victim-of): BRANCH・LEAF・TASK のいちばん大きい物が先(job の本体を止めると、その job の子が全部止まる)。それが
;;; min-victim-bytes 未満の時だけ BODY のいちばん大きい物。
;;; 止める候補(card ki-649b1b2f5838): task の単位は全部。常駐の job の単位は、その job の今の memory が平常の大きさ(JobSteady — 前の
;;; 最大の行の間ごとの最大のいちばん大きい物)の steady-ratio 倍以上の時か、平常の大きさがまだ無い時だけ。平常の大きさで動く常駐の
;;; job を止めても、機体の空きの不足が worker の外(他の Pod)から来た時は直らず、起こし直された job が同じ大きさに戻ってまた止められる
;;; だけだから(10-24 atlas で 2 時間に 11 回)。平常の大きさより steady-ratio 倍未満の速さで太る job は止める候補にならない(平常の大きさが
;;; 後を追う)— 見張りが止めるのは、平常から急に跳ねた物。
;;; 群れの止め(swarm-of — card ki-d9953fe1d739): 止める候補の単位が無い(どれも min-victim-bytes 未満か平常の大きさの job の物)のに、
;;; 1 つの job の木が機体の全量の swarm-share 以上を持ち、worker の外の使用量より大きい時は、その job の BODY 以外の単位を大きい順に、
;;; 機体の空きを DANGER の線(danger-available)の上へ min-victim-bytes ぶん戻す量まで足して、まとめて止める。訳: CLI を多数並べて走らせる常駐の
;;; job は、CLI の木と、shim に引き取られた道具の子が数百の小さい単位に割れ、1 つずつはどれも 512 MiB 未満なのに、和は機体の
;;; 半分を越える(2026-10-23 20:36〜20:46 UTC の zeus — 見張りの最大の単位 260 MiB のまま機体の空き 11 GB・PSI full 68% で機体ごと
;;; 約 3 分止まり、その job が止まって終わって子の CLI の仕事が全部切れた)。BODY(job の本体)は止めない — 群れの止めは job の
;;; 子を減らして、job そのものと残りの子を生かす。worker の外の使用量より小さい job を止めない訳は平常の大きさの止めと同じ(不足が
;;; worker の外から来た時に、job の子を止めても戻らない)。平常の大きさは問わない(群れの job は止める単位を小さく割るので、平常の
;;; 大きさの比べでは止める候補にならず、DANGER のまま機体が止まる)。
(require doeff-hy.macros [defk val])
(require doeff-hy.record [defrecord defenum])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import enum [StrEnum])  ; defenum の展開が名指す
(import bisect [bisect-left])
(import operator [attrgetter])
(import itertools [accumulate chain groupby islice repeat takewhile])
(import doeff_traverse [sequence])
(import doeff_core_effects.proc_table_effects [ProcRow])

;; byte の単位(log の値は MiB)。
(val MIB (* 1024 1024))


;; 機体の memory の様子(閉じた型): CALM = 余裕がある・TIGHT = 逼迫(log だけ — 次の段の重い task の見送りの印)・DANGER = 尽きかけ
;; (いちばん大きい単位を止める)。
(defenum MemoryLevel CALM TIGHT DANGER)

;; 止める単位の種類(閉じた型 — 頭の註): BRANCH = 子の木・LEAF = 子の無い集まりの点・BODY = 子の在る集まりの点の process だけ・
;; TASK = task の根の下の全部。
(defenum UnitKind BRANCH LEAF BODY TASK)

;; 根から下りる時の、いちばん大きい子の木が今の木に占める割合の最低。
(val DOMINANT-SHARE 0.5)
;; task の名の頭(doeff-cluster の task の job の名 — task/<id>)。
(val TASK-PREFIX "task/")


(defrecord MemoryLimits
  "見張りの閾: danger-available = 空き(MemAvailable)/ 総量 がこれ未満なら DANGER・danger-pressured-available と danger-full-avg10 =
   空き / 総量 がこれ未満で、memory の PSI の full avg10(%)がこれ以上でも DANGER(reclaim で全 process が止まり始めた)・
   tight-available と tight-full-avg10 = どちらかを越えたら TIGHT・min-victim-bytes = これより小さい単位は止めない(食った物が job の外の
   時に、小さい job を次々に止めない)・steady-ratio = 常駐の job の今の memory が平常の大きさのこの倍以上の時だけ、その job の単位を
   止める候補にする(頭の註)。既定の値の根拠: atlas の 3 回は空き 1.5〜2.5 GiB / 28.3 GiB(5〜9%)・zeus は 7 GB / 125 GiB(5.6%)
   で止まり、ふだんの最低は atlas 6.2 GiB(22%)・zeus 52 GiB(41%)。swarm-share = 群れの止め(頭の註)を掛ける job の木の、機体の全量に
   占める割合の最低(既定 0.25 — zeus の 10-23 の CLI を並べる job の木は約 64 GB / 128 GB)。"
  {:check [(< 0 danger-available danger-pressured-available 1) (< 0 danger-available tight-available 1) (> danger-full-avg10 0)
           (> tight-full-avg10 0) (>= min-victim-bytes 0) (> steady-ratio 1) (< 0 swarm-share 1)]}
  (setv #^ float danger-available 0.10)
  (setv #^ float danger-pressured-available 0.20)
  (setv #^ float danger-full-avg10 25.0)
  (setv #^ float tight-available 0.20)
  (setv #^ float tight-full-avg10 10.0)
  (setv #^ int min-victim-bytes (* 512 MIB))
  (setv #^ float steady-ratio 1.5)
  (setv #^ float swarm-share 0.25))


(defrecord NodeMemory
  "機体の memory の読み: total-bytes = MemTotal・available-bytes = MemAvailable・some-avg10 / full-avg10 = /proc/pressure/memory の
   some と full の avg10(%)— PSI の無い機体は None。"
  {:check [(> total-bytes 0) (>= available-bytes 0)]}
  (#^ int total-bytes)
  (#^ int available-bytes)
  (#^ (| float None) some-avg10)
  (#^ (| float None) full-avg10))


(defrecord ContainerMemory
  "worker の container の cgroup(v2)の memory の読み: current-bytes = memory.current・peak-bytes = memory.peak(読めない機体は None)。"
  (#^ (| int None) current-bytes)
  (#^ (| int None) peak-bytes))


(defrecord JobRoot
  "worker が起こした job の根の process 1 つ(name = job の名・pid = 根の pid — shim か待ちの子から分けた子 A)。"
  (#^ str name)
  (#^ int pid))


(defk task-root? [root]
  {:pre [(: root JobRoot)] :post [(: % bool)] :tags {:context "worker" :role "judgment"}}
  "job の根が task の物かを、名の頭(TASK-PREFIX)で判じるため。"
  (.startswith root.name TASK-PREFIX))


(defrecord JobUse
  "job 1 つの memory の今(job = job の名・pid = 根の pid・bytes = 根の木の rss の和・processes = 木の process の数)。"
  (#^ str job)
  (#^ int pid)
  (#^ int bytes)
  (#^ int processes))


(defrecord KillUnit
  "止める単位 1 つ(頭の註): job = 属する job の名・kind = 種類・head = 単位の頭の process・bytes = 単位の rss の和・
   members = 単位の process(頭が先)。"
  (#^ str job)
  (#^ UnitKind kind)
  (#^ ProcRow head)
  (#^ int bytes)
  (#^ (get tuple #(ProcRow ...)) members))


(defrecord JobSteady
  "job 1 つ(名ごと)の平常の大きさ: job = job の名・bytes = 前の最大の行の間ごとの job の memory の最大のいちばん大きい物(見張りが
   止めた間の最大は入れない — 跳ねた大きさを平常にしない)。"
  (#^ str job)
  (#^ int bytes))


(defk steady-sizes [windows]
  {:pre [(: windows tuple)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "前の最大の行の間ごとの job の最大(windows = 間ごとの JobSteady の列の列)を、job の名ごとの平常の大きさ(いちばん大きい物 — 名の
   昇順)にするため。"
  (val names (sorted (sfor window windows steady window steady.job)))
  (tuple (gfor name names
               (JobSteady :job name :bytes (max (gfor window windows steady window :if (= steady.job name) steady.bytes))))))


(defk beyond-steady? [unit uses steadies limits]
  {:pre [(: unit KillUnit) (: uses tuple) (: steadies tuple) (: limits MemoryLimits)] :post [(: % bool)]
   :tags {:context "worker" :role "judgment"}}
  "単位が止める候補かを判じるため(頭の註): TASK は候補。常駐の job の単位は、その job の今の memory(uses の同じ名の和)が平常の大きさ
   (steadies の同じ名)の steady-ratio 倍以上か、平常の大きさがまだ無い時に候補。"
  (when (= unit.kind UnitKind.TASK)
    (return True))
  (val steady (next (gfor s steadies :if (= s.job unit.job) s) None))
  (when (is steady None)
    (return True))
  (val now (sum (gfor use uses :if (= use.job unit.job) use.bytes)))
  (>= now (* limits.steady-ratio steady.bytes)))


(defk spared-steady [units uses steadies limits]
  {:pre [(: units tuple) (: uses tuple) (: steadies tuple) (: limits MemoryLimits)] :post [(: % tuple)]
   :tags {:context "worker" :role "judgment"}}
  "min-victim-bytes 以上の単位を持つのに、平常の大きさで動くので止める候補にしなかった job の平常の大きさ(JobSteady — 名の昇順)を
   並べるため(止める単位が無い時の log に、止めなかった job を名指す)。"
  ;; 大きさを先に問う(小さい単位は数万に割れうるので、単位ごとに beyond-steady? を呼ばない — card ki-72f309f23e0f)。
  (val large (tuple (gfor unit units :if (>= unit.bytes limits.min-victim-bytes) unit)))
  (<- beyond tuple (sequence (gfor unit large (beyond-steady? unit uses steadies limits))))
  (val names (frozenset (gfor #(unit candidate) (zip large beyond) :if (not candidate) unit.job)))
  (tuple (gfor s steadies :if (in s.job names) s)))


(defk mib-of [size]
  {:pre [(: size int)] :post [(: % int)] :tags {:context "worker" :role "judgment"}}
  "log に書く byte の数を MiB(切り捨て)にするため。"
  (// size MIB))


(defk meminfo-kib [text key]
  {:pre [(: text str) (: key str)] :post [(: % int)] :tags {:context "worker" :role "judgment"}}
  "/proc/meminfo の中身から key の行の値(kB)を読むため。行が無い・数でないなら ValueError(黙って 0 にしない)。"
  (val found (lfor line (.splitlines text) :setv parts (.split line) :if (and parts (= (get parts 0) (+ key ":"))) parts))
  (when (or (not found) (< (len (get found 0)) 2) (not (.isdigit (get (get found 0) 1))))
    (raise (ValueError (.format "/proc/meminfo に {} の行が無いか数でない" key))))
  (int (get (get found 0) 1)))


(defk pressure-avg10 [text kind]
  {:pre [(: text str) (: kind str) (in kind #{"some" "full"})] :post [(: % float)] :tags {:context "worker" :role "judgment"}}
  "/proc/pressure/memory の中身から kind(some か full)の行の avg10(%)を読むため。行か欄が無いなら ValueError。"
  (val values (lfor line (.splitlines text) :setv parts (.split line) :if (and parts (= (get parts 0) kind))
                    field (cut parts 1 None) :if (.startswith field "avg10=") (cut field 6 None)))
  (when (not values)
    (raise (ValueError (.format "/proc/pressure/memory に {} の avg10 が無い" kind))))
  (float (get values 0)))


(defk node-memory-of [meminfo pressure]
  {:pre [(: meminfo str) (: pressure (| str None))] :post [(: % NodeMemory)] :tags {:context "worker" :role "judgment"}}
  "/proc/meminfo の中身と /proc/pressure/memory の中身(PSI の無い機体は None)を機体の memory の読みにするため。"
  (<- total int (meminfo-kib meminfo "MemTotal"))
  (<- available int (meminfo-kib meminfo "MemAvailable"))
  (var some None)
  (var full None)
  (when (is-not pressure None)
    (<- read-some float (pressure-avg10 pressure "some"))
    (<- read-full float (pressure-avg10 pressure "full"))
    (:= some read-some)
    (:= full read-full))
  (NodeMemory :total-bytes (* total 1024) :available-bytes (* available 1024) :some-avg10 some :full-avg10 full))


(defk cgroup-bytes-of [text]
  {:pre [(: text (| str None))] :post [(: % (| int None))] :tags {:context "worker" :role "judgment"}}
  "cgroup の memory の file 1 つの中身(読めなければ None)を byte の数にするため。数でない中身は None(memory.max の max など)。"
  (val stripped (if (is text None) "" (.strip text)))
  (if (.isdigit stripped) (int stripped) None))


(defk memory-level [node limits]
  {:pre [(: node NodeMemory) (: limits MemoryLimits)] :post [(: % MemoryLevel)] :tags {:context "worker" :role "judgment"}}
  "機体の memory の様子を閾で 3 つに分けるため(MemoryLimits の註)。PSI の無い機体は空きだけで判じる。"
  (val ratio (/ node.available-bytes node.total-bytes))
  (val full (if (is node.full-avg10 None) 0.0 node.full-avg10))
  (cond
    (< ratio limits.danger-available) MemoryLevel.DANGER
    (and (< ratio limits.danger-pressured-available) (>= full limits.danger-full-avg10)) MemoryLevel.DANGER
    (or (< ratio limits.tight-available) (>= full limits.tight-full-avg10)) MemoryLevel.TIGHT
    True MemoryLevel.CALM))


(defrecord ProcTree
  "process の表の木の形: by-pid = 行の pid の昇順(pid ごとに 1 行)・pids = その pid の列・children = ppid → 子の行(pid の昇順 — 子の
   在る process だけ)。行は二分探索で、子は 1 度で引く(子を毎回 表の全部から探すと行の数の 2 乗)。"
  (#^ (get tuple #(ProcRow ...)) by-pid)
  (#^ (get tuple #(int ...)) pids)
  (#^ (get dict #(int tuple)) children))


(defk proc-tree-of [rows]
  {:pre [(: rows tuple)] :post [(: % ProcTree)] :tags {:context "worker" :role "judgment"}}
  "表の行を、木をたどる並びつきの形にするため(ProcTree の註)。同じ pid の行が 2 つ在れば後の行を使う(pid ごとに 1 行なら、根の外の
   行は親が 1 つで、木をたどって 2 度は届かない — _walk-of の註)。"
  (val unique (.values (dfor r rows r.pid r)))
  (val by-pid (tuple (sorted unique :key (attrgetter "pid"))))
  (val by-parent (sorted unique :key (attrgetter "ppid" "pid")))
  (ProcTree :by-pid by-pid :pids (tuple (map (attrgetter "pid") by-pid))
            :children (dfor #(ppid kids) (groupby by-parent :key (attrgetter "ppid")) ppid (tuple kids))))


(defk row-of [tree pid]
  {:pre [(: tree ProcTree) (: pid int)] :post [(: % (| ProcRow None))] :tags {:context "worker" :role "judgment"}}
  "pid の行を引くため(表に無い = 読む間に終わった — None)。"
  (val at (bisect-left tree.pids pid))
  (if (and (< at (len tree.pids)) (= (get tree.pids at) pid)) (get tree.by-pid at) None))


(defrecord _TreeWalk
  "row を頭とする木を 1 度たどった結末: members = 木の process(頭が先の深さ優先・兄弟は pid の昇順)・sizes = members の各々を頭と
   する木の rss の和・counts = その木の process の数・kids = その子の members の位置(pid の昇順)— どれも members と同じ位置。
   深さ優先の順なので、位置 i を頭とする木は members の i から counts[i] 個の連なり — 子の木を数え直さずに大きさと process を
   引ける(card ki-72f309f23e0f: 子の木を 1 本ずつたどり直すと、shim が引き取った孤児が 8.2 万本の時に見張りの 1 回が main の
   scheduler を何十分も塞いだ)。"
  (#^ (get tuple #(ProcRow ...)) members)
  (#^ (get tuple #(int ...)) sizes)
  (#^ (get tuple #(int ...)) counts)
  (#^ (get tuple #(tuple ...)) kids))


(defk _walk-of [tree row]
  {:pre [(: tree ProcTree) (: row ProcRow)] :post [(: % _TreeWalk)] :tags {:context "worker" :role "judgment"}}
  "row を頭とする木を、木の process の数に比例する手間で数えるため(_TreeWalk の註)。深さごとの段を上から作り(段 = その深さの行・
   行ごとの子の数・次の段 — 次の段はこの段の行の子を順に繋いだ物なので、行 i の子は次の段の starts[i] から starts[i+1] の前まで)、
   下の段から木の大きさと数を足し上げ、上の段から深さ優先の位置を振り(子の位置 = 親の位置 + 1 + 前の兄弟の木の数)、位置の順に
   並べる。1 歩ごとに dict を引かず、段の列の切り出しで数える(10 万行で 1 秒を越えないため)。根へ戻る子は除く — 根の ppid が
   木の中を指す読みの食い違いでも回らない(表は pid ごとに 1 行なので、根の外の行に届く道は 1 つ)。"
  (val head row.pid)
  (val pid-of (attrgetter "pid"))
  ;; 根へ戻る子が居うるのは根の親の子の組だけ(そこだけ根を除く)。
  (val kids-of (fn [pid] (if (= pid row.ppid)
                             (tuple (gfor c (.get tree.children pid #()) :if (!= c.pid head) c))
                             (.get tree.children pid #()))))
  (val grow (fn [rows] (let [kids (tuple (map kids-of (map pid-of rows)))]
                         #(rows (tuple (map len kids)) (tuple (chain.from-iterable kids))))))
  (val levels (tuple (takewhile (fn [level] (get level 0))
                                (accumulate (repeat None) (fn [level _] (grow (get level 2))) :initial (grow #(row))))))
  (val starts (tuple (gfor level levels (tuple (accumulate (get level 1) :initial 0)))))
  ;; 段ごとの #(木の rss の和の列 木の process の数の列)(下の段から足し上げ、段の順に並べ直す — 最後は子の無い段の下の空)。
  (val tally (fn [below depth]
               (let [edges (get starts depth)
                     spans (tuple (zip edges (cut edges 1 None)))
                     #(sizes counts) below]
                 #((tuple (gfor #(r #(a b)) (zip (get (get levels depth) 0) spans)
                                (if (< a b) (+ r.rss-bytes (sum (cut sizes a b))) r.rss-bytes)))
                   (tuple (gfor #(a b) spans (if (< a b) (+ 1 (sum (cut counts a b))) 1)))))))
  (val tallies (tuple (reversed (tuple (accumulate (range (- (len levels) 1) -1 -1) tally :initial #(#() #()))))))
  ;; 段ごとの深さ優先の位置(上の段から)。
  (val place (fn [places depth]
               (let [edges (get starts depth)
                     counts (get (get tallies (+ depth 1)) 1)]
                 (tuple (chain.from-iterable (gfor #(at a b) (zip places edges (cut edges 1 None)) :if (< a b)
                                                   (islice (accumulate (cut counts a b) :initial (+ at 1)) (- b a))))))))
  (val placed (tuple (accumulate (range (- (len levels) 1)) place :initial #(0))))
  (val kid-places (tuple (gfor #(edges below) (zip starts (+ (cut placed 1 None) #(#())))
                               (tuple (gfor #(a b) (zip edges (cut edges 1 None)) (cut below a b))))))
  ;; 段の列を繋いで、位置の順に並べ直す。
  (val flat (fn [columns] (tuple (chain.from-iterable columns))))
  (val positions (flat placed))
  (val order (sorted (range (len positions)) :key (. positions __getitem__)))
  (val ordered (fn [column] (tuple (map (. column __getitem__) order))))
  (_TreeWalk :members (ordered (flat (gfor level levels (get level 0))))
            :sizes (ordered (flat (gfor t (cut tallies 0 -1) (get t 0))))
            :counts (ordered (flat (gfor t (cut tallies 0 -1) (get t 1))))
            :kids (ordered (flat kid-places))))


(defrecord _JobWalk
  "job 1 つの根と、その木を 1 度たどった結末(root = JobRoot・walk = _TreeWalk)。"
  (#^ JobRoot root)
  (#^ _TreeWalk walk))


(defk _job-walks [rows roots]
  {:pre [(: rows tuple) (: roots tuple)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "表を木の形にし、job の根ごとに木を 1 度だけたどるため(_JobWalk の列 — roots の順)。見張りの 1 回はこれを _uses-of と _units-of で
   分けて使い、表を並べ直す・木をたどるのを 1 度で済ませる。表に根の無い job(読む間に終わった)は入れない。"
  (<- tree ProcTree (proc-tree-of rows))
  (<- found tuple (sequence (gfor root roots (row-of tree root.pid))))
  (val present (tuple (gfor #(root r) (zip roots found) :if (is-not r None) #(root r))))
  (<- walked tuple (sequence (gfor #(_ r) present (_walk-of tree r))))
  (tuple (gfor #(#(root _) walk) (zip present walked) (_JobWalk :root root :walk walk))))


(defk _uses-of [walks]
  {:pre [(: walks tuple)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "job ごとの memory の今(JobUse)を、根の木の rss の和で数えるため(walks = _job-walks の答え)。大きい順。"
  (tuple (sorted (gfor w walks (JobUse :job w.root.name :pid w.root.pid :bytes (get w.walk.sizes 0) :processes (get w.walk.counts 0)))
                 :key (fn [use] #((- use.bytes) use.job)))))


(defk job-uses [rows roots]
  {:pre [(: rows tuple) (: roots tuple)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "job ごとの memory の今(JobUse)を、表と根から数えるため(_job-walks と _uses-of の組)。"
  (<- walks tuple (_job-walks rows roots))
  (<- uses tuple (_uses-of walks))
  uses)


(defrecord Gathering
  "常駐の job の木を下りた結末(頭の註): point = 集まりの点・sides = 下りなかった兄弟(根から順)— どちらも _TreeWalk の位置。"
  (#^ int point)
  (#^ (get tuple #(int ...)) sides))


(defk gathering-of [walk]
  {:pre [(: walk _TreeWalk)] :post [(: % Gathering)] :tags {:context "worker" :role "judgment"}}
  "常駐の job の集まりの点を求めるため(頭の註): いちばん大きい子の木が今の木の DOMINANT-SHARE 以上で、今の process 自身の rss 以上の間は
   その子へ下り、下りなかった兄弟を残す。子の木の大きさは walk の数え済みの物を引く(下りるたびに子の木をたどり直さない)。"
  (val down (fn [here]
              (let [kids (get walk.kids here)
                    top (max kids :key (fn [c] #((get walk.sizes c) (- (. (get walk.members c) pid)))) :default None)]
                (if (and (is-not top None) (>= (get walk.sizes top) (* DOMINANT-SHARE (get walk.sizes here)))
                         (>= (get walk.sizes top) (. (get walk.members here) rss-bytes)))
                    top
                    None))))
  ;; 下りた道(根の位置 0 から集まりの点まで)。
  (val path (tuple (takewhile (fn [p] (is-not p None)) (accumulate (repeat None) (fn [here _] (down here)) :initial 0))))
  (Gathering :point (get path -1)
             :sides (tuple (gfor #(here next) (zip path (cut path 1 None)) c (get walk.kids here) :if (!= c next) c))))


(defk units-of-job [walk root]
  {:pre [(: walk _TreeWalk) (: root JobRoot)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "job 1 つの止める単位を並べるため(頭の註)。根(walk の頭)は単位に入れない。"
  (<- task bool (task-root? root))
  (val members walk.members)
  (when task
    (return (if (> (len members) 1)
                #((KillUnit :job root.name :kind UnitKind.TASK :head (get members 1)
                            :bytes (- (get walk.sizes 0) (. (get members 0) rss-bytes)) :members (cut members 1 None)))
                #())))
  (<- gathering Gathering (gathering-of walk))
  (val point gathering.point)
  (val head (get members point))
  (val children (get walk.kids point))
  (val own (cond (= point 0) #()
                 children #((KillUnit :job root.name :kind UnitKind.BODY :head head :bytes head.rss-bytes :members #(head)))
                 True #((KillUnit :job root.name :kind UnitKind.LEAF :head head :bytes head.rss-bytes :members #(head)))))
  ;; 子の木(BRANCH)は walk の連なりを切り出す — 単位ごとに木をたどり直さない。
  (+ own (tuple (gfor at (+ gathering.sides children)
                      (KillUnit :job root.name :kind UnitKind.BRANCH :head (get members at) :bytes (get walk.sizes at)
                                :members (cut members at (+ at (get walk.counts at))))))))


(defk _units-of [walks]
  {:pre [(: walks tuple)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "全 job の止める単位を、大きい順に並べるため(walks = _job-walks の答え・同じ大きさは job の名・頭の pid の順)。"
  (<- found tuple (sequence (gfor w walks (units-of-job w.walk w.root))))
  ;; 大きい順(同じ大きさは job の名・頭の pid の順)— 安定な並べ替えを 2 度(下位の鍵から)。
  (tuple (sorted (sorted (chain.from-iterable found) :key (attrgetter "job" "head.pid")) :key (attrgetter "bytes") :reverse True)))


(defk kill-units [rows roots]
  {:pre [(: rows tuple) (: roots tuple)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "全 job の止める単位を、表と根から並べるため(_job-walks と _units-of の組)。"
  (<- walks tuple (_job-walks rows roots))
  (<- units tuple (_units-of walks))
  units)


(defk victim-of [units uses steadies limits]
  {:pre [(: units tuple) (: uses tuple) (: steadies tuple) (: limits MemoryLimits)] :post [(: % (| KillUnit None))]
   :tags {:context "worker" :role "judgment"}}
  "DANGER の時に止める単位を選ぶため(頭の註の順): 止める候補(beyond-steady? — uses = job ごとの今の memory・steadies = job ごとの平常の
   大きさ)の中で、BRANCH・LEAF・TASK のいちばん大きい物、それが min-victim-bytes 未満なら BODY のいちばん大きい物。どれも無ければ止めない
   (None — 食った物は job の外か、平常の大きさで動く常駐の job だけ)。units は大きい順。"
  ;; 大きさを先に問う(小さい単位は数万に割れうるので、単位ごとに beyond-steady? を呼ばない — card ki-72f309f23e0f)。
  (val large (tuple (gfor u units :if (>= u.bytes limits.min-victim-bytes) u)))
  (<- beyond tuple (sequence (gfor u large (beyond-steady? u uses steadies limits))))
  (val candidates (tuple (gfor #(u candidate) (zip large beyond) :if candidate u)))
  (val leaves (tuple (gfor u candidates :if (!= u.kind UnitKind.BODY) u)))
  (val bodies (tuple (gfor u candidates :if (= u.kind UnitKind.BODY) u)))
  (cond leaves (get leaves 0)
        bodies (get bodies 0)
        True None))


(defk swarm-need [node limits]
  {:pre [(: node NodeMemory) (: limits MemoryLimits)] :post [(: % int) (>= % limits.min-victim-bytes)]
   :tags {:context "worker" :role "judgment"}}
  "群れの止めで戻す量を求めるため(頭の註): 機体の空きを DANGER の線(全量 × danger-available)の上へ min-victim-bytes ぶん戻す量。
   線の上に居る時(PSI で DANGER)も min-victim-bytes。"
  (val line (int (* node.total-bytes limits.danger-available)))
  (max limits.min-victim-bytes (- (+ line limits.min-victim-bytes) node.available-bytes)))


(defk swarm-of [units uses node container-bytes limits]
  {:pre [(: units tuple) (: uses tuple) (: node NodeMemory) (: container-bytes (| int None)) (: limits MemoryLimits)]
   :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "DANGER で止める候補の単位が無い時に、まとめて止める単位を選ぶため(頭の註の群れの止め): uses(大きい順)の中で、木が機体の全量の
   swarm-share 以上で、worker の外の使用量(機体の使用量 − container-bytes — 読めなければ問わない)より大きい最初の job の、BODY 以外の
   単位(units — 大きい順)を、和が swarm-need に届くまで頭から足す(届かなければ全部)。当たる job が無い・単位が無ければ空。"
  (val used (- node.total-bytes node.available-bytes))
  (val outside (if (is container-bytes None) None (max 0 (- used container-bytes))))
  (val job (next (gfor use uses
                       :if (and (>= use.bytes (* limits.swarm-share node.total-bytes)) (or (is outside None) (> use.bytes outside)))
                       use)
                 None))
  (when (is job None)
    (return #()))
  (<- need int (swarm-need node limits))
  (val mine (tuple (gfor unit units :if (and (= unit.job job.job) (!= unit.kind UnitKind.BODY)) unit)))
  ;; 頭から、それより前の和が need に届くまで(届いた所で止める)。
  (tuple (gfor #(unit freed) (zip mine (accumulate (gfor unit mine unit.bytes) :initial 0)) :if (< freed need) unit)))
