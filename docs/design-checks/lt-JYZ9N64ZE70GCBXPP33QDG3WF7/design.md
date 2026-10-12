# 設計検証 — bytecode の包みが依る Hy の内部を、uv.lock の版の Hy で測る(依頼 lt-JYZ9N64ZE70GCBXPP33QDG3WF7・card ki-eafa369600cd)

## この文書の目的

読み終えた人が、次の 3 つを判断できる状態になる事:

1. doeff の bytecode の包み(doeff_hy_bytecode_guard)が import でない形で依る Hy の内部が何で、Hy を上げる便のどこで測られるか。
2. 盲検 A・B が見つけた穴と、その直しが実測で効いた事。
3. 残る限界と、この便の範囲の外の所見。

## 全体の図(直した後)

```
Hy を上げる便: uv.lock の hy の行を変える
   │
   ▼
make test-changed(scripts/run_changed_tests.py の select_targets)
   │  root の pyproject.toml の [[tool.doeff.contract-tests]] prefix = "uv.lock" の行
   ▼
packages/doeff-hy/tests/test_bytecode_guard.py(guard-check)
   │
   ├─ autouse の fixture _venv_hy_is_the_locked_version
   │     venv の hy.__version__ == uv.lock の版?  違えば全部の検が「make sync してから測る」で赤
   │
   ├─ 使い捨ての環境の検(_probe_on_pypi_hy — uv run --no-project --isolated --with hy==<uv.lock の版>)
   │     macro の変更・補助の変更・reader macro の変更・使う側の呼ぶ名の macro が足された時(all / exported)・
   │     gensym の数えに code が依らない
   │
   └─ 同じ process の検(venv の Hy — fixture が uv.lock の版である事を確かめた後)
         置き場・口の包み・型検査の展開・別の名で読み直した file
   │
   ▼ 包みを動かして測る相手
doeff_hy_bytecode_guard
   ├─ records.MACRO_TABLE・READER_TABLE(Hy の module の macro の表の名)
   ├─ macro_use.RecordingTable(Hy の展開が「名 in 表」と「表[名]」で引く口で、使った名と表に無かった名を覚える)
   ├─ macro_use._exported(Hy の export が書く公開の macro の名の表 _hy_export_macros)
   └─ records.GENSYM_NAME・RENAMED_BINDING・canonical_gensyms(gensym と let が改めた束縛の名の形)
```

## 完了の範囲

実装完了まで。branch `wt/hy-bytecode-guard-internals`(基準版 = 分かれ目の origin/main d406b28914b20875e8b452c0b384adf8a0390ea7):

| commit | 中身 |
| --- | --- |
| 0de52de26 | 盲検の前の実装 — reader macro の変更の検・gensym の数えの検・検の補助 `_probe_on_pypi_hy`・records・ADR・表の注釈 |
| a63514a27 | 盲検の前の主張(evidence/design-pre-blind.md)と直す前の失敗ケースの log |
| 4eec37a06 | 盲検 A・B を受けた直し — venv の Hy の版の fixture・表に無かった名と export の検・表の名の写しを records.MACRO_TABLE へ |
| 1c9f4699a | 実験 S2 で見つけた漏れの直し — test_gensym_canonical.py の名の形の写しを records へ |

main への着地は、この記録を置いた後に `ai land request` で行う。受入の条の「doeff main で確かめる」は着地の後に main の版で測る。

## 要件(固定)

card ki-eafa369600cd の受入の条(逐語): 「bytecode の包みの Hy の内部への依りを R7 の母集団に足すか、uv.lock の変更で包みの検が選ばれる
形にして、Hy の表の引き方を .get に替えた失敗ケースが赤になる事を doeff main で確かめる」。

