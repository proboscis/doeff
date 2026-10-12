## 結論

失敗例が 1 つ見つかりました。「宣言の env にこの名を置いた宣言を、doeff の側でも断る(多層防御)」という変更です。断りの規則は 1 か所に足せば効きますが、守りの検(guard)が赤になり、guard も変えないと入れられません。原因は、guard の文字の照合が「名を書く module」と「名を知る module」を区別せず、ほかの module の実装の文字に依っている事です。

## 1. 変更の要求と、それが要る理由

- **要求**: 宣言の env-vars と environ に `CLAUDE_PROJECT_DIR` を置いた宣言を、doeff の宣言の型 `EnvVar` と coordinator の `environ-refusal` が断る。
- **理由**: 今、宣言の側を守るのは上に載る系の検 377464ce だけです。その検が見るのは、上に載る系の repo の宣言の表と `controllers/`・`scripts/` の文字だけです(origin/main で読んで確認)。手で出す宣言や、ほかの上に載る系が出す宣言は、どこも見ていません。R3 の訳(上に載る系の守りは doeff を見ていない)を逆向きにした穴なので、次に頼まれやすい変更です。§3 で予約の名は job-env-rules の受け持ちなので、置き場もそこになります。

## 2. 元の主張・前提と、予想に反して変わる物

- **前提**: P1・P2・P3 は変えません。P2 の「doeff は検めて運ぶだけ」の「検める」の内で、断りを 1 つ足すだけです。§5 の 3 行目「doeff では強制しない」は設計の選択で(§6 の前提ではない)、それを変える事がこの要求そのものです。よって契約の適用範囲内の例と判断します。
- **関係する主張**:
  - §3: guard が持つ知識は「この名・見る dir」だけ。job-env-rules が持つ知識は「予約の名と頭」。
  - 共同の不変条件: env を書く全部の module が、この名を「書かない」事で守る。
  - §5 の 1 行目: `deploy/` と `src/` の下の file にこの綴りが無い事を照合する。
- **予想される変更の範囲**: job-env-rules だけ。`EnvVar` の 1 か所に足せば、RemoteJob の構成子と coordinator の `environ-refusal` は同じ規則を読みます。
- **実際に変わる物**: job-env-rules に加えて guard。guard が赤になり、guard を変えないと着地できません。

## 3. どの知識がどこへ漏れているか

| 種類 | 中身 |
|---|---|
| 意図した公開契約の拡張 | job-env-rules が断る名が 1 つ増える。公開の口(断りの文か None)の形は変わらない。 |
| 配線の変更 | 無し。coordinator にも RemoteJob にも手を入れずに断りが効く(実測)。 |
| 共同の不変条件 | この名そのもの。guard と上に載る系がこの名を知るのは設計どおり。job-env-rules が新しくこの名を知るのは、上の契約の拡張に付く物。 |
| 隠すはずの知識の漏洩 | guard は「`src/` か `deploy/` にこの綴りを持つ file = 子の env に書く者」と判断している。`src/` には書かない module(job-env-rules・coordinator など)も在るので、guard はそれらの実装の文字(予約の表の中身や註)に依っている。そのため job-env-rules の内部の変更が guard へ波及する。 |

同じ仕組みは job-env-rules 以外でも起きます(実測)。

- child-env が、どの層から来てもこの名を外す規則を足す(Q2)と、`launch.hy` で赤になります。
- 許可表の註に「この名は足さない」と 1 行書くだけ(Q3)でも赤になります。

つまり guard が実際に強いているのは「書かない」ではなく「doeff-cluster のどこもこの名を知らない」です。守りを強める変更ほど guard の変更が要ります。

直し方はどれも、guard に新しい知識を足すか、守りを弱めるかになります。

- (a) `runtime_env_model.hy` を照合から外す: guard が job-env-rules の file の置き場を知る事になる。予約の表が別の file へ移れば、また赤になる。
- (b) 名を定数 1 つに置き、その file を照合から外す: guard が job-env-rules の内部の定数に依る事になる。
- (c) 照合を「書く形」に変える(worker.yaml の env の name、WORKER_PASS_ENV の値、Dockerfile の ENV、boot.sh の export を読む): guard が書き手ごとの書き方を知る事になる。
- (d) 綴りを組み立てる `(+ "CLAUDE_" "PROJECT_DIR")`: guard を変えずに緑になる(実測)。ただしこれは §5 に書かれた盲点をわざと使う形で、同じ形で本当に書く者も通してしまう。

