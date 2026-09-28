# doeff-linter 層の規則とエディタ向けの出力 — 仕様

この文書は、層の規則 DOEFF101〜108 と `--output-format editor-json` の決まりを書く。規則の判定の正本は doeff-linter で、
エディタ(doeff-runner)はこの出力を表示するだけで、自分では判定しない。

## 1. 呼び出し

| 呼び出し | 意味 |
|---|---|
| `doeff-linter --output-format editor-json [<path>…]` | repo 全体を判じる。path を渡すと、その下の file の違反と module だけを出す(判定は全体で行う) |
| `doeff-linter --output-format editor-json --stdin --path <file>` | stdin の内容を `<file>` として判じる(保存前の内容)。出すのはその file の違反と module だけ |
| `doeff-linter --output-format editor-json --semantic <file>` | 保存した `<file>` の定義を Jev に問うて判じる(意味の規則 — 10 節) |
| `doeff-linter --output-format editor-json --stdin --path <file> --semantic --semantic-changed` | stdin の内容の定義のうち、中身が変わった定義(cache に答えの無い定義)だけを Jev に問う。書きかけで読めない定義は問わない(エディタが編集中に打つのが止まった時) |
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
| 2 | 引数の誤り・設定が読めない・設定の名前の食い違い・型の違う値(理由は stderr) |

設定(pyproject の `[tool.doeff-linter]` のどの段でも・architecture.hy)に**この binary の知らない鍵**か、この binary に無い形の正しい
規則の ID(`DOEFF` と 3 桁)がある時は、終了コード 2 で止めない。その鍵(参照)だけを読まずに残りの規則を走らせ、設定の file のその行に
warning の違反 **DOEFF100**(設定の知らない鍵)を出す(agora-redesign #848)。設定は binary より先に進むことがあり(新しい鍵を書いた後、
置き場の binary が本線から組み直されるまでの間)、そこで全体を止めるとエディタの違反の欄が空になり hook も黙るため。書き違いも同じ形で
見える。DOEFF100 は `enable` の一覧に無くても出し、`disable` に名指した時だけ止まる。知っている鍵の正本は設定の struct の定義
(`serde_ignored` が定義に無い鍵を path つきで集める — 鍵の表を手で持たない)。

`--version` は組んだ doeff の commit を名乗る(`doeff-linter 0.2.0 (doeff <commit>)`)。commit は組み立ての env
`DOEFF_LINTER_BUILD_COMMIT`(自動の組み直しが渡す)か、手で組んだ時の git の HEAD(linter か indexer の dir に commit していない変更が
在れば `+dirty`)。

`--modified`(text・json)の時は、層の規則の違反も変更した file の物だけを出す。

登録簿に載った既知の破れ(warning)と照合中の規則の違反(info)では 1 にしない。CI や agent の hook で「新しい破れだけを止める」
ためである(この決定は戻せる。戻すなら `EditorReport::has_errors` を違反の有無に替える)。

## 2. editor-json の形(版 2)

