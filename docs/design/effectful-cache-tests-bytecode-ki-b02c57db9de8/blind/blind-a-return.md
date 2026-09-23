Opus 5.5(自己申告)

# 反例: `@effectful` のキャッシュの置き場を標準の規則(`PYTHONPYCACHEPREFIX`)に合わせる変更

S2 の予想(effectful-cache と cache-tests だけが変わる)と、S1 の「前提の書き忘れは開発機でも赤になる」は、この変更の後では両立しません。suite-pin(`conftest.py`)を直さなければ S1 の保証が黙って消え、直せば S2 の予想範囲を超えます。どちらも実測で確かめました。

## 1. 変更の要求と、それが必要になる理由

要求は「`@effectful` のキャッシュの置き場を、標準の import と同じ規則(`sys.pycache_prefix` / `PYTHONPYCACHEPREFIX`)に従わせる」ことです。これは S2(置き場・名前の付け方の変更)に当たります。

理由は次のとおりです。
- **実測:** 今の `cache_path`(`doeff/_effectful_rewrite.py:348-352`)は `source.parent / "__pycache__"` に固定しています。`PYTHONPYCACHEPREFIX` を立てても、キャッシュは元の木の `__pycache__` に書かれます。キャッシュの 3 本の検査は、`PYTHONPYCACHEPREFIX` を立てた状態でも `package/__pycache__/…pyc` があることを確かめて緑でした。
- **推測:** ソースが読み取り専用の配置(コンテナや pod)や、木を汚したくない共有の checkout では、この環境変数を使うのが Python の標準の手段です。今のままでは、キャッシュが木を汚すか、書けずに(OSError を黙って捨てて)起動のたびに compile し直すことになります。

前提はすべて守っています。land の `PYTHONDONTWRITEBYTECODE=1` は変えず(要件 3)、走行は testpaths、rootdir は repo root です。変えるのは effectful-cache の中だけです。

## 2. 関係する主張と、予想に反して変わるもの

関係する主張は 3 つです。
- **S2:** 変わるのは effectful-cache と cache-tests だけ。
- **S1:** キャッシュの 3 本の答えは環境変数に依らない。前提の書き忘れは開発機でも赤になる。
- **要件 4:** 同じ種類の問題が再び入らない。

S2 の変更を入れると、答えは `PYTHONPYCACHEPREFIX` の有無で分かれます(実測)。直し方は次の 2 通りしかありません。

- **suite-pin を直す場合:** `conftest.py` の `_bytecode_writing_off`(291-301 行)に `sys.pycache_prefix` と `PYTHONPYCACHEPREFIX` の固定を足します。その固定を確かめる `tests/test_bytecode_writing_off.py` も広げることになります(こちらは推測)。
  - これで S1 は保たれますが、S2 の予想範囲を超えます。
  - `conftest.py` は 5 つの責務を持つ file です(負荷に応じた期限の伸縮・memory の上限・watchdog・広範囲走行の受付・bytecode の固定)。
- **cache-tests だけを直す場合:** 2 本に `monkeypatch.setattr(sys, "pycache_prefix", None)` を足します。
  - これで S2 の範囲には収まりますが、新しい前提を書き忘れても、開発機と日次(どちらも環境変数なし)では緑のままです。S1 の「書き忘れは開発機でも赤」は、新しい設定については成り立ちません。

## 3. どの知識がどこへ漏れるか(契約拡張との区別)

| 区分 | 該当するもの |
|---|---|
| 意図した公開契約の拡張 | `cache_path` と名前の規則の変更。cache-tests が期待する path を書き換えるのも、名前を検査する役目の範囲内。S2 の予告どおり |
| 配線の変更 | なし(import hook と finder は不変) |
| 共同の不変条件 | 「開発機と日次で、走行中の設定が同じ」。suite-pin と land-env(外部)が共同で持つ正当な不変条件 |
| 漏洩 | 「キャッシュを書くかどうかと置き場所を決めるとき、どの process 全体の設定を読むか」という effectful-cache の中の知識 |

漏洩した知識は、次の 2 か所に `dont_write_bytecode` の 1 つだけとして写されています。
- `conftest.py` の固定
- cache-tests の前提の 1 行(`tests/test_effectful.py` 328 行・349 行)

effectful-cache が読む設定を 1 つ増やすと、この写しは黙って古くなります。それを検出する検査はありません。`tests/test_bytecode_writing_off.py` が見るのは既知の flag と環境変数だけで、走行の終わりの assert も flag だけです。

suite-pin が直る理由は suite-pin 自身の方針ではなく、effectful-cache の内部の変更です。これが「漏洩」と判断した根拠です。suite-pin の契約拡張とも読めますが、その場合は S2 の予想範囲に suite-pin が載っているはずで、載っていません。

さらに、固定する値は「日次と同じ値」なので、固定の側は land と日次の機体の環境変数の値も写し持つことになります。

## 4. 最小の変更例・再現手順・観測結果

**変更(複写先のみ):** `tree-s2/doeff/_effectful_rewrite.py` を次のように変えました。

```python
return Path(importlib.util.cache_from_source(source_path)).parent / name
```

