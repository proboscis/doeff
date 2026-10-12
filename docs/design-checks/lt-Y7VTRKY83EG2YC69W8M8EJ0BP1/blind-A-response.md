## 結論

主張の前提を守ったままで、責務分離が崩れる失敗例が 1 つ見つかりました。実コードを読み、temp dir の中で判定・解決・振る舞いを実測して確かめています。共有の作業木と `~/repos` は書き換えていません。design-pre-blind.md は盲検を保つため読んでいません。

## 1. 変更要求と、それが要る理由

**要求:** Hy の修正版（1.3.2 と仮定）が、doeff の回避している上流の欠陥を直したとします。欠陥は `hy.compat.rewriting_unparse` の再帰で、Python 3.14 と pytest 9 の組で止まらなくなるものです。doeff は Hy を 1.3.2 へ上げ、doeff-hy の当て物 `doeff_hy/ast_unparse.py` を外します。

**理由:**
- 回避を doeff の側で持つのは ADR-DOE-HY-008 R2 の方針です。
- doeff 自身の文書 `packages/doeff-hy/docs/hy-upstream-rewriting-unparse.md` が「上流が直した版が出たら…その時に当て物を消す」と手順を定めています。
- 当て物は Hy の内部の変換を写したものです（写し先 `0x1D41A`・`_KEEP`）。置いたままだと、上流の変更を古い写しで上書きし続けます。

## 2. 関係する主張・前提と、予想に反して変わる所

守っている前提:
- 配り方は git と path だけです（S2 の除外のまま）。
- uv.lock の hy は 1 行です。
- 上げる便は doeff の全部の検を新しい版で緑にします（S3 の前提）。
- 消費者の lock の Hy は上限の内です（S4 の前提）。

前提を変えていないので、契約の適用範囲の内の例です。

| 主張 | 予想 | 実際 |
|---|---|---|
| S3 | 変わるのは M-lock と M-cap だけ。M-check・M-select・M-consumer は変えない | 正しく動かすには M-cap の**下限**も変える必要があり、M-consumer も変わる。下限を守らせるなら M-check と M-select も変わる |
| S4 | 消費者の変更は doeff-min-commit と requires-dist の 1 行だけで、Hy の版は変わらない | 予想どおり Hy の版は変わらない。そのまま入れると、当て物の無い doeff-hy が Hy 1.3.1 の上で動き、壊れる |
| M-cap | 責務は「入れてよい Hy の範囲」で、持つ知識は「上限と下限」 | 不変条件と検は上限 = lock の版しか縛らない。下限 `>=1.2.0` を決める規則も検も無い |
| M-check | 上限・lock・名の 3 つが同じ版を指す | 下限は判定に入っていない。R4 の名の照合も、当て物が依る内部の名を数えていない（4 の実測） |

下限を上げた場合に消費者で起きること:
- merge-queue と code-quality の `hy==1.3.0` は解決できなくなり、宣言を書き換える必要があります。
- agora-controllers・custody・agent-commons は、doeff の版を上げる便と同じ便で lock の Hy を上げる必要があります。
- 結果として、S1 の「doeff を先に上げ、消費者はその後」という順も崩れます。

## 3. どの知識がどこへ漏れるか

**区別（欠陥ではない部分）:**
- 上限と lock を同じ commit で上げるのは、意図した共同の不変条件（R6）です。
- 消費者が doeff-min-commit と requires-dist の 1 行を直すのは、意図した配線の変更（S4）です。
- `ast_unparse.py` を消すのは、要求された機能そのものです。
- 下限を上げること自体は、公開の契約の正当な変更（範囲を狭める変更）です。

**漏洩（欠陥の部分）:**
- 「doeff-hy が正しく動く最古の Hy の版」は、doeff-hy の内部の実装だけで決まります。具体的には、どの Hy の欠陥をどの file で回避しているか（`ast_unparse.py`・`__init__.py` の `_could_be_hy_src`・`light_stack.py`・`match_fields.py`・`records.py` の gensym の形）です。
- この知識を持つ module が無く、検も無い。それなのに正しさのためには次の 3 か所へ漏れる必要があります。
  - M-cap の下限（公開の metadata）
  - 各消費者の Hy の宣言と lock（M-consumer は doeff の中身を隠すはずの側）
  - 守らせる場合は M-check の新しい規則と、当て物の file の接頭辞を知る M-select の表
