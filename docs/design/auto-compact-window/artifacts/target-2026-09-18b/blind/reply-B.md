# 反例 B — 「モデルの窓に合わせて閾値を頭打ちにする」

探索は完了しており、検査は実際に走らせて確かめました。以下が報告本文の全文です。

---

## 1. 現実的な機能要求と、提案する最小差分

**要求(現実的な形)**: 「Sonnet / Haiku は文脈の窓が 200k しかない。1M 前提の床 400k をそのまま載せると『窓より大きい閾値 = 一生畳まない』と同じで、費用の欠陥が直らない。モデルの窓の 40%(既定の 400k / 1M と同じ比)で頭打ちにしてほしい。」

この要求は実装コードの註が自分で招いています — `claude_code.hy:91` に「**1M の窓に対して 40%** ≒ 実装の途中で畳んで…」と床の根拠が書いてあり、窓が 1M でないモデルへ同じ理屈を延ばすのは素直な一般化です。

差分は `packages/doeff-agents/src/doeff_agents/sessionhost/impls/claude_code.hy` の 1 file・+30 行。**関門の関数 `claude-autocompact-value` には 1 文字も触りません**(だから差分を見た検分者には「唯一の導出点」が無傷に見える):

```hy
;: モデルごとの文脈の窓(token・公開の model card)。名簿に無いモデルは 1M(Fable / Opus)。
(setv MODEL-CONTEXT-WINDOWS {"claude-sonnet-5" 200000
                             "claude-haiku-4-5" 200000
                             "claude-haiku-4-5-20251001" 200000})
(setv MODEL-CONTEXT-WINDOW-DEFAULT 1000000)
;: 窓に対して畳み始める割合(既定の 400k / 1M = 40% と同じ比)。
(setv AUTOCOMPACT-WINDOW-RATIO 0.4)

(deff claude-model-window [model]
  {:pre [(: model (| str None))] :post [(: % int)]}
  (if (is model None)
      MODEL-CONTEXT-WINDOW-DEFAULT
      (.get MODEL-CONTEXT-WINDOWS model MODEL-CONTEXT-WINDOW-DEFAULT)))

(deff claude-autocompact-args [params]
  {:pre [(: params dict)] :post [(: % list)]}
  (setv value (claude-autocompact-value params))
  (setv ceiling (int (* (claude-model-window (.get params "model"))
                        AUTOCOMPACT-WINDOW-RATIO)))
  (setv capped (if (= value AUTOCOMPACT-AUTO)
                   value
                   (str (min (int value) ceiling))))
  [AUTOCOMPACT-ARG capped])
```

適用済みの複製: `/tmp/cexB/repo/packages/doeff-agents/src/doeff_agents/sessionhost/impls/claude_code.hy`(共有ソースは未変更。`git status` は作業前と同じ 4 file のまま)。

## 2. 本来の責務所有者と、違反する契約 — 実測つき

同じ入力を修正前 / 反例適用後で比べた実測(`/tmp/cexB/demo.py`):

| 入力 | 修正前 argv | 反例適用後 argv | CLI 受理 |
| --- | --- | --- | --- |
| 宣言なし / `claude-fable-5-1` | `--autocompact 400000` | `--autocompact 400000` | ○ |
| 宣言 200000 / `claude-fable-5-1` | `--autocompact 200000` | `--autocompact 200000` | ○ |
| **宣言なし / `claude-sonnet-5`** | `--autocompact 400000` | **`--autocompact 80000`** | **×** |
| **宣言 200000 / `claude-sonnet-5`** | `--autocompact 200000` | **`--autocompact 80000`** | **×** |
| 宣言 `auto` / `claude-sonnet-5` | `--autocompact auto` | `--autocompact auto` | ○ |
| **宣言なし / `claude-haiku-4-5`** | `--autocompact 400000` | **`--autocompact 80000`** | **×** |

`200000 × 0.4 = 80000` は幅(100,000〜1,000,000)の**下**へ落ちます。R2 の条文どおりなら、この argv で起こされた席は argv 解釈の段で死に、stream-json の行を 1 つも吐かない ── **その手番が丸ごと消える**。閾値を宣言した会話ほど確実に死に、しかも縮退の印(`--autocompact auto`)が出ないので `ps` から読めません。

### 破れる契約と、移る知識