あわせて `_write_cache` の `mkdir(exist_ok=True)` を `mkdir(parents=True, exist_ok=True)` にしました(prefix の下の dir を作るため)。

**実行した命令:** `/tmp/ki-b02c57db9de8-blind-A/run2.sh <tree> <PYTHONDONTWRITEBYTECODE の有無> <PYTHONPYCACHEPREFIX の有無> tests/test_effectful.py tests/test_bytecode_writing_off.py -k "cache or bytecode"`

中身は `env -u … [PYTHONDONTWRITEBYTECODE=1] [PYTHONPYCACHEPREFIX=/tmp/ki-b02c57db9de8-blind-A/pycache-prefix] PYTHONPATH=<tree>:<tree>/packages/doeff-core-effects /home/kento/repos/doeff/.venv/bin/python -m pytest … -q -p no:randomly -W ignore::pytest.PytestWarning` です。指定の命令に `-W` を足した点だけが違います(focused 走行で出る ADR 配線の警告を消すため)。

**結果(実測):**

| 木 | prefix なし(DWB なし / あり) | prefix あり(DWB なし / あり) |
|---|---|---|
| 提案そのまま(`tree`) | 5 passed, exit 0 / 5 passed, exit 0 | 5 passed, exit 0 / 5 passed, exit 0 |
| S2 のみ(`tree-s2`) | 5 passed, exit 0 / 5 passed, exit 0 | **2 failed, 3 passed, exit 1** / **同じ** |
| S2 + 検査 2 本に前提を追加(`tree-s2-tests`) | 5 passed, exit 0 ×2 | 5 passed, exit 0 ×2 |
| S2 + `conftest.py` の固定を拡張(`tree-s2-pin`) | 5 passed, exit 0 ×2 | 5 passed, exit 0 ×2 |

- `tree-s2` で赤になったのは `test_cache_is_named_by_the_rewrite_version_and_reused` と `test_a_new_rewrite_version_rebuilds_the_cache` です。キャッシュは `…/pycache-prefix/tmp/pytest-of-kento/…/mod.cpython-314-doeff-effectful-*.pyc` に書かれ、`package/__pycache__` にはありませんでした。
- **対照(`tree-nod1`):** 提案の木から `dont_write_bytecode` の前提行を 1 本消すと、DWB なし・ありの両方で `1 failed, exit 1` でした。既知の flag については、固定が書き忘れを開発機で赤にします。新しい設定では、上の表の `tree-s2` の prefix なしの列のとおり緑のままです。

**副次の観測(実測):** 途中の版(S2 から `parents=True` を抜き、書かない条件も落とした `tree-s2-drop`)では、`test_no_cache_is_written_while_bytecode_writing_is_off` の結果が分かれました。
- prefix なし: `1 failed, exit 1`
- prefix あり: `1 passed, exit 0`

原因は、mkdir の OSError が黙って捨てられ、検査が「判断して書かなかった」と「書けなかった」を区別できないことです。`parents=True` を入れると prefix の有無に関わらず `1 failed, exit 1` になりました。S4 の「条件を落とせば 1 本が赤」は、書き込み先に書けることが暗黙の前提になっています。

## 5. 未確認の前提・不足する情報

- **`PYTHONPYCACHEPREFIX` を立てている実際の走行場所があるか:** この機体は立っていません(`env | grep '^PYTHON'` の出力は空)。zeus と日次の pod は未確認です。どこにも無ければ、この問題は赤として表に出ず、S1 の保証が失われるだけの潜在的な欠陥にとどまります。
- **S2 がこの形で実装されるか:** 推測です。process 全体の設定を読まない名前の変更(文字列の変更だけ)なら、この反例は起きません。
- **S4 で別の設定(たとえばキャッシュを止める環境変数)を読む条件を足す場合:** 同じ構造になると推測しています(未実行・推測)。
- **範囲外(前提の変更):** land を `PYTHONPYCACHEPREFIX` 方式へ変える場合は、要件 3 に反するので契約の適用範囲外です。この場合は固定の `True` / `"1"` が日次と食い違い、prefix を無視する今の effectful-cache は被験の木へ書くことになります(未実行・推測)。
- **確かめたが反例にならなかったもの:** S3 は `-n 2` で、提案の木の 5 本が DWB なし・ありの両方で `5 passed, exit 0` でした。
- **実行していないもの:** testpaths 全部の走行、固定の拡張が他の検査(`tests/test_hyk_hyp_extensions.py` の `__pycache__` の掃除など)に与える影響、実験の変更に対する ruff と pyright。
- **複写の外への書き込み:** pytest の一時 dir は `/tmp/pytest-of-kento/` にできました。共有の venv に、実験の開始より新しい pyc が 1 つあります(`.venv/lib/python3.14t/site-packages/rich/_unicode_data/__pycache__/unicode17-0-0.cpython-314.pyc`)。環境変数なしの走行で作られたのか、他の作業のものか、区別できていません。

実験の複写とログはすべて `/tmp/ki-b02c57db9de8-blind-A/` の下にあります(`tree`・`tree-s2`・`tree-s2-tests`・`tree-s2-pin`・`tree-s2-drop`・`tree-nod1`、ログは `*.log`)。