- 根にあるのは設計の論法の非対称です。「doeff の検が確かめる Hy は lock の版 1 つだけ」という論法を上限にだけ当て、merge-queue の都合で `[1.2.0, lock の版)` の窓を検の外に残しています。
- 今も merge-queue と code-quality は、doeff の検が一度も走っていない Hy 1.3.0 で doeff を動かしています（blind-input の表と、R4 が 1.3.1 だけを照合することから）。
- 付け足しの観察: 当て物の `install()` が「上流が直した」と気づく手がかりは、版ではなく Hy の内部の形（`ast.unparse is hy.compat.rewriting_unparse`）です。上流が提案文の直し方（名前を保ったまま直す）を採ると、`install()` は上流の直した関数を doeff の写しで上書きし続けます。文書の「何もしなくなる」は成り立ちません。

## 4. 最小の再現と観測（すべて temp dir の中・実測）

1. **R6 は下限を見ない**
   - 3 つの file（defadr・uv.lock・doeff-hy の pyproject）を `/tmp/tmp.DCw2SMytfW` へ写しました。
   - lock の hy を 1.3.2、宣言を `hy>=1.2.0,<=1.3.2` に変え、実物の `locked-hy-version`・`doeff-hy-requirements`・`hy-cap-violations` を呼びました（script は `/tmp/tmp.UCCkPQwWdO/judge.py`）。
   - 結果は `[]`（緑）でした。`hy>=1.3.2,<=1.3.2`、`hy>=0.1,<=1.3.2`、下限の無い `hy<=1.3.2` もすべて `[]` です。
   - `SpecifierSet(">=1.2.0,<=1.3.2")` は 1.3.0 と 1.3.1 をどちらも通します。
2. **消費者は古い Hy のまま残る**
   - 代わりの小さな package で模しました（`/tmp/tmp.78hETWeDFH`）。1.3.2 は PyPI に無い（実測）ので、版を 1 つずらして模しています。
   - 上限 `<=1.3.0` で lock を作ると hy 1.3.0 になりました。上限を `<=1.3.1` へ上げると、`uv lock --check` は rc=1 で失敗しました。
   - `uv lock` の差分は requires-dist の 1 行だけで、hy は 1.3.0 のまま残りました。その後の check は rc=0 です。
3. **当て物が無いと止まる**
   - Python 3.14.3 で `import hy` の後に `inspect.signature(f, annotation_format=Format.STRING)` を呼びました。
   - Hy 1.3.0 でも 1.3.1 でも 6 秒で打ち切りました（rc=124）。当て物を当てると 0.001 秒で返りました。
   - agora-controllers の origin/main は `.python-version=3.14.3t`・hy 1.3.1・pytest 9.1.1 で、まさにこの組です。
4. **R4 は当て物の依る名を数えない**
   - 実物の `scan-hy-imports` は 131 件を返しました。そのうち `ast_unparse.py`・`doeff_hy/__init__.py`・`light_stack.py` の行は 0 件です。
   - `rewriting_unparse`・`true_unparse`・`_could_be_hy_src` を名指す行も 0 件でした。
5. **上流が名前を保って直しても上書きする**
   - 上流が名前を保ったまま直した形を模すと、doeff の `install()` は True を返し、上流の関数を置き換えました。

**観測すべき結果:** 上げる便では R4・R6 と test-changed で選ばれる検がすべて緑のまま、消費者の `uv lock --check` も lock を更新した後は緑になります。それでも agora-controllers が doeff-min-commit を上げた後、Hy 1.3.1 の上の pytest が止まらなくなります。止める検はどこにもありません。

## 5. 未確認の前提・不足する情報

- **推測:** 上流が 1.3.2 でこの欠陥を直すこと、直し方（名前を変えるか、保ったまま直すか）、doeff の全部の検が 1.3.2 で緑になること。上流への報告も 2026-09-23 の時点で未提出です。
- **未確認:**
  - 消費者の検に、この欠陥を引き起こす形（`from __future__ import annotations` の無い conftest に Callable の注記の fixture）が在るか。
  - merge-queue・code-quality・custody・agent-commons が実際にどの Python で動くか（`.python-version` が無い。欠陥は 3.14 以上だけで起きる）。
  - 宣言上の下限である Hy 1.2.0 での振る舞い。
  - Hy 1.3.0 で doeff の全部の検が通るか。
- **読んだだけ（実行していない）:** M-select の選び方は契約の表を読んでの判断で、`make test-changed` は走らせていません。消費者の解決は代わりの package で模したもので、実物の doeff-hy（Rust の組み立てが要る）では測っていません。
- **意図が分からない点:** 設計が M-cap の「範囲」に下限の正しさまで含めるつもりだったかは、入力からは分かりません。

WAIT: work — 依頼元の次の指示
