;; 送り手の宣言の組み立て(runtime_env の runtime-env-of-checkouts)の検 — 手元の bare repo と clone(本物の git)を、翻訳の handler
;; checkout-reads + 本物の土台(subprocess-handler・os-file-handler)で読む。模擬の土台(台本の git と memory の置き場)で読む検は
;; test_checkout_reads.hy。
;;
;; worker が取れない commit を送る前に断る: remote の url が手元の path か file://(local-remote)・commit していない変更(dirty-tree)・
;; push していない commit(commit-not-on-remote)・送り手自身の source が宣言の commit と違う(sender-source-differs)。通る時は
;; uv.lock の sha256 を checkout から計算する。
(require doeff-hy.macros [deftest defk handle <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import hashlib)
(import subprocess)
(import pathlib [Path])
(import pytest)
(import doeff [Program])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv RuntimeEnvInvalid InvalidKind])
(import doeff_cluster.shared.intent.checkout_model [LocalCheckout ProjectOfCheckout SenderSourceRoot])
(import doeff_cluster.shared.core.runtime_env [runtime-env-of-checkouts])
(import doeff_cluster.shared.protocol.checkout_reads [checkout-reads])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.os_file [os-file-handler])

(setv LOCK "httpx==0.28.1\n")


(defk git [cwd #* args]
  {:pre [(: cwd Path) (: args tuple)] :post [(: % str)]}
  "検の repo を作るために git を 1 回呼ぶ。"
  (val words (lfor a args :if (isinstance a str) a))
  (assert (= (len words) (len args)) #("子 process の引数は文字列だけ" args))
  (val done (subprocess.run ["git" "-C" (str cwd) "-c" "user.name=t" "-c" "user.email=t@example.invalid" #* words]
                            :capture-output True :text True :check True))
  (.strip done.stdout))


(defk remote-url [name]
  {:pre [(: name str)] :post [(: % str)]}
  "検の clone の origin に置く url(別の機体の worker が取れる形 — 手元の path の remote は組み立てが断る・#3167)。"
  (.format "https://example.invalid/{}.git" name))


(defk pushed-checkout [base name]
  {:pre [(: base Path) (: name str)] :post [(: % Path)]}
  "bare の remote と、1 commit を push 済みの clone を作り、clone の path を返す。push と fetch の後に origin の url を remote-url へ
   替える(追跡の ref は残るので、HEAD は remote の branch に在ると読める)。"
  (val remote (/ base (+ name ".git")))
  (val work (/ base name))
  (<- (git base "init" "-q" "--bare" (str remote)))
  (<- (git base "clone" "-q" (str remote) (str work)))
  (.write-text (/ work "uv.lock") LOCK)
  (.write-text (/ work "pyproject.toml") "[project]\nname = \"x\"\n")
  (<- (git work "add" "-A"))
  (<- (git work "commit" "-q" "-m" "first"))
  (<- (git work "push" "-q" "origin" "HEAD:main"))
  (<- (git work "fetch" "-q" "origin"))
  (<- url str (remote-url name))
  (<- (git work "remote" "set-url" "origin" url))
  work)


(defk build [work sender-root sender-repo]
  {:pre [(: work Path) (: sender-root (| str None)) (: sender-repo (| str None))] :post [(: % RuntimeEnv)]}
  "work の checkout 1 つから宣言を組み立てる(送り手の source の根は sender-root だと答える)。"
  (<- env RuntimeEnv
      (subprocess-handler
        (os-file-handler
          (checkout-reads
            (handle (runtime-env-of-checkouts #((LocalCheckout :name "app" :path (str work)))
                                              (ProjectOfCheckout :repo "app" :path "." :python "3.14")
                                              #("app/.")
                                              :sender-repo sender-repo)
              (SenderSourceRoot [] (resume sender-root)))))))
  env)


(defk refusal-of [work sender-root sender-repo]
  {:pre [(: work Path) (: sender-root (| str None)) (: sender-repo (| str None))] :post [(: % InvalidKind)]}
  "組み立てが断った種類。"
  (try
    (<- _ RuntimeEnv (build work sender-root sender-repo))
    (except [refused RuntimeEnvInvalid]
      (return refused.kind)))
  (raise (AssertionError "断るはずが通った")))


(deftest test-a-clean-pushed-checkout-becomes-a-declaration [tmp-path]
  (<- work Path (pushed-checkout tmp-path "app"))
  (<- env RuntimeEnv (build work None None))
  (<- head str (git work "rev-parse" "HEAD"))
  (val repo (get env.repos 0))
  (assert (= repo.commit head))
  (<- url str (remote-url "app"))
  (assert (= repo.url url))
  (assert (= env.project.lock-sha256 (.hexdigest (hashlib.sha256 (.encode LOCK))))))


(deftest test-uncommitted-changes-are-refused [tmp-path]
  (<- work Path (pushed-checkout tmp-path "app"))
  (.write-text (/ work "uv.lock") (+ LOCK "hy==1.1.0\n"))
  (<- kind InvalidKind (refusal-of work None None))
  (assert (= kind InvalidKind.DIRTY-TREE)))


(deftest test-an-unpushed-commit-is-refused [tmp-path]
  (<- work Path (pushed-checkout tmp-path "app"))
  (.write-text (/ work "extra.py") "X = 1\n")
  (<- (git work "add" "-A"))
  (<- (git work "commit" "-q" "-m" "local only"))
  (<- kind InvalidKind (refusal-of work None None))
  (assert (= kind InvalidKind.COMMIT-NOT-ON-REMOTE)))


(deftest test-a-remote-on-a-local-path-is-refused [tmp-path]
  ;; 本物の git の縁: `git clone <bare の path>` の既定の origin(手元の絶対 path)と file:// の remote は、別の機体の worker が取れない
  ;; (#3167 — 宣言の clone の origin が手元の path のまま宣言を組み、下見の宣言の url が手元の path になった)。
  (<- work Path (pushed-checkout tmp-path "app"))
  (val bare (str (/ tmp-path "app.git")))
  (for [url #(bare (+ "file://" bare))]
    (<- (git work "remote" "set-url" "origin" url))
    (<- kind InvalidKind (refusal-of work None None))
    (assert (= kind InvalidKind.LOCAL-REMOTE) #(url kind))))


(deftest test-a-sender-running-other-source-is-refused [tmp-path]
  ;; 送り手自身の source が宣言の repo と同じ checkout(同じ commit)なら通り、別の commit の checkout なら断る。
  (<- work Path (pushed-checkout tmp-path "app"))
  (<- env RuntimeEnv (build work (str work) "app"))
  (assert (= (. (get env.repos 0) name) "app"))
  (val other (/ tmp-path "other"))
  (<- (git tmp-path "clone" "-q" (str (/ tmp-path "app.git")) (str other)))
  (.write-text (/ other "extra.py") "X = 1\n")
  (<- (git other "add" "-A"))
  (<- (git other "commit" "-q" "-m" "newer"))
  (<- kind InvalidKind (refusal-of work (str other) "app"))
  (assert (= kind InvalidKind.SENDER-SOURCE-DIFFERS))
  (<- outside InvalidKind (refusal-of work None "app"))
  (assert (= outside InvalidKind.SENDER-SOURCE-DIFFERS)))