選んだ形: uv.lock の変更で包みの検が選ばれる形(検の file は依頼の後に card ki-33d2441b7e28 の cd305fbd0 が uv.lock の組へ足していた)。
R7 の母集団へ名や形の写しとして足さない理由: 依りは名が在るかではなく、本物の展開の下で包みの表が使った macro を覚えるかという
振る舞いで、名を照合する probe は表を `.get` で引く版を見分けられない(ADR-DOE-HY-008 の解釈に書いた)。

## 直す前の実測(基準版の検の file)

Hy 1.3.1 の 1 か所だけを替えた wheel(evidence/scripts/build_hy_variants.py)を uv の find-links で使い捨ての環境へ入れた:

| 変種 | 替えた所 | 基準版の test_bytecode_guard.py(20 本) | log |
| --- | --- | --- | --- |
| get | macroexpand が module の表を `.get(fn)` で引く | 9 本赤(使う側が 11 のまま) | before-get.log |
| table | `_hy_macros` の名を替える | 9 本赤 | before-table.log |
| reader | `_hy_reader_macros` の名を替える | 20 本緑(穴) | before-reader.log |
| gensym | gensym の名を `_hy_gs<数え>_<base>` へ | 20 本緑(穴) | before-gensym.log |
| anon | get_anon_var の名を `_hy_<種><数><元の名>` へ | 20 本緑(穴) | before-anon.log |

## 責務と公開の契約

| id | 責務 | 持つ知識 | 公開の形 |
| --- | --- | --- | --- |
| guard-recording | 展開が使った macro を拾う(macro_use.py) | Hy が module の表を引く口・表の名(records.MACRO_TABLE・READER_TABLE)・export の名の表 | `MacroRecording` → `MacroRecord` |
| guard-canonicalization | gensym の名を module の中の順へ振り直す(records.canonical_gensyms) | GENSYM_NAME・RENAMED_BINDING | `canonical_gensyms`・`gensym_renames`・`unread_gensym_texts` |
| guard-hooks | SourceFileLoader の口を包む(loader_hooks.py) | 口の包み方・compile の口 | `install`・`source_to_code_as_import` |
| guard-check | 包みを uv.lock の版の Hy で動かして測る(test_bytecode_guard.py) | 見本・使い捨ての環境・venv の版の一致の確かめ | pytest の検 |
| guard-detail-tests | 正準化の細部を venv の Hy で測る(test_gensym_canonical.py・test_macro_use_record.py) | 見本 — 名の形は records から読む | pytest の検 |
| version-source | uv.lock の hy の版を読む(scripts/semgrep_locked.py) | uv.lock の読み方 | `locked_version` |
| selection | uv.lock を変えた便が払う検(root の pyproject.toml の表) | 表の行 | 表 |
| adr-record | ADR-DOE-HY-008 の R7 の文・事実・解釈 | どの検が包みの依りを測るか | 設計記録 |
| hy-version-pin | uv.lock の hy の行・doeff-hy の Hy の上限 | — | — |