```jsonc
{
  "version": 2,
  "linter": {"version": "0.2.0", "commit": "<sha>"},  // この出力を作った binary と組んだ doeff の commit(版 1 への追加の欄)
  "root": "/abs/repo",                       // 正規化した repo の根
  "layers": [                                // 層の順(外の世界から遠い順)と説明 — 設定 layers.describe から(無い欄は null)
    {"name": "core", "summary": "業務の判断と Program", "knows": "…", "does_not_know": "…", "question": "迷った時の問い"}
  ],
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
      "registered": false,                   // 登録簿に載っているか
      "explanation": {                       // 層の規則だけ(Python の規則は null)
        "subject": "これは何か(import 先とその層・定義と kind・file の層と、それを何で決めたか)",
        "reason": "なぜ違反か(層の説明と規則を結ぶ文)",
        "law_statement": "結びつけた law の :statement の逐語 or null"
      }
    }
  ],
  "modules": [                               // 層の母集団の module(地図の材料)
    {"path": "/abs/…/goal.hy", "layer": "core", "context": "kanban", "role": "program", "violations": 1,
     "service": "kanban",                     // 置き場の `*` の段に当たった service(層が先の形なら null)
     "layer_reason": "path の置き場所で決めた — controllers/core/ の下は層 core(…)。タグの role = program もこの層の役"}
  ],
  "rules": [                                 // 走らせた規則と、針の無い law
    {"rule": "DOEFF101", "adr": "ADR-…", "statement": "core-imports-only-intent: …", "wired": true,
     "title": "層の向きに逆らう import",      // 短い日本語の名(違反の形)
     "family": "layer"}                       // 規則の家族(layer・tags・raw・naming・place・definition・class・wire・smell・jev・python・law)
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
  `title` は短い日本語の名(違反の形。針の無い law は「自動の判定がまだ無い決まり」)。`family` は規則の家族
  (`layer`・`tags`・`raw`・`naming`・`place`・`definition`・`class`・`wire`・`smell`・`jev`・`python`・`law` の閉じた集合。
  Python の文ごとの規則(DOEFF001〜031・NOQA001・知らない ID)は `python`、針の無い law は `law`)— エディタが規則の一覧を
  束ねて見せる時に使う。名と家族の判定は linter が持ち、エディタは写しを持たない。

- `signatures`・`bindings`(版 2): `--stdin` の Hy の file の defk / deff の見出しと束縛の型。全体の実行では空の列。形と読み方は 16 節。
- `rewrites`(版 2 への欄の追加): `--stdin` の Hy の file の、定義の本体の呼びを `f(a, b)` の形で見せる表示の置き換え。全体の実行では空の列。17 節。

### 説明の文(explanation・layer_reason)

- 文の雛形は規則ごとに `src/project/explain.rs` の 1 か所だけにあり、層の名前・置き場所・層の説明(`layers.describe`)・
  role の説明(`roles.describe`)・生の副作用を許す層・law の文を設定から差し込む。Rust に層の意味は書かない。
- 層の説明が無い層は、層の順(「外の世界から最も遠い層」「外の世界からの遠さの順で 2 番目の層」…)と規則の決まりだけで文を作る。
- `layer_reason`: 層は path の置き場所で決める。タグの role がその層で許されない時は「path とタグが食い違う」と書き、その role を
  許す層(無ければ「どの層の役でもない」)を添える。DOEFF105 の subject にも同じ食い違いを書く。
- 人が読む出力(text)と agent の hook の文にも、層の規則の違反ごとに「これは」「なぜ」「law」「直し方」「鍵」の行を出す。

## 3. 設定

節ごとの欄は README の「層の規則」の例のとおり。読む時に次を検め、1 つでも食い違えば終了コード 2 と理由の列を出す:
順に 2 度出る層・`order` に無い層の名(`paths`・`allow_imports` の鍵と値・`forbid_modules`・`types_only`・`roles.by_layer`・
`raw_side_effects.allowed_layers`・`laws[].layers`)・`roles.names` に無い role・層の規則でない `registry.reconciling`・doeff-linter に無い `laws[].rules` の ID・層を問わない規則(DOEFF108)だけの law の `layers`・空か `.` か絶対 path か `..` を含む層の置き場・入れ子の層の置き場・`layers` の無い `roles` と `tags`・`[tool.doeff-linter]` の直下と各節の知らない欄。

| 節 | 欄 | 既定 |
|---|---|---|
| `layers` | `order`・`paths`(層 → dir)・`exclude`(dir や file の名の完全一致)・`extensions` | extensions = hy・hyk・hyp・py |
| `layers.allow_imports` | 層 → import してよい層 | 書かない層は制限しない |
| `layers.forbid_modules` | 層 → 直に import しない module の綴り(前方一致 — `urllib.request` は `urllib.request.urlopen` に当たり `urllib.parse` には当たらない) | なし |
| `layers` | `types_only`(層の名)・`function_definers` | defk・deff・defp・defpp・defhandler・defn |
| `tags` | `module_variable_hy`・`module_variable_py`・`contract_definers`・`plain_definers`・`effect_definers`・`record_definers` | MODULE-TAGS・MODULE_TAGS・defk deff defp defpp defhandler・defn defclass defenum・defeffect・defrecord defwire(頭の辞書の `:tags` を定義のタグとして読む) |
| `roles` | `names`・`by_layer`・`describe`(role → 説明。一覧から外した古い役も書ける) | by_layer の無い層は DOEFF105 を当てない |
| `layers.describe.<層>` | `summary`・`knows`・`does_not_know`・`question` | 無ければ説明の欄は null |
| `raw_side_effects` | `allowed_layers`・`catalog_extra`(hy-index の `--raw-catalog-extra` と同じ形の JSON) | — |
| `environment_names` | `words`・`paths`・`exclude`・`exclude_parts`・`extensions`・`assembly_files` | extensions = hy・hyk・hyp・py |
| `laws`(配列) | `name`・`adr`・`statement`・`rules`・`layers` | layers が空なら全部の層 |
| `registry` | `dirs`(1 鍵 1 file の dir)・`files`(1 行 1 鍵)・`config_files`(1 行 1 鍵・設定 file の dir からの相対)・`reconciling` | dirs と files は repo の根から |
| `rules.<ID>` | `registered_severity`(登録簿に載った破れの重さ: error・warning・info) | warning |

`enable`・`disable` は Python の規則と層の規則の両方に効く(`ALL` は両方を含む)。

## 4. 母集団

- **置き場のパターン**: `layers.paths` の値は綴りか綴りの列。段 `*` は service の名に当たる(1 つまで・段まるごと)。file が 2 つの置き場に
  当たる時は段の多い方、同じなら層の順の先の方。
- **定義の規則の母集団**(DOEFF110〜112): `definitions.paths` の下(空なら repo の Hy の全部 — `.venv`・`node_modules`・`target`・`.git` は降りない)で、
  `definitions.exclude` の下でなく、区切りが `definitions.exclude_parts` に無い Hy の file。

- **層の母集団**: 各層の dir の下の file で、拡張子が `layers.extensions` に在り、path の区切りのどれも `layers.exclude` に無い物。
  module の綴りは repo の根からの path の拡張子を外し `/` を `.` にした物(`__init__` もそのまま)。
- **業務の file**(DOEFF108): `environment_names.paths` のどれかの下(末尾 `*` は path の前方一致)で、`exclude` の下になく、
  区切りのどれも `exclude_parts` に無く、拡張子が `extensions` に在る物。

## 5. 規則

鍵は `<repo の根からの path>::<段>[::<細目>]`。`<段>` は規則とその file の層に結びつけた law の名、無ければ規則の ID。

| 規則 | 判じ方 | 細目 | 位置 |
|---|---|---|---|
| DOEFF101 | import の先を母集団の module(その物か、`module.名` の module)へ解き、自分以外で `allow_imports` の外の層なら破れ。母集団の外(doeff・標準の library)は数えない。同じ先の import は 1 件 | import の先の綴り | 最初の import の記号 |
| DOEFF102 | import の先の綴りが `forbid_modules` のどれかと同じか、その下位の module / 名なら破れ(名の順に照らして最初に当たった物)。当たった module ごとに 1 件 | 当たった module の綴り | 最初の import の記号 |
| DOEFF103 | `types_only` の層の module に関数の定義(Hy は `function_definers` の頭の最上位の式、Python は最上位の def)があれば 1 件 | `definitions` | 最初の関数の名 |
| DOEFF104 | タグの無い定義があって module の頭のタグも無い、または定義が 1 つも無く頭のタグも無い | なし | 最初のタグの無い定義の名 / 1 行目 |
| DOEFF105 | 実効のタグごとに、role か context が無いか空、または role が `roles.by_layer` の外なら破れ(1 module に同じ鍵が何度も出ることがある) | role(無ければ `None`) | タグの辞書 |
| DOEFF106 | hy-index 版 3 の定義ごとの直接の証拠(raw.direct)が、`allowed_layers` の外の層の Hy の定義に在れば破れ。強い証拠は error、弱い証拠は warning。入れ子で重なる証拠は内側の定義に 1 度 | `<定義>::<証拠の名>` | 証拠の記号 |
| DOEFF107 | 経由の証拠(raw.via — 全体の実行だけ)を info で出す。経路つき。1 定義で経路と証拠の名が同じ物は 1 件 | `<定義>::via::<経路>::<証拠の名>` | 定義の名 |
| DOEFF109 | service を持つ file(置き場の `*` に当たった物)の層が `services.guarded_layers` に在り、import の先が別の service の守る層の module なら破れ。先が共有の置き場・`open_layers` の層・例外の組なら許す | import の先の綴り | 最初の import の記号 |
| DOEFF110 | Hy の `defn` / `defn/a` の定義(decorator つきも)。`do` の中も最上位として見る。`eval-and-compile` / `eval-when-compile` の中は外 | 定義の名 | 定義の名 |
| DOEFF111 | `deff` の定義の行か直前の行の註(`;` の後)に `definitions.deff_reason_marker` が無い、または理由が空・「同上」とその変形 | 定義の名 | 定義の名 |
| DOEFF119 | 業務の Hy の file の defclass を中身の証拠で分ける(12 節) | class の名 | class の名 |
| DOEFF120 | architecture.hy の在る repo の Hy と Python の module が JsonValue・JSONValue・JsonObject・JSONObject を使い、許された module(汎用の解き手・`:wire-modules` に挙げた foundation の module)でない(13 節)。module ごとに 1 件 | なし | 最初の使用 |
| DOEFF118 | `definitions.test_paths` に当たる file の、名が `test_` で始まる defn・defn/a・deff・defk・fn の束縛 | 定義の名 | 定義の名 |
| DOEFF112 | `tags.require_on` の頭の定義の :tags(defeffect は辞書の :tags)に `tags.required` の鍵(空でない文字列)が無い。`module_default` なら module の頭のタグの鍵で補う | 定義の名 | 定義の名 |
| DOEFF113 | service を持つ file のタグの :context が service の名と違う(`-` と `_` は同じに見る・共有の置き場は見ない)。info | 食い違う :context | タグの辞書 |
| DOEFF108 | 業務の file の名(拡張子を外した名)と、Hy の最上位の handler(defhandler と `[effect k]` を受ける関数)・`assembly_files` の最上位の定義の名を `-`・`_`・`.` で切り、`words` に当たれば破れ。大文字だけの名(定数)は見ない | なし(file の名)/ mangle した定義の名 | 1 行目 / 定義の名 |

### タグの読み方(Hy)

- module の頭のタグ: 最上位の `(setv|val MODULE-TAGS {…})`(3 要素ちょうど)の最初の 1 つ。
- 定義のタグ: `contract_definers` の頭の最上位の式で、名から 4 つ目までの要素を見て、文字列と `[…]` を飛ばした最初の辞書の
  `:tags {…}`(辞書の中は鍵と値の組で読む)。`effect_definers`(既定 defeffect)の頭の式は `(defeffect 名 "doc"? {:fields […] :answer 型 :tags {…}})` の形で、名から 2 つ目までのうち docstring を飛ばした最初の辞書の `:tags`。鍵と値を並べただけの古い形はタグとして読まない。`plain_definers` の頭の式はタグ無しに数える。
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

## 9. architecture.hy — service と層の唯一の宣言

repo の一番上の `architecture.hy`(または設定の `architecture = "<設定 file からの相対の path>"`)が、service と層を宣言する唯一の場所。
doeff-linter はこれを **実行せずに** doeff-indexer の Hy の読み取り器で読む。在れば層・role は ここから写し、TOML の
`[tool.doeff-linter.layers]`・`roles`・`services` は置けない(二重の宣言は設定の誤り)。TOML には規則の入り切り・重さ・登録簿の置き場所・
law の対応だけを残す。無ければ TOML の設定で今どおり動く。doeff-hy の実行時の macro(defarchitecture・defservice・layer)は未実装。

```hy
(defarchitecture agora-controllers
  :root "controllers"                         ; service の dir を置く根
  :layers [(layer core :summary "…" :knows "…" :does-not-know "…" :question "…"
                       :roles [type judgment program] :imports [core intent] :forbid-modules ["httpx"])
           (layer intent … :types-only True)
           (layer protocol …) (layer foundation …)
           (layer entry … :dependency-layers [intent protocol])]   ; 外の世界から遠い順。:dependency-layers = 依存先で読んでよい層(既定 :open-layers)
  :shared "shared"                            ; root/shared/<層>/ — どの service からも読める
  :foundation foundation                      ; root/foundation/ — service の外の層(同じ名の layer が要る)
  :open-layers [intent]                       ; 別の service から読んでよい層(既定 intent)
  :roles {:judgment "業務の判断をする純粋な関数" …}   ; role の説明
  :wire-modules ["controllers.foundation.records_client"]  ; JSON の送受信そのものを行う foundation の module(DOEFF120・13 節)
  :exclude ["tests" "__pycache__" "conftest.py"]    ; 既定のまま
  :extensions ["hy" "py"]                           ; 既定 hy・hyk・hyp・py
  :shared "shared")  ; :legacy は廃止(書くと設定の誤り — 宣言の外の module は全部 DOEFF114・115、既存の分は登録簿)
