;; 送り手の宣言の組み立て(runtime_env の runtime-env-of-checkouts)の検 — 手元の bare repo と clone(本物の git)。
;;
;; worker が取れない commit を送る前に断る: commit していない変更(dirty-tree)・push していない commit(commit-not-on-remote)・
;; 送り手自身の source が宣言の commit と違う(sender-source-differs)。通る時は uv.lock の sha256 を checkout から計算する。
(require doeff-hy.macros [deftest defk handle <- val var])
(import hashlib)
(import subprocess)
(import pathlib [Path])
(import pytest)
(import doeff [Program])
(import doeff_cluster.runtime_env_model [RuntimeEnv RuntimeEnvInvalid InvalidKind])
(import doeff_cluster.runtime_env [LocalCheckout ProjectOfCheckout SenderSourceRoot runtime-env-of-checkouts local-checkouts])

(setv LOCK "httpx==0.28.1\n")


(defk git [cwd #* args]
  {:pre [(: cwd Path) (: args tuple)] :post [(: % str)]}
  "検の repo を作るために git を 1 回呼ぶ。"
  (val done (subprocess.run ["git" "-C" (str cwd) "-c" "user.name=t" "-c" "user.email=t@example.invalid" #* args]
                            :capture-output True :text True :check True))
  (.strip done.stdout))


(defk pushed-checkout [base name]
  {:pre [(: base Path) (: name str)] :post [(: % Path)]}
  "bare の remote と、1 commit を push 済みの clone を作り、clone の path を返す。"
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
  work)


(defk build [work sender-root sender-repo]
  {:pre [(: work Path) (: sender-root (| str None)) (: sender-repo (| str None))] :post [(: % RuntimeEnv)]}
  "work の checkout 1 つから宣言を組み立てる(送り手の source の根は sender-root だと答える)。"
  (<- env RuntimeEnv
      (local-checkouts
        (handle (runtime-env-of-checkouts #((LocalCheckout :name "app" :path (str work)))
                                          (ProjectOfCheckout :repo "app" :path "." :python "3.14")
                                          #("app/.")
                                          :sender-repo sender-repo)
          (SenderSourceRoot [] (resume sender-root)))))
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
  (assert (= repo.url (str (/ tmp-path "app.git"))))
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
