## 結論

doeff-cluster の守りの検 `test-no-template-or-worker-code-hands-the-hook-marker-to-the-child` を緑のまま通し、CLAUDE_PROJECT_DIR を実行環境の job(REPLACE)の子の env へ渡してしまう実装を 1 つ作りました。検の実行と probe は、手元の実験の木(e5b5c54c7)で行いました。

## 1. 機能要求と差分

**機能要求(想定)**
- 今は、上に載る系が ConfigMap worker-env に値を足すたびに、WORKER_PASS_ENV_EXTRA にも名を書き足す必要があります。これを忘れると、実行環境の job へ移した service が値を受けられません。
- そこで `--pass-env` に `*` と書けば、worker の env のうち「宣言の env-vars・:environ に置いてよい名」を全部、子へ渡すようにします。
- 「置いてよい名」の判定には既存の `EnvVar` の規則(予約の名と頭・秘密を名指す名を断る)をそのまま使い、規則を二重に書きません。
- 上に載る系は `WORKER_PASS_ENV_EXTRA=*` に書き替えます。雛形の `$(WORKER_PASS_ENV_EXTRA)` の展開でそのまま届くので、doeff の deploy/ は変えません。

**差分**: `worker/entry/main.hy` だけ(+20/-4)。主な部分は次のとおりです(全文は `failure.diff`)。

```hy
(import doeff_cluster.shared.intent.runtime_env_model [EnvVar])
(val PASS-ALL "*")
;; machine-environment: `*` が在れば全部の名を読む
(<- found (ReadEnvironment :names (tuple (gfor n names :if (!= n PASS-ALL) n)) :prefixes (if (in PASS-ALL names) #("") #())))
;; passed-environment
(val named (tuple (gfor n wanted :if (!= n PASS-ALL) n)))
(val missing (lfor n named :if (not-in n environ) n))
...
(| (if (in PASS-ALL wanted) (! (declarable-environment environ)) {})
   (dfor n named n (get environ n)))
(defk declarable-environment [environ] ...
  (dfor #(k v) (.items environ) :if (is (EnvVar.environ-refusal {k v}) None) k v))
```

## 2. 本来の持ち主と、破る契約

**移る判断**
- 「worker の env のどの名が REPLACE の子へ届くか」を決めているのは今、次の 2 つです。
  - child-env: 閉じた許可表・頭 `LC_`・名を並べた `--pass-env`
  - 名を綴る側: worker-template の WORKER_PASS_ENV と、上に載る系の WORKER_PASS_ENV_EXTRA
- 差分の後は、この判断が job-env-rules の `EnvVar` に移ります。`EnvVar` は「宣言に置いてよい名」の規則で、前提 P2(宣言の名は上に載る系が守る)の下で CLAUDE_PROJECT_DIR を断らないように作られています。worker の env の中身を判じる知識も、この名の意味(上に載る系の包みが見分けに使う鍵)も持っていません。
- 結果として、名を列挙して許す形(許可表)が、断る名だけを並べて残りを全部通す形(拒否表)に変わります。

**名の出所が変わる**
- 前: 誰かが綴った名だけが子へ届きました。
- 後: image の ENV・envFrom・boot.sh の export・機体の unit に「たまたま在る」名が、誰も綴らないまま届きます。

**破る契約**
- (a) `--pass-env` / WORKER_PASS_ENV(_EXTRA) の公開の口は「名を `,` で並べる・無い名は起動を止める」です(help・docstring・検の file の頭の註「worker が名で宣言して子へ渡す」・worker.yaml の頭の註)。`*` は名ではなく、欠けても止まりません。
- (b) R2 と共同の不変条件。差分の後は worker の code が、自分の env に在る CLAUDE_PROJECT_DIR を子の env へ書きます。
- (c) §4 S-eff の主張「頭で広げる形は振る舞いの照合が拒む」。広げる場所が `child-environment` ではなく extra の口(`passed-environment`)だと、拒まれません。

## 3. 提示された各検査が拒まない理由(実測)

| 検 | 拒まない理由 | 実測 |
|---|---|---|
| §5-1 文字の照合(deploy/・src/ の中身に綴りが無いか) | 差分に綴りが無い(`*` と `EnvVar` の呼びだけ)。名は実行の時の env から来る | diff の中の綴り 0 件・検は 1 passed |
| §5-2 振る舞いの照合(`child-environment` に名と PATH を入れ、extra に `{}` を渡す) | `child-environment` は変えていない。広げたのは extra を作る `passed-environment` で、検は extra に `{}` を渡すのでこの道を通らない | 1 passed |
| 同じ file の残り 3 本 | 名の列の振る舞いは変えていない | 4 passed |
| §5-3(上に載る系の検 377464ce) | doeff の差分はその検の repo の外にある | 検の中身を読んでいないので、`WORKER_PASS_ENV_EXTRA=*` を拒むかは判じません |
| §5-4 | 検が存在しない | — |