## 4. 最小の変更例と再現手順、観測した結果

**変更(Q1・計 7 行)**:
- `runtime_env_model.hy` に `(val HOOK-MARKER-ENV-NAMES (frozenset #("CLAUDE_PROJECT_DIR")))` を足す。
- `EnvVar` の `__post-init__` に、この表に当たる名を `RESERVED-ENV-VAR` で断る `when` を 1 つ足す。
- `.pyi` に 1 行、`test_env_prepare.hy` の断りの表に 1 行を足す。

**手順**:
1. §8 のとおり、mktemp の下に e5b5c54c7 を clone して `uv sync --group dev` する。
2. 変更を当てる前に検を走らせる。
3. `diffs/Q1-*.patch` を当て、`uv run pytest packages/doeff-cluster/tests/test_env_prepare.hy packages/doeff-cluster/tests/test_worker_pass_env.hy -q -k "refuses_mistakes or hook_marker"` を走らせる。

**観測した結果(すべて実測)**:

| 場合 | 結果 |
|---|---|
| 当てる前(e5b5c54c7) | guard は 1 passed |
| Q1 の probe | `EnvVar` が断る。`environ-refusal` が「environ の CLAUDE_PROJECT_DIR: reserved-env-var: …」を返す。ほかの名は None |
| Q1 の検 | 1 failed・1 passed。断りの検は緑。guard は振る舞いの照合が通った後、`AssertionError: ['src/doeff_cluster/shared/intent/runtime_env_model.hy']` で赤 |
| Q1b(綴りを組み立てる形) | 断りは同じく効き、2 passed |
| Q2(child-env が外す規則) | probe では declared からも extra からも外れて `{'PATH': '/bin'}`。検は 3 passed・1 failed、guard が `['src/doeff_cluster/worker/core/launch.hy']` で赤 |
| Q3(註を 1 行足すだけ) | guard が `['src/doeff_cluster/worker/core/launch.hy']` で赤 |

## 5. 未確認の前提と、足りない情報

**実測した事**: 上の表のとおり。どれも e5b5c54c7 の clone の上だけで試し、全数の検は走らせていません。

**推測・未確認の事**:
- この要求が本当に出るかは、R3 の訳からの推測です。card や issue は見ていません。上に載る系以外に宣言を出す者が在るかも確かめていません。
- 着地の門が `runtime_env_model.hy` の変更に対して、この guard を「近い検」として選ぶかは見ていません。選ばない場合、赤は日次の全体の検で初めて出ます。
- 今の coordinator の状態に、この名を持つ宣言が在るかは見ていません。上に載る系の検の中身を読んだだけで、走らせてはいません。

**コードを読んで見つけた別の候補(実験していない)**:
- `warm_host.hy` は、待ちの子の env を `ReadEnvironment(CHILD-ENV-ALLOWED, prefixes)` から自分で組み、REPLACE で起こしています。`child-environment` を通らないので、S-conc の「宿は env を組まない」と食い違います。
- `process_host.hy` と `probes.hy` も、実行環境が在る時だけ許可表を読む分け方と、名と頭で読むという許可の形を自分で持っています。許可の形が変わると(頭の一族から一部を除く、job ごとに継ぐ名を決める、など)、3 つの宿すべてに波及すると見ています。
- ただし分けられた子は、頼みに載った env で丸ごと置き換えられます(`warm_child.py` の `replace_environ`)。なので会話の CLI の env には響かない、と読んでいます。
- `probe_rules.hy` は extra・environ・worker の層を重ねず、job の子とは違う組み方をしています。「同じ規則」という主張とは一部ずれます。

以上から、合格・安全とは結論しません。

## 残した物

差分・probe・log は `/tmp/tmp.y8eujFCrBJ` に置きました。
- 差分: `diffs/`(Q1・Q1b・Q2・Q3 の patch)
- probe: `probes/q1_probe.py`・`probes/q2_probe.py`
- log: `logs/`
- 実験の木: `/tmp/tmp.y8eujFCrBJ/doeff`(変更は逆当てして戻し済み)

WAIT: work — 頼んだ会話の次の依頼
