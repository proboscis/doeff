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
(require doeff-hy.macros [defk val])
(require doeff-hy.record [defrecord defenum])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import enum [StrEnum])  ; defenum の展開が名指す
(import bisect [bisect-left bisect-right])
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
   時に、小さい job を次々に止めない)。既定の値の根拠: atlas の 3 回は空き 1.5〜2.5 GiB / 28.3 GiB(5〜9%)・zeus は 7 GB / 125 GiB(5.6%)
   で止まり、ふだんの最低は atlas 6.2 GiB(22%)・zeus 52 GiB(41%)。"
  {:check [(< 0 danger-available danger-pressured-available 1) (< 0 danger-available tight-available 1) (> danger-full-avg10 0)
           (> tight-full-avg10 0) (>= min-victim-bytes 0)]}
  (setv #^ float danger-available 0.10)
  (setv #^ float danger-pressured-available 0.20)
  (setv #^ float danger-full-avg10 25.0)
  (setv #^ float tight-available 0.20)
  (setv #^ float tight-full-avg10 10.0)
  (setv #^ int min-victim-bytes (* 512 MIB)))


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
  "process の表の木の形: by-pid = 行の pid の昇順・pids = その pid の列・by-parent = 行の (ppid pid) の昇順・parents = その ppid の列。
   見張りの 1 回の読みの中で木を何度もたどるので、子と行を二分探索で引く(子を毎回 表の全部から探すと行の数の 2 乗)。"
  (#^ (get tuple #(ProcRow ...)) by-pid)
  (#^ (get tuple #(int ...)) pids)
  (#^ (get tuple #(ProcRow ...)) by-parent)
  (#^ (get tuple #(int ...)) parents))


(defk proc-tree-of [rows]
  {:pre [(: rows tuple)] :post [(: % ProcTree)] :tags {:context "worker" :role "judgment"}}
  "表の行を、木をたどる並びつきの形にするため(ProcTree の註)。"
  (val by-pid (tuple (sorted rows :key (fn [r] r.pid))))
  (val by-parent (tuple (sorted rows :key (fn [r] #(r.ppid r.pid)))))
  (ProcTree :by-pid by-pid :pids (tuple (gfor r by-pid r.pid)) :by-parent by-parent :parents (tuple (gfor r by-parent r.ppid))))


(defk children-of [tree pid]
  {:pre [(: tree ProcTree) (: pid int)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "pid の子の行を pid の昇順で引くため。"
  (cut tree.by-parent (bisect-left tree.parents pid) (bisect-right tree.parents pid)))


(defk row-of [tree pid]
  {:pre [(: tree ProcTree) (: pid int)] :post [(: % (| ProcRow None))] :tags {:context "worker" :role "judgment"}}
  "pid の行を引くため(表に無い = 読む間に終わった — None)。"
  (val at (bisect-left tree.pids pid))
  (if (and (< at (len tree.pids)) (= (get tree.pids at) pid)) (get tree.by-pid at) None))


(defk subtree-of [tree row]
  {:pre [(: tree ProcTree) (: row ProcRow)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "row を頭とする木の process を、頭が先の順で並べるため(深さ優先 — 親の pid が子より大きい使い回しでも 1 度ずつ)。"
  (var found #())
  (var seen (frozenset))
  (var pending #(row))
  (while pending
    (val here (get pending 0))
    (:= pending (cut pending 1 None))
    (when (not-in here.pid seen)
      (:= seen (| seen #{here.pid}))
      (:= found (+ found #(here)))
      (<- children tuple (children-of tree here.pid))
      (:= pending (+ children pending))))
  found)


(defk job-uses [rows roots]
  {:pre [(: rows tuple) (: roots tuple)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "job ごとの memory の今(JobUse)を、根の木の rss の和で数えるため。表に根の無い job(読む間に終わった)は入れない。大きい順。"
  (<- tree ProcTree (proc-tree-of rows))
  (var uses #())
  (for [root roots]
    (<- root-row (| ProcRow None) (row-of tree root.pid))
    (when (is-not root-row None)
      (<- members tuple (subtree-of tree root-row))
      (:= uses (+ uses #((JobUse :job root.name :pid root.pid :bytes (sum (gfor r members r.rss-bytes)) :processes (len members)))))))
  (tuple (sorted uses :key (fn [use] #((- use.bytes) use.job)))))


(defk tree-bytes [tree row]
  {:pre [(: tree ProcTree) (: row ProcRow)] :post [(: % int)] :tags {:context "worker" :role "judgment"}}
  "row を頭とする木の rss の和を数えるため。"
  (<- members tuple (subtree-of tree row))
  (sum (gfor r members r.rss-bytes)))


(defrecord SizedChild
  "子 1 つと、その子を頭とする木の rss の和(bytes)。"
  (#^ ProcRow row)
  (#^ int bytes))


(defrecord Gathering
  "常駐の job の木を下りた結末(頭の註): point = 集まりの点・sides = 下りなかった兄弟の行(根から順)。"
  (#^ ProcRow point)
  (#^ (get tuple #(ProcRow ...)) sides))


(defk gathering-of [tree root-row]
  {:pre [(: tree ProcTree) (: root-row ProcRow)] :post [(: % Gathering)] :tags {:context "worker" :role "judgment"}}
  "常駐の job の集まりの点を求めるため(頭の註): いちばん大きい子の木が今の木の DOMINANT-SHARE 以上で、今の process 自身の rss 以上の間は
   その子へ下り、下りなかった兄弟を残す。"
  (var here root-row)
  (var sides #())
  (while True
    (<- children tuple (children-of tree here.pid))
    (var sized #())
    (for [child children]
      (<- size int (tree-bytes tree child))
      (:= sized (+ sized #((SizedChild :row child :bytes size)))))
    (when (not sized)
      (break))
    (val total (+ here.rss-bytes (sum (gfor c sized c.bytes))))
    (val top (max sized :key (fn [c] #(c.bytes (- c.row.pid)))))
    (if (and (>= top.bytes (* DOMINANT-SHARE total)) (>= top.bytes here.rss-bytes))
        (do (:= sides (+ sides (tuple (gfor c sized :if (!= c.row.pid top.row.pid) c.row))))
            (:= here top.row))
        (break)))
  (Gathering :point here :sides sides))


(defk branch-unit [tree name head]
  {:pre [(: tree ProcTree) (: name str) (: head ProcRow)] :post [(: % KillUnit)] :tags {:context "worker" :role "judgment"}}
  "head を頭とする木を BRANCH の単位にするため。"
  (<- members tuple (subtree-of tree head))
  (KillUnit :job name :kind UnitKind.BRANCH :head head :bytes (sum (gfor r members r.rss-bytes)) :members members))


(defk units-of-job [tree root-row root]
  {:pre [(: tree ProcTree) (: root-row ProcRow) (: root JobRoot)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "job 1 つの止める単位を並べるため(頭の註)。根は単位に入れない。"
  (<- below tuple (subtree-of tree root-row))
  (<- task bool (task-root? root))
  (val members (cut below 1 None))
  (when task
    (return (if members
                #((KillUnit :job root.name :kind UnitKind.TASK :head (get members 0) :bytes (sum (gfor r members r.rss-bytes))
                            :members members))
                #())))
  (<- gathering Gathering (gathering-of tree root-row))
  (val point gathering.point)
  (<- children tuple (children-of tree point.pid))
  (var units (cond (= point.pid root-row.pid) #()
                   children #((KillUnit :job root.name :kind UnitKind.BODY :head point :bytes point.rss-bytes :members #(point)))
                   True #((KillUnit :job root.name :kind UnitKind.LEAF :head point :bytes point.rss-bytes :members #(point)))))
  (for [head (+ gathering.sides children)]
    (<- unit KillUnit (branch-unit tree root.name head))
    (:= units (+ units #(unit))))
  units)


(defk kill-units [rows roots]
  {:pre [(: rows tuple) (: roots tuple)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "全 job の止める単位を、大きい順に並べるため(同じ大きさは job の名・頭の pid の順)。"
  (<- tree ProcTree (proc-tree-of rows))
  (var units #())
  (for [root roots]
    (<- root-row (| ProcRow None) (row-of tree root.pid))
    (when (is-not root-row None)
      (<- found tuple (units-of-job tree root-row root))
      (:= units (+ units found))))
  (tuple (sorted units :key (fn [unit] #((- unit.bytes) unit.job unit.head.pid)))))


(defk victim-of [units limits]
  {:pre [(: units tuple) (: limits MemoryLimits)] :post [(: % (| KillUnit None))] :tags {:context "worker" :role "judgment"}}
  "DANGER の時に止める単位を選ぶため(頭の註の順): BRANCH・LEAF・TASK のいちばん大きい物、それが min-victim-bytes 未満なら BODY の
   いちばん大きい物。どれも未満なら止めない(None — 食った物は job の外)。units は大きい順。"
  (val leaves (tuple (gfor u units :if (and (!= u.kind UnitKind.BODY) (>= u.bytes limits.min-victim-bytes)) u)))
  (val bodies (tuple (gfor u units :if (and (= u.kind UnitKind.BODY) (>= u.bytes limits.min-victim-bytes)) u)))
  (cond leaves (get leaves 0)
        bodies (get bodies 0)
        True None))
