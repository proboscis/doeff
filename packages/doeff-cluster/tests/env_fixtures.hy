;; 実行環境の検の世界(env_fake の FakeEnvWorld と宣言)の組み立て。test_env_prepare と test_env_detached が共有する。
;; project の repo(app)と native の source を持つ repo(lib)の 2 つを並べる宣言と、その commit の remote。
(require doeff-hy.macros [defk <- val var])
(import hashlib)
(import doeff_cluster.runtime_env_model [RepoCheckout NativeWheel PythonProject RuntimeEnv])
(import doeff_cluster.env_fake [FakeEnvWorld FakeRemote FakeCommit FakeFile])

(val LOCK "httpx==0.28.1 top=httpx\nhy==1.1.0 top=hy\nclick==8.1.8 top=click\n")
(val APP-URL "file:///remotes/app.git")
(val LIB-URL "file:///remotes/lib.git")


(defk sha-of [label]
  {:pre [(: label str)] :post [(: % str)]}
  "筋書きの commit の名 → 40 桁の sha(読みやすい名で commit を書くため)。"
  (.hexdigest (hashlib.sha1 (.encode label))))


(defk lock-sha [text]
  {:pre [(: text str)] :post [(: % str)]}
  "宣言に書く uv.lock の sha256(送り手が計算する値)。"
  (.hexdigest (hashlib.sha256 (.encode text "utf-8"))))


(defk app-commit [label lock body]
  {:pre [(: label str) (: lock str) (: body str)] :post [(: % FakeCommit)]}
  "project の repo の commit 1 つ(pyproject・uv.lock・import の根の下の package)。"
  (<- sha str (sha-of label))
  (FakeCommit :sha sha
              :files #((FakeFile :path "pyproject.toml" :text "[project]\nname = \"app\"\n")
                       (FakeFile :path "uv.lock" :text lock)
                       (FakeFile :path "app/__init__.py" :text body)
                       (FakeFile :path "vendor/tool.py" :text "X = 1\n")
                       (FakeFile :path "docs/readme.md" :text "doc\n"))))


(defk lib-commit [label native]
  {:pre [(: label str) (: native str)] :post [(: % FakeCommit)]}
  "native の source を持つ repo の commit 1 つ。"
  (<- sha str (sha-of label))
  (FakeCommit :sha sha
              :files #((FakeFile :path "native/core/lib.rs" :text native)
                       (FakeFile :path "lib/__init__.py" :text "Y = 2\n"))))


(defk env-of [app lib lock [roots #("app/." "app/vendor")] [native True]]
  {:pre [(: app str) (: lib str) (: lock str) (: roots tuple) (: native bool)] :post [(: % RuntimeEnv)]}
  "project の repo(app)と native の repo(lib)の 2 つを並べる宣言。"
  (<- app-sha str (sha-of app))
  (<- lib-sha str (sha-of lib))
  (<- lock-hash str (lock-sha lock))
  (RuntimeEnv :repos #((RepoCheckout :name "app" :url APP-URL :commit app-sha)
                       (RepoCheckout :name "lib" :url LIB-URL :commit lib-sha))
              :project (PythonProject :repo "app" :path "." :lock-sha256 lock-hash :python "3.14"
                                      :native (if native
                                                  #((NativeWheel :package "lib-native" :repo "lib" :paths #("native/core")))
                                                  #()))
              :import-roots roots))


(defk base-world []
  {:pre [] :post [(: % FakeEnvWorld)]}
  "筋書きの remote: app の commit 4 つ(lock 2 種)と lib の commit 2 つ(native の source 2 種)。"
  (<- a1 FakeCommit (app-commit "app-1" LOCK "V = 1\n"))
  (<- a2 FakeCommit (app-commit "app-2" LOCK "V = 2\n"))
  (<- a3 FakeCommit (app-commit "app-3" (+ LOCK "rich==13.9.4 top=rich\n") "V = 3\n"))
  (<- a4 FakeCommit (app-commit "app-shadow" (+ LOCK "vendor-shadow==1.0 top=app\n") "V = 4\n"))
  (<- l1 FakeCommit (lib-commit "lib-1" "fn a() {}\n"))
  (<- l2 FakeCommit (lib-commit "lib-2" "fn b() {}\n"))
  (FakeEnvWorld :remotes #((FakeRemote :url APP-URL :commits #(a1 a2 a3 a4))
                           (FakeRemote :url LIB-URL :commits #(l1 l2)))))


