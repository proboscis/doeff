# doeff-validation — 独立した検査の失敗を集める(設計の記録)

- 状態: **決定済み・実装済み(branch `wt/validation`)**。operator が方向を承認した(2026-09-26 "lgtm")。
  doeff の main へ入れるのは、実装の差分とテストの結果を operator が見てから。
- 書いた者: この package の設計と実装を受けた Claude Code の会話(personal profile・Mac proboscis-mbp)・2026-09-26
- 会話: `9d1fcceb-187e-44e7-9e22-1de8dc6cecef`(operator の指示は親の会話が中継した。原文は 6 章)

## 1. 何が問題か(きっかけのコード)

agora 系の Hy のある関数は、観測した job と配置要求の一致を先頭でこう確かめている。

```hy
(when (or (!= job.key request.key)
          (!= spec.subject request.conversation)
          (not-in job.kind #(JobKind.TURN JobKind.SUMMARIZE))
          (!= job.phase Phase.PENDING)
          (is-not job.cancel None))
  (raise (AcpProtocolError "job does not match the placement request")))
;; 同じ形の (when … (raise …)) がこの後に 3 つ続く
```

1. **最初の失敗で止まる。** 1 つ目の `raise` で抜けるので、残りの食い違いは見えない。
2. **理由が 1 つの文にまとまる。** 5 種類の不一致が 1 つのメッセージになり、呼び手は型で理由を分けられない。
3. **予想できる失敗を例外で投げている。** jevlint FP007a「失敗は Result で表す」に反する。

## 2. 調べた事実(既存の package で足りないか)

`ls packages` と各 README、origin/main と全 branch の `-S doeff-validation` / `-S ValidationEffect`(0 件)で確かめた。

| 候補 | 何をするものか | この問題への関係 |
| --- | --- | --- |
| 例外のまま(`when … raise`) | 最初の失敗で抜ける | 1 章の 3 つの欠陥そのもの |
| jevstd の `Result` / `Ok` / `Err` | 値として失敗を返す | 連鎖は最初の `Err` で止まる。jevstd は doeff に依存しているので、doeff の package が jevstd に依存すると依存の向きが逆になる |
| doeff-traverse の `Traverse` + `sequential` / `parallel` / `parallel_fail_fast` | 集まりの要素ごとに処理を走らせ、要素ごとに `Try` で分離して失敗を記録する(`Inspect` で要素ごとの結果)。逐次・並行・fail-fast を handler で選ぶ | **土台として使う**(決定 2)。検査 1 件を 1 要素にすると、「項目の中で落ちたらその項目だけ止め、他の項目は続く」がそのまま要素ごとの分離になる。手元で確かめた: 効果も Program も要素にでき、要素の中で投げた例外は `failed=True` の要素になり、`parallel_fail_fast(1)` は最初の例外をそのまま投げる |
| doeff-traverse の `Fail` + `fail_handler` | 失敗の場所に handler が代わりの値を入れる | 使わない。代わりの値で続ける意味は、検査の失敗を集める意味と違う |
| doeff-core-effects の `Try` | 例外を `Ok` / `Err` に変える | 値で受けたい所の口として使う(決定 3) |
| doeff の効果の読み取り / 書き込みの分類 | — | **無い**(`docs/22-capability-classes.md` の分類は handler が継続をどう扱うかで、読み書きの区別ではない) |

## 3. 議論した論点(要約)

2026-09-26 に 5 つの論点を「選択肢・利点・危険・推奨」で並べて operator と議論した。結論は 6 章。

1. **check が落ちた後も本体が続く危険。** 「逐次の本体の中で集める」(soft assertion の形)では、落ちた値で
   後ろの効果(書き込み・外部への送信)が走る。効果の読み書きの印が doeff に無いので、handler で止める案は
   成り立たない。→ 形の上で起きなくする: `validate` の直下は独立した項目だけ(決定 1)。
2. **最後に例外を投げる形と FP007a。** doeff の中の例外は Program の失敗の出口で、呼び手は `Try` で値に
   戻せる。例外の型が 1 つに決まり(`ValidationException`)、中の失敗が閉じた型の列で読めれば、FP007a の
   趣旨(呼び手が型で扱えない失敗の出口を作らない)に反しない。FP007a の文面を直すかは operator に別途。