## 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
| --- | --- | --- | --- | --- |
| 表の名と「名 in 表」「表[名]」の引き方が uv.lock の版の Hy で効く | 振る舞いの契約テスト(macro・補助・reader macro を変えたら使う側が作り直される。包みなしは古いまま) | test_a_macro_change_recompiles_the_user_on_pypi_hy ほか・test_a_reader_macro_change_recompiles_the_user_on_pypi_hy[none-10/before-hy-15] | uv.lock の変更 → 契約の組 → 使い捨ての環境 | 見本は小さい(下の限界) |
| 表に無かった名(「名 in 表」が偽)と export の名の表 | 振る舞いの契約テスト(全部を require する使う側が呼ぶ名の macro が提供元に足されたら作り直される) | test_a_new_macro_named_like_a_function_the_user_calls_recompiles_the_user_on_pypi_hy[all/exported] | 同上 | export の形は提供元が Hy の export を使う時だけ効く(doeff の source は今使っていない) |
| gensym と let の名の形 | 振る舞いの契約テスト(同じ使う側を 2 度 compile し、間で gensym の数えを 100 進めても code が `==`) | test_the_code_does_not_depend_on_how_many_gensyms_the_process_made_on_pypi_hy | 同上 | marshal の bytes は参照の数で揺れるので比べない |
| 同じ process の検も uv.lock の版を測る | autouse の fixture(venv の hy.__version__ と uv.lock の版の一致) | test_bytecode_guard.py の _venv_hy_is_the_locked_version | file の全部の検の前 | 同じ版の名で中身の違う Hy は見分けない(PyPI の版は中身が変わらず、locked_version は index の外の版を止める) |
| 名の形と表の名の知識は records に 1 か所 | 検は records の定数・関数から読む(写しを置かない) | test_bytecode_guard.py の records.MACRO_TABLE・test_gensym_canonical.py の records.GENSYM_NAME・RENAMED_BINDING・gensym_renames・unread_gensym_texts・test_macro_use_record.py の records.MACRO_TABLE | 実験 S1・S2 で、直すのが records.py だけで足りる事を測った | 静的な禁止(semgrep など)は置いていない — 写しを足した便は次の版上げで records の外も直す事になり、実験で見える |
| uv.lock の版で測る検が uv.lock の組に在る | 表の検(読み手を呼ぶ検の file を source から数える) | tests/test_contract_table_follows_packages.py(ki-33d2441b7e28) | repo 全体の行で毎回 | file 単位(検 1 本ごとの環境は fixture が持つ) |

## 盲検 A・B と、再現・修正

起動の記録は evidence/blind-meta.md、入力は evidence/blind-input.md・blind-a-prompt.md・blind-b-prompt.md、返答は evidence/blind-a-reply.md・
blind-b-reply.md(加工なし)。

### 盲検 A — 表の名を替えた版上げで、guard-check も変わる

| A の主張 | 設計者の再現 | 結論と直し |
| --- | --- | --- |
| (a) 検の期待値 `("_hy_macros", "pkg.macros", "answer")` が表の名を字で写していて、包みを直しても赤のまま | 実験 S1(exp-s1-*): 直した後の版では、uv.lock を 1.3.2 へ上げ表の名を替えた代役を入れ、records.MACRO_TABLE だけを直すと 25 本緑(exp-s1-c) | 成立。期待値を records.MACRO_TABLE へ(4eec37a06)。test_macro_use_record.py の子の script の写しも同じく |
| (b) 同じ file に、使い捨ての環境の検と venv の Hy を測る同じ process の検が同居し、`--no-sync` の便では新旧 2 つの版で測られる | 実験 S1-a(exp-s1-a-unsynced.log): fixture が無ければ同じ process の検は venv の古い Hy を測る | 成立。autouse の fixture で、venv の Hy が uv.lock の版でなければ全部の検を「make sync してから」で赤に(4eec37a06)。sync の後は全部の検が uv.lock の版を測る |
| (c) ADR の R7 の文と事実が `_hy_macros` を字で書いていて、赤にならずに古くなる | — | R7 の文と解釈を records の定数を指す形へ。事実の節は 2026-10-12 の実測の記録なので字のまま残す |
| 付随: `_module_being_loaded`(hy.importer の loader_module_obj の写し)の検は同じ process で走る | — | (b) の fixture で、sync の後は uv.lock の版で測られる |
| 付随: `_hy_export_macros` の読みはどの検にも無い | fixed-export.log(下) | 成立。exported の検を足した |
| 付随: UsedMacro.table が Hy の内部の名を値として持つ | — | 害は無い(record_is_current が先に hy_version を比べる)。直さない — 限界に残す |

### 盲検 B — 包みに新しい依りを足し、その検を同じ process に置くと、全部の検を通る