**置き場所だけが分かれ目である事の確認(実測)**: 同じ規則(`EnvVar.environ-refusal`)を `child-environment` の篩に入れた差分では、検は赤になりました(AssertionError `{'CLAUDE_PROJECT_DIR': '/work', 'PATH': '/bin'}`)。

## 4. 最小の手順と期待結果(すべて実測)

1. §8 の手順で e5b5c54c7 の木を作り、`uv sync --group dev` を実行する。
2. `git apply failure.diff`
3. 守りの検を走らせる: `uv run pytest packages/doeff-cluster/tests/test_worker_pass_env.hy -q -k hook_marker`
   - 差分の前も後も 1 passed。file 全体では 4 passed。
4. probe `compose_child_env.py` を走らせる。
   - 組み方: main.hy と同じ順(machine-environment → passed-environment → worker-context-environ)のあと、`process_host.start-job` と同じ順(許可表の ReadEnvironment → `job-launch`)。答え手は本物の subprocess-handler。
   - 入力: worker の env に `CLAUDE_PROJECT_DIR=/work`(検と同じ値)と雛形の名を置き、`--pass-env` には雛形の名の列に `*` を足した値を渡す。

| 版 | `--pass-env` | 結果 |
|---|---|---|
| 差分の後 | 雛形の名の列 + `*` | env-mode replace・CLAUDE_PROJECT_DIR=/work が子の env に入る |
| 差分の前 | 雛形の名の列 + `*` | worker の起動が止まる(ValueError: --pass-env の名が worker の環境に無い: *) |
| 前・後とも | 名の列(AGENT_ENV・AGENT_CLAUDE_HOME) | CLAUDE_PROJECT_DIR は入らない |

   - 付随の観測: 差分の後に `*` を渡すと、許可表にも名の列にも無い `UV`(probe を起こした `uv run` が置いた名)も子へ渡りました。

5. 意味の確かめ: 上に載る系の包み(agora 6aa5749c の `tool-stream-shell.sh` の写し)を実行した。
   - CLAUDE_PROJECT_DIR が無い時: nice 19(下げが効く)
   - 在る時: nice 10(下げが外れる)
   - SCHED_IDLE の差は、この環境の呼び手が既に SCHED_IDLE なので見分けられませんでした。

## 5. 未確認の前提と、足りない情報

- **本番の worker の env にこの名が在るかは見ていません。** 上に載る系の worker-env.env(6aa5749c)には在りません(grep 0 件)。image の実物の ENV(`FROM ${BASE}`)も見ていません。実害が出るのはこの名が worker の env に在る時だけで、これは検の振る舞いの照合と同じ前提です。
- **子の env から CLI の env まで届くかは、読んだだけで実行していません。** agora-controllers 81d7d064 の `agent_runtime.hy` の `child-env` を読むと、job の process の env を写し、`INHERITED-PREFIXES` に CLAUDE_PROJECT_DIR は入っていません。前提 P1 も確かめていません。
- 上に載る系が実際に `*` と書くかは想定です。
- probe は `main()` 全体(coordinator への接続・StartProcess)を起こさず、組み立ての関数を同じ順で呼んだものです。
- **推測**: boot.sh が export する名(GIT_SSH_COMMAND など)も子へ渡るはずですが、実行していません。
- 走らせていない検: test_k8s_declarations.py・他の test・lint・semgrep・architecture.hy・型の検。これらが通るとも落ちるとも言いません。この失敗例をもって、何かが安全・合格とは結論しません。

## 実験の置き場

`/tmp/tmp.WBO5FXUrPr`
- `failure.diff`: 失敗例の差分
- `contrast-in-child-environment.diff`: 同じ規則を `child-environment` に置いた対照の差分
- `probe/compose_child_env.py`: 子の env を組む probe
- `logs/01〜08`: 検・probe・包みの実行の記録
- `upper/tool-stream-shell.sh`: 上に載る系の包みの写し
- `doeff/`: 実験の木。main.hy は失敗例の版のまま、launch.hy は元の版に戻してあります。

WAIT: work — 呼び手(設計検証の担当)の次の依頼