| 契約 | 破れ方 |
| --- | --- |
| `autocompact-derivation`「argv に載せる閾値の文字列を決める**唯一の点**」 | 決定点が 2 つになる。最終値を決めるのは `claude-autocompact-args` の頭打ちで、そちらは幅の定数を知らない |
| 不変条件「戻りは `auto` か 100k〜1M の 10 進整数のみ」 | `claude-autocompact-value` の返りは今も不変条件を満たす(`400000`)。**argv に載る値だけ**が幅の外へ出る。関門を通した**後**に加工しているため |
| CC3「CLI の綴り・受ける幅が変わる → `autocompact-derivation` の定数 1 点だけ」 | 幅を直す日に `AUTOCOMPACT-MIN-TOKENS` を動かしても、`ratio × window` はその幅を知らない。床の知識が 2 か所に分かれる |
| §2「他から隠す知識 = 幅の定数、床の値、縮退の向き」 | agentd に「どのモデルの窓が何 token か」「窓の何割で畳むか」という**方策の知識**が新しく住みつく。これは宣言側(`route-dispatcher` / `acp-charter`)が持つはずだった判断 |
| R7「誤りは縮退し、縮退したことが外から読める」 | 頭打ちは静かに起きる。結果は縮退でも `auto` でもなく、そのまま死ぬ値 |
| §3 charter の意味「会話が名乗った値が第一」 | 会話が 200000 と宣言しても 80000 に黙って書き換えられる。宣言と実行の対応が切れる |

**契約の型検査は止めません(実測)**: `deff` の `:pre` / `:post` は実行時に発火しますが**型ちょうど**です — `claude-autocompact-value('not-a-dict')` は `AssertionError: expected dict, got str` を出す一方、`:post [(: % str)]` は `"80000"` を通します。したがって同じ頭打ちを `claude-autocompact-value` の**中**に書いても契約は通ります。関門の外へ出したのは、差分の見た目を無傷にするためだけです。

## 3. 提示された各検査がそのコードを拒否しない理由(すべて実測、`-k` の焦点走行のみ)

**単体 deftest 4 本 — 8 passed(修正前も 8 passed)**

反例が回避するのではなく、4 本とも **model が `claude-fable-5` / `claude-fable-5-1` 固定**なので窓が 1M 扱いになり、頭打ちの天井が `400000` = 床と一致して差が出ません。

- `test_claude_argv_always_declares_the_compaction_threshold`: `min(400000, 400000)` → `"400000"`。位置・凍結接頭・`--effort` index 4 は無変更。
- `test_claude_argv_carries_the_threshold_the_conversation_declared`: `min(200000, 400000)` → `"200000"`。
- `test_claude_argv_never_carries_a_threshold_that_kills_the_turn`: 悪い値は `claude-autocompact-value` が先に `auto` へ倒し、頭打ちは `auto` を素通しする分岐を持つ。反例の候補 `[50000 2000000 "nope" True {} -1]` に**幅の外へ出る組み合わせ(小さい窓のモデル)が 1 つも無い**。
- `test_claude_resume_argv_declares_the_threshold_too`: resume も同じ関数を通るので `"200000"`。

**適合検査 `conformance/test_s13_argv_wiring.py`** — 読んだ限り `--autocompact` への assert が 1 つも無い(検査するのは `--dangerously-skip-permissions` / `--settings` / `--mcp-config` / `--strict-mcp-config` と M1 の golden path)。加えて `launch_m1` は `model` 引数を持たないので params の model は None → 窓 1M → `"400000"` で挙動自体が変わらない。**この検査は走らせていません**(実 daemon を起こすため)。根拠は source の読みです。

**静的検査 `.semgrep.yaml`(repo 直下・全 rule)— 0 findings(修正前も 0 findings)**

impls を対象にする rule は `doeff-agents-substrate-clean-impls` ちょうどで、禁じるのは `subprocess` / `sqlite3` / `open(` / `os.system`。反例は純関数の追加なので当たりません。定数の単一の家を守る rule は 2 本ありますが、どちらも綴りで名指しです — `ready-pattern-literal-outside-physics-home` は python の adapters の `*READY_PATTERN`、`repl-idle-budget-literal-single-home` は `setv REPL-IDLE-MAX-WAIT-SECONDS`。**`AUTOCOMPACT-*` を単一の家に縛る rule も、agentd に役 / モデルの知識が入るのを断る rule も存在しません。**

**`ruff`** — `.hy` は対象外(input §6 の宣言どおり。ruff は `.py` / `.pyi` しか読まない)。

## 4. 同じ設定で検査通過と責務違反の両方を確かめる最小手順