| B の主張 | 設計者の再現 | 結論と直し |
| --- | --- | --- |
| 関数の中の require の macro を記録する直し(compiler の局所の状態を開く口を包む)と、その検を test_bytecode_guard.py に同じ process の検として足すと、局所の状態の開き方を替えた代役の Hy でも全部緑 | exp-s3-pre-before-fix.log: 直す前の版 0de52de26 に B の差分と代役 localstate(版の名 1.3.1)で 24 passed | 成立。直した後の版 1c9f4699a では、uv.lock を 1.3.2 へ上げると sync の前は 26 本が fixture で赤(exp-s3-a)、代役を sync すると B の足した検が「記録に無い」で赤(exp-s3-b)、替えていない 1.3.2 なら 26 本緑(exp-s3-c) |
| 使い捨ての環境の検に、表に無かった名を覚える道の見本が無い(Hy が `in` をやめて KeyError で知ると見逃す) | fixed-tryget.log: 直した後の版で、表を `in` で確かめない代役は exported と all の 2 本が赤 | 成立。test_a_new_macro_named_like_a_function_the_user_calls_recompiles_the_user_on_pypi_hy を足した |
| (付随・本物の不具合)今の包みは関数の中の require の macro を記録しない — macro を変えても使う側は古い展開のまま | out-of-scope-local-require.log: 1c9f4699a・本物の Hy 1.3.1 で B の probe が `11 11 11`(変えた後も 11) | この便の範囲の外(包みの記録の欠け)。依頼者へ範囲の外の所見として返す |
| (付随)B の差分は依りの知識を guard-hooks に置いた(本来は guard-recording) | — | 静的に強制していない — 限界に残す |

### 設計者の実験で見つけた漏れ(S2)

実験 S2(exp-s2-*): uv.lock を 1.3.2 へ上げ、let が改めた束縛の名の形を替えた代役を入れ、records.RENAMED_BINDING だけを直すと
test_bytecode_guard.py は 25 本緑(exp-s2-c)。しかし test_gensym_canonical.py は名の形の写し(`_GENSYM`・`_RENAMED`・字の前置きの assert・
形の検の `_hy_let_` の絞り・`'bad _hy_gensym_x_5 here'`)で 3 本赤のまま(exp-s2-d)。写しを records の定数と関数へ寄せ(1c9f4699a)、
records を直した代役で 8 本緑(exp-s2-e)・records を直さない代役で 3 本赤(exp-s2-f — 形の変化はまだ知らせる)・本物の Hy で 8 本緑。

## 変更シナリオの予測と実測

| id | 予測(盲検の前) | 実測(直す前) | 実測(直した後) | 差の原因 |
| --- | --- | --- | --- | --- |
| S1 表の引き方・表の名が変わる版上げ | hy-version-pin と guard-recording だけ | 直す前の版では検の期待値の字(guard-check)も変える必要があり、sync していない便では同じ process の検が古い Hy を測った | uv.lock と records.py の 2 か所で 25 本緑(exp-s1-c)。sync の前は fixture が止める(exp-s1-a) | 知識の漏れ(期待値の字の写し)と、検の環境の契約の欠け(同じ process の検の版) |
| S2 gensym・let の名の形が変わる版上げ | hy-version-pin と guard-canonicalization だけ | test_gensym_canonical.py の名の形の写しも直す必要があった(exp-s2-d) | uv.lock と records.py で test_bytecode_guard.py 25 本・test_gensym_canonical.py 8 本緑(exp-s2-c・exp-s2-e) | 知識の漏れ(venv の検の名の形の写し) |
| S3 包みに Hy の内部への新しい依りを足す | 包みの source と test_bytecode_guard.py の検 1 本 | 同じ process に置いた検は uv.lock の版を測らず、版上げを見逃した(exp-s3-pre) | 同じ置き方で、sync の前は fixture が止め、sync の後は足した検が版上げの破れを赤で知らせる(exp-s3-a・b・c) | 検の環境の契約の欠け(同じ process の検の版) |

## 正常例・失敗例の実行記録(最後の版 1c9f4699a)

