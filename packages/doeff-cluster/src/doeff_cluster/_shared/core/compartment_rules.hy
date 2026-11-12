;;; machine の用途の区画の宣言(資源 MachineCompartments — ADR-DOE-CLUSTER-005 R1〜R3)の形の検め・綴り・写しの純粋な判断。
;;; coordinator の資源の口(作り・書き直し)・保存の読み直し・heartbeat の返事の綴り・worker の返事の読みと cgroup の書きの決めが同じ規則を
;;; 読む。型は doeff_cluster._shared.intent.compartment_model。
(require doeff-hy.macros [defk deff val var])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import collections.abc [Mapping Sequence])
(import posixpath)
(import doeff_cluster._shared.intent.compartment_model [Compartment MachineCompartments HeldCompartment COMPARTMENT-USES PROTECTED-USES
                                                       GUARDABLE-USES SINGLE-USES])


;; 1 MiB(byte)。
(val MIB (* 1024 1024))
;; spec の欄と、区画 1 行の欄(この外の欄は断る)。
(val MACHINE-COMPARTMENTS-KEYS (frozenset #("compartments")))
(val COMPARTMENT-KEYS (frozenset #("use" "worker" "layer" "memoryMiB" "memoryLimitMiB" "cpuMillis")))
;; 層の名(cgroup の 1 段の名 — WORKER_CGROUP_LAYOUT の名と同じく英小文字・数字・-)。
(val LAYER-CHARS (frozenset "abcdefghijklmnopqrstuvwxyz0123456789-"))


(deff whole-number? [#^ (| (get Mapping #(str object)) (get Sequence object) str int float bool None) value]  ; cannot be a defk: compartment-refusal の cond の述語(Program の外の純粋な判断)
  {:pre [(: value (| (get Mapping #(str object)) (get Sequence object) str int float bool None))] :post [(: % bool)] :tags {:context "doeff-cluster" :role "judgment"}}
  "値が 0 以上の整数か(真偽値は整数として受けない)を知るため。"
  (and (isinstance value int) (not (isinstance value bool)) (>= value 0)))


(deff guarded? [#^ str use #^ int memory-mib #^ int memory-limit-mib]  ; cannot be a defk: compartment-files の内包表記と site の宣言の読み(Program の外の純粋な判断)が呼ぶ
  {:pre [(: use str) (: memory-mib int) (: memory-limit-mib int)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "judgment"}}
  "区画が守る区画(層に memory.min = memoryMiB を掛け、worker の Pod の requests に数える — R3)かを知るため: use services と worker はいつも、
   hosted は memoryLimitMiB > memoryMiB を書いた時だけ(止めてはいけない仕事の層 — R3 の改め 2026-11-12)。"
  (or (in use PROTECTED-USES) (and (= use "hosted") (> memory-limit-mib memory-mib))))


(deff compartment-refusal [#^ str machine #^ int index #^ (| (get Mapping #(str object)) (get Sequence object) str int float bool None) row]  ; cannot be a defk: machine-compartments-refusal の loop(Program の外の純粋な判断)が呼ぶ
  {:pre [(: machine str) (: index int) (: row (| (get Mapping #(str object)) (get Sequence object) str int float bool None))] :post [(: % (| str None))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "machine の区画の列の index 番目の 1 行(JSON の object)が受けられない理由(受けられれば None)を知るため(R1 の書く時の検め)。"
  (setv where (.format "MachineCompartments {} の compartments[{}]" machine index))
  (when (not (isinstance row dict))
    (return (.format "{} は {{use worker layer memoryMiB memoryLimitMiB? cpuMillis}} の object: {!r}" where row)))
  (setv unknown (sorted (- (frozenset row) COMPARTMENT-KEYS)))
  (setv use (.get row "use"))
  (setv outside (= use "outside"))
  (setv worker (.get row "worker" ""))
  (setv layer (.get row "layer" ""))
  (setv memory (.get row "memoryMiB"))
  (setv limit (.get row "memoryLimitMiB" memory))
  (cond
    unknown (.format "{} の知らない欄: {}" where unknown)
    (not-in use COMPARTMENT-USES) (.format "{} の use は {} のどれか: {!r}" where (sorted COMPARTMENT-USES) use)
    (and outside (or worker layer)) (.format "{} の outside は worker も layer も名指さない: worker {!r}・layer {!r}" where worker layer)
    (and (not outside) (not (and (isinstance worker str) worker (not-in "/" worker))))
      (.format "{} の worker は「/」を含まない空でない文字列: {!r}" where worker)
    (and (not outside) (not (and (isinstance layer str) layer (<= (frozenset layer) LAYER-CHARS))))
      (.format "{} の layer は英小文字・数字・- の cgroup の 1 段の名: {!r}" where layer)
    (not (whole-number? memory)) (.format "{} の memoryMiB は 0 以上の整数: {!r}" where memory)
    (not (whole-number? limit)) (.format "{} の memoryLimitMiB は 0 以上の整数: {!r}" where limit)
    (and (in "memoryLimitMiB" row) (not-in use GUARDABLE-USES))
      (.format "{} の memoryLimitMiB は use {} だけが書く(ほかの区画の上限は memoryMiB): {!r}" where (sorted GUARDABLE-USES) limit)
    (< limit memory) (.format "{} の memoryLimitMiB {} が memoryMiB {} より小さい" where limit memory)
    (and (in "memoryLimitMiB" row) (= use "hosted") (<= limit memory))
      (.format "{} の守る hosted は memoryLimitMiB {} を memoryMiB {} より大きく書く(上限の無い守りは置かない)" where limit memory)
    (not (whole-number? (.get row "cpuMillis"))) (.format "{} の cpuMillis は 0 以上の整数: {!r}" where (.get row "cpuMillis"))
    True None))


(deff machine-compartments-refusal [#^ (| (get Mapping #(str object)) (get Sequence object) str int float bool None) name #^ (| (get Mapping #(str object)) (get Sequence object) str int float bool None) spec]  ; cannot be a defk: coordinator の資源の口と保存の読み直し(Program の外)が呼ぶ純粋な判断
  {:pre [(: name (| (get Mapping #(str object)) (get Sequence object) str int float bool None)) (: spec (| (get Mapping #(str object)) (get Sequence object) str int float bool None))]
   :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "machine 1 台の区画の宣言(name = machine の名・spec = {compartments: [...]})が、行 1 つの中で受けられない理由(受けられれば None)を
   知るため(R1): 欄の形・outside は高々 1 つ・(worker, layer) は行の中で 1 度・worker 1 つは tasks と services を高々 1 つずつ持つ。行を
   またぐ検め(worker は 1 台の machine の下だけ・(worker, layer) は全部の行で 1 度)は compartments-clash。"
  (when (not (and (isinstance name str) name (not-in "/" name)))
    (return (.format "machine の名は「/」を含まない空でない文字列: {!r}" name)))
  (when (not (isinstance spec dict))
    (return (.format "MachineCompartments {} の spec は {{compartments: [...]}} の object: {!r}" name spec)))
  (setv unknown (sorted (- (frozenset spec) MACHINE-COMPARTMENTS-KEYS)))
  (when unknown
    (return (.format "MachineCompartments {} の spec の知らない欄: {}" name unknown)))
  (setv rows (.get spec "compartments"))
  (when (not (isinstance rows list))
    (return (.format "MachineCompartments {} の compartments は区画の列: {!r}" name rows)))
  (for [#(index row) (enumerate rows)]
    (setv refusal (compartment-refusal name index row))
    (when (is-not refusal None)
      (return refusal)))
  (when (> (len (lfor row rows :if (= (get row "use") "outside") row)) 1)
    (return (.format "MachineCompartments {} の outside は高々 1 つ" name)))
  (setv seen #())
  (for [row rows :if (!= (get row "use") "outside")]
    (setv pair #((get row "worker") (get row "layer")))
    (when (in pair seen)
      (return (.format "MachineCompartments {} の worker {} の層 {} が 2 度在る(層 1 つに区画 1 つ)" name #* pair)))
    (setv seen (+ seen #(pair))))
  (for [use SINGLE-USES
        worker (sorted (sfor row rows :if (= (get row "use") use) (get row "worker")))]
    (when (> (len (lfor row rows :if (and (= (get row "use") use) (= (get row "worker") worker)) row)) 1)
      (return (.format "MachineCompartments {} の worker {} が use {} の区画を 2 つ持つ(高々 1 つ)" name worker use))))
  None)


(deff machine-compartments-of [#^ str name #^ (get Mapping #(str object)) spec]  ; cannot be a defk: coordinator の資源の口と保存の読み直し(Program の外)が呼ぶ純粋な読み
  {:pre [(: name str) (: spec (get Mapping #(str object)))] :post [(: % MachineCompartments)] :tags {:context "doeff-cluster" :role "judgment"}}
  "受けられる宣言(machine-compartments-refusal が None の名と spec)→ MachineCompartments。memoryLimitMiB の無い行は memoryMiB。"
  (MachineCompartments :machine name
                       :compartments (tuple (gfor row (get spec "compartments")
                                                  (Compartment :use (get row "use") :worker (.get row "worker" "") :layer (.get row "layer" "")
                                                               :memory-mib (get row "memoryMiB")
                                                               :memory-limit-mib (.get row "memoryLimitMiB" (get row "memoryMiB"))
                                                               :cpu-millis (get row "cpuMillis"))))))


(deff machine-compartments-spec [#^ MachineCompartments row]  ; cannot be a defk: coordinator の資源の行・保存の綴り(Program の外)が呼ぶ純粋な綴り
  {:pre [(: row MachineCompartments)] :post [(: % (get dict #(str object)))] :tags {:context "doeff-cluster" :role "judgment"}}
  "MachineCompartments → 資源の spec の JSON の形(machine-compartments-of の逆 — outside は worker と layer を、memoryMiB と同じ上限は
   memoryLimitMiB を置かない)。"
  {"compartments" (lfor c row.compartments
                        (| {"use" c.use}
                           (if (= c.use "outside") {} {"worker" c.worker "layer" c.layer})
                           {"memoryMiB" c.memory-mib}
                           (if (= c.memory-limit-mib c.memory-mib) {} {"memoryLimitMiB" c.memory-limit-mib})
                           {"cpuMillis" c.cpu-millis}))})


(deff compartments-clash [#^ (get tuple #(MachineCompartments ...)) others #^ MachineCompartments row]  ; cannot be a defk: coordinator の資源の口(Program の外)が呼ぶ純粋な判断
  {:pre [(: others (get tuple #(MachineCompartments ...))) (: row MachineCompartments)] :post [(: % (| str None))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "書く行 row が、ほかの machine の行 others(同じ名の行を除く)とぶつかる理由(ぶつからなければ None)を知るため(R1): worker 1 つは
   1 台の machine の下だけに在る(したがって (worker, layer) も全部の行で 1 度)。"
  (setv mine (frozenset (gfor c row.compartments :if c.worker c.worker)))
  (for [other others
        worker (sorted (& mine (frozenset (gfor c other.compartments :if c.worker c.worker))))]
    (return (.format "worker {} は MachineCompartments {} の下に在る — machine {} の下に重ねて書けない(worker は 1 台の machine の下だけ)"
                     worker other.machine row.machine)))
  None)


(deff held-compartments [#^ (get tuple #(MachineCompartments ...)) rows #^ str worker]  ; cannot be a defk: heartbeat の返事と GET /state の画面(coordinator の純粋な判断)が呼ぶ
  {:pre [(: rows (get tuple #(MachineCompartments ...))) (: worker str)] :post [(: % (get tuple #(HeldCompartment ...)))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "worker が持つ区画(R2 — 行からの写し・層の名の順)を知るため。どの行も名指さない worker は空(区画を持たない)。"
  (tuple (sorted (gfor row rows
                       c row.compartments
                       :if (= c.worker worker)
                       (HeldCompartment :use c.use :layer c.layer :memory-mib c.memory-mib :memory-limit-mib c.memory-limit-mib
                                        :cpu-millis c.cpu-millis))
                 :key (fn [h] h.layer))))


(deff held-compartment-json [#^ HeldCompartment held]  ; cannot be a defk: heartbeat の返事の綴りの内包表記(Program の外)が呼ぶ純粋な綴り
  {:pre [(: held HeldCompartment)] :post [(: % (get dict #(str object)))] :tags {:context "doeff-cluster" :role "judgment"}}
  "HeldCompartment → heartbeat の返事の compartments の 1 行の JSON の形(worker の held-compartments-of が読む)。"
  {"use" held.use "layer" held.layer "memoryMiB" held.memory-mib "memoryLimitMiB" held.memory-limit-mib "cpuMillis" held.cpu-millis})


(defk held-compartments-of [field]
  {:pre [(: field (| list dict str int float bool None))] :post [(: % (| (get tuple #(HeldCompartment ...)) None))]
   :tags {:context "doeff-cluster" :role "judgment" :reads "json"}}
  "heartbeat の返事の欄 compartments を、worker の持つ区画の列にするため(None = 欄の無い返事 — 退いた世代への返事・古い coordinator)。
   形の違う欄は ValueError(拍の except が名乗れないの 1 行にする — 黙って空に読まない: 空は「区画を持たない」と読まれる)。"
  (when (is field None)
    (return None))
  (when (not (isinstance field list))
    (raise (ValueError (.format "heartbeat の返事の compartments が列でない: {}" (. (type field) __name__)))))
  (var held #())
  (for [#(index row) (enumerate field)]
    (when (not (and (isinstance row dict) (in (.get row "use") COMPARTMENT-USES) (!= (.get row "use") "outside")
                    (isinstance (.get row "layer") str) (.get row "layer") (<= (frozenset (.get row "layer")) LAYER-CHARS)
                    (whole-number? (.get row "memoryMiB")) (whole-number? (.get row "memoryLimitMiB"))
                    (whole-number? (.get row "cpuMillis")) (>= (.get row "memoryLimitMiB") (.get row "memoryMiB"))))
      (raise (ValueError (.format "heartbeat の返事の compartments[{}] の形が違う: {!r}" index row))))
    (:= held (+ held #((HeldCompartment :use (get row "use") :layer (get row "layer") :memory-mib (get row "memoryMiB")
                                        :memory-limit-mib (get row "memoryLimitMiB") :cpu-millis (get row "cpuMillis"))))))
  held)


(defk compartment-files [held layers-dir]
  {:pre [(: held (get tuple #(HeldCompartment ...))) (: layers-dir str)] :post [(: % (get tuple #(tuple ...)))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "worker が持つ区画を層の cgroup の file に写す書きの列(#(path 中身) — 層の名の順・R3)を決めるため: 区画ごとに層 layers-dir/<layer> の
   memory.max = memoryLimitMiB、守る区画(guarded?)は memory.min = memoryMiB も。CPU の量は書かない(cpu.max を掛けない — R3)。"
  (tuple (gfor h held
               #(name value) (+ #(#("memory.max" h.memory-limit-mib))
                                (if (guarded? h.use h.memory-mib h.memory-limit-mib) #(#("memory.min" h.memory-mib)) #()))
               #((posixpath.join layers-dir h.layer name) (str (* value MIB))))))


(defk held-limit-bytes [held layer]
  {:pre [(: held (get tuple #(HeldCompartment ...))) (: layer str)] :post [(: % (| int None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "層 layer を区画が持てば、その memory.max(byte — memoryLimitMiB)を知るため(None = 区画の無い層 — 見張りが今までどおり掛け直す)。"
  (next (gfor h held :if (= h.layer layer) (* h.memory-limit-mib MIB)) None))
