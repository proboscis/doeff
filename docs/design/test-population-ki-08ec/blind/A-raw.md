# 盲検 A の返答(未加工)

- 起動: Claude Code の Agent tool・subagent_type general-purpose・model = opus(claude-opus-5-5)・新しい文脈(fork / resume なし)
- 要求 effort: xhigh — Agent tool は effort を受け付けない(指定できず・実際の effort は未確認)
- 1 番目の候補 gpt-6-astra / low は使わなかった: 2026-09-24 の operator の決定(委譲の既定を Opus 5.5 へ移した)に従う
- 機体: この pod(agora の会話の宿)・profile = この会話と同じ(子へ資格は渡していない)
- agentId: af41a4b79ee2c14d5・所要 440 s・tool 34 回
- 入力: blind/input-common.md(sha256 は SHA256SUMS.txt)と SKILL の A の依頼文(逐語)
- 模型の置き場: /tmp/ki08ec-cx(pod の一時領域)

---

## 反例: 処理ステージ `packages` の失敗名が repo の根からの相対にならず、直すと Makefile と固定のテストの両方を変えることになる

### 1. 変更要求と、それが必要になる理由
要求は「`packages` の赤を、台帳の失敗名だけで帰属判定と修理ができる形にする(名前を repo の根から pytest へそのまま渡せる形にする)」です。

- 設計を入れると、package の赤が初めて処理ステージごとに台帳へ載ります(F3 の時点では 1 本も走っていません)。
- 着地の道具は、FAILED 行の先頭の語を逐語のまま失敗名にします。その名前は帰属判定・対照走行の cache・修理依頼の証拠に使われます(`land.py` の `_failed_name_pytest` / `failed_test_names` の註)。
- design.md の F7 は「失敗名は package をまたいで一意」を前提にしています。したがってこの要求は前提の範囲内です。

### 2. 関係する主張と、予想に反して変わるもの
- 関係する主張: F7、S6(試験の口は既存の `PACKAGE_UV_RUN` で足りる)、§3.1。§3.1 では `population-pin` が「走らせ方の細部」を隠し、`make-test-packages` が「日次の存在」を隠すことになっています。
- 実測では F7 が成り立ちません(4 節)。直すには `make-test-packages` の呼び方を変える必要があります(`cd $dir && pytest tests/` を、repo の根からの `pytest $dir/tests` にする)。
- すると `population-pin` の §3.4 の 2. と 3. が、母集団は変わっていないのに赤になります。偽の runner が cwd を記録して、訪ねた package と失敗させる package を決めているからです。S6 の試験の口も「cwd で package を見分ける」形から変わります(これは設計の文面からの推測です)。

### 3. どの知識がどこへ漏れるか(拡張との区別)
- **漏洩(`population-pin` → Makefile の走らせ方)**: 「package の母集団 = 偽の runner が見た cwd の集合」という前提は、`cd` で走らせるという Makefile の細部そのものです。これは §3.1 で `population-pin` が隠すと宣言した知識です。
- **公開契約の拡張で済む部分(`make-test-packages`)**: 着地の道具は失敗名を逐語で読み、処理ステージをまたいで集合として合わせ、repo の根から pytest へ渡します。この読み方に合わせる必要があります。ただし契約を「出す失敗名は repo の根から pytest へ渡せる node id」と中立に書けば、日次を名指さずに済みます。
- **持ち主の無い共同の不変条件**: 「全処理ステージの失敗名が repo の根相対で互いに一意」は、`gate-full-stages`・`make-test-packages`・`root-testpaths`・外部の着地の道具にまたがります。§3.1 にはこれを持つ module がありません。着地の道具は処理ステージの内側の `cd` を知らないので、外部側では直せません。

### 4. 最小の再現と観測(実測)
`/tmp/ki08ec-cx` に写しを作りました。
- repo の根: pytest 設定(ini)入りの pyproject と conftest
- `packages/a` と `packages/b`: どちらにも `tests/test_cli.py::test_fail`(assert False)
- Makefile: §3.3 の形(赤の後も続ける)

手順:
```
make -s test-packages PACKAGE_UV_RUN='<doeff venv>/python -m' > stage.log
PYTHONPATH=~/dotfiles/agentcli/src python3 -c "from agentcli.land import failed_test_diagnostics; print(failed_test_diagnostics(open('stage.log').read()))"
```

観測(pytest 9.1.1):
- rc 2、`test-packages failed: a b` が出ます。
- FAILED 行は 2 本とも `tests/test_cli.py::test_fail` でした。
- 着地の道具の実物の抽出器は `(2, ['tests/test_cli.py::test_fail'])` を返しました。2 件の赤が 1 つの名前に畳まれ、どの package かが消えます。
- `--collect-only` の node id は `packages/a/tests/...` です(rootdir は repo の根)。一方、結果の要約の FAILED 行は cwd からの相対でした。`--rootdir` を付けても変わりません。repo の根から `pytest packages/a/tests` で走らせた時だけ `packages/a/tests/...` になります。

実際の repo では、cwd 相対で衝突する path が 6 組あります。例えば `tests/unit/test_effect_handlers.py` は 5 つの package にあり、gemini・openai・openrouter の 3 つは `test_effect_exports` など関数名まで同じです。repo の根にこの path は無いので、修理席が名前をそのまま渡すと file が見つかりません。

### 5. 未確認の前提と不足する情報
- 実際の repo では走らせていません(制約による)。確認は同じ pytest の版で作った写しの上だけです。
- 次の 2 点はコードと註を読んだうえでの推測です。
  - `remote_check` が処理ステージの出力を手を加えずに log へ運ぶこと
  - 修理席が実際に名前を repo の根から渡していること
- F7 の根拠の file(`evidence/rootdir-and-collisions.log`)は worktree にありません。F7 が何を測ったかは確認できませんでした(推測: `--collect-only` の node id)。
- 別の候補(未実測): package が自分の pyproject に `[tool.pytest.ini_options]` を持つと、rootdir と conftest の読み込み範囲がその package に移ると考えられます。そうなると repo の根の conftest(R6 の期限の調整・watchdog・広範囲テストの受付)と `timeout = 60` が、その package の走行に効かなくなるはずです。つまり repo の根の pytest 設定が、package の母集団の設定を暗黙に兼ねています。
- `[test-admission] full_sessions = 1` は、すでに実際の pytest の走行数(1 + package 数)と合っていません。ただしこの値を読む本番のコードは dotfiles に見つからず(テストだけ)、影響は未確認です。
- これで反例を探し尽くしたわけではなく、合格・安全とは判断していません。

写しは `/tmp/ki08ec-cx` にあります(共有の source は変更していません)。
