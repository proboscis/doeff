;; worker が job の子へ渡す自分の環境変数(--pass-env・boot.sh の WORKER_PASS_ENV・E13)の検。
;;
;; 実行環境の job の子は worker の環境を許可表でしか継がない(handlers.child-environment)。機体の設定(家や作業場所の path・預かり所の
;; URL)は job の宣言ではなく worker の持ち物なので、worker が名で宣言して子へ渡す。名乗った名が無ければ起動を止める。
(require doeff-hy.macros [deftest <- val])
(import json)
(import pathlib [Path])
(import pytest)
(import doeff_cluster._shared.intent [runtime_env_model])
(import doeff_cluster [JobSpec])
(import doeff_cluster._shared.intent.runtime_env_model [WITHHELD-ENV-NAMES])
(import doeff_cluster [CodeLayout WorkerPolicy])
(import doeff_cluster._worker.entry.main [passed-environment])
(import doeff_cluster [child-environment]
        doeff_cluster._worker.core.launch [job-launch JobLaunch])
(import doeff_cluster [shim-spans]
        doeff_cluster._worker.core.shim_timing [ShimSpans])

(val PACKAGE (. (Path __file__) (resolve) parent parent))
;; Claude Code が hook・MCP の起動にだけ置く env の名(検の側の綴り — 検の dir は下の照合の外)。
(val MARKER "CLAUDE_PROJECT_DIR")


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


(deftest test-the-worker-withholds-the-hook-marker-from-every-layer-of-the-child-env
  ;; Claude Code は hook・MCP の起動にだけ env CLAUDE_PROJECT_DIR を置く。上に載る系の Bash の包み(ConfigMap worker-shell)はこの名の有無で
  ;; hook・MCP の起動と Bash の道具の命令を見分け、無い時だけ命令を SCHED_IDLE・nice で下げて出力を流す。job の子の CLI の env にこの名が
  ;; 在ると Bash の道具の命令も hook と見なされ、下げと流しが黙って外れる(card ki-222003f40484・設計検証 lt-2F7ATZXHBCYA0WX1F36A6ZC1JE)。
  ;; worker はこの名(WITHHELD-ENV-NAMES)を、どの層から来ても子の env に置かない。反例(盲検 B): --pass-env を「宣言に置ける名を全部」へ
  ;; 広げても、許可表を頭で広げても、宣言の environ に書いても、子へ届かない。
  (assert (in MARKER WITHHELD-ENV-NAMES) WITHHELD-ENV-NAMES)
  (val with-marker {MARKER "/work" "PATH" "/bin"})
  (for [child [(! (child-environment with-marker {} {} {})) (! (child-environment {} with-marker {} {}))
               (! (child-environment {} {} with-marker {})) (! (child-environment {} {} {} with-marker))]]
    (assert (= child {"PATH" "/bin"}) child))
  ;; job の子の起こし方(job-launch)の 2 つの道: 実行環境の job(REPLACE)も木の job(EXTEND の足す分)も、worker の文脈・宣言の environ・
  ;; worker の環境のどれに在っても置かない。木の job が EXTEND で継ぐ worker の環境そのものは外せない — worker の環境に置かない事は下の
  ;; 雛形の照合が守る。
  (<- shim ShimSpans (shim-spans (WorkerPolicy)))
  (val runtime (json.dumps {"project" {"repo" "app" "path" "."} "envVars" [{"name" MARKER "value" "/work"}]}))
  (for [spec [(JobSpec "task/t1" "app.main" #("task") "rev1" :runtime-env runtime :env-key "k1" :environ #(#(MARKER "/work")))
              (JobSpec "svc" "app.main" #("service") "rev1" :environ #(#(MARKER "/work")))]]
    (<- plan JobLaunch (job-launch spec "/roots/env-k1" "1-abc" 1 :python "/py" :hy-command "/bin/hy" :extra-env with-marker
                                   :layout (CodeLayout) :allowed-env with-marker :worker-pid 42 :program-path None
                                   :program-env "DOEFF_PROGRAM_FILE" :work-dir "/jobs/t1" :shim-grace-ms shim.shim-grace-ms
                                   :notice-env "DOEFF_NOTICE_FD"))
    (val names (lfor e plan.env e.name))
    (assert (and (in "PATH" names) (not-in MARKER names)) #(plan.env-mode names))))


(deftest test-only-the-child-env-name-rules-spell-the-hook-marker
  ;; doeff-cluster の中でこの名を綴るのは、子の環境変数の名の約束の置き場(WITHHELD-ENV-NAMES を持つ runtime_env_model)1 つ。worker の
  ;; 雛形(deploy/ — Pod の env・WORKER_PASS_ENV・機体の dir・boot.sh・image)はこの名を書かない(木の job は worker の環境を EXTEND で継ぐ
  ;; ので、worker の環境に在れば外せない)。ほかの src も綴らない — 名を足す・外す判断は置き場の 1 つを読む。上に載る系の ConfigMap
  ;; worker-env はこの repo の外。
  (val owner (.resolve (Path runtime_env_model.__file__)))
  (val writers (lfor path (+ (sorted (.rglob (/ PACKAGE "deploy") "*")) (sorted (.rglob (/ PACKAGE "src") "*")))
                     :if (and (.is-file path) (not-in "__pycache__" path.parts) (!= (.resolve path) owner)
                              (in MARKER (.read-text path :errors "replace")))
                     (str (.relative-to path PACKAGE))))
  (assert (= writers []) writers)
  (assert (in MARKER (.read-text owner :encoding "utf-8")) owner))
