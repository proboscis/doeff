## 結論

S1(Hy を上げる)と S3(light_stack だけを変える)の主張が成り立たない例を 1 つ見つけ、仮の Hy の版で再現しました。新しい Hy が `inspect.stack()[n].frame` を読む形に変わると、回避を light_stack.py の中だけで正しく直しても R7 の検は赤のままです。緑にするには ADR の file(数える・測る・判定・版の決定・R1〜R7 の記録・反例の検を全部持つ file)の判定と反例の検を変える必要があります。

## 1. 変更要求と、それが必要になる理由

要求は「Hy を新しい版へ上げる。その版の `hy.macros`・`hy.compiler` は、呼び出し元の frame を `inspect.stack()[n][0]` ではなく `inspect.stack()[n].frame` で読む」です。

これが起こりうると考える理由は 2 つです。
- ADR 自身が、law `internal-reliances-exist-in-pypi-hy` の反例 3 にこの形を挙げています(ADR の 581 行)。
- 入っている Hy 1.3.1 の `hy/reader/hy_reader.py:142` は、すでに `inspect.stack()[1].frame` を使っています。本家が両方の書き方をしている実例です(grep で実測)。

S1 の決まりでは、依りが壊れたら直すのは doeff-hy の回避です。自然な直し方は、light_stack が返す行を「frame 1 つの名前つき tuple」に替え、`[0]` と `.frame` の両方で読めるようにする事です。変更は light_stack.py の中の 5 行で済みます。

## 2. 関係する主張・前提と、予想に反して変わる所

- **S1**:「変えない所 = 検の code(数える・測る・判定)」。前提の「照合の版は uv.lock から読む」は守っています(uv.lock の hy の 1 行だけを替え、検はその版で測りました)。
- **S3**:「light_stack.py だけ・ADR の file は変えない」。前提の「`_HY_CALLER_MODULES` を module から読む」も守っています(module の一覧は変えていません)。
- 依りの種類は `light-stack` のままです。種類の語彙を足す S4(共有の契約の拡張)には当たりません。

予想に反して変わるのは、ADR の file(`docs/adr/defadr_doeff_hy_008_hy_is_not_forked.hy`)の次の所です。
- **判定**: `frame-only-use`(196〜207 行)と `stack-call-violations` の文「light_stack の軽い版は frame 1 つの tuple を返す」(234 行)。
- **反例の検**: `judgments-reject-the-counterexamples` の 711〜712 行(`.frame` は赤、と固定している)。
- **規則・反例の文**: R7 の文(557 行)と law の反例 3(581 行)。
- **責務の表**: 盲検の入力の表の reliance-judgment の行(「`inspect.stack(…)[n][0]`」)も変わります。

## 3. どの知識がどこへ漏れているか

漏れているのは、light_stack の行の形(返す行がどの読み方に応えるか)です。これは doeff-hy の回避の内部の決め事です。ところが判定は、その写しを `[n][0]` という字面の規則として固定で持っています。

- **片側だけを実物から読んでいる**: 判定は Hy の側の使い方は実物の source から読みますが、light_stack の側は読みません。`_HY_CALLER_MODULES` は module から読むのに、行の形は写しのままです。R7 が「写しの台帳を置かない」(557 行・549 行)と言っている、その写しに当たります。
- **区別**:
  - 公開の契約の拡張ではありません(種類と probe の出力の形は不変)。
  - 配線の変更でもありません(uv.lock の変更はもう R7 を選びます)。
  - 「Hy の使い方 ⊆ light_stack の行が応える読み方」という共同の不変条件を検で確かめる事自体は正当です。欠陥は、その片側を持ち主から読まずに写しで持っている事です。
- **責務の表と主張が食い違っている**: 表は判定に light_stack の行の形を持たせており、S1・S3 はその判定が回避の変更で変わらないと言っています。

## 4. 再現の手順と観測した結果(すべて実測)

