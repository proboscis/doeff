## 失敗例: 記録の中の検の名の file を「検」と読む 2 つ目の読み手(make test-changed の選び)

S1 の主張「変えるのは記録の新しい dir だけ」は、make test-changed の経路では成り立ちません。記録の中の写しを、記録と関係の無い後の変更でも、make test-changed が赤にします。

### 1. 変更要求と、それが要る理由

- 運用の変更は S1 そのものです。設計検証の記録が、直した版の検の写しを evidence/ の下に test_*.py の名で置く形を続けます。すでに記録 lt-D4D7YPF2BHEVKEHH2JJ7GT0W8W がこの形で main に在ります(`evidence/scripts/test_handle_scope.revision.py`)。
- その後に来る普通の library の直しが引き金です。例は `packages/doeff-traverse/doeff_traverse/handlers.py`(sequential・fail_handler)の不具合直しです。この module には生きた逆依存の検が 17 本在り、日常的に直される物です。記録とは関係がありません。

### 2. 関係する主張・前提と、予想に反して変わる物

- **元の主張と前提**
  - S1: M-pop・M-roots・M-contract・M-seal は変えない。前提は「記録の中の検の名の file は写しで、日次で走らせる物ではない」。
  - M-records: 着地の後は書き換えない。
  - 本例はこの前提を守ります。写しを走らせたいという要求は含みません。契約の適用範囲の中の例です。
- **予想に反して関わる物**: `scripts/run_changed_tests.py`(make test-changed の選び)と doeff-linter の `--affected-tests` です。どちらも責務の表に載っていません。どちらも「検の file」を名の規則だけで決め、EXCLUDED を読みません。
  - 変えた検の選び: `select_targets`(run_changed_tests.py:423)は、`is_test_file` に当たる変えた file を全部選びます。
  - 逆依存の選び: linter の母集団は `git ls-files` の全部です(affected_tests.rs:6-7・237)。`extend_with_reverse`(run_changed_tests.py:560)が絞るのは、名の規則と file が在るかどうかだけです。
  - 集めの誤りは赤として数えます(`_classify_run`、run_changed_tests.py:816「集める時に落ちた」)。
- **起きる事**: 記録を着地させる branch でも、後の handlers.py の直しでも、make test-changed が赤(rc 1)になります。緑に戻す道は 3 つしかありません。
  - (a) 着地済みの記録を書き換える。M-records の契約を破ります。
  - (b) run_changed_tests.py や linter に記録の除外を教える。S1 が変えないとした範囲の外の module を変えます。
  - (c) EXCLUDED を共有の宣言へ移す。M-pop の契約が変わります。

### 3. どの知識がどこへ漏れるか

- **共同の不変条件(正当で、漏れではない)**: 「何が検の file か」の名の規則です。M-pop の `_test_file_patterns` と run_changed_tests の `collector_patterns`(:344)は、同じ源(root の ini と doeff-adr の `DEFAULT_FILE_PATTERNS`)を読んでいます。
- **漏れ**: 「名の規則に当たるが、検ではない file」の知識です。これは tests/test_daily_test_population.py の module の中の定数 `EXCLUDED`(:106)にしか在りません。
  - 記録の側の判断「証拠を test_ の名のまま置く」が、名の規則を読むすべての読み手に波及します。具体的には、変えた検の選び・逆依存の選び・linter の母集団です。
  - EXCLUDED は pytest の検の module の中に在ります。標準ライブラリだけの uv script である run_changed_tests.py からは import できません(import すると doeff と pytest を読み込みます)。直すには、表を写す(2 つ目の定義になり、ずれる)か、表を移すしかありません。
- **契約の拡張との区別**: 次の 2 つを意図して入れるなら、正当な「公開契約の拡張+配線の変更」です。
  - linter に `--exclude` を足す。
  - run_changed_tests が除外の宣言を読む。

  ただし S1 はこれらが要らないと主張しているので、問題は主張の側の欠けです。
