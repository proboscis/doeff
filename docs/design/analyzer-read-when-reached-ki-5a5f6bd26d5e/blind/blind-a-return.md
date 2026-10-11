Opus 5.5(claude-opus-5-5)です。

## 要旨

失敗例が見つかりました。S5(thread で並べる)の主張は「変わる module: なし」ですが、実際には誤った答えが出ます。直すには clause-emits・clause-reading・fact-reader の 3 つの責務を変える必要があります。原因は、隠すはずの「節の emits を読む reader は 1 つの handler の全部の節で共有され、書き換わる状態を持つ」という知識が、公開の値 `HandlerEffects` を通して漏れていることです。

## 1. 変更要求と、それが要る理由

- **要求:** 下流の閉じの検 10 本を、同じ process の中の thread で並べて走らせる。土台の handler は 1 回だけ読み(`analyze_handler` / `analyze_env`)、job ごとの `check_coverage` を thread pool で回す。
- **理由:**
  - 閉じの検は 1 本 1〜7 s、置き場に答えが無い時は 20〜60 s かかります(`result_cache.py` の docstring)。
  - この作業木の venv はすでに free-threaded の Python 3.14.3 で、GIL は無効でした(実測)。
  - これは材料の S5「解析を thread で並べる・節を作った thread と別の thread で emits を引く」そのものです。前提を広げた例ではありません。

## 2. 関係する主張・前提と、予想に反して変わるもの

**前提を守っているか**

| 前提 | この例での扱い |
|---|---|
| P2 | 守っています。emits を引くのは解析が終わった後の `check_coverage` の中だけで、事実を読んでいる途中では引きません。 |
| P3 | 関係しません。result cache は切ってあり(`DOEFF_EFFECT_ANALYZER_CACHE=off`)、`cached_result` も呼びません。 |
| P1 | 入力の側では何も崩していません。S5 の条件(別の thread が同じ handler の節を引く)だけで、設計の側が P1 の後半「報告を作る間、同じ reader の事実の読みは途中で止まっていない」を守れなくなります。 |

- もし P1 を「呼び手が同じ reader の節を同時に引かない義務」と読むなら、S5 は契約の適用範囲外です。
- ただしその場合も、呼び手はこの義務を守れません。下の「独立の解析」の例で、呼び手が何も共有していないのに、process の覚え(`_absent_receiver`)を通して reader が共有されていたからです。

**変わる責務**(S5 の予想ではすべて「変わらない」)

| 責務 | 予想に反する点 |
|---|---|
| clause-emits | `_ClauseEmits.__get__` は「読んだか確かめる → 読む → 書く」の 3 手が一度に行われず(atomic でなく)、「読むのは高々 1 回」が崩れます。 |
| clause-reading | `_read_function` / `_inline_handler` は handler を 1 回読むごとに `_Reader()` を 1 つ作ります。`_emits_read_later` が、それを全部の節の読む関数に握らせます。 |
| fact-reader | `_Reader.facts` は読む前に空の `_Facts` を `_facts` に置きます(再帰への備え)。別の thread からは、これが読みかけのまま見えます。`_local_reads` も同じ形です。 |
| report-walk | `_walk` がその読みかけの事実を吸い込み、effect の欠けた報告を作ります。 |
| `_absent_receiver` | `functools.cache` で process に 1 つです。節と reader が、全部の解析で共有されます(実測: p1 と p2 で同じ object)。 |
| 公開の契約 | `HandlerEffects` は、以前は thread の間で共有できる値でした(直す前の版は 200/200 正しい)。今は共有すると答えが壊れます。これは拡張ではなく、契約が狭くなっています。 |

## 3. どの知識がどこへ漏れるか

**漏れている知識**
- 「遅れて読む関数が、どの reader で読むか」は、clause-emits が隠すと宣言しています。
- しかし実際には、その reader は handler 1 回分(`_absent_receiver` なら process 全体)の節で共有され、読みかけの状態を外へ見せます。
- fact-reader の寿命は「解析 1 回」とされていますが、clause-emits の寿命「読む前は reader を握る」によって、公開の値の寿命まで延びています。読む時刻と thread も、`pass_through` を呼ぶ側に移っています。
- その結果、thread で並べる側も、clause-emits の lock の粒度を決める人も、clause-reading と fact-reader の内側を知る必要が出ます。

**直し方の候補と、それぞれ変わる責務**(すべて /tmp の写しで実測)

| 直し方 | 変える責務 | 結果 |
|---|---|---|
| V1: 節ごとの lock | clause-emits だけ(自分の知識だけで書ける形) | 足りません。別の節どうしでは 189/200 が誤り。 |
| V2: process に 1 つの lock | clause-emits だけ | 正しくなります(200/200、20/20)。ただし遅れた読みが全部一列になり、8 thread で 2.2〜2.7 s(V3 は 0.71〜0.79 s)。S5 の狙いである並びを失います。正しさも、clause-reading の共有の仕方と P2 を知っていることに依ります。 |
| V3: reader ごとの lock + 節ごとの 1 回 | fact-reader・clause-reading・clause-emits | 正しくなります(200/200、20/20)。並びも保ち、既存の検 30 本も通りました。 |

