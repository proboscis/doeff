# 設計検証: Hy の .pyc の記録の照らしの、process をまたぐ覚え(card ki-38508e6e56fd)

## この文書の目的

読み手が次の 3 つを判断できるようにする。

1. 照らしの費用を減らすために process をまたいで覚えた物が、古い展開を見逃さない不変条件 B1 を破らない理由。
2. 将来の変更(保存先・副作用・機体をまたぐ共有・並行)が、どの責務の中に収まるか。
3. 盲検 A・B が見つけた 2 つの穴と、その直しと再検証の結果。

## 完了の範囲

- 範囲: 実装完了まで。
  - 本体: doeff main ce9c19488(L2053)。
  - 盲検の後の直し: doeff main cf7ea9cc0(L2061)。
  - pin の引き上げ: agora-controllers main 816dffa8f(L6082)。
- 受入の条: card の 3 条はどれも ☑。
  - 性能の条 cefc8ccca は、配り役が子 card ki-be596ac8276f へ移した。この検証の要件には入れない。
- 盲検の前に保存した主張: claims.md(sha256 d9f9df0d6b58e60d8fe90f9e70bd688d8d79f44e431c82b4d870299032100945・2026-10-11 13:46 UTC)。
  - 下の「事前の主張」は claims.md を書き換えていない。

## 要件(固定)

| id | 要件 |
|---|---|
| R1 | B1: digest が依った物のどれかが記録の時と違えば、照らしは古いと判じる。process をまたいでも同じ。依った物 = macro を定義した file・macro が辿る値の module の file・環境変数など file の外で決まる値。 |
| R2 | 新しい process は、前の process と同じ中身の source を解き直さない。 |
| R3 | card の受入 c8a812c6d・c44cacf91・c40d0dda5 を満たす。 |

## 責務と公開契約

report.json の modules と同じ。要点は次のとおり。

- macro_use は「何を digest に入れるか」と照らしの判じを持つ。
  - digest は process の中でだけ覚える(_REMEMBERED)。
  - stat の覚えは照らし 1 回の中だけに置く(_CHECKED_STATES)。
- bound_source は「source の文の読み方」と .binding の entry の形・鍵を持つ。
  - 読みは中身で決まる純な計算なので、process をまたいで覚えてよい唯一の物。
- code_store は置き場の並び・鍵の綴り・読み書き・掃除の時刻・壊れた entry の扱いを持つ。
  - Hy の拡張子の定義点(HY_SOURCE_SUFFIXES)もここにある。
- loader_hooks は「いつ照らし、いつ compile し直すか」を持つ。digest の作り方は知らない。
- records は記録の形を持つ。

## 事前の主張(claims.md の写し)

| シナリオ | 軸 | 主張 | 予想の波及 |
|---|---|---|---|
| S-hw | hardware | 対象外。読みは純 Python で機体に依らない。Python の build の違いは鍵の sys.version が分ける。 | — |
| S-storage | storage | 置き場の在処・並びの変更は code_store だけが変わる。 | code_store |
| S-effects | effects | 置き場に書けない・source が読めない時も、答えは変わらず速さだけが落ちる。 | なし |
| S-dist | distribution | 作業木・版・機体をまたいで置き場を共有しても、同じ中身だけが当たる。digest は覚えないので、B1 は毎 process 辿り直して守る。 | なし |
| S-conc | concurrency | 同時の読み書きで半分の entry は読まれない。壊れた entry は除いて解き直す。 | code_store の作法のまま |
| S-sim | simulation | 対象外。この層は Python の起動の時点で入り、Program の外。 | — |

盲検の後に足したシナリオは S-storage-ext(盲検 A・軸 storage)。主張は claims.md の前提「path が効くのは拡張子と誤りの文だけ」と、loader_hooks の宣言「拡張子の定義点は code_store」から引いた。

| シナリオ | 主張 | 予想の波及 |
|---|---|---|
| S-storage-ext | Hy の source の拡張子を 1 つ足すと、定義点(code_store)と doeff_hy の登録だけが変わる。 | code_store・doeff_hy の登録 |

