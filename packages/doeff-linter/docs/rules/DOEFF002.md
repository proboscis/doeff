# DOEFF002: 可変な属性の名

## この文書の目的

DOEFF002 が鳴らす属性と、鳴らさない属性の境(class 自身の属性か、継承元の属性か)を読んで予測でき、framework が持つ属性を表へ足して直せるようになる。

## 規則が言うこと

class の `__init__`・`__post_init__`・`__set_name__` の外で `self.<名>` へ書く属性は、書き換わる状態なので、名の頭を `mut_`(公開)か `_mut`(非公開)にする。型は問わない(list・dict・set だけでなく、数・真偽・文字列も)。

継承元の属性への書きは、この規則の対象ではない。framework が決めた名は、変えると framework が読まなくなる。たとえば Textual の `App.sub_title` は、書くと header の表示が変わる属性で、`self._mut_sub_title` に変えると表示が止まる(agora-redesign #2951)。

## 属性が誰の物かの決め方

規則は class ごとに、次の語で属性を分ける。

| 語 | 意味 |
|---|---|
| 置く | 初期化(`__init__`・`__post_init__`・`__set_name__`)と構築(`__new__`)が `self` へ書く。そこから `self.<method>()` で呼ぶ同じ class の method(呼びの先の先も)が書く。class の欄に書く(注釈つきの欄・代入・method・入れ子の class・`__slots__` の名) |
| 同じ repo の基底 | 同じ file の class と、import が指す repo の中の file の class(下の探し方) |
| 見えない基底 | 属性の全部を読めない基底。repo の外の class(`textual.app.App` など)・解けない import・読めない file・`make_base()` のような名で指せない式。`object`・`abc.ABC`・`typing.Generic`・`typing.Protocol` は属性を持たないと分かっているので数えない |

| 状況 | 例 | 判定 |
|---|---|---|
| class か同じ repo の基底が置いた名を init の外で書く | `__init__` で `self.rows = []`、`tick` で `self.rows = [1]` | 鳴らす(基底が repo の外の class でも) |
| 見えない基底が無く、だれも置いていない名を init の外で初めて書く | 基底の無い class の `tick` が `self.cache = {}` | 鳴らす(class 自身の属性を後から作っている) |
| 見えない基底が在り、class も同じ repo の基底も置いていない名を書く | `class X(App): def tick(self): self.sub_title = "x"` | 鳴らさない(見えない基底の属性と読む) |
| 表に載る基底の名を書く | `App` の子が `__init__` でも `self.sub_title = …` を置き、`tick` で書き換える | 鳴らさない(置いていても継承元の名) |

## 同じ repo の基底を探す道

基底は、その file の import(`from m import B`・`import m as x` の `x.B`・`import a.b` の `a.b.B`・相対 import・添字つきの `Generic[T]` は添字を外す)から class を引く。

| 引く先 | 探し方 |
|---|---|
| 同じ file | 基底の名の class を、基底を書いた class より前に書いたもの(無ければ、関数の中も含めて 1 つだけならそれ) |
| 絶対 import | repo の根までの上の dir のうち `__init__.py` を持たない dir と、その `src` から、module の path(`a/b.py` か `a/b/__init__.py`)を引く。`__init__.py` を持つ dir は import の起点ではない |
| 相対 import | 読んでいる file の dir から、点の数だけ上る |
| `__init__.py` の再 export | `from .a import B` を 8 段まで辿る |

repo の根は、linter の実行が決めた根(`--root`・設定 file の dir)。根の上の dir は探さないので、`.git` の無い木(commit の hook の HEAD の木・日次の写し)でも同じ file を探す。見つからない・読めない基底は見えない基底として読み、鳴らす向きへは外れない。

## 表 `data/framework_attributes.json` に足す

repo の外の基底が持つと分かっている属性を、完全な名の基底ごとに置く。表の名は、class が init でも置いていても継承元の名として鳴らさない。

| 欄 | 内容 |
|---|---|
| `transparent_bases` | 属性を 1 つも持たないと分かっている基底(見えない基底に数えない) |
| `framework_attributes` | 基底の完全な名(`from textual.app import App` の `App` は `textual.app.App`)→ その基底が持つ属性の名。基底の属性の全部ではない(載っていても、基底はほかの属性を持ちうる物として読む) |

載せる条件は、framework の source で、その名が基底の class に宣言されていて、利用者が書く口として文書化されていると確かめた物だけ。基底の子の class(`textual.widgets.Static` など)は、基底の名を別に載せない限り見えない基底で、表の名は効かない(init の外で初めて書く名は、見えない基底の規則で鳴らさない)。

## 外れる向き

| 外れ | 起きること |
|---|---|
| 見えない基底が在る class が、init の外で自前の属性を初めて作る | 鳴らさない(継承元の属性と区別できない)。init が呼ぶ method で置けば鳴らす |
| 別の package の `src` にある基底(monorepo の別 package) | 探さず見えない基底に数える |
| 表に無い framework の属性を init でも置いている | 鳴らす(表に足して直す) |