3. **前の値に依る検証で集める意味。** Cats の `Validated` が applicative に限られるのは、集める `ap` と
   止まる `flatMap` を同じ型に置くと法則(`ap` は `flatMap` から作ったものと一致する)が破れるから。
   AssertJ の `SoftAssertions`・pytest-check は逐次の本体のまま集める(本体が読み取りと比較だけで、目的が
   診断だから許される)。JUnit 5 の `assertAll` は入れ子の block で線を引く。→ operator の判断
   "validation is fundamentally set of parallel operation instead of monadic sequential computation"
   で applicative を採った。
4. **`(check = x 0)` と `(check (= x 0))`。** 両方を受け、同じ記録にする(決定 4)。
5. **入れ子と fail-fast。** 内側の例外を外側が 1 件として足す。handler で切り替える(決定 5)。

## 4. 形(実装したもの)

### 4.1 置き場

| 何 | どこ |
| --- | --- |
| 実行時: `validate`・`check`・`perform`・`judge`・失敗の型 | `packages/doeff-validation/src/doeff_validation/`(依存は doeff・doeff-traverse・doeff-core-effects) |
| Hy のマクロ: `validate`・`check`・契約の `check` の展開 | `packages/doeff-hy/src/doeff_hy/macros.hy` の末尾の節(ADR-DOE-HY-005 R5 — macro は doeff-hy にだけ置く。新しい file を足すと名簿の増額が要るので、名簿にある `macros.hy` に置いた) |
| 契約の組み立ての分岐 | 同 `macros.hy` の `_contract-code`(defk / do! の `:pre` / `:post`) |
| `!` の書き換えを validate の中へ持ち込まない | 同 `macros.hy` の `_BANG-OPAQUE-HEADS` に `validate` を足した(ADR-DOE-HY-003 R4「自分の do の文脈を持つ形は外側が中に入らない」の一員) |

展開した code は `doeff_validation` を import する(`hy.I` ではなく import にしたのは、doeff-hy-check の
pyright が型を追えるようにするため)。別名は `_doeff_validation*` だけを使う: defk の本体の中で import した
名前は関数の局所名になるので、defk が使う `_doeff_do` などと同じ名前にすると、それより前の参照が未束縛に
なる(doeff-hy-check で実測した)。

### 4.2 `validate`

- 直下に並べるのは独立した項目で、`check` と Program の 2 種類。`Traverse` の 1 要素ずつになる。
- 項目を全部走らせ、落ちた項目の失敗を項目の順に集める。1 つでもあれば `ValidationException` を投げる。
  無ければ `None`(「確かめて、だめなら投げる」ための形で、値は返さない)。
- Program の項目の中で投げられた `ValidationException` は 1 件の失敗(中の失敗を持つ)として受ける。
  それ以外の例外は、検査の失敗ではない異常として集めずにそのまま投げる。
- 直下に裸の `(<- …)` / `(! …)` を書いたら展開の時点で誤り。
- defk の本体では `(! (validate …))` か `(<- _ (validate …))` で実行する(doeff-hy は変えない。
  ADR-DOE-HY-001 の「文の位置の裸の Program は誤り・自動で束縛しない」をそのまま守る)。

### 4.3 `check`

- `(check 演算子 引数 … :reason 理由)` / `(check 式 :reason 理由)`。括弧の形も分解して同じ記録にする。
  `and` / `or` / `if` / `cond` などの短絡・制御の形は分解しない(平たい形で書いたら展開の時点で誤り)。
- 各引数は項目の中で左から評価する(Hy は thunk にして遅らせるので、引数の評価の例外もその項目の失敗になる)。
- **効果の引数は `(! …)` の字面の印で見分ける。** 印の付いた引数だけを実行し、結果の値で比べる。印の無い
  引数は Program の値でも実行せずにそのまま比べる。実行時に Program 型かで見分ける案は採らない: Program の
  値そのものを比べたい稀な場合と区別できないため。判定の値そのものが Program だった時は、印の付け忘れとして
  `CheckError`(`TypeError`)にする(Program の値は常に真なので、黙って通るのを防ぐ)。
- 記録: 判定が偽なら `CheckFailure`(式の字面・評価した各引数の字面と値・理由)、評価が例外なら
  `CheckError`(加えて落ちる前に評価できた引数と例外)。
- **書ける所は `validate` の直下と、defk / do! の `:pre` / `:post` の中だけ。** それ以外(defk の本体・
  `fn`・内包表記・トップレベル)は `check` マクロそのものが展開の時点で誤りにする。理由: 検査がすべて
  `validate` か契約の所に並んで見える(helper の中に隠れない)・外で書いた check を静的に見つけられる・
  項目の独立が字面で保証される。