## 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| B1(digest を覚えない) | 子の process をまたぐ振る舞いの検。条 1〜3 に加え、別の top の package の値を「変えない照らし → 変更 → 照らし」の順で試す。 | test_macro_use_record.py の「process をまたぐ覚え」の 5 本 | 変更箇所に近い pytest・日次の全体検証 | 静的な規則は無い。検が試した形(別の木・別 module・別 package・環境変数)の外の生きた値は検が覆わない。 |
| class を中身で辿るか公開の形で覆うか | 標準の importlib.machinery.EXTENSION_SUFFIXES で判じる。知らない末尾は中身で辿る(安全側)。 | macro_use._python_source・検 test_a_class_in_a_source_with_an_unlisted_extension_is_covered_by_its_body | 同上 | 拡張の module の末尾を Python が足す時は標準の値が追う。 |
| Hy の拡張子の定義点は 1 つ | bound_source・records は code_store.HY_SOURCE_SUFFIXES を import する(写しを置かない)。 | bound_source._HY_SUFFIXES・records._SOURCE_SUFFIXES | import の時に同じ object を引く | 写しの新設を止める静的な規則は無い。 |
| binding の鍵の網羅 | 読み方の種類・hy.repr の登録・読めない file を分ける検。 | test_bound_source_store.py の 4 本 | 同上 | 鍵の欄の足し忘れを静的には見ない。読み方の code の変更は bound_source.py 自身の sha256 で覆う。 |
| 置き場の並びと書き方は code_store だけ | bound_source は content_key・entry_path・read_entry・write_entry・discard_entry だけを呼ぶ。 | bound_source の置き場の節 | code-quality の fast・review | import の向きの規則は guard の package の中を分けない(盲検 B の観測)。 |
| entry の形の型 | marshal の境界の BindingParts と、形を確かめる _binding_from_parts。形が違えば _Missing を返し entry を除く。 | bound_source | 実行時 | 型検査は日次。 |

## 盲検 A・B

出自の記録は blind-meta.md に書いた。

- 第 1 候補の gpt-6-astra は、codex の資格が無く(401)起こせなかった。
- 第 2 候補として Opus 5.5 を Agent tool の新しい文脈で起こした。

| 盲検 | 返答 | 中身 |
|---|---|---|
| A | blind-a-response.md | 拡張子 .hym を足すと、macro_use._python_source の拡張子の写しが古いまま残る。class を公開の形だけで覆い、macro の file の変更を見逃す。 |
| B | blind-b-response.md | 照らしの答えを loader_hooks で置き場に覚える差分。鍵は記録が名指す package の file と環境。今の検を全部通るのに、別の top の package の値の変更を見逃す。 |

## 再現と修正

| 失敗例 | 直す前の再現 | 直し | 直した後 |
|---|---|---|---|
| A | ce9c19488 で照らし True・値 3。証拠 repro-a-before.txt。検は 84072f2bb で赤(3 と 30 で同じ digest)。 | cf7ea9cc0: _python_source を EXTENSION_SUFFIXES で判じる・写しを定義点から引く | 照らし False・値 30。.hy の対照の digest は直す前と同じ。証拠 repro-a-after.txt・green-cf7ea9cc0.log |
| B | 盲検 B の差分の写しで、照らし True・値 [3, 5](repro-b-mod.txt)。ce9c19488 では False・[30, 5](repro-b-base.txt)。新しい検は B の差分の写しで赤(repro-b-newtest-on-bdiff.txt)。 | 84072f2bb: 別の top の package の値の検を足す。cf7ea9cc0: B1 の条に「照らしの答えも覚えない」を書く。 | main で緑(green-cf7ea9cc0.log) |

事前の予想と実際の波及の差:

- S-storage-ext の予想は code_store と登録だけだった。
- 実際には macro_use・bound_source・records の写しも変える必要があった。
- 原因は「どの file が source か」という import の口の知識が、macro_use に漏れていたこと。
- 直しでこの知識を 2 つに寄せた。
  - 拡張の module かどうかは標準の値で判じる。
  - Hy の拡張子は定義点を引く。

## 実装の証拠

- 検の一式: doeff-hy の照らしの検 4 file が 75 passed(guard-suite-242c8e32e.log。中身は L2061 と同じ版)。
- 品質検査: code-quality --scope changed は L2053 の基点 c4f1423f4 で passed、L2061 の基点 bf6e78bc3 で passed。どちらも型検査は撃たない段。
- 置き場・副作用・並行の正常例と失敗例: store_checks.log(着地した cf7ea9cc0 で撃った)。

## 未確認

- 実際に起こした model の観測。Agent tool の自己申告は無く、effort xhigh を受け付けたかも分からない。
- 機体をまたぐ置き場の共有(S-hw・S-dist の機体の部分)は撃っていない。この機体の作業木 2 つで代えた。
- 盲検 A の「records の抜けが doeff-adr の item_cache に及ぶか」は、写しを無くしたので及ばない形になった。ただし item_cache 側は撃っていない。
- 性能の条 cefc8ccca(子 card ki-be596ac8276f へ移った)。
