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
| `tags` | `module_variable_hy`・`module_variable_py`・`contract_definers`・`plain_definers`・`effect_definers` | MODULE-TAGS・MODULE_TAGS・defk deff defp defpp defhandler・defn defclass defrecord defenum・defeffect |
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
| DOEFF111 | `deff` の定義の行か直前の行の註(`;` の後)に `definitions.deff_reason_marker` が無い | 定義の名 | 定義の名 |
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
           (layer protocol …) (layer foundation …) (layer entry …)]   ; 外の世界から遠い順
  :shared "shared"                            ; root/shared/<層>/ — どの service からも読める
  :foundation foundation                      ; root/foundation/ — service の外の層(同じ名の layer が要る)
  :open-layers [intent]                       ; 別の service から読んでよい層(既定 intent)
  :roles {:judgment "業務の判断をする純粋な関数" …}   ; role の説明
  :exclude ["tests" "__pycache__" "conftest.py"]    ; 既定のまま
  :extensions ["hy" "py"]                           ; 既定 hy・hyk・hyp・py
  :legacy ["controllers/agora_sim" (legacy "controllers/core" :layer core)])  ; 移行の途中の置き場(縮める向きだけ)
(defservice land-notice "着地の報せ" {:depends-on [messaging] :layers [core intent protocol entry]})
```

- 層の置き場は `root/*/<層>`(service と shared)と、foundation の層は `root/<foundation>`、`(legacy "dir" :layer 層)` の dir。
- service の dir は名の `-` を `_` にした物(`land-notice` → `controllers/land_notice/`)。`{:dir "…"}` で変えられる。
- 読み違い(知らない鍵・重複した service や層・存在しない層や service の名・:foundation の層が無い)は `architecture.hy:行:列: 理由` の形で
  設定の誤り(終了コード 2)。
- editor-json の最上位に `architecture`(name・root・layers(name・summary・knows・does_not_know・question・roles)・shared・foundation・
  open_layers・legacy・services(name・dir・description・depends_on・layers))。無ければ null。

| 規則 | 判じ方 | 鍵の細目 | 位置 |
|---|---|---|---|
| DOEFF114 | root の下の module が、宣言した service の宣言した層・shared の層・foundation・legacy のどれにも入らない(root の直下、service の dir の直下)。`__init__` は外 | なし | file の頭 |
| DOEFF115 | root の直下の dir が宣言した service・shared・foundation・legacy でない / service の中の dir が宣言した層でない。dir ごとに 1 件(鍵の path は dir) | なし | dir の最初の file の頭 |
| DOEFF116 | service A の module が service B の module を import した時、B が A の :depends-on に無い、または読む先が B の :open-layers の層でない。shared と foundation は service ではないので見ない。宣言の DOEFF109 はこれに置き換わる(architecture.hy の在る repo では DOEFF109 の設定を置けない) | import の先 | import の記号 |
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

- **撃つのは `--semantic`(path の引数の file、無ければ git で変わった file)と `--semantic-all`(設定した層の全定義)の時だけ。** 決定的な規則の実行
  (エディタの保存ごと・hook・text / json)は cache を読むだけで、Jev を呼ばない(キーも要らない)。
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
```

editor-json: violation の `source`(`linter` = 決定的な規則・`jev` = 意味の判定)と `probability`(Jev の違反だけ)、最上位の `semantic`
(`model`・`wire`・`judged`・`unjudged`・`asked`・`cost_usd`(gateway だけが返す)・`input_tokens`・`served_model`・`calibration` = not-run / ok / drifted / failed)。
