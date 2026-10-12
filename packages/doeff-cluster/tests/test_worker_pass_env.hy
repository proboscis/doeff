;; worker が job の子へ渡す自分の環境変数(--pass-env・boot.sh の WORKER_PASS_ENV・E13)の検。
;;
;; 実行環境の job の子は worker の環境を許可表でしか継がない(handlers.child-environment)。機体の設定(家や作業場所の path・預かり所の
;; URL)は job の宣言ではなく worker の持ち物なので、worker が名で宣言して子へ渡す。名乗った名が無ければ起動を止める。
(require doeff-hy.macros [deftest <- val])
(import pathlib [Path])
(import pytest)
(import doeff_cluster.worker.entry.main [passed-environment])
(import doeff_cluster.worker.core.launch [child-environment])

(val PACKAGE (. (Path __file__) (resolve) parent parent))


(deftest test-named-worker-settings-reach-the-child
  (val environ {"AGENT_HOME" "/scratch/home" "AGENT_URL" "http://x:1" "SECRET_TOKEN" "t" "PATH" "/bin"})
  (<- passed dict (passed-environment "AGENT_HOME, AGENT_URL" environ))
  (assert (= passed {"AGENT_HOME" "/scratch/home" "AGENT_URL" "http://x:1"}) passed)
  ;; 子の環境: 許可表の物 + 渡した設定 + 宣言。名乗っていない変数(資格を含む)は継がない。
  (val child (! (child-environment environ passed {"DECLARED" "1"} {"DOEFF_WORKER_NAME" "w"})))
  (assert (= (get child "AGENT_HOME") "/scratch/home"))
  (assert (= (get child "AGENT_URL") "http://x:1"))
  (assert (not-in "SECRET_TOKEN" child) child)
  (<- none dict (passed-environment "" environ))
  (assert (= none {})))


(deftest test-a-missing-name-stops-the-worker
  (with [e (pytest.raises ValueError)]
    (<- (passed-environment "AGENT_HOME,AGENT_MISSING" {"AGENT_HOME" "/h"})))
  (assert (in "AGENT_MISSING" (str e.value))))


(deftest test-the-worker-build-stores-reach-the-child
  ;; 反例: worker の Rust の wheel・Hy の bytecode の保存先と cargo / rustup の場所が許可表に無ければ、job の子の uv sync
  ;; は doeff-vm を最初から組み直す(#3972 — 5 分半)。DOEFF_ で始まる名は宣言の env-vars に書けないので worker が継ぐ。
  (val environ {"DOEFF_WHEEL_CACHE" "/work/state/wheels" "DOEFF_HY_CODE_STORE" "/work/state/doeff-hy-code-store"
                "CARGO_HOME" "/work/state/cargo" "RUSTUP_HOME" "/usr/local/rustup" "DOEFF_OTHER" "x"})
  (val child (! (child-environment environ {} {} {})))
  (assert (= child {"DOEFF_WHEEL_CACHE" "/work/state/wheels" "DOEFF_HY_CODE_STORE" "/work/state/doeff-hy-code-store"
                    "CARGO_HOME" "/work/state/cargo" "RUSTUP_HOME" "/usr/local/rustup"})
          child))


(deftest test-no-template-or-worker-code-hands-the-hook-marker-to-the-child
  ;; Claude Code は hook・MCP の起動にだけ env CLAUDE_PROJECT_DIR を置く。上に載る系の Bash の包み(ConfigMap worker-shell)はこの名の有無で
  ;; hook・MCP の起動と Bash の道具の命令を見分け、無い時だけ命令を SCHED_IDLE・nice で下げて出力を流す(card ki-bb74c5116086)。job の子の
  ;; CLI の env にこの名が在ると Bash の道具の命令も hook と見なされ、下げと流しが黙って外れる(設計検証 lt-JXPJ5PK5PYCJN5D9CGNCC6SWV1 の
  ;; 盲検 A-1)。上に載る系の宣言の側の検は上に載る系の repo に在り、ここは worker の側(依頼 lt-2F7ATZXHBCYA0WX1F36A6ZC1JE)。
  ;; worker の env(image の ENV・Pod の env)にこの名が在っても、許可表(名と頭)は子へ渡さない。
  (val child (! (child-environment {"CLAUDE_PROJECT_DIR" "/work" "PATH" "/bin"} {} {} {})))
  (assert (= child {"PATH" "/bin"}) child)
  ;; worker の雛形(deploy/ — Pod の env・WORKER_PASS_ENV・boot.sh・image)と worker の code(src/)はこの名を書かない。上に載る系の
  ;; ConfigMap worker-env(WORKER_PASS_ENV_EXTRA)はこの repo の外。
  (val writers (lfor path (+ (sorted (.rglob (/ PACKAGE "deploy") "*")) (sorted (.rglob (/ PACKAGE "src") "*")))
                     :if (and (.is-file path) (not-in "__pycache__" path.parts)
                              (in "CLAUDE_PROJECT_DIR" (.read-text path :errors "replace")))
                     (str (.relative-to path PACKAGE))))
  (assert (= writers []) writers))
