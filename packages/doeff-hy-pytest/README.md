# doeff-hy-pytest

doeff-hy の pytest plugin(entry point `pytest11` の名 `doeff_hy`)だけを持つ配布物です。doeff-hy が依存として引くので、
利用者が直接書く必要はありません(git の rev で doeff-hy を引く repo は `[tool.uv.sources]` にこの package の行も足します)。

- 役目: pytest の起動の時点で `doeff_hy.ast_unparse.install()` を当てる(Hy の `ast.unparse` の差し替えが Python 3.14 の
  注記の文字列化で止まらなくなる不具合の当て物 — 仕組みは `packages/doeff-hy/src/doeff_hy/ast_unparse.py`)。
- 別の配布物にする理由: pytest は pytest11 の entry point を持つ配布物の全 module に assert の書き換えの印を付けます。
  doeff-hy がこの entry point を持つと、wheel で入れた repo で `doeff_hy/macros.hy` などを Python として書き換えようとして
  import が SyntaxError で落ちました(2026-09-26 に dotfiles の agentcli の pin を上げた時の実測)。この配布物は `.hy` を持たないので、
  印が付いても害がありません。

## Hy の検の時間の上限(`doeff_hy_pytest/budget.py`)

模擬の handler で回す検が遅くなったことに気づくための上限です(proboscis/agora-redesign#907)。利用側の
`[tool.pytest.ini_options]` に置きます。上限の 3 つ(実行・印ごとの実行・収集)がどれも無ければ何もしません。

| 設定 | 意味 |
|---|---|
| `doeff_test_call_budget_seconds` | `.hy` の検の file から集めた検 1 本の実行(call の段階・fixture の setup と teardown を含まない)の上限の CPU 秒。印ごとの上限に当たらない検に使う |
| `doeff_test_call_budget_by_marker` | 印ごとの実行の上限の CPU 秒(list・1 行 = `印=秒`・例 `["real_world=10"]`)。検の印(module の頭の `pytestmark` を含む)に当たる行があればその秒、複数当たれば最も長い秒。当たらなければ `doeff_test_call_budget_seconds`(それも無ければ測らない)。`=` の無い行・数でない秒・0 以下・同じ印の 2 行は起動の時点で止まる |
| `doeff_test_collect_budget_seconds` | `.hy` の検の file 1 本の収集(import を含む)の上限の CPU 秒。file 単位で印を持たないので 1 つの値。キャッシュ無しの変換と、別の module の初回の import(共有の依存の一度きりの重さ — 並び順でどの file に乗るかが変わる)の CPU 秒は引いて判定する(実行の上限も同じ) |
| `doeff_test_budget_mode` | `report`(既定 — 超えても赤にせず、警告と終わりの一覧だけ)か `fail`(超えた検を赤にする・登録簿に載った検が上限の半分以下で終われば古い登録として赤) |
| `doeff_test_budget_registry` | 上限を超えてよい既存の検の登録簿の dir(list・1 行 = 1 dir・`"dir"` の 1 つの値の書き方もそのまま読める)。どの dir も 1 鍵 1 file・`<鍵の sha256 の先頭 12 字>.txt`・1 行目が鍵・2 行目から理由。載った鍵は上限を超えても赤にしない。`fail` の形では、載った検が上限の半分以下で終わると古い登録として赤にし、消す file を名指す(上限の半分から上限までは「消せる」と報告だけ・走らなかった検は判じない — agora-redesign #1726)。超えた時の文が足し先に挙げるのは 1 行目の dir |
| `doeff_test_call_budget_steps` | 検 1 本の実行の上限の doeff-vm の歩数(正の整数 — 機体の負荷で揺れない決まった数・agora-redesign #2670)。在れば実行は歩数で判じ、CPU 秒は測って報告に出すだけ。登録簿は `doeff_test_budget_registry` と同じ dir(載った行は歩数で判じる・古い登録の決まりも同じ)。doeff-vm に数の口(`doeff_vm.doeff_vm.vm_work_counts`)が無い build は名指しの警告を出して CPU 秒で判じる。印ごとの秒の上限に当たる検(本物の I/O)は秒のまま。歩数が 0 の検(VM を回さず木を読むだけの検)は上限の内。歩数は不変条件の検査の有無で変わらないので、検査つきの build でも `fail` の形なら歩数の超過は赤 |

数の口がある時は、検 1 本ごとに歩数と handler の呼び出しの回数も測る。終わりの一覧に、測れた検の数と歩数の合計を 1 行出し、`-v` の時は検ごとに 1 行(歩数・handler の回数・CPU 秒)出す — 歩数の上限を決める材料。

例(手元の検 1 秒・縁の検 10 秒・登録簿 2 つ):

```toml
[tool.pytest.ini_options]
doeff_test_call_budget_seconds = 1.0
doeff_test_call_budget_by_marker = ["real_world=10"]
doeff_test_collect_budget_seconds = 1.0
doeff_test_budget_mode = "fail"
doeff_test_budget_registry = ["scripts/test_budget/OVER-BUDGET", "scripts/doeff_lint/TEST-KIND-BREACHES"]
```

- 判定は CPU 時間(`time.process_time`)で行い、壁時計を併記します。壁時計は機体の混み具合で伸び縮みするためです。
- バイトコードのキャッシュが無い import の変換(`SourceFileLoader.source_to_code` — Hy の import もここを通る)に使った
  CPU 時間は引いて判定します。着地の門と日次の走行は `PYTHONDONTWRITEBYTECODE=1` で撃つので、キャッシュ無しの回を
  丸ごと除く形では一度も判定されないためです。
- 鍵は、実行なら pytest の nodeid、収集なら rootdir からの file の path です。上限の内に戻った登録は終わりの一覧に
  「消せる」と出ます。
