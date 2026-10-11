Opus 5.5(claude-opus-5-5)

# 責務分離の失敗例: gensym の名の振り直しの規則を直すと、型検査の展開の保存が古い文を返し続ける

失敗例が 1 つ見つかりました。下の表と数字は、`/tmp/ki1475-blind-A/` の下の写しで実際に測ったものです。共有の source と作業木は変えていません(写しは測った後に元の中身へ戻しました)。

## 1. 変更要求と、それが要る理由

**変更要求**: 文字列の定数の中に埋まった gensym の名(例 `'bound _hy_gensym_v_12'`)も振り直すように、振り直しの規則を広げる。規則は `doeff_hy_bytecode_guard/records.py` の `gensym_renames`・`renamed` にあります。

**理由**:
- 規則には、直しを前もって促す検査が既にあります。`tests/test_gensym_canonical.py:480-485` は、doeff-hy の macro が文字列の中に gensym を埋めるようになると赤になります。その検査が「振り直せない形」の例に使う文字列は `'bad _hy_gensym_x_5 here'` です。
- 型検査の展開にも同じ穴があることを実測しました(下の E1)。同じ process で先に何を展開したかによって、保存される文が変わります。#3869 が名の振り直しで消そうとした依存と同じ種類です。
- 規則の置き場所は設計で 1 つに決まっています。static_check.py の docstring(371-391 行)に「振り直しの規則は、テストの実行の側の code の正準化と同じ 1 つ(`doeff_hy_bytecode_guard.gensym_renaming`)」とあります。なので直す先は static_check.py の写しではなく records.py になるのが自然です。

## 2. 関係する主張・前提と、予想に反して変わる責務

**関係する主張**:
- S3: 後処理を変えると記録が古くなり、展開し直す。変更の範囲は post-processing だけで、walker・record・store・expanders は変えない。
- R3: 引いた答えは、展開し直した答えと同じでなければならない。
- walker の不変条件「中身が変われば digest が変わる」。
- store の「記録が今に合う entry だけを返す」。
- POST_PROCESSING の註(static_check.py 545-548 行)は、後処理の中に「gensym の名の振り直し」を数えています。

**前提は守っています**:
- P1: この変更は中身の変更そのもので、S3 が扱う場面です。Hy と Python の版は変えていません。
- P2: 展開の文に入るのは gensym の名だけで、path は入りません。
- P3: 置き場は全部の回で同じ `lib` の dir です。
- 入口と子の数は関係しません(limit 1・親で展開)。
- S3 の「後処理」を static_check.py の中の関数だけと読むなら、この変更は S3 の適用範囲の外です。その場合でも、§3・§4 の表のどの主張も、この変更で保存を古くする役を持っていません。どちらの読みでも穴は残ります。

**予想に反して変わる所**: R3 を守るには、records.py(record の責務。規則の持ち主)の変更だけでは足りません。次のどちらかも要ります。
- store: `static_cache.CACHE_VERSION` を上げる。しかも `_read_entry` の literal の `"version": 3`(125 行)も合わせて変える必要があります。
- walker: `_NAMED_ONLY`(macro_use.py 636 行)を変える。ただし単純に外すと壊れます(§4)。

record 自身の決まり(RECORD_TAG を上げる)は、型検査の保存には効きません。

## 3. どの知識がどこへ漏れるか

1. **walker の辿りの規則が post-processing へ漏れる**:
   - `_NAMED_ONLY` は `doeff_hy_bytecode_guard` を名だけ入れる package として扱います。理由として書かれているのは「記録の作り方そのもの(記録の形は RECORD_TAG で覆う)」です。つまり「この package の code は記録の形にしか効かない」と判じています。
   - ところが `with_canonical_gensyms` は `gensym_renaming`(loader_hooks.py 134-141 行)を呼びます。その中身は records の `gensym_renames`・`renamed` で、出力は展開の文に入ります。
   - §3 では「POST_PROCESSING の範囲」は post-processing が持つ知識とされています。しかし実際の範囲は walker の規則が決めています。
2. **store の版の意味が record へ漏れる**:
   - `record_to_json`(records.py 209 行)は RECORD_TAG を書きません。`record_fingerprint`(226 行)と `place` にも RECORD_TAG は入りません。
   - そのため、規則の持ち主が自分の決まりどおり RECORD_TAG を上げても、効くのは .pyc の側だけです。型検査の保存を古くするには、別の package の store の CACHE_VERSION と、`_read_entry` の literal を知っている必要があります。
3. **S3 の検が通る理由は偶然の覆いです**:
   - `test_a_changed_type_check_post_processing_expands_again` が変えるのは static_check.py だけです。
   - POST_PROCESSING の digest には static_check.py の file 全体の sha256 が入っています(実測。閉包の中に `vars` で読む関数があるためと見ています)。static_check.py の変更が捕まるのはこのためです。
   - 名だけ入れる package の code の変更は、何も覆いません。

**種類の区別**:
- 公開契約の拡張: この変更自体には要りません。`gensym_renaming` の形は変わりません。きちんと直すなら record の公開の形を広げる手(規則の版の行を足す・tag を JSON に入れる)はありえますが、それが要るのはこの漏洩があるからです。
- 配線の変更: ありません。
- 共同の不変条件: 「展開の結果を変える code は、記録の digest か保存の版のどちらかで覆う」。post-processing・walker・record・store の 4 つにまたがりますが、持ち主も検査もありません。
- 隠すはずの知識の漏洩: 上の 1 と 2 です。

