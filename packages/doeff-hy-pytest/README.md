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
`[tool.pytest.ini_options]` に置きます。上限の 2 つがどちらも無ければ何もしません。

| 設定 | 意味 |
|---|---|
| `doeff_test_call_budget_seconds` | `.hy` の検の file から集めた検 1 本の実行(call の段階・fixture の setup と teardown を含まない)の上限の CPU 秒 |
| `doeff_test_collect_budget_seconds` | `.hy` の検の file 1 本の収集(import を含む)の上限の CPU 秒 |
| `doeff_test_budget_mode` | `report`(既定 — 超えても赤にせず、警告と終わりの一覧だけ)か `fail`(超えた検を赤にする) |
| `doeff_test_budget_registry` | 上限を超えてよい既存の検の登録簿の dir(1 鍵 1 file・`<鍵の sha256 の先頭 12 字>.txt`・1 行目が鍵・2 行目から理由) |

- 判定は CPU 時間(`time.process_time`)で行い、壁時計を併記します。壁時計は機体の混み具合で伸び縮みするためです。
- バイトコードのキャッシュが無い import の変換(`SourceFileLoader.source_to_code` — Hy の import もここを通る)に使った
  CPU 時間は引いて判定します。着地の門と日次の走行は `PYTHONDONTWRITEBYTECODE=1` で撃つので、キャッシュ無しの回を
  丸ごと除く形では一度も判定されないためです。
- 鍵は、実行なら pytest の nodeid、収集なら rootdir からの file の path です。上限の内に戻った登録は終わりの一覧に
  「消せる」と出ます。