- **付け足し(実測)**: 今在る写しは、名に `.` を含みます(`test_handle_scope.revision`)。そのため pytest は版に関係なく import できず、集めの誤りになります。名が正しい写しなら実際に走りますが、その場合の結果は推測です。API の名の変更で ImportError になる、または不具合を再現するための写しは直した後に赤になる、と見ています。

### 4. 最小の再現と観測すべき結果

試した file は /tmp/blind-9r40-A/ の下にだけ置きました。作業木は終わった後も `git status` が空のままです。

1. **逆依存の選び(実測)**: `python3 -I -B /tmp/blind-9r40-A/scripts/select_like_test_changed.py packages/doeff-traverse/doeff_traverse/handlers.py`
   - run_changed_tests の純粋な関数と、実物の linter を呼びます。
   - 結果: session `.` の束に、`逆依存 docs/design-checks/lt-D4D7…/evidence/scripts/test_handle_scope.revision.py(距離 1)` が入りました。同じ束に `tests/test_handle_scope.py` も入ります(log は /tmp/blind-9r40-A/select-handlers.log)。
2. **S1 の着地の branch(実測)**: 同じ script に、記録の design.md と写しを変えた file として渡しました。
   - 結果: 契約の組 2 本の後ろに `変えた検 …/test_handle_scope.revision.py` が選ばれました(select-record.log)。
3. **集め(実測)**: 作業木の根で、次の命令を集めだけで走らせました。
   `uv run --project ~/repos/doeff --no-sync python -B -m pytest -p no:cacheprovider --collect-only -q --continue-on-collection-errors tests/test_handle_scope.py docs/design-checks/lt-D4D7YPF2BHEVKEHH2JJ7GT0W8W/evidence/scripts/test_handle_scope.revision.py`
   - 結果: `ModuleNotFoundError: No module named 'test_handle_scope.revision'; 'test_handle_scope' is not a package`、`33 tests collected, 1 error`。
   - /tmp の最小の写し(`test_scope.revision.py`)でも、単独と同時の両方で同じ誤りが出ました(pytest 9.1.1。uv.lock の版と同じ)。
4. **全体の流れ(推測。1〜3 の組み合わせ)**: origin/main から切った木で handlers.py に注釈 1 行を足し、`make test-changed` を走らせます。予想される観測は 3 つです。
   - 写しが `赤 … 集める時に落ちた` と出る。
   - 要約の行が `land-focus-counts: … red=1 … rc=1` になる。
   - 赤の原因は記録の写しで、変更した人が直すべき物ではない。

### 5. 未確認の前提・不足する情報

- **make test-changed を最後まで通しては走らせていません(推測)**: 作業木に .venv が無く、読むだけの約束なので作りませんでした。「赤になる」は、`_classify_run` の読みと 3 の実測から推論したものです。
- **3 の実測は主 checkout の venv で行いました(a3c8aa861。3198e5548 の子孫ではありません)**: 誤りは file 名から来る import の誤りなので、版には依らないと推測しています。/tmp の最小の写しで裏づけました。
- **linter の版**: uv の cache に在った doeff-linter 0.2.0(a40add2d9+dirty)を使いました。作業木の版の linter で同じ答えになるかは未確認です。affected_tests.rs の読みからは同じと推測しています。
- **赤が登記を止めるか**: 未確認です。land-queue の gate は検を走らせません。agora の側の入口が test-changed を必須にしているかは、この repo からは読めませんでした。
- **docs/design/ の model/test_*.py 4 本**: 同じ形で逆依存に選ばれるかは測っていません。
- **副次の例(読んだだけで、測っていません)**: S2 で新しい種類の記録が封の file を持つ場合、M-pop の EXCLUDED に加えて M-seal の `RECORD_KINDS`(tests/test_sealed_records_are_named.py:21)も変える必要があります。記録の種類の一覧が 2 か所に在るためです。封を使わない種類なら、変えるのは M-pop だけです。
- 探索は上に挙げた file に絞りました。全数の検査はしていません。この結果から「安全」とは結論しません。

WAIT: work — 利用者の次の入力
