;; 実行環境の準備が焼く道具へ写し元の木(--inherit)を渡すのは、宣言の全部の repo の木を写した 1 つの完成済みの root が、新しい root と
;; 同じ platform と依存のキー(project の repo の commit の外の全部 — uv.lock・他の repo の commit・python・import の根)を持つ時だけ
;; (ADR-DOE-BUILD-002 R3c)。その時だけ venv と Hy の版と木の外の macro の提供元が同じで、木の中の差は焼く道具が file の同じさで見分ける。
;; 依存のキーの違う root から .pyc を引き継ぐと、木の外の macro の提供元(venv の doeff-hy など)の変わった展開を照らさずに残してしまう。
(require doeff-hy.macros [deftest defk val <-])
(import doeff_cluster [env-key RuntimeEnv])
(import doeff_cluster._worker.core.env_prepare [inherit-trees])
(import doeff_cluster._worker.intent.env_prepare_model [PrepareRequest KnownRoot])
(import tests.env_fixtures [LOCK HY-2-LOCK env-of])

(setv PLATFORM "linux-x86_64")


(defk request-of [env known]
  {:pre [(: env RuntimeEnv) (: known tuple)] :post [(: % PrepareRequest)] :tags {:context "doeff-cluster-test" :role "program"}}
  "新しい root の準備の要求(worker と同じ置き場 state/roots/<キー>)。"
  (<- key str (env-key env PLATFORM))
  (PrepareRequest :env env :key key :platform PLATFORM :root (.format "/state/roots/{}" key)
                  :compile-jobs None :known known :min-free-bytes 1024))


(defk known-of [env root made [platform PLATFORM]]
  {:pre [(: env RuntimeEnv) (: root str) (: made int) (: platform str)] :post [(: % KnownRoot)] :tags {:context "doeff-cluster-test" :role "program"}}
  "完成済みの root 1 つ。"
  (KnownRoot :env env :root root :platform platform :made-ms made :hy-version None))


(deftest test-a-root-with-the-same-dependencies-is-the-source-of-every-tree
  ;; project の repo(app)の commit だけが違う root から写した木は、その root の木を写し元に渡す。
  (<- old RuntimeEnv (env-of "a1" "l1" LOCK))
  (<- new RuntimeEnv (env-of "a2" "l1" LOCK))
  (<- before KnownRoot (known-of old "/state/roots/k1" 1))
  (<- request PrepareRequest (request-of new #(before)))
  (<- inherits dict (inherit-trees request))
  (assert (= inherits {"app" "/state/roots/k1/app" "lib" "/state/roots/k1/lib"}) inherits))


(deftest test-a-root-with-other-dependencies-is-not-a-source
  ;; uv.lock が違う・他の repo(lib)の commit が違う・platform が違う root からは引き継がない(保存先から書く)。
  (<- new RuntimeEnv (env-of "a2" "l1" LOCK))
  (<- other-lock-env RuntimeEnv (env-of "a1" "l1" HY-2-LOCK))
  (<- other-lib-env RuntimeEnv (env-of "a1" "l0" LOCK))
  (<- same-env RuntimeEnv (env-of "a1" "l1" LOCK))
  (<- other-lock KnownRoot (known-of other-lock-env "/state/roots/k1" 1))
  (<- other-lib KnownRoot (known-of other-lib-env "/state/roots/k1" 1))
  (<- other-platform KnownRoot (known-of same-env "/state/roots/k1" 1 "darwin-arm64"))
  (for [known #(other-lock other-lib other-platform)]
    (<- request PrepareRequest (request-of new #(known)))
    (<- inherits dict (inherit-trees request))
    (assert (= inherits {}) #(known.root inherits))))


(deftest test-the-newest-root-holding-the-repos-is-the-source-even-when-an-older-one-matches
  ;; 写し元は木を写す元(最も新しく完成した、同じ repo を持つ root)と同じ root — それの依存のキーが違えば、古い root の依存のキーが
  ;; 同じでも引き継がない(木は新しい root から写されたので、古い root の木と同じ file ではない)。
  (<- new RuntimeEnv (env-of "a2" "l1" LOCK))
  (<- same-env RuntimeEnv (env-of "a1" "l1" LOCK))
  (<- newer-env RuntimeEnv (env-of "a3" "l1" HY-2-LOCK))
  (<- same KnownRoot (known-of same-env "/state/roots/k1" 1))
  (<- newer KnownRoot (known-of newer-env "/state/roots/k3" 3))
  (<- request PrepareRequest (request-of new #(same newer)))
  (<- inherits dict (inherit-trees request))
  (assert (= inherits {}) inherits))
