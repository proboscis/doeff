;; 機体の主 checkout の上で branch の worktree を作り・並べる git の台本(sim/worktree_git_script.hy)の検。台本を memory の置き場の上で
;; scripted-command-peer に渡し、呼びの答え(終了 code と出力)と、作った置き場(登録と木の印)を読む。
;;
;;   - fetch が origin の branch を写し、origin の branch から新しい branch の木を切ると、一覧に主 checkout と木が本物の綴りで並ぶ
;;   - 木が取り出している branch は、もう 1 つの木にも branch -f にも使えない(本物と同じ 128)
;;   - 木の dir を消すと prune が登録を外す
;;   - 失敗ケース: origin に届かない筋書きの fetch・主 checkout でない dir・知らない呼びは、本物と同じ所で断る
(require doeff-hy.macros [deftest defk <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff [with_handlers])
(import doeff_core_effects [state])
(import doeff_core_effects [MemoryFiles MakeDirectory ReadText RemoveTree FileFailed])
(import doeff_core_effects [memory-file-handler])
(import doeff_core_effects.process_effects [ProcessOutcome RunProcess])
(import doeff_core_effects.scripted_process [scripted-command-peer])
(import doeff_cluster [OriginBranch worktree-git-command sha-of])

(val MAIN "/repos/app")
(val TREE "/work/trees/app-wt-x")
(val ORIGINS #((OriginBranch :repo "app" :branch "wt/x") (OriginBranch :repo "lib" :branch "wt/y")))


(defk git [#* args]
  {:pre [(: args tuple)] :post [(: % ProcessOutcome)] :tags {:context "doeff-cluster-test" :role "program"}}
  "主 checkout MAIN へ git の呼び 1 つを出すため(作業場所の翻訳と同じ git -C <main> の形)。"
  (<- outcome ProcessOutcome (RunProcess :argv (+ #("git" "-C" MAIN) args)))
  outcome)


(defk worktree-journey []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "主 checkout を置き、fetch → 新しい branch の木 → 一覧 → 使用中の branch の断り → 木の dir を消して prune → 一覧、を順に出す筋。"
  (<- (MakeDirectory (+ MAIN "/.git")))
  (<- fetched ProcessOutcome (git "fetch" "--quiet" "origin"))
  (<- added ProcessOutcome (git "worktree" "add" "--track" "-b" "wt/x" TREE "origin/wt/x"))
  (<- mark str (ReadText (+ TREE "/.git")))
  (<- listed ProcessOutcome (git "worktree" "list" "--porcelain"))
  (<- again ProcessOutcome (git "worktree" "add" "/work/trees/other" "wt/x"))
  (<- forced ProcessOutcome (git "branch" "-f" "wt/x" "refs/remotes/origin/main"))
  (<- (RemoveTree TREE))
  (<- pruned ProcessOutcome (git "worktree" "prune"))
  (<- after ProcessOutcome (git "worktree" "list" "--porcelain"))
  #(fetched.exit-code added.exit-code mark listed.stdout again.exit-code forced.exit-code pruned.exit-code after.stdout))


(deftest test-the-script-adds-lists-guards-and-prunes-worktrees-like-git
  (<- got tuple (with_handlers [(state) (memory-file-handler (MemoryFiles)) (scripted-command-peer #((worktree-git-command ORIGINS True)))]
                  (worktree-journey)))
  (<- main-sha str (sha-of "app" "main"))
  (<- x-sha str (sha-of "app" "wt/x"))
  (val main-block (.format "worktree {}\nHEAD {}\nbranch refs/heads/main\n" MAIN main-sha))
  (assert (= got #(0 0 "gitdir: /repos/app/.git/worktrees/app-wt-x\n"
                   (+ main-block "\n" (.format "worktree {}\nHEAD {}\nbranch refs/heads/wt/x\n" TREE x-sha))
                   128 128 0 main-block))
          got))


(defk refusals-journey []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "届かない origin への fetch・主 checkout でない dir への呼び・知らない呼びを出す筋(主 checkout MAIN は置く)。"
  (<- (MakeDirectory (+ MAIN "/.git")))
  (<- unreachable ProcessOutcome (git "fetch" "--quiet" "origin"))
  (<- elsewhere ProcessOutcome (RunProcess :argv #("git" "-C" "/repos/none" "worktree" "prune")))
  (<- unknown ProcessOutcome (git "status"))
  #(#(unreachable.exit-code unreachable.stderr) #(elsewhere.exit-code elsewhere.stderr) unknown.exit-code))


(deftest test-the-script-refuses-where-git-refuses
  (<- got tuple (with_handlers [(state) (memory-file-handler (MemoryFiles)) (scripted-command-peer #((worktree-git-command ORIGINS False)))]
                  (refusals-journey)))
  (assert (= got #(#(128 "fatal: unable to access 'origin': Could not resolve host")
                   #(128 "fatal: cannot change to '/repos/none': No such file or directory")
                   2))
          got))
