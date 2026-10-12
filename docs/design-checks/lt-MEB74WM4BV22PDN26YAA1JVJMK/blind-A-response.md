## 失敗例: S1(venv を root の外へ移す)を主張どおりに実装すると、M-rules と表に無い 2 つの module も変える必要がある

失敗例が 1 つ見つかりました。S1 の主張は「venv の場所を知るのは M-venv-make と M-judge だけ」です。しかしこの 2 つだけを変えると、2 つの事が起きます。
- 待ちの子が空の venv で起き、起動を断り続ける。
- 冷えた道・分かれる道のどちらでも、入口の検め(起動時に宣言と版が一致するかを確かめる処理)が root-unmarked で落ちる。

### 1. 変更の要求と、それが要る理由
要求は S1 そのものです。`UV_PROJECT_ENVIRONMENT=<置き場>/<キー>` を使い、`uv sync` と `uv run` が使う venv を root の外の共有の置き場に揃えます。

理由は次のとおりです(この部分は推測)。
- 同じ commit の root から複製する時、`.venv` は持ち越さない決まりです(`env_translation.hy` の `NOT-COPIED`)。そのため root を作るたびに venv を作り直しています。
- root の掃除(大きさの上限・disk の空き)と venv の寿命を分けたい。

前提は全部そのままにしています。Linux、worker 1 つ、uv 0.12.19、方法は材料にある `UV_PROJECT_ENVIRONMENT` です。

### 2. 関係する主張と、予想に反して変わる物
主張 S1 の予測は「変わる = M-venv-make・M-judge」「変わらない = M-host・M-forked・M-rules」です。不変条件は `entry-env == uv-run-environ(project-dir, env)` です。

予想に反して変わる物:
- **(A)** `/home/ubuntu/.worktrees/doeff-wt-ki-b884-design-check/packages/doeff-cluster/src/doeff_cluster/shared/protocol/runtime_facts.hy` の `marked-root`(23–32 行、73 行で使う)。
  - 責務は「入口の検め」の材料を読むことで、venv の場所とは別の仕事です。
  - 渡される値は `doeff_core_effects/os_process.hy` の 190–193 行が答える `realpath(sys.prefix)` です。
- **(B)** 待ちの子の起こし方に関わる 3 か所。
  - M-rules(主張では「変わらない」): `worker/core/warm_rules.hy` の `warm-launch`(79–93 行)と `warm-child-argv`(136–142 行)。
  - 型: `worker/intent/worker_model.hy` の `WarmLaunch`(225–230 行)。欄は root・project・preload だけです。
  - 表に無い module: `worker/protocol/warm_host.hy` の `start-warm-child`(83–88 行)。子へ渡す env は許可表(`CHILD-ENV-ALLOWED`)の物だけです。
- **(C) 配線だけで、欠陥ではない**: `<置き場>` を worker の設定から渡す場合、`HostSettings` から `start-job`(M-host)を通って `job-launch` へ引数が 1 つ増えます。root の path(`code-path`)から導けるなら要りません。
- **(D) 推測**: `env_store` と `env_upkeep` は root を測って消すだけなので、root を消しても共有の venv は残ります。

### 3. どの知識がどこへ漏れているか
**(A) 隠すはずの知識の漏洩です。**
- `marked-root` は `realpath(sys.prefix)` から上の dir へ辿り、root の完成の印 `.doeff-env-ready.json` を探します。docstring にも「この process の venv を置いた root」とあります。
- つまり「venv は root の下に在る」という venv の置き場の規則を、入口の検めの側が持っています。
- 公開契約(`ReadRuntimeFacts` から `ProcessFacts`)の形は変わりませんが、実装がこの規則に依っています。
- 直すには root を別の手段で知る必要があります。例えば、子の文脈の環境変数(`run_context_rules`)に root を足す契約の拡張か、module の出所から辿る形です。どちらも別の責務を持つ module が変わります。
- `.venv` を symlink で残す案も同じく落ちます(`realpath` で解かれるため。実測)。
- `runtime_identity.hy` の 47–48 行に `<pdir>/.venv/` の写しも在ります。古い写しとして残るだけで、振る舞いは壊れない見込みです(推測)。

**(B) 不変条件の書き漏れと、M-rules の公開契約の拡張です。**
- 分かれた子 B は待ちの子の interpreter そのもの(`sys.prefix`・読み込み済みの module)で走ります(`warm_child.py` 137–158 行)。
- そのため「待ちの子を起こした uv run が選ぶ venv」「entry-env の `VIRTUAL_ENV`」「冷えた道で uv が選ぶ venv」の 3 つが同じである必要があります。この条件は不変条件の表にありません。
- 今は、uv の既定(`<project>/.venv`)に 2 か所が別々に頼っています。
  - M-judge の `uv-run-environ` は、その既定を文字列で写しています。
  - 待ちの子の側(`warm-child-argv` と `warm_host`)は、uv に任せています。
