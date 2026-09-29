# agora-controllers deploy/bytecode.py の読み(commit 53ac0be6b・2026-09-29)

- `compile_source`(943〜958 行): `SourceFileLoader(<module 名>, <path>).source_to_code(data, path)` を module の外で呼ぶ
  → この仕組みの compile の口の包みは import の途中の module を見つけられないので記録を足さない。
- 同じ所で頭は `hash_pyc_header(MAGIC, source_hash(data))`(PEP 552 の hash 方式・FLAG_CHECK_SOURCE なし = unchecked)。
  → 実行時の読みの口の包みは unchecked-hash を信じる(Python が source と突き合わせないのと同じ)。
- `canonical_body`(494 行〜): marshal の参照の付け方だけを綴り直し、値は変えない(読み戻して突き合わせる)。
  → 記録(code の定数の中の str の組)が在っても値のまま残る。
- `rehead-tree` / `check`: .pyc の頭と body だけを扱い、隣の file を見ない → .pyc 以外の file を作らないこの仕組みと矛盾しない。