1. **ベースライン**: R7 の検は緑(1 passed)。
2. **仮の Hy の wheel を作る**: `hy-1.3.1+blindframe` を、入っている 1.3.1 から作りました(`macros.py` の 3 か所と `compiler.py` の 2 か所の `[n][0]` を `.frame` へ)。作り方は `/work/agent-workspaces/a-R6ECWFH17Q0S5X4EKT02N5XNQB/scripts/build_fake_hy.py`。uv.lock の 1893 行を `version = "1.3.1+blindframe"` に替え、`UV_FIND_LINKS=<wheel の dir>` を付けて走らせました。
3. **require が実際に動くかを確かめる**(`scripts/check_require.py`):

   | 組み合わせ | require の結果 |
   |---|---|
   | 元の light_stack + 仮の Hy | `AttributeError: 'tuple' object has no attribute 'frame'` |
   | 直した light_stack(`collections.namedtuple("_LightFrameInfo", ["frame"])`)+ 仮の Hy | 42 が返る(動く) |
   | 直した light_stack + 本物の 1.3.1 | 42 が返る(動く) |

4. **R7 の検を走らせる**:
   - 直した light_stack + 仮の Hy では赤になりました。違反は 5 件(hy.compiler の 55 行・810 行、hy.macros の 153 行・179 行・203 行)で、どれも「… は inspect.stack の [n][0](frame だけ)を読む形でない — light_stack の軽い版は frame 1 つの tuple を返す」という文です。直した後なので、この文は事実と合わない誤検出です。
   - 元の light_stack + 仮の Hy でも、同じ 5 件で赤でした。判定は light_stack の中身を読まないので、直す前と後を区別できません。
   - 仮の Hy が `.frame` を読む限り、光らせる module を外す(高速化を捨てる)以外に、doeff-hy の中だけで R7 を緑に戻す道はありません。これは判定が `.frame` を無条件に赤にする事から読んだ結論です。
5. **ADR を直すとどうなるか**:
   - `frame-only-use` を `.frame` も通す形に替えると、R7 は緑になりました。代わりに `judgments-reject-the-counterexamples` が 712 行で `AssertionError: []` を出して赤になりました。
   - 判定だけを緩め、light_stack を元の tuple のままにすると、R7 は緑なのに require は落ちます(偽陰性)。2 か所の形を手で揃え続けるしかない、という事です。
6. **後始末**: 作業木は全部元へ戻しました(`git status` は `?? BLIND_INPUT.md` だけ)。

## 5. 未確認の前提・不足(推測と実測の区別)

- **推測**: 実際の新しい Hy が `.frame` 形へ移るかは分かりません(PyPI の最新は 1.3.1)。仮の wheel は模擬で、中の `hy.__version__` は "1.3.1" のままです。この検には影響しません。
- **推測**: namedtuple の行が速さに与える影響は測っていません。小さいと推測しています。
- **走らせていない検**: R6(上限)の検。版の文字が違うので赤になるはずですが、この話には関係しません。`make test-changed` と全体の検も走らせていません。
- **別件(主な例ではない)**:
  - Hy 言語で書かれた module(例 `hy.core.util` の `(get (.stack inspect) (+ n 1) 0)`)を `_HY_CALLER_MODULES` に足すと、probe が Hy の source を返し、判定の `ast.parse` が SyntaxError(29 行)で落ちます。確かめたのはこの仕組みだけで、R7 の検は通していません。1.3.1 ではこの関数は呼ばれていないので、現実味は低めです。
  - 数え方の外にある形(`(.attr compiler)` のメソッド呼び出しの形、Hy の source の `(setv hy.X.y …)`、`getattr`・`setattr`・`__dict__.get` の文字列の名 — 例 macros.hy の `_doeff_module_bindings`、ast_unparse.py の `true_unparse`)も見つけました。ただし表と ADR の解釈が数える形を限っているので、失敗例とはせず、走らせて確かめてもいません。
- **作業場所の外に残した物**: uv の cache に、仮の wheel(局所の版の印 `+blindframe` 付きで、PyPI の版とはぶつからない)が入りました。模擬に使った file は `/work/agent-workspaces/a-R6ECWFH17Q0S5X4EKT02N5XNQB/` に在ります。

WAIT: work — 呼び出し元の次の依頼
