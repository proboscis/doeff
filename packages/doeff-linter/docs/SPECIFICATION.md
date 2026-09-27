# doeff-linter 層の規則とエディタ向けの出力 — 仕様

この文書は、層の規則 DOEFF101〜108 と `--output-format editor-json` の決まりを書く。規則の判定の正本は doeff-linter で、
エディタ(doeff-runner)はこの出力を表示するだけで、自分では判定しない。

## 1. 呼び出し

| 呼び出し | 意味 |
|---|---|
| `doeff-linter --output-format editor-json [<path>…]` | repo 全体を判じる。path を渡すと、その下の file の違反と module だけを出す(判定は全体で行う) |
| `doeff-linter --output-format editor-json --stdin --path <file>` | stdin の内容を `<file>` として判じる(保存前の内容)。出すのはその file の違反と module だけ |
| `--config <file>` | 設定 file。`[tool.doeff-linter]` を持つ pyproject.toml の形でも、節の中身だけの TOML でもよい |
| `--root <dir>` | repo の根。層の置き場・登録簿・鍵の path はここからの相対 |

- 設定を探す順: `--config` があればそれ。無ければ今の dir から上へ、`[tool.doeff-linter]` を持つ pyproject.toml を探す。
- repo の根: `--root` があればそれ。無ければ見つけた pyproject.toml の dir。`--config` を渡した時は今の dir。
- `--stdin` と `--path` は editor-json の時だけ使える。`--stdin` に `--path` が無ければ終了コード 2。

### 終了コード

| コード | 意味 |
|---|---|
| 0 | error の違反が無い(warning と info はあってもよい) |
| 1 | error の違反がある(登録簿に無い新しい破れ) |
| 2 | 引数の誤り・設定が読めない・設定の名前の食い違い(理由は stderr) |

`--modified`(text・json)の時は、層の規則の違反も変更した file の物だけを出す。

登録簿に載った既知の破れ(warning)と照合中の規則の違反(info)では 1 にしない。CI や agent の hook で「新しい破れだけを止める」
ためである(この決定は戻せる。戻すなら `EditorReport::has_errors` を違反の有無に替える)。

## 2. editor-json の形(版 1)

```jsonc
{
  "version": 1,
  "root": "/abs/repo",                       // 正規化した repo の根
  "violations": [
    {
      "rule": "DOEFF101",                    // 規則の ID
      "law": "core-imports-only-intent",     // 設定で結びつけた law の名(無ければ null)
      "adr": "ADR-…",                        // law の ADR(無ければ null)
      "severity": "error",                   // error | warning | info
      "path": "/abs/repo/controllers/core/goal.hy",
      "range": {"start": {"line": 12, "character": 8}, "end": {"line": 12, "character": 40}},
      "message": "…",                        // 何が破れか(日本語)
      "hint": "…",                           // 直し方の 1 行
      "key": "controllers/core/goal.hy::core-imports-only-intent::controllers.foundation.records",  // 層の規則だけ。Python の規則は null
      "registered": false                    // 登録簿に載っているか
    }
  ],
  "modules": [                               // 層の母集団の module(地図の材料)
    {"path": "/abs/…/goal.hy", "layer": "core", "context": "kanban", "role": "program", "violations": 1}
  ],
  "rules": [                                 // 走らせた規則と、針の無い law
    {"rule": "DOEFF101", "adr": "ADR-…", "statement": "core-imports-only-intent: …", "wired": true}
  ],
  "errors": []                               // 読めなかった file・登録簿・目録の理由
}
```

- 位置は 0 始まりの行と UTF-16 の code unit の列(VS Code の Position と同じ)。日本語や絵文字を含む行でも列は UTF-16 で数える。
- 行だけの規則(DOEFF001〜031)は、違反の文の頭から行末までを範囲にする。
- `path` は正規化した絶対の path(symlink と `..` を解いた物)。repo の根の中の file は、全体の実行でも `--stdin` の実行でも `root` と repo の根からの path をつないだ物になり、エディタは同じ path で結果を差し替えられる。path の引数で絞る時も同じ正規化で比べる。
- `modules[].context`・`role` は module の頭のタグ、無ければ最初の定義のタグ。層の母集団の外の file は `modules` に出ない。
- `rules`: 有効な規則ごとに、結びつけた law があれば law ごとに 1 件(`statement` = `<law の名>: <law の文>`)、無ければ規則の文で 1 件。
  設定の節が無い層の規則は `wired: false`(違反を出さない)。`rules` の空な law(針の無い law)は `rule` に law の名を入れて `wired: false`。