## 4. 最小の入力・変更例と再現手順(実測)

**入力**: `proj/mac.hy`:
```hy
(defmacro bound-twice [x]
  (setv g (hy.gensym "v"))
  `(do (setv ~g ~x) (print ~(+ "bound " (str g))) (+ ~g ~g)))
```
`proj/user.hy` は `(require mac [bound-twice])` と `(setv value (bound-twice 1))` の 2 行です。`proj/other.hy` は同じ形で `(bound-twice 2)` を使います。

**仮の直し**: records.py に `GENSYM_EMBEDDED` を足し、`gensym_renames` と `renamed` が文の中に埋まった名も読んで替えるようにしました。差分は約 10 行で、`scripts/fix.diff` にあります。

**手順**:
1. 15db080f3 の `doeff_hy` と `doeff_hy_bytecode_guard` を `$B/lib` へ写す。
2. `env PYTHONPATH=$B/lib PYTHONPYCACHEPREFIX=$B/pyc DOEFF_HY_CODE_STORE=$B/code-store XDG_CACHE_HOME=$B/xdg uv run --frozen --no-sync --project <作業木> python $B/scripts/drive2.py $B/proj <保存先> [file…]` を実行する。
   - `drive2.py` は `project_closure(...,limit=1)` を呼び、`load` を包んで引けた数を数えます。その後 `project()` で展開し直した文と比べます。

| 段 | 写しの状態 | 保存先 | 引けた/外れた | 保存から出た print | 展開し直した print | 同じ |
|---|---|---|---|---|---|---|
| E1 | 元のまま(other→user の順) | cache2(空) | 0/2 | `'bound _hy_gensym_v_2'` | `_3` | false(既存の穴) |
| E2 | records.py を直した | cache2 | **1/0** | `'bound _hy_gensym_v_2'` | `_1` | **false** |
| E2b | 直した | cache3(空) | 0/2 | `_1` | `_1` | true(直しは効いている) |
| E3 | 直した上で RECORD_TAG を 5→6 | cache2 | **1/0** | `_2` | `_1` | **false** |
| E4 | さらに CACHE_VERSION を 3→4 | cache2 | 0/1 | `_1` | `_1` | true |
| E4b | E4 と同じ版でもう 1 回 | cache2 | **0/1** | — | — | 版 4 で書いた entry を `"version": 3` の照合が読まない |

**digest の測り**:
- POST_PROCESSING の digest は、直す前も後も `41dc7b05…` で同じでした。
- `records.gensym_renames` を `value_reference` で名指した digest も `7402d801…` で変わりません。なので `also` に足しても効きません。
- static_check.py に注釈を 1 行足すと `59b47052…` に変わり、records.py に注釈を足しても `41dc7b05…` のままでした。
- 直す前と後の entry の記録の指紋は、どちらも `36d3e9…` で同じでした。
- `_NAMED_ONLY` から `doeff_hy_bytecode_guard` を外すと、digest は `environment-read` になりました。code_store が環境変数を読むためです。照らしはいつも「古い」と判じるので、保存がまったく効かなくなり、R1 が崩れます。

## 5. 未確認の前提・不足する情報

- **実測したこと**: 上の E1〜E4b、digest の値、`environment-read` になること。どれも写しの上で、親だけで展開した場合です。
- **推測で、確かめていないこと**:
  - 日次の対象(.hy 約 2,450 本)の展開に、文字列に埋まった gensym が実際にあるか。全数を調べることは禁じられているので測っていません。
  - 型検査の展開(static_view の下)で、doeff-hy の macro が今そういう文字列を出しているか。既存の検査は通常の compile だけを見ています。
  - pyright の判定への影響。基点の照合は `comparable_message`(static_baseline.py 84-91 行)で Hy の番号をならすので、影響は小さいと推測します。ただし R3 の「文が同じ」は、それと関わりなく破れています。
  - 直す前の版では、日次は F1・F2 のせいで毎回外れていたので、古い entry が日次で使われることはありませんでした。直した後は回をまたいで引くので、この穴が日次に出やすくなったと推測します(日次そのものでは測っていません)。
  - `python -m` で撃つ入口と展開の子の経路では、この例を走らせていません。記録の digest は入口に依らないので同じ結果になると推測します。
  - .pyc の側が RECORD_TAG を上げると古いと判じられることは、code を読んで判じただけで、走らせてはいません。
  - 後処理が名だけ入れる package に頼っている所が `gensym_renaming` のほかにあるか(例: compile の間に macro の表を差し替える `RecordingTable`)は、全部は調べていません。
  - S3 の「後処理」に、bytecode_guard に置いた規則を含める意図だったのかは、材料に書かれていません。
  - 既存の pytest は走らせていません。この例を覆う検査はありません。

**関係する file**(どれも `/work/toolset/worktrees/doeff-wt-design-check-ki1475/packages/doeff-hy/` の下):
- `src/doeff_hy_bytecode_guard/macro_use.py` の 636 行
- `src/doeff_hy_bytecode_guard/records.py` の 49・209・226・337・393 行
- `src/doeff_hy_bytecode_guard/loader_hooks.py` の 134-141 行
- `src/doeff_hy/static_check.py` の 371-391・548 行
- `src/doeff_hy/static_cache.py` の 48・90-96・125 行

実験に使った物は `/tmp/ki1475-blind-A/` に残してあります(`scripts/drive2.py`・`scripts/digest.py`・`scripts/fix.diff`・`proj/`)。

WAIT: work — 呼び出し元の次の依頼
