;;; 機体の主 checkout の上で branch の worktree を作り・並べる git の台本(2026-10-23 — 使い手の repo の模擬の世界に在った git の答え手を、
;;; 業務を知らない台本としてここへ移した — 使い手の模擬は汎用の子 process の効果を名指さずに組む)。doeff の scripted-process-handler か scripted-command-peer に
;;; 渡す ScriptedCommand 1 つで、本物の git を起こさず、外側の file の答え手(多くは memory-file-handler)の置き場の上に、本物と同じ置き場の形で
;;; worktree を作り・並べる:
;;;
;;;   主 checkout        <main>/.git(dir — 筋書きが置く)
;;;   手元の branch      <main>/.git/refs/heads/<名>(file — sha)
;;;   origin を追う branch <main>/.git/refs/remotes/origin/<名>(file — fetch が筋書きの OriginBranch から写す)・origin/HEAD は既定の branch main
;;;   worktree の登録    <main>/.git/worktrees/<木の dir の名>/gitdir(<木>/.git の path)と HEAD(ref: refs/heads/<名>)
;;;   木の印             <木>/.git(file — gitdir: <登録の dir>)
;;;
;;; 受ける呼び(ほかの形は 2 で断る。業務を知らない: origin の branch は筋書きが渡す):
;;;   git -C <main> worktree list --porcelain                主 checkout と登録の木を本物の綴りで並べる
;;;   git -C <main> worktree prune                           木の dir が無い登録を外す
;;;   git -C <main> fetch --quiet origin                     origin の branch を refs/remotes/origin/ へ写す(届かない筋書きは 128)
;;;   git -C <main> rev-parse --verify --quiet <ref>         ref の file が在れば 0(sha)・無ければ 1
;;;   git -C <main> merge-base --is-ancestor <古> <新>        古の先が新の先と同じか、新の branch の 1 つ前の commit(筋書きは古いだけの手元の
;;;                                                          branch を `<名>^` の sha で置く — sha-of)なら 0・ほかは 1
;;;   git -C <main> branch -f <branch> <元>                  手元の branch を元の先へ進める(木か主 checkout が取り出している branch は 128)
;;;   git -C <main> worktree add <木> <branch>               手元の branch の木(branch が無い・ほかの木が使っている・木が在る は 128)
;;;   git -C <main> worktree add [--track] -b <branch> <木> <元>  元(origin/<名> か origin/HEAD)から新しい branch を切った木
;;; <main> が主 checkout でなければ 128(本物の「cannot change to」と同じ所で断る)。呼びの記録は持たない — 使い手の検は結末と、木を作った
;;; 置き場(登録と木の印)を読む。
;;;
;;;   (scripted-command-peer #((worktree-git-command #((OriginBranch :repo "app" :branch "wt/x")) True)))
(require doeff-hy.macros [defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import hashlib)
(import posixpath)
(import doeff_core_effects [StatPath PathStat PathKind FileFailed ReadText WriteText MakeDirectory ListDirectory DirEntry RemoveTree])
(import doeff_core_effects.process_effects [ProcessOutcome RunProcess])
(import doeff_core_effects.scripted_process [ScriptedCommand])
(import doeff_traverse [sequence])

(val GIT-DIR ".git")
(val ORIGIN "origin")
;; origin の既定の branch(origin/HEAD が指す branch — 筋書きが宣言しなくても在る)。
(val DEFAULT-BRANCH "main")


(defrecord OriginBranch
  "origin に在る branch 1 つ(筋書きが宣言する): repo = 主 checkout の dir の名・branch = branch の名。"
  (#^ str repo)
  (#^ str branch))


(defrecord Registration
  "worktree の登録 1 つ: name = 登録の dir の名・worktree = 木の path・branch = 木が取り出している branch の名。"
  (#^ str name)
  (#^ str worktree)
  (#^ str branch))


(defk passed [stdout]
  {:pre [(: stdout str)] :post [(: % ProcessOutcome)] :tags {:context "doeff-cluster" :role "foundation"}}
  "通った呼びの答えを組むため。"
  (ProcessOutcome :exit-code 0 :stdout stdout :stderr ""))


(defk refused [code stderr]
  {:pre [(: code int) (: stderr str)] :post [(: % ProcessOutcome)] :tags {:context "doeff-cluster" :role "foundation"}}
  "断った呼びの答えを組むため(本物の git の終了 code と文の形)。"
  (ProcessOutcome :exit-code code :stdout "" :stderr stderr))


(defk sha-of [repo branch]
  {:pre [(: repo str) (: branch str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "foundation"}}
  "branch の先の commit の sha を決めるため(主 checkout の名と branch から導く 40 字 — 同じ branch はどの機体でも同じ sha)。"
  (.hexdigest (hashlib.sha1 (.encode (+ repo ":" branch) "utf-8"))))


(defk kind-of [path]
  {:pre [(: path str)] :post [(: % PathKind)] :tags {:context "doeff-cluster" :role "foundation"}}
  "path の種類を読むため(調べられなければ無い物と読む)。"
  (<- seen (| PathStat FileFailed) (StatPath path))
  (if (isinstance seen PathStat) seen.kind PathKind.MISSING))


(defk file-written [path text]
  {:pre [(: path str) (: text str)] :post [(: % None)] :tags {:context "doeff-cluster" :role "foundation"}}
  "file を親の dir ごと書くため(書けなければ台本の世界の壊れ — 例外で止める)。"
  (<- made (| None FileFailed) (MakeDirectory (posixpath.dirname path)))
  (<- written (| None FileFailed) (WriteText path text))
  (for [answer #(made written)]
    (when (isinstance answer FileFailed)
      (raise (RuntimeError (+ "台本の git が書けない: " answer.path " — " answer.detail)))))
  None)


(defk ref-sha [main ref]
  {:pre [(: main str) (: ref str)] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "foundation"}}
  "ref の file の sha を読むため(無ければ None)。"
  (<- text (| str FileFailed) (ReadText (+ main "/" GIT-DIR "/" ref)))
  (if (isinstance text FileFailed) None (.strip text)))


(defk registration-of [root name]
  {:pre [(: root str) (: name str)] :post [(: % (| Registration None))] :tags {:context "doeff-cluster" :role "foundation"}}
  "登録の dir 1 つ(root/name の gitdir と HEAD)を読むため(読めない・branch を取り出していない登録は None)。"
  (<- gitdir (| str FileFailed) (ReadText (+ root "/" name "/gitdir")))
  (<- head (| str FileFailed) (ReadText (+ root "/" name "/HEAD")))
  (if (and (isinstance gitdir str) (isinstance head str) (.startswith (.strip head) "ref: refs/heads/"))
      (Registration :name name :worktree (posixpath.dirname (.strip gitdir)) :branch (cut (.strip head) (len "ref: refs/heads/") None))
      None))


(defk registrations [main]
  {:pre [(: main str)] :post [(: % (get tuple #(Registration ...)))] :tags {:context "doeff-cluster" :role "foundation"}}
  "主 checkout main の worktree の登録を並べるため(登録の dir が無ければ空)。"
  (val root (+ main "/" GIT-DIR "/worktrees"))
  (<- kind PathKind (kind-of root))
  (when (!= kind PathKind.DIRECTORY)
    (return #()))
  (<- entries (| (get tuple #(DirEntry ...)) FileFailed) (ListDirectory root))
  (when (isinstance entries FileFailed)
    (return #()))
  (<- read (get tuple #((| Registration None) ...)) (sequence (gfor entry (sorted entries :key (fn [e] e.name)) (registration-of root entry.name))))
  (tuple (gfor r read :if (is-not r None) r)))


(defk porcelain-block [worktree repo branch]
  {:pre [(: worktree str) (: repo str) (: branch str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "foundation"}}
  "`worktree list --porcelain` の木 1 つの塊を本物の綴りで組むため。"
  (<- sha str (sha-of repo branch))
  (.format "worktree {}\nHEAD {}\nbranch refs/heads/{}\n" worktree sha branch))


(defk listed [main repo]
  {:pre [(: main str) (: repo str)] :post [(: % ProcessOutcome)] :tags {:context "doeff-cluster" :role "foundation"}}
  "`worktree list --porcelain` に答えるため(主 checkout が先・登録の名の順)。"
  (<- held (get tuple #(Registration ...)) (registrations main))
  (<- blocks (get tuple #(str ...)) (sequence (+ #((porcelain-block main repo DEFAULT-BRANCH))
                                                 (tuple (gfor r held (porcelain-block r.worktree repo r.branch))))))
  (! (passed (.join "\n" blocks))))


(defk pruned [main]
  {:pre [(: main str)] :post [(: % ProcessOutcome)] :tags {:context "doeff-cluster" :role "foundation"}}
  "`worktree prune` に答えるため(木の dir が無い登録を外す)。"
  (<- held (get tuple #(Registration ...)) (registrations main))
  (for [r held]
    (<- kind PathKind (kind-of r.worktree))
    (when (= kind PathKind.MISSING)
      (<- _removed (| None FileFailed) (RemoveTree (+ main "/" GIT-DIR "/worktrees/" r.name)))))
  (! (passed "")))


(defk fetched [main repo origins]
  {:pre [(: main str) (: repo str) (: origins (get tuple #(OriginBranch ...)))] :post [(: % ProcessOutcome)]
   :tags {:context "doeff-cluster" :role "foundation"}}
  "`fetch --quiet origin` に答えるため(origin の branch と既定の branch を refs/remotes/origin/ へ写す)。"
  (val branches (+ #(DEFAULT-BRANCH) (tuple (gfor o origins :if (= o.repo repo) o.branch))))
  (for [branch branches]
    (<- (file-written (+ main "/" GIT-DIR "/refs/remotes/" ORIGIN "/" branch) (+ (! (sha-of repo branch)) "\n"))))
  (<- (file-written (+ main "/" GIT-DIR "/refs/remotes/" ORIGIN "/HEAD") (+ "ref: refs/remotes/" ORIGIN "/" DEFAULT-BRANCH "\n")))
  (! (passed "")))


(defk verified [main ref]
  {:pre [(: main str) (: ref str)] :post [(: % ProcessOutcome)] :tags {:context "doeff-cluster" :role "foundation"}}
  "`rev-parse --verify --quiet <ref>` に答えるため(ref の file が在れば 0・無ければ 1)。"
  (<- sha (| str None) (ref-sha main ref))
  (if (is sha None) (! (refused 1 "")) (! (passed (+ sha "\n")))))


(defk ancestry [main repo older newer]
  {:pre [(: main str) (: repo str) (: older str) (: newer str)] :post [(: % ProcessOutcome)] :tags {:context "doeff-cluster" :role "foundation"}}
  "`merge-base --is-ancestor <older> <newer>` に答えるため(頭の註の表)。"
  (<- a (| str None) (ref-sha main older))
  (<- b (| str None) (ref-sha main newer))
  (when (or (is a None) (is b None))
    (return (! (refused 128 (.format "fatal: Not a valid object name {}" (if (is a None) older newer))))))
  (val name (.removeprefix (.removeprefix newer (+ "refs/remotes/" ORIGIN "/")) "refs/heads/"))
  (<- parent str (sha-of repo (+ name "^")))
  (if (or (= a b) (= a parent)) (! (passed "")) (! (refused 1 ""))))


(defk forced [main branch start]
  {:pre [(: main str) (: branch str) (: start str)] :post [(: % ProcessOutcome)] :tags {:context "doeff-cluster" :role "foundation"}}
  "`branch -f <branch> <start>` に答えるため(頭の註の表 — 取り出されている branch は本物と同じく断る)。"
  (<- held (get tuple #(Registration ...)) (registrations main))
  (val using (next (gfor r held :if (= r.branch branch) r.worktree) (if (= branch DEFAULT-BRANCH) main None)))
  (when (is-not using None)
    (return (! (refused 128 (.format "fatal: cannot force update the branch '{}' used by worktree at '{}'" branch using)))))
  (<- sha (| str None) (ref-sha main start))
  (when (is sha None)
    (return (! (refused 128 (.format "fatal: invalid reference: {}" start)))))
  (<- (file-written (+ main "/" GIT-DIR "/refs/heads/" branch) (+ sha "\n")))
  (! (passed "")))


(defk added [main repo target branch start]
  {:pre [(: main str) (: repo str) (: target str) (: branch str) (: start (| str None))] :post [(: % ProcessOutcome)]
   :tags {:context "doeff-cluster" :role "foundation"}}
  "`worktree add` に答えるため: start = None は手元の branch の木・在れば start から新しい branch を切る(頭の註の 2 つの形)。"
  (<- held (get tuple #(Registration ...)) (registrations main))
  (val using (next (gfor r held :if (= r.branch branch) r) None))
  (when (is-not using None)
    (return (! (refused 128 (.format "fatal: '{}' is already used by worktree at '{}'" branch using.worktree)))))
  (<- local (| str None) (ref-sha main (+ "refs/heads/" branch)))
  (when (and (is start None) (is local None))
    (return (! (refused 128 (.format "fatal: invalid reference: {}" branch)))))
  (when (and (is-not start None) (is-not local None))
    (return (! (refused 128 (.format "fatal: a branch named '{}' already exists" branch)))))
  (var head local)
  (when (is-not start None)
    (val remote-ref (+ "refs/remotes/" (if (= start (+ ORIGIN "/HEAD")) (+ ORIGIN "/" DEFAULT-BRANCH) start)))
    (<- base (| str None) (ref-sha main remote-ref))
    (when (is base None)
      (return (! (refused 128 (.format "fatal: invalid reference: {}" start)))))
    (:= head base))
  (<- kind PathKind (kind-of target))
  (when (!= kind PathKind.MISSING)
    (return (! (refused 128 (.format "fatal: '{}' already exists" target)))))
  (val name (posixpath.basename target))
  (val registration (+ main "/" GIT-DIR "/worktrees/" name))
  (<- made (| None FileFailed) (MakeDirectory target))
  (when (isinstance made FileFailed)
    (return (! (refused 128 (.format "fatal: could not create directory of '{}': {}" target made.detail)))))
  (<- (file-written (+ target "/" GIT-DIR) (+ "gitdir: " registration "\n")))
  (<- (file-written (+ registration "/gitdir") (+ target "/" GIT-DIR "\n")))
  (<- (file-written (+ registration "/HEAD") (+ "ref: refs/heads/" branch "\n")))
  (<- (file-written (+ main "/" GIT-DIR "/refs/heads/" branch) (+ head "\n")))
  (! (passed "")))


(defk worktree-git-answer [origins reachable request]
  {:pre [(: origins (get tuple #(OriginBranch ...))) (: reachable bool) (: request RunProcess)]
   :post [(: % ProcessOutcome)] :tags {:context "doeff-cluster" :role "foundation"}}
  "台本の git の呼び 1 つに答えるため(頭の註の表)。reachable = origin に届くか(偽なら fetch を 128 で断る)。"
  (val argv request.argv)
  (when (not (and (>= (len argv) 4) (= (get argv 1) "-C")))
    (return (! (refused 2 (.format "台本の git が知らない呼び: {}" argv)))))
  (val main (get argv 2))
  (val rest (tuple (cut argv 3 None)))
  (val repo (posixpath.basename main))
  (<- mark PathKind (kind-of (+ main "/" GIT-DIR)))
  (when (!= mark PathKind.DIRECTORY)
    (return (! (refused 128 (.format "fatal: cannot change to '{}': No such file or directory" main)))))
  (match rest
    #("worktree" "list" "--porcelain") (! (listed main repo))
    #("worktree" "prune") (! (pruned main))
    #("fetch" "--quiet" origin) :if (= origin ORIGIN)
    (if reachable
        (! (fetched main repo origins))
        (! (refused 128 (.format "fatal: unable to access '{}': Could not resolve host" ORIGIN))))
    #("rev-parse" "--verify" "--quiet" ref) (! (verified main ref))
    #("merge-base" "--is-ancestor" older newer) (! (ancestry main repo older newer))
    #("branch" "-f" branch start) (! (forced main branch start))
    #("worktree" "add" target branch) (! (added main repo target branch None))
    #("worktree" "add" "--track" "-b" branch target start) (! (added main repo target branch start))
    #("worktree" "add" "-b" branch target start) (! (added main repo target branch start))
    _ (! (refused 2 (.format "台本の git が知らない呼び: {}" argv)))))


(defn #^ ScriptedCommand worktree-git-command [#^ tuple origins #^ bool reachable]  ; defk にできない: handler の列(scripted-command-peer・ProcessScript)を組む時に Program の外で呼ぶ
  "台本の git を ScriptedCommand 1 つにするため(origins = origin に在る branch・reachable = 機体から origin に届くか)。"
  (ScriptedCommand :name "git" :run (fn [commands request] (worktree-git-answer origins reachable request))))