**欠陥でないもの**
- 意図した公開契約の拡張(`Clause(emits=<引数なしの関数>)`・`clause.emits` は常に `ProgramEffects`)。
- 配線の変更(`_emits_read_later`・`_dispatch_of`。`_DISPATCHES` の競合は AST だけで決まる値なので無害)。
- 文書にある共同の不変条件(`_READING` を節を作った時に写すこと・P2・P3)。

欠陥は、文書に無い「reader の共有」です。S5 の主張「reader ごとで、別の解析と混ざらない」と逆のことが起きています。

## 4. 最小の入力と再現手順

**入力**(`scripts/fixture.py` が作ります)
- effect: `Ping`・`Pong`・`Far`
- `common()`: `Far` を出す `@do` の関数
- `two_clauses(effect, k)`: `Ping` の枝と `Pong` の枝の両方が `yield common()` をする handler
- 変更: 主 thread で `h = analyze_handler(...)` を読み、thread A が `check_coverage([Ping],[h])`、thread B が `check_coverage([Pong],[h])` を引く
- 正しい答え: どちらも gaps = `['Far']`

**撃ち方**

```
W=/work/toolset/worktrees/doeff-wt-ki-5a5f-design-check
DOEFF_EFFECT_ANALYZER_CACHE=off PYTHONPATH=/tmp/ki5a5f-blind-A/<after|before|fix-v1|fix-v2|fix-v3> PYTHONDONTWRITEBYTECODE=1 \
  uv run --no-sync --project $W python /tmp/ki5a5f-blind-A/scripts/<script> <fixture の dir> [引数]
```

- `before` は change.patch を写しに逆に当てた版です。

**観測結果(実測)**

1. **順を固定した再現**(`race_hook.py`)
   - 使った hook は `_locate` の前で thread A を待たせるだけで、読む中身には触りません。
   - 後の版: A は `['Far']`。B は `[]` で `complete=True` と誤ります。同じ handler で主 thread から引き直しても `[]` のままで、誤りが残ります。新しく読み直した handler を 1 thread で引くと `['Far']`。
   - 直す前の版: 全部 `['Far']`。
   - V1 は後の版と同じく誤り、V3 は正しい答えでした。
2. **hook なしの自然な競合**(`race_stress.py <dir> 200 <filler>`)
   - 後の版: 200 回中、filler 0 で 186 回、30 で 196 回、300 で 199 回が誤り。
   - `PYTHON_GIL=1`(GIL あり)でも 200 回全部が誤りでした。free-threaded に限った問題ではありません。
   - 直す前の版: 200 回全部が正しい。
3. **独立の 2 つの解析**(`absent_share.py`)
   - 2 つの Program はどちらも `open_bind(maybe_one(), lambda: …)`(`:absent` の形)を使います。
   - share: p1 と p2 の受け手の節が同じ object で、握る `_Reader` も同じでした。
   - race-warm(30 process・hook なし): 後の版は 12 回、片方の解析で `Raise` の gap が消えました(GIL ありでは 2 回)。直す前の版は 30 回全部が正しい。
   - race-hook: 後の版は p2 が `[]`、p1 が `['Raise']`。直す前の版は両方 `['Raise']`。
4. **並びの損**(`parallel_cost.py <dir> 8 4000`、36 CPU): 後の版 0.71〜0.75 s、V2 2.21〜2.68 s、V3 0.71〜0.79 s。

材料の 4 の検(test_read_when_reached など)には thread を使う検が 1 つもありません。tests を grep して確かめました。

## 5. 未確認の前提と、推測・実測の区別

- **推測:**
  - 誤った Coverage が `cached_result` で disk に置かれると、依る module が変わるまで process をまたいで使い回される(測っていません)。
  - 「高々 1 回」が崩れることは、p2 だけが `[]` を得た結果から推しました。読みの回数を直に数えてはいません。
- **未確認:**
  - 下流(agora-controllers)が実際に thread で並べるか、`HandlerEffects` を共有するか、10 本の検が `:absent` を使うか。下流は読んでいません。
  - 下流の時間がどれだけ縮むか。並びの損は合成した大きな module で測った値です。
  - 材料が P1 を呼び手の義務とするつもりかどうか。材料には書かれていません。
  - V3 は試作です。package の検は 2 file(30 本)だけ撃ちました。入れ子の時の deadlock は P2 からの推論だけで、`_local_reads`・`_reports` の競合は個別に試していません。自分を入れ直す handler(`_READING`)を thread をまたいで引く場合も試していません。
- **作業の範囲:**
  - 作業木の追跡されている file は変えていません(`git status` で確認)。
  - 直す前の版は git の履歴からではなく、change.patch を逆に当てて作りました。

実験の file は `/tmp/ki5a5f-blind-A/` の下にあります: `scripts/` に `fixture.py`・`race_hook.py`・`race_stress.py`・`absent_share.py`・`parallel_cost.py`、版の写しが `after`・`before`・`fix-v1`〜`fix-v3`。

WAIT: work — 頼んだ側(親の会話)の次の依頼
