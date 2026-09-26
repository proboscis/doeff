# doeff-hy-pytest

doeff-hy の pytest plugin(entry point `pytest11` の名 `doeff_hy`)だけを持つ配布物です。doeff-hy が依存として引くので、
利用者が直接書く必要はありません(git の rev で doeff-hy を引く repo は `[tool.uv.sources]` にこの package の行も足します)。

- 役目: pytest の起動の時点で `doeff_hy.ast_unparse.install()` を当てる(Hy の `ast.unparse` の差し替えが Python 3.14 の
  注記の文字列化で止まらなくなる不具合の当て物 — 仕組みは `packages/doeff-hy/src/doeff_hy/ast_unparse.py`)。
- 別の配布物にする理由: pytest は pytest11 の entry point を持つ配布物の全 module に assert の書き換えの印を付けます。
  doeff-hy がこの entry point を持つと、wheel で入れた repo で `doeff_hy/macros.hy` などを Python として書き換えようとして
  import が SyntaxError で落ちました(2026-09-26 に dotfiles の agentcli の pin を上げた時の実測)。この配布物は `.hy` を持たないので、
  印が付いても害がありません。
