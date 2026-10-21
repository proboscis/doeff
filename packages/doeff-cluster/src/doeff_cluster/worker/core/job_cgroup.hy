;;; 子の job を置く cgroup の判断(card ki-ed9dca403f58): worker の本体(heartbeat を送る process)と、worker が起こす子 process(job の
;;; shim・待ちの子・入口の検め・木と root の準備の git や uv)を別の cgroup に置くため。機体の配備(systemd の unit — Delegate= と
;;; DelegateSubgroup=)は本体を自分の cgroup に置き、その兄弟の cgroup にだけ CPU の重み(cpu.idle)と枠(cpu.max)を掛ける。
;;; 子は fork した親の cgroup を継ぐので、配備だけでは分けられない — worker が子を起こす時に兄弟の cgroup へ入れる。
;;;
;;; 入れ方: 子の argv を `/bin/sh -c PLACE-SCRIPT <兄弟の cgroup.procs> <元の argv>` に包む。sh は自分の pid を cgroup.procs へ書いて
;;; 兄弟の cgroup へ移り、元の argv を exec する(pid は変わらない — 子の表・process group・止めはそのまま効く)。書けなければ sh は
;;; 元の命令を走らせずに 0 以外で終わる(置けない子を本体の cgroup で黙って走らせない)。
;;; ⚠ 包んだ子は「命令が無い」を ProcessNotStarted でなく、立った子の終わり(sh の 127)で答える(外の答え手が確かめるのは /bin/sh)。
;;;
;;; 兄弟の名は worker の環境変数 DOEFF_WORKER_JOB_CGROUP(worker/entry/main が読む・無ければ包まない — Pod の worker は今のまま)。
;;; path は worker の自分の cgroup(/proc/self/cgroup の cgroup v2 の行 `0::<path>`)の親の下の名。
(require doeff-hy.macros [defk val])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import posixpath)


;; cgroup v2 の根の mount の場所。
(val CGROUP-ROOT "/sys/fs/cgroup")
;; 子の頭で走る sh の script: $0 = 兄弟の cgroup.procs・"$@" = 元の argv。
(val PLACE-SCRIPT "echo $$ >\"$0\" && exec \"$@\"")


(defk job-cgroup-procs [own-cgroup sibling]
  {:pre [(: own-cgroup str) (: sibling str)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "子を置く兄弟の cgroup の cgroup.procs の path を決めるため。own-cgroup = /proc/self/cgroup の中身・sibling = 兄弟の名
   (DOEFF_WORKER_JOB_CGROUP の値)。cgroup v2 の行が無い・自分が根に居る(兄弟が無い)・名が 1 段の名でない時は断る
   (ValueError — 置けない子を本体の cgroup で黙って走らせる起動にしない)。"
  (val lines (lfor line (.splitlines own-cgroup) :if (.startswith line "0::") (cut line 3 None)))
  (when (!= (len lines) 1)
    (raise (ValueError (.format "/proc/self/cgroup に cgroup v2 の行(0::)がちょうど 1 つ無い: {!r}" own-cgroup))))
  (val own (.strip (get lines 0)))
  (when (or (not (.startswith own "/")) (= own "/") (in ".." (.split own "/")))
    (raise (ValueError (.format "worker の cgroup {!r} の親の下に兄弟を置けない(根に居るか、名前空間の外の path)" own))))
  (when (or (not sibling) (in "/" sibling) (in sibling #("." "..")))
    (raise (ValueError (.format "DOEFF_WORKER_JOB_CGROUP {!r} は兄弟の cgroup の 1 段の名で書く" sibling))))
  (posixpath.join CGROUP-ROOT (cut (posixpath.dirname own) 1 None) sibling "cgroup.procs"))


(defk placed-argv [procs argv]
  {:pre [(: procs str) (: argv tuple)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "子の argv を、兄弟の cgroup へ移ってから元の argv を exec する sh で包むため(頭の註)。"
  (+ #("/bin/sh" "-c" PLACE-SCRIPT procs) argv))
