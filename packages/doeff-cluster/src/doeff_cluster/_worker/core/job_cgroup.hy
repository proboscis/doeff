;;; 子の job を置く cgroup の判断: worker の本体(heartbeat を送る process)と、worker が起こす子 process(job の
;;; shim・待ちの子・入口の検め・木と root の準備の git や uv)を別の cgroup に置くため。機体の配備(systemd の unit — Delegate= と
;;; DelegateSubgroup=)は本体を自分の cgroup に置き、その兄弟の cgroup にだけ CPU の重み(cpu.idle)と枠(cpu.max)を掛ける。
;;; 子は fork した親の cgroup を継ぐので、配備だけでは分けられない — worker が子を起こす時に兄弟の cgroup へ入れる。
;;;
;;; 入れ方: 子の argv を `/bin/sh -c PLACE-SCRIPT <兄弟の cgroup.procs> <元の argv>` に包む。sh は自分の pid を cgroup.procs へ書いて
;;; 兄弟の cgroup へ移り、元の argv を exec する(pid は変わらない — 子の表・process group・止めはそのまま効く)。書けなければ sh は
;;; 元の命令を走らせずに 0 以外で終わる(置けない子を本体の cgroup で黙って走らせない)。
;;; ⚠ 包んだ子は「命令が無い」を ProcessNotStarted でなく、立った子の終わり(sh の 127)で答える(外の答え手が確かめるのは /bin/sh)。
;;;
;;; 兄弟の名は worker の環境変数 DOEFF_WORKER_JOB_CGROUP(worker/entry/main が読む・無ければ包まない)。
;;; path は worker の自分の cgroup(/proc/self/cgroup の cgroup v2 の行 `0::<path>`)の親の下の名。
;;;
;;; service の類: 機体の配備が DOEFF_WORKER_SERVICE_CGROUP も名指すと、常駐の job(JobSpec.once が偽)の shim だけを
;;; その兄弟へ置く(worker/protocol/process_host の start-job)。配備は兄弟ごとに memory の守り(memory.min)と上限(memory.max)を分けて掛け、
;;; 1 本の task が上限まで memory を使っても、kernel はその兄弟の中だけで回収して止め、常駐の job の兄弟は回収されない。
;;;
;;; 根(root): 兄弟の cgroup.procs を書く cgroup v2 の mount の場所。既定は CGROUP-ROOT。k8s の Pod の worker の /sys/fs/cgroup は自分の
;;; container の cgroup を根とする名前空間の読み取りだけの見えなので、起動の script(deploy/boot.sh の層 — WORKER_CGROUP_LAYOUT)が
;;; 書ける見え(host の cgroupfs の mount の中の、同じ cgroup の dir)を DOEFF_WORKER_CGROUP_DIR で渡す。/proc/self/cgroup の path は
;;; 名前空間の根からの path なので、その見えの下に繋げる。
;;;
;;; job ごとの子(jobs の層の下 — 機体の書きを job の名で読むため): task(JobSpec.once)は jobs の層の下の job の名の子
;;; (job-subgroup-name)に置き、名の無い子(shim の外の git・uv・待ちの子・入口の検め)は共有の子 SHARED-CGROUP に置く。cgroup v2 は子に
;;; controller を掛けた cgroup に process を置けないので、jobs の層そのものには process を置かない — 層に process が残っていない時だけ、層の
;;; 子へ io を掛ける(親が io を渡す時だけ — 子ごとの io.stat が読めるようになる)。memory.max・cpu の値と PSI は層 jobs のまま(子を含めて数える)。
;;; 終わった job の子は、次の task を置く前の掃き(SWEEP-SCRIPT — 宿 worker/protocol/process_host)が io.stat を読んでから rmdir で消す。
(require doeff-hy.macros [defk val])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import hashlib)
(import posixpath)
(import re)