(defservice land-notice "着地の報せ" {:depends-on [messaging] :layers [core intent protocol entry]})
```

- 層の置き場は `root/*/<層>`(service と shared)と、foundation の層は `root/<foundation>`。
- 宣言の外でも、root の下の段に層の名がある dir(層が先の dir — `controllers/core/…`)の module は、:role のタグから層を推して層の規則をかける
  (role を許す層が 1 つならその層・2 つ以上なら path の段の層・推せなければ path の段の層に置いてタグの規則 DOEFF104・105 が理由を出す)。
  module の `layer_reason` は「タグで決めた — …」。旧い機能の dir(層の名の段が無い)は層の規則の母集団に入らない。
- operator 2026-09-27 逐語 "we dont want 'legacy' stuff. we want anything all flagged" — `:legacy` は廃止。
- service の dir は名の `-` を `_` にした物(`land-notice` → `controllers/land_notice/`)。`{:dir "…"}` で変えられる。
- 読み違い(知らない鍵・重複した service や層・存在しない層や service の名・:foundation の層が無い)は `architecture.hy:行:列: 理由` の形で
  設定の誤り(終了コード 2)。
- editor-json の最上位に `architecture`(name・root・layers(name・summary・knows・does_not_know・question・roles)・shared・foundation・
  open_layers・services(name・dir・description・depends_on・layers))。無ければ null。

| 規則 | 判じ方 | 鍵の細目 | 位置 |
|---|---|---|---|
| DOEFF114 | root の下の module が、宣言した service の宣言した層・shared の層・foundation のどれにも入らない(root の直下・service の dir の直下・宣言に無い dir の中 — 層が先の dir も旧い機能の dir も例外なし)。file ごとに 1 件。hint に移し先の案(`<root>/<:context のタグ>/<path の段の層か :role のタグの層>/<名>`)。`__init__` は外 | なし | file の頭 |
| DOEFF115 | root の直下の dir が宣言した service・shared・foundation でない / service の中の dir が宣言した層でない。dir ごとに 1 件(鍵の path は dir) | なし | dir の最初の file の頭 |
| DOEFF116 | service A の module が service B の module を import した時、B が A の :depends-on に無い、または読む先が、A の module の層が依存先で読んでよい層(その層の `:dependency-layers`、無ければ `:open-layers`)でない。組み立ての層(agora は entry)だけ `:dependency-layers [intent protocol]` で依存先の翻訳の handler も読める(operator 2026-09-28 "A okay")。shared と foundation は service ではないので見ない。宣言の DOEFF109 はこれに置き換わる(architecture.hy の在る repo では DOEFF109 の設定を置けない) | import の先 | import の記号 |
| DOEFF117 | 宣言した依存(A の :depends-on の B)を、A のどの module も読んでいない。info。全体の実行だけ | `A>B` | architecture.hy の defservice の名 |
| DOEFF113 | 宣言した service の中の :context の食い違いは warning に上がる | | |

### 既存の道具との対応

| 考え方 | doeff-linter(architecture.hy) | Tach | import-linter | Nx | Deptrac / ArchUnit |
|---|---|---|---|---|---|
| 部品の宣言 | `defservice`(dir = service) | `tach.toml` の `modules` | 契約の `containers` | project | layer / slice の定義 |
| 部品の間の依存 | `:depends-on`(DOEFF116) | `depends_on` | `independence`・`forbidden` の契約 | タグの `depConstraints` | ruleset / `slices().should().notDependOnEachOther()` |
| 公開する口 | `:open-layers`(intent の層) | `interfaces` | — | 公開の entry point | — |
| 部品の中の層 | `:layers` と layer の `:imports`(DOEFF101) | `layers` | `layers` の契約 | タグの `onlyDependOnLibsWithTags` | layer の ruleset / `layeredArchitecture()` |
| 宣言に無い物 | DOEFF114・115 | —(module に入らない file は対象外) | — | — | Deptrac の未分類(uncovered)の報告 |
| 使っていない依存 | DOEFF117 | `tach check --exact`(使っていない depends_on) | — | — | — |
| 既知の破れの固定 | 登録簿(`registry`)と `registered_severity` | 除外の設定 | `ignore_imports` | — | baseline / `FreezingArchRule` |

intent の層は Tach の interfaces に当たる — 別の service が読んでよいのは、その service が外へ出す要求と答えの型(intent)だけ。

## 10. 意味の規則(DOEFF201・202 — Jev)

決定的な規則では読めない「コードが何をしているか」を、Jev(TypeSafe の System One の model)に Noul(確率)の問いで問う。

| 規則 | 問い(英語のまま・`src/project/semantic.rs` の 1 か所) | 当てる層(設定) | 既定の閾値 |
|---|---|---|---|
| DOEFF201 | 要求を相手の話し方へ言い換えるのを越えて、業務の判断(誰に許すか・業務の決まり・宛先・業務の結果)をしているか(jev-lint の J2) | `semantic.business_decision.layers` | warning p ≥ 0.8・info p ≥ 0.6 |
| DOEFF202 | 通信の手段(URL や query・HTTP の method や status・JSON の wire・SQL・宛先の address)を知っているか(jev-lint の J3) | `semantic.transport_knowledge.layers` | warning p ≥ 0.6・info p ≥ 0.4 |

- **撃つのは `--semantic`(path の引数の file、`--stdin` なら `--path` の file、無ければ git で変わった file)と `--semantic-all`(設定した層の全定義)の時だけ。**
  `--semantic-changed` を足すと、そのうち手元の cache に答えの無い定義(中身が変わった定義)だけを撃つ(エディタが編集中に打つのが止まった時に使う)。
  決定的な規則の実行は Jev を呼ばない(キーも要らない): エディタの 1 file(`--stdin`)は cache を読むだけ、全体の実行(hook・text / json)は cache を読み、
  代理が設定されていれば cache に無い定義を代理に「覚えている時だけ」問う(下の「Jev の呼び出しを覚える代理」)。
- **書きかけで読めない定義(閉じない括弧・対応しない閉じ括弧・閉じない文字列)は、どの実行でも問わない**(未判定に数える)。
- 問う定義 = 設定した層の Hy の最上位の defn・defk・deff・defp・defpp・defhandler・defeffect・defclass・defrecord・defenum。
- state = 定義の名・kind・file・申告の `:tags` を消した source(`semantic.source_limit` 字 = 既定 1,800 で切る)・置かれた層の説明(architecture.hy の layer の説明か、
  `layers.describe`)。
- cache = repo の根の `.doeff-linter/semantic-cache/<鍵>.json`(git の外に置く — `.gitignore` に足すかは repo ごと)。鍵 = sha256(model・問いの JSON・層の説明・
  タグを消した source)。申告の役は鍵に入れず、判定の後にコードで比べる。cache の答えが無い定義は違反にせず、最上位の `semantic.unjudged` に数える(合格に倒さない)。
- 重さは warning か info だけ(当たり外れを測り終えるまで error にしない — 設定にも error の欄は無い)。外れは登録簿に載せる。
- 宛先・model・キーは doeff の `packages/doeff-jev/src/doeff_jev/target.py` と同じ決め方(Rust に写した — 決め方は 1 つ):
  環境変数 `JEV_BASE_URL` / `JEV_MODEL` / `JEV_WIRE` / `JEV_API_KEY` / `JEV_API_KEY_FILE` → 設定 file `~/.config/jev/client.json` → 既定 = TypeSafe 直
  (`https://api.typesafe.ai/v1/systemone`・model `jev-latest`・キーは `TYPESAFE_API_KEY` → `~/.config/jev/api_key`)。gateway は名指した時だけ。
  通信の形(direct と gateway の要求と答え)は同じ package の `wire.py` の写し。キーの値は設定・出力・log・cache の鍵に書かない。
- 同時に `semantic.workers`(既定 8)本・時間切れ `semantic.timeout_seconds`(既定 30)・429 と 5xx は 3 回まで撃ち直す。撃てなかった定義は理由を `errors` に積み、未判定に数える。
- **較正の見張り**: 撃つ実行ごとに、同梱の正例と反例(`data/semantic_calibration.json` — jev-lint の札の際どくない物、問いごとに 1 つずつ)を 1 回問い、正例 p ≥ 0.8・反例 p ≤ 0.2 の
  幅から外れたら cache を捨てて `errors` に警告を出す(model の中身が変わった疑い)。答えた model の版つきの名は `semantic.served_model` に出す(direct の答えだけが返す)。

設定:

```toml
[tool.doeff-linter.semantic]
business_decision = { layers = ["protocol"], warning = 0.8, info = 0.6 }
transport_knowledge = { layers = ["core"], warning = 0.6, info = 0.4 }
workers = 8
timeout_seconds = 30
source_limit = 1800
# Jev の呼び出しを覚える代理(doeff の packages/doeff-jev-proxy)— 無ければ使わない
proxy_url = "http://jev-proxy.example:8878/v1/systemone"
proxy_token_file = "~/.config/jev/proxy-token"   # 既定
proxy_peek_timeout_ms = 5000                     # 既定(覚えている時だけの問いの束を全部合わせた上限)
```

**Jev の呼び出しを覚える代理**: 本文(state・問い・model)を正規化した sha256 で答えを覚え、初めての鍵だけを本物の Jev に渡す server
(doeff の `packages/doeff-jev-proxy`・口は TypeSafe の `/v1/systemone` と同じ)。

- 宛先は repo ごとの設定 `proxy_url` で向ける(機体全体の環境変数にはしない — 向けない repo は今までどおり)。環境変数 `JEV_BASE_URL` が在ればそちらが勝つ。
- 代理へは代理の token(`proxy_token_file` の中身)だけを送る。TypeSafe のキーは送らない。token の file が無ければ撃たない(キーが無いのと同じ理由を出す)。
- 全体の実行・hook は、手元の cache に無い読める定義を、代理の鍵の束で「覚えている時だけ」問う(`POST <proxy_url>/peek`・本文
  `{"keys": [鍵 …]}`・1,000 個ずつの束を並べて撃つ — 定義 1 つずつ撃たず、本文も送らない。代理は覚えている答えだけを
  `{"answers": {鍵: 答え}}` で返し、本物の Jev を呼ばない)。返った答えを手元の cache に書く。代理に届かない・時間切れの時は、残りの束を撃たず
  手元の cache だけで動く。
- 代理の鍵 = sha256(`jev-proxy-key-1` + 改行 + 本文を決まった綴りにした物 — object の鍵を符号位置の順に並べ・区切りの空白なし・文字は UTF-8 のまま)。
  代理(`key.hy` の `normalize-request`)と linter(`semantic.rs` の `proxy_key`)が同じ鍵を作ることは、代理の見本
  `packages/doeff-jev-proxy/tests/key_contract.json` を両方の検が読んで確かめる(小数は綴りが言語で違うので見本に入れない — linter の本文は小数を持たない)。
- 較正の見張りの問いは `Cache-Control: no-cache`(覚えを使わない — model の中身が変わったことを代理の覚えが隠さないため)。

editor-json: violation の `source`(`linter` = 決定的な規則・`jev` = 意味の判定)と `probability`(Jev の違反だけ)、最上位の `semantic`
(`model`・`wire`・`judged`・`unjudged`・`asked`・`peeked`(代理が覚えていた答えを受け取った数)・`cost_usd`(gateway だけが返す)・`input_tokens`・
`served_model`・`calibration` = not-run / ok / drifted / failed)。`wire` は宛先の形と決め方(例 `direct(default)`・`direct(env)`・`direct(repo)` = repo の代理)。


## 11. 素の関数(deff)の理由と検の書き方 — DOEFF110・111・118・203

operator 裁定 2026-09-27(逐語 "yeah reading config, that's exactly where effects like Ask comes in, no excuse" / "yeah non-deftest must be forbidden" /
"about the reason text, we want jev to tell if it's acceptable right?"): 理由の種類の札は書かせない。受け入れるかは Jev が理由の文と source を見て決める。

architecture.hy の `defarchitecture` に、受け入れる理由と受け入れない理由の型を宣言する(名・説明・直し方は Rust に書かない):

```hy
:plain-callable-reasons [(reason library-callback "外の library が素の関数を決まった形で呼ぶ(sorted の key・dataclass の __post_init__ など)")
                         (reason macro-time "マクロの展開の時に呼ぶ")]
:rejected-plain-callable-reasons [(reason config-read "設定の file・環境変数・引数を読む" :fix "Ask などの effect で設定を受け取る defk にする")
                                  (reason test-helper "検の本体・検の値を組む補助" :fix "deftest にし、補助は defk にして (<- …) で呼ぶ")
                                  (reason handler-assembly "handler の並びを組み立てる" :fix "(defk handlers-of [foundation] …) にする")]
```

- 註の形: `; defk にできない: <自由な理由>`(定義の行か、直前の註だけの行)。`; defk にできない(<種類>): <理由>` も受け付けるが要求しない。
- **DOEFF111**(決定的): 註が無い・理由が空・「同上」とその変形(上と同じ・上に同じ・前と同じ・同前)だけを error。理由の中身は判じない。
  reason に受け入れる理由の一覧を差し込む。
- **DOEFF110**: defn の同じ行の註が一覧の種類を名乗れば hint =「deff にする(理由 X)」、そうでなければ「defk にする」。
- **DOEFF203**(意味・Jev・Choice): 理由の文がある deff ごとに、定義の source(タグを消した物)と理由の文を state にして、受け入れる理由・受け入れない型・none
  から選ばせる(問いの文は `src/project/semantic.rs` の `plain_callable_wire` に英語で 1 か所)。重さ:
  受け入れない答え(型か none)を選び確率が `semantic.plain_callable.warning_min`(既定 0.4)以上 → warning、それ未満 → info。受け入れる理由を選んでも
  受け入れない答えの確率の和が `info_min`(既定 0.4)以上 → info。error にはしない。reason と hint には、選ばれた受け入れない型の `:fix` を出す。
  撃つのは `--semantic` / `--semantic-all` の時だけ。閾値は仮置き(当たり外れを測り終えるまで)。
- **DOEFF118**(検は deftest だけ): `definitions.test_paths` の glob(`**` = 0 個以上の段・`*` = 段の中の任意の綴り・`/` の無い綴りは file の名に当てる)
  に当たる Hy の file で、mangle した名が `test_` で始まる `defn`・`defn/a`・`deff`・`defk` と `(setv 名 (fn …))` / `(val 名 (fn …))` は error。鍵は
  `<path>::DOEFF118::<名>`。登録簿に載れば `registered_severity`。

## 12. class の中身 — DOEFF119・DOEFF204

operator 2026-09-27(逐語 "well, a class like Point2D/Point3D could be a class right? but clients and stores... they are completely suited for
handlers/effects.." / "such distinction could be passed to jev?")。名前では判じず、中身の証拠で分ける。母集団は `definitions` の業務の Hy の file。

- **許す(出さない)**: 基底に例外・Enum・Protocol(名の終わりが Error・Exception・Warning・Enum・Flag・Protocol・NamedTuple・TypedDict)か、
  repo の外の module の class(import の束縛の module の先頭の段が repo の根に無い・束縛の無い裸の名は組み込み)を持つ class。
- **DOEFF119 error — 外の世界に触る**: class の中の定義(method・欄の初期値)か class の式そのものに、hy-index 版 3 の生の副作用の強い証拠
  (raw.direct か raw.via — DOEFF106 と同じ目録)がある。`__init__` で欄に置く資源(`(setv self.http (httpx.Client))`)もここに入る。
  直し方 = 土台の handler(資源は defhandler の直下の `(session val …)`、ListRows・PutRow などの effect に答える)。
- **DOEFF119 warning — 変わる状態を持つ**: `__init__`・`__post_init__`・`__new__` の外の method が self の欄を書き換える(`setv`/`setx` の的が
  `self.x`・`(get self.x k)`・`(. self x)`、`+=` など、`del`、`setattr self`、`(.append self.x …)` などの変える method)。直し方 =「値は defrecord(不変)、
  振る舞いは新しい値を返す純粋な関数、状態は world などの handler の (session var …) 1 か所に置き、変化は effect で流す。速さのために書き換えが要る時も
  書き換えは handler の中だけ」。
- **DOEFF119 info — 欄だけ**: dunder 以外に処理を持つ method が無い(`__post_init__` の検めだけも)。直し方 = defrecord(:tags・:check)。
- 経由の証拠(raw.via)は全体の実行だけが計算する。1 file の実行(エディタの保存)は直接の証拠だけで判じる。
- **DOEFF204(Jev・Choice・warning か info だけ)**: DOEFF119 が何も出さず、dunder 以外に処理を持つ method のある class だけを問う。state = class の
  source(タグを消して `source_limit` で切る)と欄の宣言(`名: 型`)。選択肢 = value / external-world / stateful / other(問いの文は
  `src/project/semantic.rs` の `ClassRole` の 1 か所)。external-world か stateful を `semantic.class_role.warning_min`(既定 0.7)以上で warning、
  `info_min`(既定 0.5)以上で info。較正の見張りは合成の 2 例(client を欄に持つ窓口・Point2D)の external-world の確率を比べる。

## 13. JsonValue の使い場所 — DOEFF120

agora-redesign #840・operator 2026-09-28(逐語 "and i dont think we should make anyone use that directry instead of actually parsing and
validating it like pydantic does")。JsonValue(素の `dict | list | str | int | float | bool | None` を名で包んだだけの型)に触ってよいのは、
汎用の解き手と、送受信そのものを行う foundation の module だけ。ほかの module は型のある値だけを見る。例と設定は [rules/DOEFF120.md](rules/DOEFF120.md)。

- **有効になる条件**: architecture.hy が在る(`project_wired` は architecture の有無)。
- **母集団**: repo の Hy(hy・hyk・hyp)と Python(py)の file の全部。`.` で始まる隠し dir と `node_modules`・`target`・`__pycache__`・`venv`・
  `site-packages` は降りない。4 つの名のどれも本文に無い file は読まない。
- **数える物**: Hy は読み取り器の記号で、最後の `.` の段が `JsonValue`・`JSONValue`・`JsonObject`・`JSONObject` の物(import・定義・注釈・契約・
  tuple の中・quote の中)。文字列・註・docstring・`#_` の form は数えない。Python は字句の名(名前・属性の名・import の名)と、注釈(引数・
  戻り値・`x: T`)と型の別名(`X: TypeAlias = …` の値・`type X = …`)の文字列の中の語(前後が識別子の文字でない物)。docstring・註・f 文字列は数えない。
- **許す module**(`json_value_allowance` — 方針はこの関数 1 つだけにあり、差し替えられる):
  1. module の綴り(repo の根からの path の拡張子を外し `/` を `.` にした物)が `doeff_hy.wire`・`doeff_records.wire` か、`.` + それで終わる。
  2. architecture.hy の `:wire-modules` の pattern(`.` 区切り・`*` は段の中の任意の綴り・`**` は 0 個以上の段)に当たり、かつ層の置き場で
     `:foundation` の層に入る。当たっても foundation の外なら許さない(説明に訳)。
- **違反**: module ごとに 1 件・error。鍵 `<path>::DOEFF120`(細目なし)。位置は最初の使用の名。説明の主体に使った名・数・最初の行・ほかの行
  (20 行まで・残りは数)。登録簿に載れば `registered_severity`。
- **設定の誤り**: `:wire-modules` の要素が module の綴りでない(`/` を含む・空の段・段の中の `**`)・同じ綴りの 2 度書き・`:foundation` の無い宣言。
- **戻し方**: 規則を外す(型に起こした値はそのままで害は無い)。

## 14. 臭いの規則 — DOEFF121〜125・DOEFF205

operator の決定 2026-09-28 未明(逐語 "lets add them")。題材は agora-controllers の controllers/kanban/core/tag_judgment.hy の decide-tag
(決定の時点の linter はこの file に何も出していなかった)。記録は doeff の ADR-DOE-HY-007 R9〜R11。

設定:

```toml
[tool.doeff-linter.smells]
shape_check_layers = ["core"]          # DOEFF121 を当てる層(判断の層)。無ければ DOEFF121 は配線されない
[tool.doeff-linter.rules.DOEFF124]
severity = "info"                      # 臭いの規則の重さ(warning・info — 既定 warning・error は選べない)
[tool.doeff-linter.semantic]
mixed_concerns = { layer = "core", roles = ["judgment", "program"], warning_min = 0.7, info_min = 0.5 }   # DOEFF205
```

| 規則 | 当たる形 | 鍵の細目 | 位置 |
|---|---|---|---|
| DOEFF121 | 判断の層の file の定義で、`(val|var|setv|setx|:=|<- n (.get x "欄"))` の n か `(.get x "欄")` そのものへの `(isinstance … T)` | `<定義>::<欄>` | `.get` の式 |
| DOEFF122 | `(match 主 …)` の腕の pattern の頭の型が失敗の型で、腕の本体(`do` なら最後の式)が `(return E)` で、E が主・pattern で束ねた名・`:as` の名、または腕の中でそれらから作った名(`(<- n T X)` など)を参照する | `<定義>::<主の名>`(主が式なら型の名) | pattern |
| DOEFF123 | 並んだ 2 つの子 `(<- x …)` `(return x)` で、定義の中の x(`x` と `x.欄`)の出現がちょうど 2 | `<定義>::<x>` | `<-` の式 |
| DOEFF124 | 文字列の引数を含む `(+ …)` の、同じ値 v の欄 `v.a`・`(str v.b)`・`(repr v.c)` が 2 つ以上 / f 文字列の `{v.a}…{v.b}` | `<定義>::<v>` | 式 |
| DOEFF125 | for / while の中(内包表記は数えない)の `(:=|setv|setx xs (+ xs #(…)))`・`(… xs (+ xs […]))` | `<定義>::<xs>` | 式 |

- 同じ定義の同じ細目は 1 件だけ出す。母集団は `definitions` の業務の Hy の file(検の置き場も含む — 母集団の設定で決める)。
- **失敗の型**(DOEFF122): repo の Hy の file のうち `:failure` か `:absent` の綴りを含む物を読み、`(defrecord 名 "doc"? {… :failure True …} …)` の名と
  `(defeffect 名 "doc"? {… :failure [A …] :absent [B …]})` の型を集める。名は file の import(`(import m [T])` → `m.T`・`(import m :as n)` の `n.T` →
  `m.T`)と定義の場所(束縛の無い裸の名 → その file の module)で module まで解き、腕の型も同じく解いて比べる — 同じ名の型が別の module にあっても、
  宣言した方だけが失敗の型。宣言が 1 つも無ければ DOEFF122 は何も出さない。
- **重さ**: 既定は warning(ADR-DOE-HY-007 R9 — Absent / Raise の段 1・2 が本線に入ったので info から上げた)。登録簿に載った warning は info。
- **DOEFF205**(Jev・Choice・warning か info だけ): 役(定義の :tags の role・無ければ module の頭のタグ)が `roles` の最上位の関数(defk・deff・
  defn・defp)に、mixed / shape-only / judgment-only / neither から選ばせる。state = 定義の source(タグを消して `source_limit` で切る)と、
  `layer` の層の説明(summary・knows・does_not_know)。mixed を `warning_min` 以上で warning、`info_min` 以上で info。較正の見張りは
  decide-tag(正例)と card-tags-of(反例)の mixed の確率を比べる。問いの文は `src/project/semantic.rs` の `MixedConcerns` の 1 か所。

## 15. defk の素の呼び — DOEFF126

coordinator の決定 2026-09-28(戻せる・agora-redesign #798 に記録)。事実: #798 の直しの便で `latest-by-ref` を defk に改めたのに、
それを呼ぶ deff(`text-at`・`expected-inputs`)と検 4 file が素のまま呼んでいた — defk を素で呼ぶと答えではなく Program が返り、型の
誤りで落ちずに静かに間違った値として流れる(検で見つかった)。記録は doeff の ADR-DOE-HY-007 R13。

- **defk の集合**: repo の Hy の file(`(defk` の綴りを含む物)の最上位の `(defk 名 …)`(`do` と `eval-and-compile` の中も)を
  `<module>.<名>` で集める。呼びの頭は file の import と定義の場所で module まで解いて比べる(`smells::Scope` と同じ)。追えない呼び
  (引数で受けた関数・method)は拾わない。
- **拾う所**(答えを値として使う所): 比べ・演算・真偽の組み合わせ(`=`・`+`・`in`・`not`・`and` …)と答えを読む組み込みの関数
  (`len`・`str`・`get`・`sorted`・`isinstance` …)の引数、method の的と引数(`(.get (f …) "欄")`・`(.append out (f …))`)と属性(`(. (f …) 欄)`)、条件(`if`・`when`・
  `unless`・`while` の頭・`cond` の条件)、繰り返しの元(`for` の束ねの元・内包表記の元)、record の欄(頭が大文字の型を作る呼びの引数 —
  doeff の package の型と、effect として出す位置 `(<- (T …))`・`(! (T …))` の型は除く)。その位置の中の `if`・`when`・`cond`・`do`・`let`
  の枝も同じ。
- **拾わない所**: `(<- …)` の右辺・`(! …)`・`(return …)`、Program を受ける呼びの引数(repo の関数へ渡す形も — Program を受けて走らせる
  関数かもしれず追えない)、名への束ね、関数の答えとして返す形。初版は「Program として渡す所の外は全部」だったが、agora の本線で 239 件
  (Program を受けて走らせる run-on・in-record・run-wired などへ渡す形が大半)になり、error の重さでは外れが重いので、答えとして使う所に
  絞った(本線 1 件)。
- **重さ**: error(静かな誤りなので)。登録簿に載れば `registered_severity`(既定 warning)。母集団は `definitions` の業務の file と検の置き場。
- **鍵**: `<path>::DOEFF126::<定義>::<呼んだ defk>`(定義の外は `<module>`)。位置は呼びの式。
- **引数で受けた関数の素の呼び**(2 つ目の形): repo の全部の呼び `(g … 引数 …)` のうち、引数が repo の defk の名か `(fnk …)` の物を
  集め(位置の引数は何番目か・keyword の引数は名)、呼び先 g の最上位の定義(defk・deff・defn・defn/a)の引数の並びでその名を引く。g の
  本体の中でその名を頭にした呼び `(名 …)` が Program として渡す所(`(<- …)` の右辺・`(! …)`・`(return …)`・`(yield …)`・Program を
  受ける呼びの引数)の外に在れば、呼び先のその呼びを違反にする(名への束ね `(setv v (名 …))` も — 呼び手が defk を渡すので Program が
  束なる)。鍵 `<path>::DOEFF126::<定義>::<引数>`、説明に渡した所と渡した物。事実: agora L1550 の `rows-by-text` の `field-of`。
- **拾えない範囲**(ADR-DOE-HY-007 R14): 呼び手が defk を変数や欄に入れてから渡す形・partial などで包んで渡す形、呼び先がその引数を
  さらに別の関数へ渡してそこで素で呼ぶ形(1 段だけ追う)、呼び先が method・入れ子の関数・名で引けない物、repo の外の呼び手。

## 16. defk の見出しと束縛の型 — editor-json 版 2(agora-redesign #849)

エディタ(doeff-runner)が defk の型・effect・tags を読むだけの表示で描くための材料。型の読み方はこの linter の
`src/project/signatures.rs` の 1 か所に置き、エディタは写しを持たない(operator 2026-09-28 "I will never edit the source manually" —
書く向きの変換は持たない)。`--stdin --path <file>` の時だけ、その file の分を出す(全体の実行では空)。

```jsonc
"signatures": [{
  "kind": "defk",                        // defk | deff
  "name": "fetch", "path": "/abs/core/flow.hy",
  "range": {…},                          // 名の範囲
  "full_range": {…},                     // 定義の form 全体
  "contract_range": {…},                 // 契約の辞書 {:pre … :post … :effects … :tags …}(無ければ null)
  "params": [{"name": "id", "type": TypeRef | null}],   // :pre の (: 名 型)。書いていなければ null
  "answer": TypeRef | null,              // :post の (: % 型)(無ければ名の注記 #^ T)
  "absent": true,                        // 答えが Maybe か(Absent が呼び手へ抜けうる)
  "raises": [TypeRef],                   // 呼び手へ抜けうる Raise の型
  "effects": {"declared": [EffectRef] | null,   // :effects(#800)。書いていなければ null
              "inferred": [EffectRef]},         // 推論(下)
  "tags": {"context": "demo", "role": "program"}
}],
"bindings": [{
  "form": "<-",                          // <- | val | var | setv | :=
  "name": "row", "path": "…",
  "range": {…}, "form_range": {…}, "head_range": {…},   // 名・form 全体(括弧を含む)・頭の記号
  "annotation_range": {…} | null,        // (<- x T e) の T
  "value_range": {…} | null,
  "type": TypeRef | null,                // 分からなければ null(別の型で埋めない)
  "origin": "effect",                    // annotation | effect | call | literal | constructor | var | unknown
  "absent": true, "raises": [TypeRef]
}]
// TypeRef = {"kind": "name", "name": "Row", "definition": {"path", "range"} | null}
//         | {"kind": "union", "members": [TypeRef]} | {"kind": "apply", "head": TypeRef, "args": [TypeRef]}
//         | {"kind": "unknown", "text": "書かれた綴り"}
// EffectRef = {"name", "definition": {"path", "range"} | null, "answer": TypeRef | null, "absent": [TypeRef], "failure": [TypeRef]}
```

- **型の式**: 記号は名、`(| a b)` は和(入れ子は平らにする)、`(of H a …)` は当て、それ以外は読めない式(`unknown`)。名は file の
  import と定義の場所で module まで解き(`smells::Scope`)、repo の Hy の定義(defrecord・defwire・defenum・defclass・defeffect・deftype・
  頭が大文字の val)に当たれば `definition` に位置を入れる。組み込みの型と解けない名は null。
- **推論した effect**: 本体で撃った呼び(`(<- …)` の右辺・`(! …)` の中身)の頭が defeffect ならその effect、defk ならその defk の
  推論を足す。repo の Hy の file 全部から集めた表の上の不動点(保存前の file は stdin の中身で読む)。handler で受けた effect は引かない
  (上から見積もった集合)。宣言との食い違いはそのまま出し、エディタが見せる。
- **Absent と Raise**: `:absent` を宣言した effect(か `absent` の defk)を撃つと答えは Maybe。`(<- … :absent F)` は F の頭の型の
  Raise に替わり、`absent-as` の中は Absent を受けている。Raise = 撃った effect の `:failure` と撃った defk の Raise(`on-raise` の
  program の中は引く)。
- **束縛の型**: `(<- x T e)` は注釈(Absent / Raise は e から)、`(<- x e)` は e の頭が effect なら値の答え(`:answer` の要素から
  `:absent` と `:failure` を除いた物)、defk ならその答え。`val` / `var` / `setv` は字面(文字列・数・真偽・None・辞書・列)・型の名の呼び・
  deff の答え・`(! e)`。`(var x None)` は後で別の値を入れる置き場なので分からない(null)。`(:= x v)` は同じ最上位の定義の中の
  `(var x …)` の型。quote の中は読まない。
- **時間**: 保存前の 1 file の実行で、repo の Hy の file を全部読んで表を作る(agora-controllers の約 950 file で 0.4〜1.2 秒)。
- **エディタ側の知らない語**: 閉じた集合(規則の家族・見出しの種類・束縛の形と出どころ・型の式の種類)に linter が語を足しても、
  エディタは出力を捨てず、その項目だけ既定の見た目にして「拡張が古い」を出す(#848)。
- **assert の引数**: `(assert 条件 文)` の条件と文も答えとして使う所(defk を条件に置くと Program は常に真で検が常に通る)。
- **名に束ねてから使う形**(1 つの定義の中だけ): `setv`・`val`・`var`・`:=`・`let` で defk の素の呼びを束ねた名が、答えとして使う所
  (比べ・組み込みの関数・method の的と引数・`名.method` の呼び・属性・条件・繰り返しの元)に出れば、束ねた呼びを違反にする。
  Program として渡す・返す形は拾わない。同じ名を `(<- 名 …)` でも束ねる定義はその名を追わない。束ねた名を別の定義へ渡す形・
  入れ物に入れて取り出す形は拾えない(ADR-DOE-HY-007 R14)。

## 17. 呼びを `f(a, b)` の形で見せる表示の置き換え — editor-json の `rewrites`(agora-redesign #849)

エディタが defk / deff の本体の呼びを Python に近い形で**見せるだけ**の材料(operator 2026-09-28 "also, maybe we could make the func
call look like f(a,b) instead of (f a b)?"・式の途中の effect は `!` の印を残す案 A "lets try A")。source の Hy は変えない。式の形を
読むのは `src/project/call_view.rs` の 1 か所で、エディタは Hy を読み直さずに描く。版 2 への欄の追加(古いエディタは読み飛ばす)。
`--stdin --path <file>` の時だけその file の分を出す(全体の実行では空)。

```jsonc
"rewrites": [{
  "kind": "call",                 // call | method | infix | prefix | perform | bind | subscript | attribute
  "path": "/abs/core/flow.hy",
  "range": {…},                   // 元の式全体(括弧を含む)
  "original": "(f a :k v)",       // 元の lisp(一字一句)
  "text": "f(a, k=v)",            // 中の置き換えも当てた表示(改行と字下げは元のまま)
  "edits": [{"range": {…}, "text": "(", "effect": null}],   // 元の文字の範囲(空なら挿すだけ)を隠して text を見せる。effect = 装置の絵の effect の名
  "parts": [{"range": {…}, "name": "f", "role": "defk",      // effect | defk | deff | type | function | builtin | local | method
             "definition": {"path", "range"} | null, "answer": TypeRef | null}],
  "parent": 3 | null              // 外側の置き換えの番号(外を元の lisp で見せる時は中も元の lisp)
}]
```

| lisp | 表示 |
|---|---|
| `(f a b)`・`(f a :key v)`・`(f #* xs)` | `f(a, b)`・`f(a, key=v)`・`f(*xs)` |
| `(.get row "k")`・`(get row "k")`・`(. row id)` | `row.get("k")`・`row["k"]`・`row.id` |
| `(+ a b)`・`(= a b)`・`(is-not x None)`・`(not x)` | `a + b`・`a == b`・`x is not None`・`not x`(Python の優先順位で、変わる所だけ括弧) |
| `(! (f a))` | `!f(a)`(撃つ呼びが effect なら `!` の edit に effect の名) |
| `(<- (f a))`(名の無い `<-`) | `<- f(a)` |
| `(Effect a)` | `Effect(a)`(`(` の edit に effect の名。`!` の中では `!` が持つ) |

- **呼びと読む頭**: import した名・repo の定義(defk・deff・型・defeffect)・この file の最上位の定義・Python の組み込み・定義の中の
  局所の名(引数と束縛)・`a.b` の形の名。`require` で入る macro と、制御の形(`when`・`if`・`match`・`for` …)・知らない頭は lisp のまま
  (中の呼びだけ置き換える)。lisp の形の中の演算は括弧を残す(`(when (a and b) …)`)。
- **字下げ**: 作り直さない。引数が次の行へ続く所は前の引数の後ろに `,` を挿すだけ。頭と最初の項が別の行の演算・method は lisp のまま。
- 名の束縛(`(<- x T e)`・`val` …)は 16 節の `bindings` が描き、ここは値の式だけを置き換える。quote の中は読まない。

## 18. `:effects` の宣言と推論の食い違い — DOEFF127

operator の訂正 2026-09-28(agora-redesign #849 に記録)。逐語 "such linter info must be displayed where it's violating and must show
what it is violating"。前は拡張(doeff-runner)が defk の見出しの中で宣言と推論を比べ、「宣言なし」「違反 2」の札にまとめて出していた。
判じる所を linter に移し、違反している所に規則の違反として出す(拡張は描くだけ)。

- **対象**: `:effects` を書いた defk だけ。`:effects` は任意(#800)なので、書いていない defk は対象外(「宣言なし」は違反にしない)。
  母集団は `definitions` の業務の file(検の置き場も含む — `paths` の下なら)。
- **推論**: 16 節の見出しと同じ `signatures::World`(1 か所)。本体で撃つ呼び(`(<- …)` の右辺・`(! …)`)の頭を module まで解き、
  effect ならそれ、repo の defk ならその推論を辿って集める。撃つ位置に分岐(`if`・`when`・`cond`・`match`・`do`・`let`)を置いた形は、
  枝の呼びを 1 つずつ撃ったと数える。
- **違反 2 種**:
  - 宣言に無い effect を起こす — 位置 = その effect に至る最初の撃った呼びの頭(defk を経由するならその defk の名)。文に起こす effect と
    経由した呼びを書く。
  - 宣言した effect を起こさない — 位置 = `:effects` の中のその名。**追えない呼び**(repo の外の関数・deff・method・名で引けない物)を
    1 つでも撃つ defk では、推論の集合が欠けうるので出さない。初版(この抑えなし)は agora の本線で 23 件のうち 19 件がこの形の外れ
    (`read-typed` のような foundation の関数を経由する protocol の defk)だったので抑えた(本線 4 件 — 全部が宣言に無い effect)。
- **重さ**: warning(戻せる決定・#849)。error にしない理由 = 推論は handler で受けた effect を引かない上からの見積もりで、本体の中で
  受けている effect を「宣言に無い」と出しうる。info にしない理由 = 見出しの札をやめて違反の場所だけに出す以上、見落とされない重さが要る。
  登録簿に載れば `registered_severity`。
- **鍵**: `<path>::DOEFF127::<定義>::<effect>`(effect は module を外した綴り)。