- Python の `check(...)` は `CheckSpec` を作るだけで、単独では Program にならない(`yield` できない)。
  これが Python で同じ制約を表す形。`perform(program)` が `(! …)` に当たる。

### 4.4 契約(`:pre` / `:post`)の `check`

- defk / do! の契約に `(check …)` を書ける。**`check` の形の条件だけ**を新しい意味で評価する: 全部を
  評価して失敗を集め、1 つでもあれば `ValidationException`(`context` = `"<関数名> pre-condition"` など)を
  投げる。引数の `(! …)` はその場で実行する(defk / do! の本体は生成器)。
- 型の `(: x T)` と真偽の式は**今までどおり `assert`**(最初の失敗で `AssertionError`)。型の assert を先に
  置き、check はその後に評価する(check の式が型を前提にできる)。
- deff の契約に `check` を書いたら展開の時点で誤り(deff は使わない方針 — operator 2026-09-26 "we dont
  want deff or defn used at all. we want to use defk by default")。
- 契約の check は関数の中でその場で順に評価するので、traverse の handler は要らない。

### 4.5 走らせ方

`sequential()`(逐次・集める)/ `parallel(n)`(並行・集める。`scheduled` が要る)/
`parallel_fail_fast(n)`(最初の失敗で止める。`ValidationException` は 1 件)を組み立ての根で選ぶ。

## 5. 既存の契約との互換

### 5.1 利用箇所の数(2026-09-26・`~/repos` の下の git repo の作業樹を `git grep` で数えた)

`{:pre [` の数(defk / deff / do! の契約の数の目安)と、`(deff ` の数:

| repo | `{:pre [`(file 数) | `(deff ` |
| --- | --- | --- |
| pr-review | 2937(158) | 0 |
| agora-controllers | 2344(350) | 13 |
| proboscis-ema | 1986(363) | 564 |
| agent-control-plane | 1288(164) | 0 |
| doeff | 1190(86) | 232 |
| argus | 831(86) | 0 |
| ai-cli | 89(16) | 0 |
| merge-queue | 82(12) | 0 |
| kubeacp | 69(4) | 0 |
| herdr-hud-deploy-snap | 12(4) | 0 |
| herdr-hud | 6(2) | 0 |

### 5.2 選択肢

| 案 | 利点 | 危険 |
| --- | --- | --- |
| A. 全部の契約を新しい意味にする(型の検査も集めて `ValidationException`) | 1 つの意味にそろう | 約 10,800 箇所の振る舞いが一度に変わる: `AssertionError` → `ValidationException`、1 件で止まる → 全部評価(後ろの条件が前の条件の成立を前提にしていると、前が落ちた時に後ろが別の例外で落ちる)、`python -O` で assert が消える挙動も変わる。`pytest.raises(AssertionError)` やメッセージの文言に合わせた既存のテストが赤になる |
| A'. A に加えて `ValidationException` を `AssertionError` の子にする | `except AssertionError` の呼び手は壊れない | 「予想できる業務の失敗」を「プログラムの誤り」の型の子にすると、例外の使い分け(README)が型の上で崩れる。1 件で止まる → 全部評価の変化は残る |
| B. **`check` の形の条件だけを新しい意味にする(採用)** | 既存の契約は 1 つも変わらない。新しい意味は書き手が `check` と書いた所だけ | 1 つの契約の中に 2 つの意味(assert と check)が並ぶ。型の assert が先に落ちた時は check は評価されない |
| C. 新しい key(`:requires` / `:ensures`)を足す | 既存と完全に分かれる | 契約の置き場が 2 つになり、同じことを 2 通りに書ける |

**採用: B。** 既存の契約の意味を黙って変えない。型の検査は「続けられない誤り」(呼び手の誤り)なので assert の
ままが意味にも合う。値の一致の検査は `check` で書き、集める。既存の真偽の式の条件を `check` に書き換えるかは、
repo ごとに別の依頼で決める。

## 6. 決定

決めたのは親の会話(会話 `9d1fcceb`・2026-09-26)。戻せる決定として決め、operator が方向を承認した。
各決定の後に operator の原文を置く。

1. **`validate` の直下は独立した項目だけ(applicative)。** 項目は `check` と Program(defk の呼び出し)。
   soft assertion の形(逐次の本体の中で集める)は採らない — 落ちた値で後ろが走る危険と巻き添えの失敗が、
   形の上で起きなくなるため。直下の裸の `<-` は展開の時点で誤り。前の値に依る処理は validate の前か、
   helper の defk の中の validate へ。
   - "yeah validation is fundamentally set of parallel operation instead of monadic sequential computation..."
   - "right... it's difficult, basically a check function becomes defk so we need to allow set of defk in validate"
2. **実装は doeff-traverse の上の薄い層。** 検査 1 件を traverse の 1 要素にし、要素ごとの分離で失敗を集める。
   新しい効果は作らなかった(check は効果を出さず、validate に渡す値を作るだけになったので、`Check` の効果も
   専用の handler も要らなくなった)。
3. **失敗が 1 つでもあれば最後に `ValidationException`(全部の失敗を持つ)を投げる。** 値で受けたい所は既存の
   `Try`。例外の使い分けは README に書いた。FP007a の文面との関係は 3 章の 2(直すかは operator に別途)。
   - "validate is not just simple list of 'check'. it's algebraic effects! … # if any of check is failing, throw
     ValidationException, and include accumulated errors"
   - "I am still not sure if try-catch should be completely avoided,,"
4. **check の形は `(check = x 0)` と `(check (= x 0))` の両方を受け、式・各引数の値・`:reason` を記録する。**
   短絡の `and` / `or` は分解しない。
5. **入れ子: 内側の例外を外側が 1 件の失敗として足す。handler は集める(既定)と fail-fast。**
6. **check は `validate` の直下(と契約)にだけ書ける。** helper の defk の本体の check は展開の時点で誤り。
   検査のまとまりの使い回しは、helper に自分の validate を持たせる。
   - "hmm perhaps check should only be allowed inside validate, not in nested defk"
7. **check の引数は効果の式でもよい。効果は `(! …)` の字面の印で見分ける**(4.3)。validate の本体で書く時は
   `(! (validate …))`。doeff-hy の `_STATEMENT-FORM-HEADS` に validate を足す案と、ADR-DOE-HY-001 への例外の
   1 条は取り消した(doeff-hy の文の規則は変えない)。
   - "but check's aarg should accept effectful expressions"
   - "do we need <- _ for validate?" / "for example we could do !(validate ...)"
     (Hy の文法では `!(…)` は 2 つの式に読まれるので、綴りは `(! (validate …))`)
8. **defk / do! の `:pre` / `:post` の check を validate と同じ意味で評価する**(4.4)。既存の契約は変えない(5 章 B)。
   - "and actually such checks are to be done in pre/post of defk"
9. **関数は defk で書く。** doeff-validation の Hy の例とテストは defk だけで書いた。defn はマクロの展開の時に
   呼ばれる helper(`macros.hy` の節)だけで、理由をその節の頭注に書いた。
   - "we dont want deff or defn used at all. we want to use defk by default"
10. 承認: "lgtm"(2026-09-26・対象 = 上の validate の形と、defk の :pre / :post をその意味で評価する方針)。

この会話が実装の中で決めたこと(戻せる決定):

- macro を doeff-hy の `macros.hy` の節に置いた(ADR-DOE-HY-005 R5。新しい file は名簿の増額 = operator の裁定が
  要るので避けた)。
- `validate` を `_BANG-OPAQUE-HEADS` に足した。足さないと、defk が本体の `!` を先に書き換え、check の引数の効果が
  項目の中ではなく defk の本体で(validate の前に・順に)実行され、項目の独立と失敗の収集が崩れる。
- 失敗の型を `CheckFailure` と `CheckError` の 2 つに分けた(判定が偽 / 評価が例外)。
- validate は `None` を返す(項目の値の tuple は返さない)。

## 7. 戻す手順

まだ main に何も入れていない。取りやめる時は branch `wt/validation` を捨てる。main に入れた後に戻す時は、
次を 1 つの commit で戻す: `packages/doeff-validation/`、root の `pyproject.toml` の workspace と dev の登録
2 行と `uv.lock` の該当行、`packages/doeff-hy/src/doeff_hy/macros.hy` の `_contract-code` /
`_is-validation-check` / 末尾の validate・check の節 / `_BANG-OPAQUE-HEADS` の `"validate"`、この文書。
契約に `check` を書いた利用側は、`check` を真偽の式の条件に書き戻す。