;; cgroup v2 の根の mount の場所。
(val CGROUP-ROOT "/sys/fs/cgroup")
;; 子の頭で走る sh の script: $0 = 兄弟の cgroup.procs・"$@" = 元の argv。
(val PLACE-SCRIPT "echo $$ >\"$0\" && exec \"$@\"")
;; jobs の層の下の、job の名の無い子を置く共有の子の名(_ で始まる — job の名の子は _ で始めないので重ならず、掃きも消さない)。
(val SHARED-CGROUP "_shared")
;; job の名の子の名の長さの上限(cgroupfs の名は 255 byte まで — 長い名は頭を残して指紋を付ける)。
(val SUBGROUP-NAME-MAX 120)
;; 掃きの sh の script: $0 = jobs の層の dir・"$@" = 消さない子の名(走っている job)。_ で始まる子と残す名を除いた子ごとに、io.stat を
;; 読んでから rmdir し(process の居る子は rmdir が断るので残る)、消せた子を「<名>\t<io.stat の行を ; で繋いだ物>」の 1 行で出す。最後に、
;; 層に io が渡されていて子へまだ掛けていなければ掛けを試す(層に process が残っている間は kernel が断る — 次の掃きで試し直す)。
(val SWEEP-SCRIPT
  (+ "for d in \"$0\"/*/; do n=${d%/}; n=${n##*/}; case $n in _*|'*') continue ;; esac; "
     "k=; for x in \"$@\"; do [ \"$x\" = \"$n\" ] && k=1; done; [ -z \"$k\" ] || continue; "
     "s=$(tr '\\n' ';' <\"$0/$n/io.stat\" 2>/dev/null); rmdir \"$0/$n\" 2>/dev/null && printf '%s\\t%s\\n' \"$n\" \"$s\"; done; "
     "if grep -qw io \"$0/cgroup.controllers\" 2>/dev/null && ! grep -qw io \"$0/cgroup.subtree_control\" 2>/dev/null; then "
     "echo +io >\"$0/cgroup.subtree_control\" 2>/dev/null; fi; exit 0"))