- 待ちの子の起動は `job-launch` を通りません。そのため M-judge だけに venv の指定を置くことはできず、`WarmLaunch` に欄を足す拡張と、`warm_host` の配線が要ります。
- 2 つの場所が関わること自体は正当な共同の不変条件です。問題は、主張がそれを「変わらない」側に分類していることです。

**検の見逃し(推測・未実行)。**
- `tests/test_task_venv_on_both_paths.hy` は `PATH` と `VIRTUAL_ENV` の文字列だけを比べています(181 行)。待ちの子の `WarmLaunch` も手で `:project REPO` と組んでいます(135 行)。
- 開発の checkout には `REPO/.venv` が在るので、待ちの子は普通に起きます。entry-env が共有の venv を名乗っていても、実際の interpreter の食い違いはこの検で拾えない見込みです。

### 4. 最小の変更例・再現手順・観測すべき結果
**変更例(主張どおりの最小の形)**
- `env_translation` の `SyncProject` の env に `UV_PROJECT_ENVIRONMENT=<S>/<key>` を足し、`site-packages` と `interpreter-of` も `<S>/<key>` から読む。
- `launch.hy` の `child-environment` に同じ変数を足し、`uv-run-environ` の venv を `<S>/<key>` にする。
- それ以外の module は触らない。

**再現(実測。一時 dir `/tmp/blind-a.WnD0`、uv 0.12.19)**
1. 依存の無い `pyproject.toml` を `$T/root/app` に置き、`uv lock --offline` を走らせる。
2. root の完成の印を置く: `echo '{}' > $T/root/.doeff-env-ready.json`
3. venv を共有の置き場に作る: `UV_PROJECT_ENVIRONMENT=$T/shared/env-k1 uv sync --offline --frozen --project $T/root/app`
4. 冷えた道を真似る: `env -i PATH=… UV_PROJECT_ENVIRONMENT=$T/shared/env-k1 uv run --no-sync --frozen --project $T/root/app python -c '<prefix・VIRTUAL_ENV・PATH の頭を表示>'`
   - 結果: 3 つとも `$T/shared/env-k1` を指す。
5. 待ちの子の起動を真似る(今の `warm_host` と同じく env は許可表だけ): 同じ命令を `UV_PROJECT_ENVIRONMENT` 無しで走らせる。
   - 結果: `Creating virtual environment at: $T/root/app/.venv` と出し、root の中に空の venv を黙って作って exit 0。
   - その空の venv の python では `import doeff_cluster` が `ModuleNotFoundError` になる。
6. 作業木の `.venv` の python で `$T/scripts/marked_root_probe.py`(本物の `marked-root` と `os-file-handler` を呼ぶ)を走らせる。
   - `$T/root/app/.venv` からは `$T/root` が見つかる。
   - `$T/shared/env-k1` からは None。
   - symlink の `.venv`(共有の置き場を指す)からも None。

**本番で観測すべき結果**
- (a) 待ちの子の log の最後の行が `No module named 'doeff_cluster'` になり、準備完了の印が書かれません。断りの回数が `warm-refusal-limit` に届くと、その root から分かれる task が env-failed(ENV-INCOMPATIBLE)で終わります。root の中には空の `.venv` が残ります。この連鎖はコードを読んだ推論です。
- (b) 入口の検めを撃つ job が、どちらの道でも起動の時に「入口の検めが不一致(root-unmarked): venv の上に完成の印を持つ root が無い」で落ちます。`marked-root` が None を返すことは実測です。agora-controllers 側の流れ(`with-agora-host` → `runtime-checked`)は読んだだけです。

### 5. 未確認の点と、不足する情報
- **実測した物**: uv の 2 つの振る舞い(手順 4・5)、`marked-root` の 3 つの戻り値、空の venv に doeff_cluster が無いこと。
- **推測(コードを読んだだけ)**:
  - 待ちの子の断りから env-failed に至る流れ。
  - 検の見逃し(手順を実行していない)。
  - (D) の掃除。
  - agora-controllers の job が全部 `runtime-checked` を通るか。読んだのは、渡された作業木の外の共有の checkout `~/repos/agora-controllers`(357e769b9)の `controllers/foundation/host.hy` だけです。本番の版と同じかは確かめておらず、全部の job も見ていません。
- **不足する情報**:
  - `<置き場>` を root の path から導くのか、worker の設定にするのか(C の配線が要るかが変わる)。
  - 主張の側が `runtime_facts` を M-venv-make の「venv の中を読む」に含める意図だったか。
  - 相対 path の `UV_PROJECT_ENVIRONMENT` は試していない。
  - 実験の python は uv が選んだ 3.14.3 freethreaded で、本番の版とは違う。
- **詰めていない別の候補(S3)**: 冷えた道の service は shim が `DOEFF_WORKER_NOTICE_FD` を置きます。分かれる道は `relay=None` なので置きません。service を分ける道に広げると、env の面でも M-forked と `WarmRequestWire` が変わる見込みです。
- **やらなかった事**: S1 の実装も worker 全体も走らせていません。pytest も走らせていません。作業木は変えていません(`git status` は空)。一時 dir `/tmp/blind-a.WnD0` は再現用に残してあります。