## 3. 設定

節ごとの欄は README の「層の規則」の例のとおり。読む時に次を検め、1 つでも食い違えば終了コード 2 と理由の列を出す:
順に 2 度出る層・`order` に無い層の名(`paths`・`allow_imports` の鍵と値・`forbid_modules`・`types_only`・`roles.by_layer`・
`raw_side_effects.allowed_layers`・`laws[].layers`)・`roles.names` に無い role・層の規則でない `registry.reconciling`・doeff-linter に無い `laws[].rules` の ID・層を問わない規則(DOEFF108)だけの law の `layers`・空か `.` か絶対 path か `..` を含む層の置き場・入れ子の層の置き場・`layers` の無い `roles` と `tags`・`[tool.doeff-linter]` の直下と各節の知らない欄。

| 節 | 欄 | 既定 |
|---|---|---|
| `layers` | `order`・`paths`(層 → dir)・`exclude`(dir や file の名の完全一致)・`extensions` | extensions = hy・hyk・hyp・py |
| `layers.allow_imports` | 層 → import してよい層 | 書かない層は制限しない |
| `layers.forbid_modules` | 層 → 直に import しない module の一番上の綴り | なし |
| `layers` | `types_only`(層の名)・`function_definers` | defk・deff・defp・defpp・defhandler・defn |
| `tags` | `module_variable_hy`・`module_variable_py`・`contract_definers`・`plain_definers`・`effect_definers` | MODULE-TAGS・MODULE_TAGS・defk deff defp defpp defhandler・defn defclass defrecord defenum・defeffect |
| `roles` | `names`・`by_layer` | by_layer の無い層は DOEFF105 を当てない |
| `raw_side_effects` | `allowed_layers`・`catalog_extra`(hy-index の `--raw-catalog-extra` と同じ形の JSON) | — |
| `environment_names` | `words`・`paths`・`exclude`・`exclude_parts`・`extensions`・`assembly_files` | extensions = hy・hyk・hyp・py |
| `laws`(配列) | `name`・`adr`・`statement`・`rules`・`layers` | layers が空なら全部の層 |
| `registry` | `dirs`(1 鍵 1 file の dir)・`files`(1 行 1 鍵)・`reconciling` | — |

`enable`・`disable` は Python の規則と層の規則の両方に効く(`ALL` は両方を含む)。

## 4. 母集団

- **層の母集団**: 各層の dir の下の file で、拡張子が `layers.extensions` に在り、path の区切りのどれも `layers.exclude` に無い物。
  module の綴りは repo の根からの path の拡張子を外し `/` を `.` にした物(`__init__` もそのまま)。
- **業務の file**(DOEFF108): `environment_names.paths` のどれかの下(末尾 `*` は path の前方一致)で、`exclude` の下になく、
  区切りのどれも `exclude_parts` に無く、拡張子が `extensions` に在る物。

## 5. 規則

鍵は `<repo の根からの path>::<段>[::<細目>]`。`<段>` は規則とその file の層に結びつけた law の名、無ければ規則の ID。