| 変種(find-links の代役・版の名 1.3.1) | 結果 | 赤の検 | log |
| --- | --- | --- | --- |
| base(替えない) | 25 passed | — | fixed-base.log |
| get | 11 failed | macro・補助の変更・置き場・木をまたぐ検と、表に無かった名の検 2 本 | fixed-get.log |
| table | 11 failed | get と同じ 11 本 | fixed-table.log |
| reader | 1 failed | reader macro の変更の検(before-hy-15) | fixed-reader.log |
| gensym | 1 failed | gensym の数えの検 | fixed-gensym.log |
| anon | 1 failed | gensym の数えの検 | fixed-anon.log |
| tryget | 2 failed | 表に無かった名の検(all・exported) | fixed-tryget.log |
| export | 1 failed | 表に無かった名の検(exported) | fixed-export.log |

本物の PyPI の Hy 1.3.1(find-links なし・1c9f4699a): test_bytecode_guard.py 25 passed・test_gensym_canonical.py 8 passed・
test_macro_use_record.py の触った子の script を使う検 1 passed・ADR-DOE-HY-008 の反例の判定 1 passed・tests/test_contract_table_follows_packages.py
15 passed(final-checks.log)。盲検の前の版(0de52de26)の本物の Hy では 23 passed(after-pypi-full.log)。uv.lock だけを変えた便が
test_bytecode_guard.py を選ぶ事: selection-uv-lock.log。

## 限界(未確認・強制していない所)

- 同じ版の名で中身の違う Hy(手で組み直した wheel)を venv と使い捨ての環境で使い分けると、同じ process の検は venv の Hy を測る。
  PyPI の版の中身は版ごとに変わらず、locked_version は index の外の版を止めるので、本物の版上げでは起きない。
- Hy の内部への新しい依りを包みに足す便が、その検を test_bytecode_guard.py でなく test_macro_use_record.py などに置くと、登記の前には
  uv.lock の版で測られない(日次では測られる)。どの変更が「Hy の内部への依り」かを静的に見分ける検は置いていない。
- 依りの知識を包みのどの module に置くか(guard-hooks か guard-recording か)は強制していない(盲検 B の付随の指摘)。
- 見本は小さい(macro・reader macro・gensym を 1 つずつ・関数の外の require)。Hy が一部の形だけ引き方を替える(入れ子の展開だけ
  `.get`)と見逃しうる。`hy.R` の 1 回の require は source の文から読み、この便の検は測らない。
- UsedMacro.table は Hy の内部の名を値として記録に持つ(害は無い — 盲検 A)。
- 会話の外の task(long-command eafa-variants-after — 0de52de26 の 6 変種)は列で queued のまま終わらなかった。同じ測りを前に出して
  fixed-*.log として取り直した。task が後で走ると evidence/ に after-<変種>.log を書くが、この記録は参照しない。

## この便の範囲の外の所見(依頼者へ返す)

1. 包みは関数の中の require(`(defn f [] (require m [answer]) (answer))`)で入れた macro を記録しない — macro を変えても使う側は古い展開の
   まま(盲検 B の実測と設計者の再現 out-of-scope-local-require.log・11 → 11 → 11)。Hy は局所の表(compiler.local_state_stack)を module の
   表より先に引く。
2. 包みの外にも Hy の内部の名への依りが在り、uv.lock の版で測る検に入らない: packages/doeff-effect-analyzer/python/doeff_effect_analyzer/result_cache.py:395
   (`_hy_macros`・`_hy_reader_macros` を字で読む)・packages/doeff-hy/src/doeff_hy/static_baseline.py(Hy が振る名 `_hy_anon_<番号>`・
   `_hy_let_first_<番号>` の形)。
3. macro_use._exported は `_hy_export_macros` を字で持つ(records の外)。この便の検が振る舞いを測るが、名の定義元は records に寄せていない
   (macro_use.py は自分の file の sha256 を置き場の鍵に使うので、注釈や定数の移動だけの変更でも全部の worker の読みの覚えが 1 度冷える)。
