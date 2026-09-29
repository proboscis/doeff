# v1 の後の追記(2026-09-29 15:2x)

- 索引を doeff へ移す子 #1291 が main に入った(c6783e8e2)。doeff の code がフォークだけの名前(`hy.importer` の
  `read_valid_records`・`add_compile_record`・3 つの記録の名)を import する所は 0 になった。
- そのため ADR-DOE-HY-008 の import の例外の台帳(FORK-ONLY-IMPORT-EXCEPTIONS)は空で着地する。rebase の後、台帳に 5 つを
  残したまま撃つと R5 の検査(原因の消えた例外)が 5 つとも赤になることを確かめてから空にした。
- v1 の本文 6 節の「lazy_collection.py の read_valid_records は #1291 が置き換える」は、この時点で済んでいる。
- #1291 の担当(w3K:p1R)は、この仕組みの公開の口 `doeff_hy_bytecode_guard.macro_dependencies` が main に入ったら、
  doeff-adr の item_cache.py の中の同じ辿り方をこの口へ差し替える(辿り方を 2 か所に残さない)。