| 規則 | 判じ方 | 細目 | 位置 |
|---|---|---|---|
| DOEFF101 | import の先を母集団の module(その物か、`module.名` の module)へ解き、自分以外で `allow_imports` の外の層なら破れ。母集団の外(doeff・標準の library)は数えない。同じ先の import は 1 件 | import の先の綴り | 最初の import の記号 |
| DOEFF102 | import の先の一番上の綴りが `forbid_modules` に在れば破れ。綴りごとに 1 件 | 一番上の綴り | 最初の import の記号 |
| DOEFF103 | `types_only` の層の module に関数の定義(Hy は `function_definers` の頭の最上位の式、Python は最上位の def)があれば 1 件 | `definitions` | 最初の関数の名 |
| DOEFF104 | タグの無い定義があって module の頭のタグも無い、または定義が 1 つも無く頭のタグも無い | なし | 最初のタグの無い定義の名 / 1 行目 |
| DOEFF105 | 実効のタグごとに、role か context が無いか空、または role が `roles.by_layer` の外なら破れ(1 module に同じ鍵が何度も出ることがある) | role(無ければ `None`) | タグの辞書 |
| DOEFF106 | hy-index 版 3 の定義ごとの直接の証拠(raw.direct)が、`allowed_layers` の外の層の Hy の定義に在れば破れ。強い証拠は error、弱い証拠は warning。入れ子で重なる証拠は内側の定義に 1 度 | `<定義>::<証拠の名>` | 証拠の記号 |
| DOEFF107 | 経由の証拠(raw.via — 全体の実行だけ)を info で出す。経路つき。1 定義で経路と証拠の名が同じ物は 1 件 | `<定義>::via::<経路>::<証拠の名>` | 定義の名 |
| DOEFF108 | 業務の file の名(拡張子を外した名)と、Hy の最上位の handler(defhandler と `[effect k]` を受ける関数)・`assembly_files` の最上位の定義の名を `-`・`_`・`.` で切り、`words` に当たれば破れ。大文字だけの名(定数)は見ない | なし(file の名)/ mangle した定義の名 | 1 行目 / 定義の名 |

### タグの読み方(Hy)

- module の頭のタグ: 最上位の `(setv|val MODULE-TAGS {…})`(3 要素ちょうど)の最初の 1 つ。
- 定義のタグ: `contract_definers` の頭の最上位の式で、名から 4 つ目までの要素を見て、文字列と `[…]` を飛ばした最初の辞書の
  `:tags {…}`。`effect_definers` の頭の式は鍵と値の並びの `:tags {…}`。`plain_definers` の頭の式はタグ無しに数える。
- 辞書は、文字列の値を持つ keyword の鍵が 1 つ以上ある時だけ「名乗った」とみなす(空の辞書は名乗っていない)。
- 同じ名の定義が 2 つあれば、後の物のタグで前の物を上書きする。
- 実効のタグ = 定義のタグ全部 + (タグの無い定義がある、または定義のタグが 1 つも無い時の)module の頭のタグ。
- import の読み方: 最上位だけでなく関数の中・quote の中の `(import …)` も数える。`(import m [a b])` は `m.a`・`m.b`、
  `[a :as b]` は `m.a` と `m.b` の両方、`(import m :as x)` は `m`。相対の綴りは module の綴りから解く。名は Hy の mangle で直す。

### タグの読み方(Python)

最上位の `MODULE_TAGS = {…}`(鍵と値がどちらも定数の組)を module の頭のタグとする。定義ごとのタグは無い。import は関数・class・
if・try などの中も数え、`from m import a` は `m.a`、`import a.b` は `a.b`。

## 6. 性能

- 全体の実行は、DOEFF107 が有効なら repo の根の Hy の全部を hy-index で索引する(経由の証拠が file をまたぐため)。
  無効なら判じる file だけを索引する。
- `--stdin` の 1 file の実行は、層の dir の file の一覧(読まない)と、その 1 file の解析だけで判じる。経由の証拠は出さない。
- 実測(agora-controllers の本線・2026-09-27): 全体 約 1.1 秒・1 file 10 ミリ秒未満。

## 7. 既存の判定との照合

agora-controllers の `scripts/module_tags.hy` の `breaches-of` と、DOEFF101〜105 の鍵の集合(重複の数を含む)が一致することを、
本線 46bbb24b(23 module・11 件)と c3d41111(18 module・15 件)で確かめた。DOEFF108 は `scripts/check_business_fakes.hy` の
`assembly-breaches` の環境の語(到達の解析と、外の世界の effect の表を使う)を名前だけの検査に替えたので、handler が答える effect を
見ない分だけ多く出る(本線で 15 件多く、少ない物は 0 件)。

## 8. 読みの限界

- DOEFF106・107 は Hy だけ(hy-index の事実が Hy だけのため)。module の最上位の式(定義の外)の副作用は数えない。
- DOEFF108 は名前だけで判じ、handler が業務の effect に答えるかは見ない。
- import の先は母集団の module にだけ解く。repo の中でも層の dir の外の module は数えない。