```bash
# 0) 隔離複製(共有ソースは触らない)
SRC=/Users/s22625/.worktrees/doeff-wt-autocompact-window
mkdir -p /tmp/cexB/repo/packages/doeff-agents
cp -R "$SRC/packages/doeff-agents/src" /tmp/cexB/repo/packages/doeff-agents/src
cp "$SRC/.semgrep.yaml" /tmp/cexB/repo/.semgrep.yaml
# 1) 上の差分を /tmp/cexB/repo/.../impls/claude_code.hy に当てる(適用済みの複製あり)

# 2) 単体 deftest(焦点。8 本 / 収集 40 本 — 受付の上限 400 の下)
cd "$SRC" && PYTHONPATH=/tmp/cexB/repo/packages/doeff-agents/src \
  /Users/s22625/repos/doeff/.venv/bin/python -m pytest \
  packages/doeff-agents/tests/test_sessionhost_impls.py \
  -k "claude_argv or claude_resume_argv" -q
# 期待: 8 passed（PYTHONPATH を $SRC/... に替えた修正前も 8 passed）

# 3) 静的検査
cd /tmp/cexB/repo && semgrep scan --config .semgrep.yaml --metrics=off \
  packages/doeff-agents/src/doeff_agents/sessionhost/impls/
# 期待: 0 findings（修正前も 0 findings）

# 4) 責務違反の実測
cd /tmp && PYTHONPATH=/tmp/cexB/repo/packages/doeff-agents/src \
  /Users/s22625/repos/doeff/.venv/bin/python /tmp/cexB/demo.py
# 期待: sonnet-5 / haiku-4-5 の行が --autocompact 80000（幅 100k〜1M の外）
```

⚠ `-p no:randomly` は受付の門が断ります(実測 — 「受付の結線を外す切替」として拒否)。また doeff の editable install は `~/repos/doeff` を指すので、worktree の版を測るには `PYTHONPATH` が必須です。

## 5. 未確認の前提、不足する情報

- **実測**: 単体 deftest 8 本の合否(修正前後)、semgrep 0 findings(修正前後)、導出値の表、`deff` の契約が型ちょうどであること、受付の門が `-p no:randomly` を断ること。
- **推測(未実行)**: 適合検査 S13。source を読んで `--autocompact` への assert が無いことと `launch_m1` に model 引数が無いことを確認しただけで、走らせていません。**「S13 が通る」と断定はしません。**
- **推測(未検証の外部事実)**: 「幅の外の値で CLI が argv 解釈の段で死ぬ」は input R2 と実装の註(claude 2.1.274 実測)を前提に置いただけで、私自身は claude を起こしていません。ここが偽なら反例の被害は「意図しない閾値が黙って載る」に縮みます(責務違反は残ります)。同じく Sonnet / Haiku の窓 200k も公開情報としての前提で、この場では検証していません。
- **不足している検査**: `docs/adr/*.hy` の defadr 法に `autocompact` を含む条文は 1 つもありません(grep 実測)。input §6 に挙がっていない検査(pyright・doeff-linter・pre-commit 等)は走らせておらず、それらが拒否するかは**不明**です。とくに doeff-linter(Rust)の不変性 / 品質の規則は未確認で、ここが反例を捕まえる可能性は残ります。
- **段 2(未実装)の影響**: `route-dispatcher` が役ごとの値を配る側は未実装なので、実際に運用へ出た時に `charter.auto_compact_window` と併せてどう壊れるかは、段 2 の実装形しだいです。
- 「反例が見つからない = 合格・安全」とは結論していません。上記のとおり、現在の検査束は **agentd 側に閾値・モデル・幅の知識が増えることを 1 本も断っていない** ので、同型の反例(たとえば `params["effort"]` や作業場の種別で閾値を分ける)は同じ穴からいくらでも通ります。

---

関連する絶対 path:
- 対象実装: `/Users/s22625/.worktrees/doeff-wt-autocompact-window/packages/doeff-agents/src/doeff_agents/sessionhost/impls/claude_code.hy`
- 単体検査の入口: `/Users/s22625/.worktrees/doeff-wt-autocompact-window/packages/doeff-agents/tests/test_sessionhost_impls.py`(本体は `sessionhost_impls_deftests.hy`)
- 反例の適用済み複製: `/tmp/cexB/repo/packages/doeff-agents/src/doeff_agents/sessionhost/impls/claude_code.hy`
- 実測スクリプトと log: `/tmp/cexB/demo.py`・`/tmp/cexB/baseline.log`・`/tmp/cexB/patched.log`