(defk job-cgroup-procs [own-cgroup sibling [root CGROUP-ROOT]]
  {:pre [(: own-cgroup str) (: sibling str) (: root str)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "子を置く兄弟の cgroup の cgroup.procs の path を決めるため。own-cgroup = /proc/self/cgroup の中身・sibling = 兄弟の名
   (DOEFF_WORKER_JOB_CGROUP か DOEFF_WORKER_SERVICE_CGROUP の値)・root = 書ける cgroup v2 の見えの根(頭の註)。cgroup v2 の行が無い・
   自分が根に居る(兄弟が無い)・名が 1 段の名でない時は断る(ValueError — 置けない子を本体の cgroup で黙って走らせる起動にしない)。"
  (val lines (lfor line (.splitlines own-cgroup) :if (.startswith line "0::") (cut line 3 None)))
  (when (!= (len lines) 1)
    (raise (ValueError (.format "/proc/self/cgroup に cgroup v2 の行(0::)がちょうど 1 つ無い: {!r}" own-cgroup))))
  (val own (.strip (get lines 0)))
  (when (or (not (.startswith own "/")) (= own "/") (in ".." (.split own "/")))
    (raise (ValueError (.format "worker の cgroup {!r} の親の下に兄弟を置けない(根に居るか、名前空間の外の path)" own))))
  (when (or (not sibling) (in "/" sibling) (in sibling #("." "..")))
    (raise (ValueError (.format "兄弟の cgroup の名 {!r} は 1 段の名で書く(DOEFF_WORKER_JOB_CGROUP・DOEFF_WORKER_SERVICE_CGROUP)" sibling))))
  (posixpath.join root (cut (posixpath.dirname own) 1 None) sibling "cgroup.procs"))


(defk layer-cpu-pressure [procs]
  {:pre [(: procs str)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "兄弟の cgroup(procs = job-cgroup-procs の答えの cgroup.procs)の CPU の PSI の file の path を決めるため。機体の測り
   (worker/core/load_rules の LoadPaths の task-cpu-pressure)が、task を置く層の実行の待ちを読む。"
  (posixpath.join (posixpath.dirname procs) "cpu.pressure"))


(defk placed-argv [procs argv]
  {:pre [(: procs str) (: argv tuple)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "子の argv を、兄弟の cgroup へ移ってから元の argv を exec する sh で包むため(頭の註)。内側の宿が自分の子を別の兄弟へ包んだ argv
   (常駐の job の services・実行環境の準備の prepare)は包み直さない — jobs の層の上限は追い出しの線から掛け直され 0 まで下がるので、jobs を
   経ると移る前に OOM kill で止まる(2026-11-04 22:05 JST の k3s-0: 上限 0 MiB の間、準備の process が終了 -9 で 5 回続けて落ちた)。"
  (if (= (cut argv 3) #("/bin/sh" "-c" PLACE-SCRIPT))
      argv
      (+ #("/bin/sh" "-c" PLACE-SCRIPT procs) argv)))


(defk job-subgroup-name [name]
  {:pre [(: name str)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "job の名から、jobs の層の下の job の子の名を決めるため(頭の註の「job ごとの子」)。英数字・-・_ の外の字(/ や .)は _ にする(. を
   残さないので cgroup の file の名 cgroup.procs・io.stat と重ならない)。_ で始まる名は頭に job を付ける(共有の子 SHARED-CGROUP と掃きの
   除外に重ねない)。SUBGROUP-NAME-MAX を越える名は頭を残し、元の名の sha256 の頭 12 桁を付ける。"
  (var safe (re.sub "[^A-Za-z0-9_-]" "_" name))
  (when (or (not safe) (.startswith safe "_"))
    (:= safe (+ "job" safe)))
  (if (<= (len safe) SUBGROUP-NAME-MAX)
      safe
      (+ (cut safe (- SUBGROUP-NAME-MAX 13)) "-" (cut (.hexdigest (hashlib.sha256 (.encode name "utf-8"))) 12))))


(defk layer-subgroup-procs [procs child]
  {:pre [(: procs str) (: child str)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "層(procs = job-cgroup-procs の答えの、層の cgroup.procs)の下の子 child の cgroup.procs の path を決めるため(共有の子 SHARED-CGROUP か
   job の子 job-subgroup-name)。"
  (posixpath.join (posixpath.dirname procs) child "cgroup.procs"))


(defk sweep-argv [procs kept]
  {:pre [(: procs str) (: kept tuple)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "層(procs = 層の cgroup.procs)の下の終わった job の子を消す掃きの argv を作るため(SWEEP-SCRIPT・kept = 消さない job の子の名)。"
  (+ #("/bin/sh" "-c" SWEEP-SCRIPT (posixpath.dirname procs)) kept))


(defn #^ tuple io-row [#^ str line]  ; defk にできない: io-totals の内包表記の中で 1 行ずつ呼ぶ(Program を返すと並びに組めない)
  "io.stat の 1 行を #(<major:minor> 読んだ byte 書いた byte) に(device の行でなければ空の tuple)。rbytes・wbytes の無い行と数でない値は 0。"
  (setv parts (.split line))
  (if (not (and parts (re.fullmatch "[0-9]+:[0-9]+" (get parts 0))))
      #()
      (do (setv fields (dfor part (cut parts 1 None) :if (in "=" part) (get (.split part "=" 1) 0) (get (.split part "=" 1) 1)))
          #((get parts 0) (int-or-zero (.get fields "rbytes" "")) (int-or-zero (.get fields "wbytes" ""))))))


(defn #^ int int-or-zero [#^ str text]  ; defk にできない: io-row の欄の読みの中の小さな変換(Program を返すと数に組めない)
  "10 進の数の綴りを数に(綴りが数でなければ 0)。"
  (if (.isdigit text) (int text) 0))


(defk io-totals [stat]
  {:pre [(: stat str)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "io.stat の中身(行か、掃きの出す ; で繋いだ行)を、device ごとの #(<major:minor> 読んだ byte 書いた byte) の並びにするため(device の
   順)。rbytes・wbytes の無い行と形の違う欄は 0 に数える(kernel の版で欄が増えても読みを落とさない)。"
  (tuple (sorted (gfor row (gfor line (.split (.replace stat ";" "\n") "\n") (io-row line)) :if row row))))


(defk swept-groups [stdout]
  {:pre [(: stdout str)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "掃きの出力(SWEEP-SCRIPT — 1 行に「<子の名>\t<io.stat>」)を、消した子ごとの #(子の名 io-totals の答え) の並びにするため(形の違う行は
   飛ばす)。io-totals は Program を返すので内包表記では呼べない: for と ! で順に読み、tuple に組む。"
  (var groups #())
  (for [line (.splitlines stdout)]
    (val parts (.partition line "\t"))
    (when (get parts 0)
      (:= groups #(#* groups #((get parts 0) (! (io-totals (get parts 2))))))))
  groups)
