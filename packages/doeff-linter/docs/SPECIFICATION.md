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
| `doeff-linter --output-format editor-json --baseline-report <file> [<path>…]` | 基点(main の先端)で走らせた editor-json の出力 `<file>` と比べ、基点に無い critical を `new_critical` に出す(下の「基点との比べ」) |

- 設定を探す順: `--config` があればそれ。無ければ今の dir から上へ、`[tool.doeff-linter]` を持つ pyproject.toml を探す。
- repo の根: `--root` があればそれ。無ければ見つけた pyproject.toml の dir。`--config` を渡した時は今の dir。
- `--stdin` と `--path` は editor-json の時だけ使える。`--stdin` に `--path` が無ければ終了コード 2。

### 終了コード

| コード | 意味 |
|---|---|
| 0 | error の違反が無い(warning と info はあってもよい) |
| 1 | error の違反がある(登録簿に無い新しい破れ) |
| 2 | 引数の誤り・設定が読めない・設定の名前の食い違い・型の違う値(理由は stderr) |
| 3 | error の違反は無いが、意味の規則で問うはずだった定義に答えを得られなかった(測れなかった — Jev に届かない・鍵が無い・較正が撃てない。緑ではない・agora-redesign #1160) |
| 4 | `--baseline-report` の時だけ: 基点に無い critical がある(新しい critical)。1・3 より先に判じる(2 は常に最優先) |

### 基点との比べ(`--baseline-report`・agora-redesign #1803)

マージ前の検査と commit の hook は、保存した既知の一覧を持たず、基点と commit の両方で linter を走らせ、commit の critical の識別子の集合が
基点の集合に含まれていれば通す(#1762 の決定 A)。基点の側を走らせるのは呼び手の役で、linter は基点の出力 file を読んで比べるだけ
(linter が git の木を組み直すと、事実の cache と repo の根の扱いが 2 重になるため)。

- **比べる物**: `level` が `critical` の違反(登録簿に載って warning に下がった物も含む — 重大さは登録簿で下げない)。
- **識別子**: `<path>::<規則>::<名>`(行番号を含めない)。鍵を持つ違反は `key` そのもの、鍵の無い違反(Python の文ごとの規則)は名の代わりに `message`。
- **件数ではなく集合**: 3 件直して 1 件足した commit は、件数が減っても新しい critical が 1 件ある。
- **file の移動・改名**: 規則と名が同じで path だけ違い、基点のその識別子が今は消えている時は、1 対 1 で同じ破れとみなす。
  基点の識別子が残ったまま別の path に同じ名が増えたら、新しい破れ。
- **出力**: editor-json の一番上の欄 `new_critical`(識別子の辞書順の列)。基点と比べない時は `null`。版は上げない(欄の追加)。
- **範囲**: path を名指した実行では、名指しの下の違反だけを比べる。基点の出力も同じ path の名指しで作る(呼び手の役)。
- **誤り**: 基点の file が読めない・JSON でない・`violations` の列が無い・text / json の出力で使った時は終了コード 2。
- 戻し方: この欄・引数・`src/baseline.rs` を消す(呼び手が使う前なら影響なし)。

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
      "base_severity": "error",              // 登録簿と照合中で下げる前の規則そのものの重さ(版 2 への欄の追加)
      "standing": "new",                     // new(新しい)| registered(登録簿の既知)| reconciling(照合中で info に下げた)
      "level": "critical",                   // 規則の重大さ(設定 rules.<ID>.level。無ければ base_severity から)— 登録簿で下げない
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
  "errors": [],                              // 読めなかった file・登録簿・目録の理由
  "new_critical": null                       // --baseline-report の時だけ: 基点に無い critical の識別子の列(1 節「基点との比べ」)
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

- `severity` は下げた後の重さ、`base_severity` は規則そのものの重さ。登録簿に載った error は `registered_severity`(既定 warning)、
  載った warning は info、照合中の規則は info に下がる。エディタはこの 2 つと `standing` で「重い規則の破れが新しい分・既知の分で何件残るか」
  を数える(下げた理由の判定は linter が持つ)。Python の文ごとの規則は下げないので 2 つは同じで `standing` は `new`。

- `signatures`・`bindings`(版 2): `--stdin` の Hy の file の defk / deff の見出しと束縛の型。全体の実行では空の列。形と読み方は 16 節。
- `rewrites`(版 2 への欄の追加): `--stdin` の Hy の file の、定義の本体の呼びを `f(a, b)` の形で見せる表示の置き換え。全体の実行では空の列。17 節。
- `bodies`(版 2 への欄の追加): `--stdin` の Hy の file の、定義ごとの本体の文字の行(読む面が描く)。全体の実行では空の列。20 節。

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
| `translation_effects` | `handler_layers`・`intent_layers`・`max_depth`(DOEFF130 — 21 節) | protocol・intent・8 |
| `registry` | `dirs`(1 鍵 1 file の dir)・`files`(1 行 1 鍵)・`config_files`(1 行 1 鍵・設定 file の dir からの相対)・`reconciling` | dirs と files は repo の根から |
| `rules.<ID>` | `registered_severity`(登録簿に載った破れの重さ: error・warning・info) | warning |
| `rules.<ID>` | `level`(規則の重大さ: critical・major・minor・info。どの規則でも書ける。登録簿で下げない — エディタが「手つかずの critical」を数える軸) | 下の「既定の重大さ」の表、表に無い規則は規則そのものの重さから(error = major・warning = minor・info = info) |

`enable`・`disable` は Python の規則と層の規則の両方に効く(`ALL` は両方を含む)。

### 既定の重大さ(agora-redesign #1041)

責務の境界の違反は、どの repo でも critical(operator 2026-09-29 "responsibility boundary violations are always CRITICAL to make our
doeff code testable")。repo ごとに写すと片方が黙って古くなるので、既定の表は doeff-linter の 1 か所(`ProjectRule::default_level` —
網羅の match で、新しい規則は決めずに足せない)に置き、repo の設定の `rules.<ID>.level` は既定と違う所だけを書く(書けば repo の宣言が勝つ)。

| 既定 | 規則 |
|---|---|
| critical — 責務の境界 | DOEFF101(層の向き)・102(層で禁じた module)・103(型だけの層の関数)・106(生の副作用の直接の証拠)・109(service の境界)・116(service の依存)・130(翻訳の handler が業務の intent を出す) |
| critical — そのほか | DOEFF114・115(宣言に無い置き場所)・126(defk を素で呼ぶ — Program が値として流れる)・128(読めない file — 判定が欠け、0 件に見えても合格ではない) |
| major — Jev の判定(較正が済むまで) | DOEFF201(翻訳の層の業務の判断)・202(判断の層の通信の手段)・205(形の検めと判断の混ざり) |
| 規則そのものの重さから | 上に無い規則の全部(Python の文ごとの規則 DOEFF001〜031 も) |

Jev の規則(201・202・205)は、較正が済んで信頼できるまで critical にしない(operator の決定 B・2026-09-30 — agora-redesign #1762 / #1801。#942 で
critical にしたのを戻した)。確率で info にも出るが、level は規則の軸なので info の当たりも major に数える(外れは誤判定の一覧へ — 10 節)。

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
| DOEFF106 | hy-index 版 3 の定義ごとの直接の証拠(raw.direct)が、`allowed_layers` の外の層の Hy の定義に在れば破れ。architecture.hy に `:world-handlers` を書いた repo では層で許さず、名簿の定義の module の file だけに許す(`allowed_layers` は書けない・raw_side_effects の節が無くても当たる — agora-redesign #1140)。強い証拠は error、弱い証拠は warning。入れ子で重なる証拠は内側の定義に 1 度 | `<定義>::<証拠の名>` | 証拠の記号 |
| DOEFF107 | 経由の証拠(raw.via — 全体の実行だけ)を info で出す。経路つき。1 定義で経路と証拠の名が同じ物は 1 件。architecture.hy の `:world-handlers` に宣言した定義には入らない(その先の生の副作用はその定義の責務 — agora-redesign #1902)。宣言した定義を通らない別の経路で届けば当たる | `<定義>::via::<経路>::<証拠の名>` | 定義の名 |
| DOEFF131 | `:world-handlers` の `:wraps` に挙げた doeff の実 I/O の handler を、名簿の定義(とその中の入れ子の定義)の外で名指す。値として渡す参照(`with-handlers` の列)も呼び出しも数え、import の行は数えない。名指しの先は索引の参照の `target`(呼び出しと同じ名前の解決)。同じ定義の同じ handler は 1 件。既定の重大さ critical(agora-redesign #1106 の R1・#1140) | `<定義>::world::<module:名>` | 名指した記号 |
| DOEFF106・131(層の外) | `:world-handlers` を書いた repo では、層の置き場の外の Hy の file(層の外の dir・`:root` の外で `:raw-io-roots` に挙げた dir — 書かなければ `:root` だけ)にも DOEFF106・131 を当てる。検の file(path の段の :exclude・名が test_ / conftest)・defadr を持つ file・deftest の中の当たりは外す(縁のテストは DOEFF133)。message に「層の置き場の外」(agora-redesign #1147) | DOEFF106・131 と同じ | 同じ |
| DOEFF132 | `:world-handlers` の定義 1 本ずつ — module が層の置き場に無い(無い module か層の外)・foundation の外の層に在る・Hy の module に定義が無い。全体の実行だけ。既定の重大さ critical(agora-redesign #1106 の R2・#1141) | `<module:名>` | architecture.hy の名簿の要素 |
| DOEFF133 | テストの種類(手元 / 縁)を届く先から導く — 全体の索引で定義の間の辺(呼び出し・参照・入れ子)を組み、名簿の定義・`:wraps` の handler を名指す定義・強い生の I/O の証拠を持つ定義から逆向きに辿る。届く deftest は縁で `:edge-mark` の印(deftest の `:marks` か module の `pytestmark`)が要り、届かない deftest は印を持たない。食い違いを critical で出し、縁の message に届く道を書く。Python の検は数えない(R6)。Hy の定義が名指す repo の中の Python の関数(repo の根からの `a/b.py` か `a/b/__init__.py`)は、その中の強い生の I/O の証拠(import を通した目録の名・呼び出しの頭の組み込み — 型の注記と except の型は数えない)と、名前で決まる呼び先(同じ module の関数・import した関数・class の構築と `self.m` / `cls.m`・Hy の定義へ戻る呼び)まで深さの上限なしに辿る。値の上の属性の呼びと実行時の import は辿らない(Hy の図と同じ範囲)。構文の壊れた・読めない module は報告の errors に名乗る(agora-redesign #1798)。`:edge-mark` を書いた時だけ当たる(agora-redesign #1106 の R3・#1142) | `<定義>::edge` / `<定義>::local` | deftest の名 |
| DOEFF137 | 縁の検の無い許可名簿の handler — `:world-handlers` の handler ごとに、空でない `:interpreters` を持つ deftest(`:interpreters` の要素は file の外の定数の記号なので読み解かず、空でない列かだけを見る — deftest の設定の dict の読みは DOEFF133 の `:marks` と同じ)のうち、DOEFF133 と同じ定義の辺の図(呼び出し・参照・入れ子)を辿ってその handler の定義に届く物が 1 本も無ければ 1 件。`:interpreters` を持たない deftest が届くだけでは数えない。理由つきの `:contract-test (none …)` の handler は判じない・理由の無い `:contract-test none` は縁の検が在っても 1 件(#1796)。全体の実行だけ・有効にする条件は DOEFF133 と同じ(`:world-handlers` と `:edge-mark`)。既定の重大さ critical(agora-redesign #1363) | `<module:名>` | architecture.hy の名簿の要素 |
| DOEFF135 | テストは deftest だけ — `:test-forms {:tests [..] :check-scripts [..] :runners [..]}` の綴りの型で file を選び、:tests の Python の file の `def test_*`(python-test・件数を message に)、:tests の file の module ごとの skip(字下げの無い段の `pytest.skip`・`allow_module_level`・`pytestmark` の `mark.skip` — 束ねの名と印が別の行に在る複数行の束ねも括弧が閉じるまで読み、束ねの名の行を指す・#1426)、:check-scripts の file(check-script)、:runners の file(runner)を file ごとに 1 件。全体の実行だけ。既定の重大さ critical(agora-redesign #1106 の R6・#1144) | `python-test` / `module-skip` / `check-script` / `runner` | file の頭か最初の当たりの行 |
| DOEFF136 | 模擬の環境で回していない service — `:verification-environment` を書いた repo で、entry の層を持つ service ごとに、その service の entry の層の dir(`<root>/<service の dir>/entry`)の定義から呼び手(呼び出し・参照・入れ子 — DOEFF133 と同じ図)を逆向きに辿り、模擬の環境の dir の下の deftest に 1 本も届かなければ defservice の位置で critical(鍵 = service の名)。entry の層を宣言しても定義が 0 本の service は数えない(回す組み立てが無い)。service の中の tests は模擬の環境の外なので数えない。索引は `defsystem`(doeff-cluster の系の宣言)も定義として読むので、deftest → defsystem → entry の経路も届く(agora-redesign #1143・R5・#1111)。 |
| DOEFF163 | 不変条件を宣言していない service — code を持つ service(DOEFF136 と同じ母集団: :layers に entry があり、`<root>/<service の dir>/entry` の下に Hy の定義が 1 本以上)ごとに、defservice の `:invariants`(業務の不変条件の関数 `"module.path:関数"` の列)が無いか空なら 1 件、名指した関数の定義が repo の Hy の索引に無ければ 1 件、定義の `:tags` の `:role` が `judgment` でなければ 1 件(置き場は問わない — 模擬の環境の `*_invariants.hy` でよい)。位置は defservice の名。既定 critical、登録簿に載った欠けは warning。全体の実行だけ(agora-redesign #1559・#1155 の定義 1 の条 (b)) | `<service>` / `<service>::<module:関数>` | defservice の名 |
| DOEFF142 | handler の引数に client・可変の店 — `:handler-arguments {:files [..] :exclude [..] :store-names [..] :store-suffixes [..] :keep-mark "…" :value-types [..]}` の :files に当たる Hy の file の `defhandler`(入れ子も)の引数 1 つずつを分ける: `client`(型の名の末尾が Client / Connection / Pool / Engine・名が client / conn / connection / …-client / …-conn)・`container`(dict / list / set / bytearray / MutableMapping … — `(get dict …)` を含む)・`object`(repo の Hy と Python の class の索引で frozen でない class と、索引に無い型 — frozen の dataclass・frozen の設定・Enum・NamedTuple・defrecord と :value-types は値)・`named`(型の注記が無く名が :store-names / :store-suffixes)。本文(次の最上位の form まで)に :keep-mark の註が在る handler は数えない。判定は渡した file だけで決まり、class の索引は file ごとのキャッシュで引く(1 file の実行でも repo を読み直さない)。既定の重大さ critical(agora-redesign #1189 / #1366) | `<handler>::<引数>` | 引数の位置 |
| DOEFF143 | 業務の効果に答える偽の handler — `:business-fakes {:simulation [..] :assembly [..] :tests [..] :skip [..] :production [..] :sets [..] :simulation-prefix "…" :production-prefix "…" :entry-string-modules [..] :entry-string-files [..] :business-modules [..] :lower-layer-modules [..] :external-effects "dir" :counterexamples "dir" :unserved "dir"}`。定義の辺の図(DOEFF133 と同じ・系の値の中の辺も本番で回る辺として数える)を、模擬の根(:simulation と :assembly の定義・:sets の file の :simulation-prefix の定義)と本番の入口(:production の file のうち :sets の :production-prefix の定義・defsystem・`(when (= __name__ "__main__") …)` の中の名・`"<module>:<名>"` の入口の文字列 — :entry-string-files の本文も読む)から前向きに辿る。模擬の根からだけ届く defhandler の節が、:business-modules の効果に tap でなく(節の本体が同じ効果を出し直さずに)答え、外の世界の表にも反例の表にも無ければ critical(鍵 = `<handler>::<効果>`)。模擬の根からだけ届く節が :lower-layer-modules の効果に tap でなく答えるのも critical(下の層の偽物は下の層の正典 1 つだけ・鍵 = `lower:<handler>::<効果>`)。表の腐りも出す: 反例の表の行が当たらない(DOEFF157 の検だけから届く節も照らす — 業務の効果か :lower-layer-modules の効果に答える節)・外の世界の表の行にどの偽物も答えない・本番の答え手が無い(本番の code の Python の `isinstance(effect, X)` も答え手に数える・:unserved に載せた物を除く)。表は 1 鍵 1 file の dir(1 行目が鍵・2 行目から理由)。全体の実行だけ。既定の重大さ critical(agora-redesign #1375 / #1189) | `<handler>::<効果>` / `lower:<handler>::<効果>` / `counterexample-unused::…` / `external-unused::…` / `external-unserved::…` | 節の位置・表の dir |
| DOEFF155 | 組み立ての形の破れ — `:assembly-shape {:translation-point "with-*-translation" :retired-function "…" :translations "…" :translation-layer "…" :intent-layer "…"}`(組み立ての層・組の file・業務の module・外の世界の表は `:business-fakes` から読む — 両方を書いた時だけ)。出す物: :sets の本番の file が残る・組み立ての層に :retired-function の定義が残る・組み立ての層の翻訳の列の 1 点(名が :translation-point の型の defk)の `(with-handlers [#* 列 …] 本体)` が翻訳の列でない import した名を並べる(翻訳の列 = :translation-layer の置き場の :translations の定数か、置き場の外の service の旧い module の列で要素が全部置き場の外の業務の答え手の物・同じ module の val の列は中へ開く)・列の並び(列が出し直す別の service の :intent-layer の効果に答える列が、それより前に無い)・:translations の定数が別の service の :translation-layer の定義を並べる。全体の実行だけ。既定の重大さ critical(agora-redesign #1376 / #1189) | `set-file` / `retired` / `stray:<名>` / `order:<列>:<効果>` / `own:<定義>` | 定義の位置・組の file |
| DOEFF156 | 翻訳の先か土台の答えが業務の効果 — 宣言は DOEFF155 と同じ。:translations の定数から届く翻訳の handler が、自分の答えない業務の効果を名指す(:intent-layer の効果は常に・それ以外は同じ列の handler が答えない物)・出し直した効果に答える同じ列の handler がどれも列の後ろに在る(定数が literal の列の時だけ)・:sets の file の組の関数(:simulation-prefix / :production-prefix)が届く handler のうち同じ module の :translations の外の物が業務の効果に tap でなく答える。業務の効果 = :business-modules の効果のうち外の世界の表に無い物。全体の実行だけ。既定の重大さ critical(agora-redesign #1376) | `target:<handler>:<効果>` / `inner:<handler>:<効果>` / `foundation:<組の関数>:<handler>:<効果>` | handler か組の関数の位置 |
| DOEFF157 | 検だけの偽物 — 宣言は DOEFF143 と同じ `:business-fakes`。検の根(:tests の file の定義の全部)から届き、本番の入口からも模擬の根からも届かない定義の節が、:business-modules か :lower-layer-modules の効果に tap でなく答え、外の世界の表にも反例の表にも無ければ critical(検だけが使う本番の code の置き場の業務の写しも数える)。わざと壊した反例の handler は反例の表に理由つきで載せる。全体の実行だけ。既定の重大さ critical(agora-redesign #1377 / #1189) | `<handler>::<効果>` | 節の位置 |
| DOEFF158 | intent の効果の答え手が翻訳の 1 つでない — 宣言は DOEFF155 と同じ(`:business-fakes` と `:assembly-shape` の両方)。本番の入口から届く節のうち、効果の定義元の module の置き場が :intent-layer の層で、tap でなく答える物を数える。節の file が :translation-layer の層の外なら critical、同じ効果に答える :translation-layer の handler が 2 つ以上ならその全部を出す(ADR R9 — 本番と模擬で同じ翻訳 1 つ)。全体の実行だけ。既定の重大さ critical(agora-redesign #1377) | `outside:<handler>::<効果>` / `shared:<handler>::<効果>` | 節の位置 |
| DOEFF164 | 壊した handler の反例が無い service — 宣言は DOEFF143 と同じ `:business-fakes` の `:counterexamples` と `:verification-environment`。反例の節 = 反例の表の鍵に当たり本番の入口から届かない節。節の効果の定義元の file を含む service の dir が持ち主(どの service の下にも無ければ土台の効果)。entry の層に定義を持つ service ごとに、持ち主がその service か土台の反例の節に(DOEFF136 と同じ図を逆向きに)届く deftest の 1 本でもその service の entry の層の定義に届けば有り、1 本も無ければ defservice の位置で critical(登録簿に載せれば registered_severity で下げる)。あわせて DOEFF143・157 の表の照らしは、本番から届かない節の鍵が表に在ればどの効果に答える節でも当たりに数える(土台の効果に答える壊した handler も表に載せられる)。全体の実行だけ。既定の重大さ critical(agora-redesign #1560 / #1155) | service の名 | defservice の位置 |
| DOEFF165 | intent の効果の網羅の欠け — 宣言は DOEFF158 と同じ(`:business-fakes` と `:assembly-shape` の両方)。宣言の file の置き場が :intent-layer の層の defeffect ごとに、DOEFF143 と同じ到達の図から 3 列を読む: 手元の検から出すか(検の file の定義から届く定義のうち、答え手〔効果の節・defhandler〕と宣言を除いてその効果を名指す物)・模擬の答え手(模擬の根から届く効果の節の handler)・本番の答え手(本番の入口から届く効果の節の handler)。どれかが空の効果を 1 件にし、本文と `--explain` に 3 列を出す。持ち主の service は宣言の file の置き場を dir に持つ defservice(最も長い前方一致)。Python の class で宣言した効果は数えない(索引の図の外)。全体の実行だけ。既定の重大さ critical(agora-redesign #1562 K4 — #1561 K3 の報告だけの表を失敗に。repo の登録簿に載った既知の欠けは warning) | `<service>::<効果>`(service の無い効果は `-::<効果>`) | 効果の宣言の位置 |
| DOEFF167 | 反例も外した理由も無い不変条件の条 — DOEFF164(service に反例が 1 本あれば緑)を条ごとに細かくした物。条の名は `:invariants` の関数が返す値で静的に読めないので、defservice の `:clauses ["条" …]` に宣言し、反例を持たない条は `:clause-exemptions {"条" "理由" …}` に理由を書く(`:clauses` の内・理由は空でない)。反例の節がどの条を破るかは、反例の表(`:counterexamples` の 1 鍵 1 file)の 2 行目から後の `breaks: <service>::<条> …` の行で名乗る。母集団と効く反例の節は DOEFF164 と同じ(entry の層に定義を持つ service・表の鍵に当たり本番の入口から届かない節)。条ごとに、名乗る節に届く deftest の 1 本でもその service の entry の層の定義に届けば有り、理由つきで外した条は数えず、どちらも無ければ defservice の位置で critical(鍵の細目 `<service>::<条>`)。`:clauses` を書かない code を持つ service は service の名で 1 つ。宣言に無い service か条を名乗る `breaks:`・綴りの誤りは設定の誤り(errors)。全体の実行だけ。既定の重大さ critical(agora-redesign #1713) | `<service>::<条>`(宣言の欠けは service の名) | defservice の位置 |
| DOEFF166 | 登録簿の当たらない古い行 — `[tool.doeff-linter.registry]` の `dirs`・`files`・`config_files` の鍵のうち、鍵の区切り(`<path>::<law の名か規則の ID>[::<細目>]` の 2 つ目)が指す規則を**この実行で判じた**(有効・判定が繋がっている・意味の規則でない・`reconciling` でない)のに、どの所見の鍵にも当たらない物。区切りから規則を引けない鍵(登録簿の dir を共用する他の検の鍵)と、判じていない規則の鍵は判じない。path を名指さない全体の実行だけ(名指しの file・保存前の 1 file では、当たる所見が範囲の外に在りうる)。登録簿を設定した repo でだけ繋がる。既定の重大さ critical(agora-redesign #1724・#1706 — 縮める向きの登録簿を消し忘れで緩めない) | 登録簿の鍵 | 載った登録簿の file(dir なら鍵の `*.txt`)の先頭 |
| DOEFF144 | 公開面の型が素の写像・素の組 — `:typed-values {:files [..] :except [..]}`(glob は DOEFF150 と同じく根に錨を下ろす)に当たる Hy と Python の file の公開面(名が `_` で始まらない物 — `Class.欄` は最後の段で見る)の型の注記を読む: class の欄(Hy の defclass・defrecord・defwire の直下の `(#^ T 名)`・`#^ T 名`・`(setv #^ T 名 …)`/Python の class の直下の `名: T`)・関数と method の戻り値(`(defn #^ T 名 …)`・defk / deff の `:post [(: % T)]`/`-> T`。入れ子の関数は数えない)。赤 = 素の写像(dict・Dict・Mapping・MutableMapping・typing / collections.abc の同名・JsonValue・JSONValue・JsonObject・JSONObject)・素の組(tuple・Tuple)・値が object / Any の写像・長さの決まった組 `tuple[A, B]`・それらを中身に持つ入れ物と union の枝・文字列の型 `"A \| B"` の枝。赤でない = `dict[str, Row]`・`tuple[X, ...]`・FrozenMap。名に型の注記の在る defk / deff の `:post` は写像だけを赤にし、注記の無い `:post` は素の組も赤にする。Hy の defn / defk / deff が長さ 2 以上の組の literal `#(a b)` を答えにする形(最後の式か `(return #(a b))`)も赤。file 1 つで判じ、名指しの path の扱いは DOEFF150 と同じ。既定の重大さ critical(agora-redesign #1191) | `field:<Class.欄>` / `return:<名か Class.method>` / `post:<名>` / `pair:<名>` | 型の注記(答えの組は組の literal) |
| DOEFF145 | 型の宣言の `@dataclass` に kw_only=True が無い — `:record-stubs {:files [..] :except [..]}` に当たる .pyi のうち同じ dir に同じ名の .hy が在る物で、.hy の最上位の `(defrecord 名 …)` か飾りに `(dataclass … :kw-only True …)` を持つ `(defclass [飾り …] 名 …)`(名は Python の名へ mangle)を、.pyi の最上位の class が `@dataclass` / `@dataclasses.dataclass`(呼びの形も)で `kw_only=True` の literal 無しに宣言する。file 1 つで判じる。既定の重大さ critical(#1191) | class の名 | `@dataclass` の飾り |
| DOEFF146 | 判定を1か所に閉じ込めた語彙 — `:single-point-vocabulary` の群ごとに、`:files`(repo の根に錨を下ろした glob)に当たり `:except`(判定の1点)に当たらない file を読み、`:patterns`(正規表現 `r"…"`・行に1件)のどれかが当たる行が1行でもあれば file ごとに1件出す(当たった行の数を message に書く)。既定の重大さ critical(agora-redesign #1192・#1371 — 元は agora-controllers の一時の検 check_screen_slice_single_point.hy) | 群の名 | file の最初に当たった行 |
| DOEFF150 | 使わないと決めた綴り — `:retired-words` の群ごとに、`:files`(repo の根に錨を下ろした glob — `/` の無い型は根の直下の file だけ)に当たり `:except` に当たらない file を読む。`:in lines`(既定)は行ごとに、語として単独で在る `:words`(前後が英字・`_`・`-` でない所)と `:patterns`(正規表現 `r"…"`・行に 1 件)を数え、`:rule-lines` の綴りを含む行は数えない。数えるのは実際に使う code の中の綴りだけで、註・docstring・文書の中の綴りは数えない(agora-redesign #1794・#1762 の決定 Q2-3): `.md` の file は数えない(file ごと)・Hy は `;` の註(文字列の外の `;` から行末)と `def…` の形(defk・defn・deff・defclass・defhandler・deftest・defadr …)の docstring(名と引数の列・任意の `{…}` の meta の後の最初の文字列で、後にまだ form が在る物)を数えない・Python は `#` の註(文字列の外)と docstring(行の最初の非空白から始まる三重引用符の文字列)を数えない・shell・toml・ほかの file は引用符の外の `#` の註(行頭か空白の後の `#` から行末 — `$#`・`${#…}` は註でない)を数えない。どの種類でも 1 行目の shebang(`#!`)の行は数える。記号・欄名と docstring でない文字列は数える — command の文字列(`"cd x && PYTHONPATH=. hy"`)は実行される綴りなので数える。`:in names` は定義の名(Hy の `def…` の形と `setv`・`val`・`var` の左辺・Python の def と class の名)だけを見る。`:in paths` は file の名(最後の `.` より前 — dir の名と中身は見ない)だけを見て、退役した名の file を置き直さない(agora-redesign #1369)。`:rule-lines` は `:in lines` の時だけ書ける。`:contract-files [<repo の根からの path> …]`(`:in lines` の時だけ書ける・agora-redesign #1893)は契約の JSON の file の列で、architecture.hy を読む時にどの深さのオブジェクトのキーの名と、キー `enum` の配列の文字列の要素・キー `const` の文字列の値(契約の綴り)を集める(註・例の値・ほかの配列の中身は集めない)。`:words` の語が契約の綴りに在れば、その語は次の 2 か所でだけ数えない — 文字列で中身がその語ちょうどの物(Hy の普通の文字列 `"mail"`・Python の `.py` / `.pyi` の接頭辞の無い 1 行の文字列 `"mail"`・`'mail'`)と、Hy の defwire の本体の欄の定義の名(`(#^ str mail)`・`(setv #^ T mail v)`)。変数・引数・defrecord / defclass の欄・loop の変数・属性の読み(`x.mail`)・語を含む長い文字列と、契約に無い語は今どおり数え、`:patterns` には効かない。file が無い・JSON でない・契約の綴りが 1 つも無い・空の列・根の外の path は宣言の誤り(終了コード 2)。書かない群は今までどおり。file 1 つで判じるので、名指しの path が在ればその下の file だけを読む(repo 全体を読まない)。既定の重大さ critical(agora-redesign #1193) | `:words` の当たりは語・`:patterns` の当たりは群の名 | 当たった綴り |
| DOEFF151 | 使わないと決めた呼び — `:retired-calls` の群ごとに、`:files` に当たる Hy の file の `(呼び …)` の形(頭の記号が `:calls` の綴り)を数える。註・文字列・`#_` で読み捨てた form・値として名指すだけの所は数えない。Python の file は数えない。境目の部品(`:boundary-parts`)の module の中では、呼びの綴りが生の副作用の目録で分類でき、その触れる先(DOEFF106 と同じ写し方 — `time.time` は clock)を部品が `:touches` に宣言している呼びだけを数えない(#1894)。名指しの path の扱いと重大さは DOEFF150 と同じ(#1193) | 呼びの綴り | 呼びの頭の記号 |
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
  :verification-environment "agora_sim"      ; 模擬の環境(本番の組み立てのまま handler だけを差し替えて全 service を走らせる検証の環境)の置き場 1 つ — service ではない。DOEFF114・115 にしない(列は受けない)。DOEFF136 はこの下の deftest を「手元のテスト」と数える
  :foundation foundation                      ; root/foundation/ — service の外の層(同じ名の layer が要る)
  :open-layers [intent]                       ; 別の service から読んでよい層(既定 intent)
  :placed-dependencies [core intent protocol] ; 置き場の決まった module にだけ依存してよい層(DOEFF140 — 書かなければ当てない)
  :blind-definitions [(blind "controllers.screen.core.entrance:entrance-guarantee" :forbid-words ["view.policy" "message-class"]
                        :no-imports True :allow-requires ["doeff-hy.macros"] :why "…")]  ; 決めた材料だけで判じる定義(DOEFF141)
  :allowed-heads [(allowed-heads "controllers.screen.entry.server:_confined" :heads ["defk" ":" "<-" "Try" "when" "return"]
                    :why "…")]  ; 呼んでよい頭を決めた定義(DOEFF147)
  :call-sites [(call-site "Try" :files ["controllers/screen/**/*.hy"] :except ["controllers/screen/**/tests/**"]
                 :sites [(site "controllers.screen.entry.server:_confined" :count 1)] :why "…")]  ; 頭を呼んでよい場所と回数(DOEFF159)
  :broad-catches [(broad-catch "screen" :files ["controllers/screen/**/*.hy" "controllers/screen/**/*.py"] :except ["controllers/screen/**/tests/**"]
                   :carriers [(carrier "controllers.screen.core.deferred:carry-to-inbox" :event "Queued")] :why "…")]  ; 広い例外の捕捉は運搬の境界だけ(DOEFF160)
  :roles {:judgment "業務の判断をする関数" …}   ; role の説明
  :wire-modules ["controllers.foundation.records_client"]  ; JSON の送受信そのものを行う foundation の module(DOEFF120・13 節)
  :edge-mark "real_world"                  ; 縁のテストの pytest の印の名(DOEFF133 — :world-handlers が要る)
  :static-readers ["controllers.shared.core.foundation_closure:ClosureCase"]  ; 渡された値を実行せずに読むだけの定義 — この呼び出しの引数の中の参照は DOEFF133・136 の「届く」の辺にしない(module の印は行頭から始まる pytestmark の宣言だけを読む)
  :systems {:carriers ["scripts.declare_system:SystemPart"] :runners ["doeff_cluster.local:sim-cluster"]}  ; 系の値と系を回す入口 — defsystem の定義(本体と呼び出しの引数)と :carriers の呼び出しの引数の中から外の世界に届く検は、:runners のどれかにも届く時だけ縁(DOEFF133・書かなければ今までどおり)
  :edge-touches [http db process clock cluster network thread]  ; 縁と数える触れる先(DOEFF133 — 書かなければ全部)
  :test-forms {:tests ["test_*.hy" "test_*.py"] :check-scripts ["scripts/check_*.hy"] :runners ["*_deftest_runner.hy"]}  ; テストの形の決まり(DOEFF135)
  :retired-words [(retired-words "vocabulary" :words ["mail"] :files ["controllers/**/*.hy" "scripts/**/*.sh"]   ; 使わないと決めた綴り(DOEFF150)
                    :except ["controllers/kanban/forbidden-terms.json"] :rule-lines ["使わない"]
                    :contract-files ["docs/contracts/record-service.json"] :instead "Message")   ; 契約の綴りの文字列と defwire の欄の定義は数えない
                  (retired-words "names" :patterns [r"(?i)conversation"] :files ["controllers/chat/**/*.hy"] :in names :instead "chat・agent")]
  :retired-calls [(retired-calls "clock" :calls ["Now" "time.time"] :files ["controllers/**/*.hy"] :except ["**/tests/**"]   ; 使わないと決めた呼び(DOEFF151)
                    :instead "(GetMonotonic) か (GetTime)")]
  :handler-arguments {:files ["src/**/*.hy"] :exclude ["**/tests/**"] :store-names ["state" "store"] :store-suffixes ["-store"] :keep-mark "引数に残す理由:"}  ; handler の引数の決まり(DOEFF142)
  :business-fakes {:simulation ["sim/**"] :assembly ["*/entry/**"] :tests ["**/tests/**"] :production ["src/**"] :sets ["**/handler_sets.hy"] :simulation-prefix "emulated" :production-prefix "production" :business-modules ["app.orders"] :external-effects "tables/EXTERNAL-EFFECTS"}  ; 偽の handler の決まり(DOEFF143)
  :assembly-shape {:translation-point "with-*-translation" :retired-function "handlers-of" :translations "TRANSLATION-HANDLERS" :translation-layer "protocol" :intent-layer "intent"}  ; 組み立ての形(DOEFF155・156)
  :typed-values {:files ["controllers/**/*.hy" "controllers/**/*.py"] :except ["**/tests/**" "**/adr/**"]}   ; 公開面の型の注記を読む file(DOEFF144)
  :record-stubs {:files ["controllers/**/*.pyi"]}   ; 同じ名の .hy と突き合わせる型の宣言(DOEFF145)
  :single-point-vocabulary [(vocabulary-scope "job-phase" :patterns [r"\bJOB-PHASE-[A-Z]+\b"]   ; 判定を1か所に閉じ込めた語彙(DOEFF146)
                              :files ["controllers/screen/glue/**"] :except ["controllers/screen/glue/slice.hy"] :instead "slice.hy の答えを読む")]
  :confined-spellings [(confined-spelling "screen-http" :patterns [r"\(HttpRequest\s"] :files ["controllers/screen/**"]   ; 書いてよい file を決めた綴り(DOEFF148)
                         :except ["controllers/screen/tests/**" "controllers/screen/protocol/*.hy"] :why "…")]
  :counted-spellings [(counted-spelling "screen-post-seats" :pattern r"\x22POST\x22" :files ["controllers/screen/protocol/record_service.hy"]   ; 数を決めた綴り(DOEFF161)
                        :within ["append-record-events"] :count 1 :why "…")]
  :effect-census [(effect-census "screen" :files ["controllers/screen/effects.py" "controllers/screen/intent/socket.hy"]   ; effect の宣言の全体(DOEFF162)
                    :effects ["PostIntake" "Send" …] :why "…")]
  :field-holders [(field-holders "record-cache" :type "RecordCache" :files ["controllers/screen/core/*_types.py"]   ; 型の欄を持つ class の顔ぶれ(DOEFF149)
                    :holders ["ConversationSent"] :why "…")]
  :world-handlers [(world-handler "controllers.foundation.host:with-agora-process"   ; 外の世界に触れてよい定義の許可名簿(下の註)
                     :touches [http file clock env] :answers [HttpRequest ReadText]
                     :wraps ["doeff_core_effects.os_file:os-file-handler"])
                   (world-handler "controllers.foundation.clock:real-clock" :touches [clock]
                     :contract-test (none :doeff-test "packages/doeff-time/tests/test_clock.hy::test-clock-contract"))]   ; 縁の検を求めない handler(DOEFF137 — 理由つきの none)
  :boundary-parts [(boundary-part "controllers.agora_sim.local_screen_socket_client"   ; 模擬の環境から本物の外の世界へ届く境目の部品(下の註)
                     :touches [network clock thread] :reason "…")]
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
- `{:public-contract http}` の service は、公開の契約が HTTP の口だけ。他の service の `:depends-on` に載せると設定の誤り
  (in-process で読む近道を止める — 置き場の状態を持つ service が 2 つ目の持ち主を作らせないため・agora-redesign #978)。書かない = in-process。
  値は `http` だけ(他は設定の誤り)。載せられないので、その service の module を import すると DOEFF116 が当たる。
- `{:invariants ["module.path:関数" …]}` は、その service の業務の不変条件の関数(記録を受けて破りの列を返す純粋な判断・`:role "judgment"`)の列
  (agora-redesign #1559)。書かない = None。綴りが `module:名` でない・同じ名指しの 2 度書き・列でない値は設定の誤り。宣言の欠けと名指しの外れは DOEFF163。
- `:world-handlers` は、外の世界に触れてよい定義の許可名簿(agora-redesign #1106 — operator 2026-09-29 "only allow small set of handlers to touch
  actual world")。要素 `(world-handler "module.path:名" :touches [..] :answers [..]? :wraps [..]? :contract-test (none …)?)` の欄は、`:touches` = 触れる先(必須・閉じた語
  http・db・file・process・clock・env・cluster・network・thread)・`:answers` = 答える effect の名・`:wraps` = 中で動かす doeff の実 I/O の handler
  (`"module:名"`・名簿の定義は書けない)・`:contract-test` = 縁の検を求めない handler の印(書き方の定義元は DOEFF137 の直し方の文 — 値は理由のテストの名つきの
  `(none :doeff-test "file::名")` / `(none :repo-test "file::名")` で、型 `ContractTest::Waived(ContractTestWaiver::{DoeffContractTest, CoveredByRepoTest}(TestNodeId))`
  に読む。理由の無い記号 `none` は読めるが DOEFF137 が鳴る。ほかの値・空や形違いのテストの名は設定の誤り・書かなければ DOEFF137 が縁の検を求める・agora-redesign #1796)。名簿を書くには `:foundation` が要る。名簿を書いた repo では `[tool.doeff-linter.raw_side_effects]
  allowed_layers` は二重の宣言(設定の誤り)— 生の I/O を許す所は名簿だけで決める(規則は agora-redesign #1134 の子で足す)。
  読み違い(綴りが `module:名` でない・語の外・同じ定義や語の 2 度書き・`:touches` の無い要素)は設定の誤り。
- `:boundary-parts` は、模擬の環境から本物の外の世界へ届く境目の部品(agora-redesign #1797)— 人が回す入口や縁の台のように、実 I/O
  そのものが役目で effect に答える handler ではない module を名指す。要素 `(boundary-part "module.path" :touches [..] :reason "…")` の欄は、
  `:touches` = 許す生の副作用の種類(必須・`:world-handlers` と同じ閉じた語)・`:reason` = なぜ実 I/O そのものが役目か(必須)。宣言した
  module の中では、触れる先(生の I/O の分類を上と同じく写した物)が `:touches` に入る証拠を DOEFF106 で当てない(層の中の file と層の置き場の
  外の file の両方)。種類の外の証拠と宣言の無い module は今どおり当たる。証拠は索引に残るので、部品に届く deftest は DOEFF133 が縁と数え
  `:edge-mark` の印を求める。効くのは `:world-handlers` を書いた repo(許す所を名簿で決める repo)だけ。読み違い(綴りが module の dotted で
  ない・語の外・同じ module や語の 2 度書き・`:touches` か `:reason` の無い要素)は設定の誤り。
- 実 I/O の handler は doeff の目録 `data/world_handlers.json`(doeff-linter に同梱・agora-redesign #1209)から知る。目録の要素 = `{"handler": "module:名", "touches": [..], "why": "…"}`。`:wraps` は目録に在る物だけ(外は設定の誤り)。DOEFF131 は目録の handler のどれもを相手にし、DOEFF133 は目録の handler・名簿の定義・生の I/O の証拠のうち触れる先が `:edge-touches` に当たる物だけを縁に数える(生の I/O の分類は async・thread → thread、time・random → clock と写す。種つきの `random.Random` は生の I/O の証拠から外す — 後述「種つきの疑似乱数と外部 I/O」)。
- 読み違い(知らない鍵・重複した service や層・存在しない層や service の名・:foundation の層が無い)は `architecture.hy:行:列: 理由` の形で
  設定の誤り(終了コード 2)。
- editor-json の最上位に `architecture`(name・root・layers(name・summary・knows・does_not_know・question・roles)・shared・foundation・
  open_layers・services(name・dir・description・depends_on・layers・public_contract — `in-process` か `http`・invariants — `{module, name}` の列か null))。無ければ null。

| 規則 | 判じ方 | 鍵の細目 | 位置 |
|---|---|---|---|
| DOEFF114 | root の下の module が、宣言した service の宣言した層・shared の層・foundation のどれにも入らない(root の直下・service の dir の直下・宣言に無い dir の中 — 層が先の dir も旧い機能の dir も例外なし)。file ごとに 1 件。hint に移し先の案(`<root>/<:context のタグ>/<path の段の層か :role のタグの層>/<名>`)。`__init__` は外 | なし | file の頭 |
| DOEFF115 | root の直下の dir が宣言した service・shared・foundation でない / service の中の dir が宣言した層でない。dir ごとに 1 件(鍵の path は dir) | なし | dir の最初の file の頭 |
| DOEFF116 | service A の module が service B の module を import した時、B が A の :depends-on に無い、または読む先が、A の module の層が依存先で読んでよい層(その層の `:dependency-layers`、無ければ `:open-layers`)でない。組み立ての層(agora は entry)だけ `:dependency-layers [intent protocol]` で依存先の翻訳の handler も読める(operator 2026-09-28 "A okay")。shared と foundation は service ではないので見ない。宣言の DOEFF109 はこれに置き換わる(architecture.hy の在る repo では DOEFF109 の設定を置けない) | import の先 | import の記号 |
| DOEFF117 | 宣言した依存(A の :depends-on の B)を、A のどの module も読んでいない。info。全体の実行だけ | `A>B` | architecture.hy の defservice の名 |
| DOEFF140 | `:placed-dependencies` の層の module(service と shared の置き場 — 層が先の旧い dir と foundation は外)が、root の下の層の置き場の外の module(service の dir の直下・宣言に無い dir の中)を import する。読む先は import の綴りの file(無ければ親の module の file)で決め、層の索引に在る物・package の印(`__init__`)・root の外は数えない。渡された file の import と置き場だけで判じる(repo 全体の索引は読まない)。同じ module は 1 件。既定 critical(agora-redesign #1188) | 読む先の module | 最初の import |
| DOEFF141 | 決めた材料だけで判じる定義 — `:blind-definitions` の定義ごとに、定義から呼び出しと名指し(値として渡す所)で推移的に届く repo の Hy の定義(入れ子を含む・索引が名前を解いた先で、repo に Hy の file が在る module だけ)の本体に、`:forbid-words` の綴りが部分一致で在る(註は除く)と、届いた定義と語ごとに 1 件。`:no-imports True` なら定義の module の import と require(`:allow-requires` の module の require は macro の読み込みなので除く)を module ごとに 1 件。宣言した定義が無ければ architecture.hy の位置で 1 件(母集団 0 を緑にしない)。読むのは宣言の module と届いた先の module の file だけ(repo 全体の索引は組まない)。全体の実行だけ。既定 critical(agora-redesign #1368) | `<届いた定義>:<語>` / `import:<module>` / `missing` | 語の最初の出現・import・architecture.hy の宣言 |
| DOEFF147 | 呼んでよい頭を決めた定義 — `:allowed-heads` の定義ごとに、定義の form(入れ子を含む)の `( … )` の頭の綴り(記号と keyword — 特殊形式と macro も含む)が `:heads` に無ければ、頭ごとに 1 件。文字列・註・`#_` で読み捨てた form・tuple と `[ … ]`・`{ … }` の要素は頭に数えない。宣言した定義(file の top level の、頭が `def` で始まる form)が無ければ architecture.hy の位置で 1 件(母集団 0 を緑にしない)。読むのは宣言の module の file だけ(索引も組まない)。全体の実行だけ。既定 critical(agora-redesign #1372・#1413) | `<頭>` / `missing` | 頭の最初の出現・architecture.hy の宣言 |
| DOEFF148 | 書いてよい file を決めた綴り — `:confined-spellings` の群ごとに、`:files`(repo の根に錨を下ろした glob)に当たる Hy・Python(`.hy`・`.py`・`.pyi`)の file のうち `:except` に当たらない物を読み、註を落とした本文(文字列の中は数える — DOEFF146 と違う)に `:patterns` のどれかが当たれば file ごとに 1 件(当たりの数を message に書く)。`:except` が空なら `:files` のどこにも書かない綴り。`:files` に当たる file が無い群は architecture.hy の位置で 1 件(母集団 0 を緑にしない)。歩くのは glob の頭の `*` を含まない dir だけ。全体の実行だけ。既定 critical(agora-redesign #1373・#1436 — 元は agora-controllers の一時の判定 intake_only_rules.hy) | `<群>` / `<群>:missing` | file の最初の当たり・architecture.hy の宣言 |
| DOEFF161 | 数を決めた綴り — `:counted-spellings` の宣言ごとに、`:files` に当たる file(拡張子を問わない — Hy と Python は註を落とし文字列は数える・ほかはそのまま読む)で `:pattern` の当たりを数え、`:count N`(ちょうど)か `:at-least N`(以上)に合わなければ 1 件。`:within [..]` があれば、Hy の file の top level の定義(頭が `def` で始まる form)のうち名指した物ごとに、その form の中で数える。数える file や定義が無ければ architecture.hy の位置で 1 件(母集団 0 を緑にしない)。全体の実行だけ。既定 critical(agora-redesign #1373・#1437 — 元は agora-controllers の一時の判定 intake_only_rules.hy) | `<名>` / `<名>:<定義>` / `<名>:missing` / `<名>:<定義>:missing` | 最初の当たりか定義の頭・architecture.hy の宣言 |
| DOEFF162 | effect の宣言の全体 — `:effect-census` の宣言ごとに、`:files` の Hy・Python の file(註を除く)で `:base`(既定 `EffectBase`)を継ぐ class の宣言(Python の `class X(… base …):`・Hy の `(defclass [飾り] X [… base …])`。`:base` が `EffectBase` なら Hy の `(defeffect X …)` も — defeffect は常に EffectBase を継ぐ・#1464)を集め、`:effects` の一覧と比べる。一覧に無い宣言・同じ名の 2 つ目の宣言は宣言の位置で、宣言の無い一覧の effect と `:files` に当たる file の無い宣言は architecture.hy の位置で 1 件ずつ。全体の実行だけ。既定 critical(agora-redesign #1373・#1438 — 元は agora-controllers の一時の判定 intake_only_rules.hy の 2) | `<名>:<effect>` / `<名>:<effect>:twice` / `<名>:<effect>:missing` / `<名>:missing` | 宣言の位置・architecture.hy の宣言 |
| DOEFF149 | 型の欄を持つ class の顔ぶれ — `:field-holders` の宣言ごとに、`:files` に当たる Python の file(`.py`・`.pyi`)の module の直下の class のうち、本体の直下の欄(注記つきの代入 `名: 注記`)の注記に `:type` の綴りが語として在る class を持ち手と数え、`:holders` の一覧(空でよい = どの class も持たない)と比べる。一覧に無い持ち手は class の位置で、その欄を持たない(か無い)一覧の class は architecture.hy の位置で 1 件ずつ。`:classes` を書けば名指した class だけを数え、無い class も 1 件。`:files` に当たる Python の file が無い宣言も 1 件(母集団 0 を緑にしない)。構文木にならない file は読めない物として出す。全体の実行だけ。既定 critical(agora-redesign #1374 — 元は agora-controllers の一時の判定 record_body_rules.hy の ③) | `<名>:<class>` / `<名>:<class>:absent` / `<名>:<class>:missing` / `<名>:missing` | class の位置・architecture.hy の宣言 |
| DOEFF159 | 頭を呼んでよい場所と回数 — `:call-sites` の宣言ごとに、`:files` の glob に当たる Hy の file(`:except` を除く)の `(頭 …)` の呼び(文字列・註・`#_` の中は除く)を集め、どの `:sites` の定義の中にも無い呼びは file ごとに 1 件。場所ごとに、`:count` を書けば呼びの数の食い違い、`:parent` を書けば直ぐ外の form の頭の食い違い、`:branch` を書けば条件の form にその記号が在る cond・when・if・unless の枝の外の呼びを 1 件。場所の定義が無い・`:files` に当たる file が 0 なら architecture.hy の位置で 1 件(母集団 0 を緑にしない)。歩くのは glob の字義どおりの頭の dir だけ。全体の実行だけ。既定 critical(agora-redesign #1372・#1414) | `<頭>:outside` / `<頭>:count:<名>` / `<頭>:parent:<名>` / `<頭>:branch:<名>` / `<頭>:missing:<名>` / `<頭>:empty` | 最初の呼び・場所の定義の名・architecture.hy の宣言 |
| DOEFF160 | 広い例外の捕捉は運搬の境界だけ — `:broad-catches` の群ごとに、`:files` の glob に当たる Hy・Python の file(`:except` を除く)の広い捕捉を集める。広い捕捉 = Hy の `(except [] …)` と、捕まえる型の form に `Exception`・`BaseException`・`AssertionError`(`.` で区切った最後の段)が在る `(except [型] …)`・`(except [名 型] …)`(文字列・註・`#_` の中は除く)、Python の型の無い `except:` と、型がそれらの名・属性・その組の `except …:`。許すのは `:carriers` の定義(Hy の file の top level の form)の中で、捕捉が例外を名で束縛し、その捕捉の中の `(:event の出来事 …)` の呼びがその名を引数に直に渡す物だけ。境界の外の捕捉は file ごとに 1 件、名を束縛しない・出来事へ渡さない境界の中の捕捉は境界ごとに 1 件。境界の定義が無い・`:files` に当たる file が 0 なら architecture.hy の位置で 1 件(母集団 0 を緑にしない)。Python の file が構文として読めなければ実行の誤り。全体の実行だけ。既定 critical(agora-redesign #1372・#1415 — 元は agora-controllers の一時の判定 fault_boundary_rules.hy の (d)) | `<名>:outside` / `<名>:unbound:<定義の名>` / `<名>:missing:<定義の名>` / `<名>:empty` | 最初の捕捉・architecture.hy の宣言 |
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

| 規則 | 問い(英語のまま・`src/project/semantic.rs` の 1 か所) | 当てる層(設定) | 既定の閾値 | 既定の重大さ(3 節) |
|---|---|---|---|---|
| DOEFF201 | 要求を相手の話し方へ言い換えるのを越えて、業務の判断(誰に許すか・業務の決まり・宛先・業務の結果)をしているか(jev-lint の J2) | `semantic.business_decision.layers` | warning p ≥ 0.8・info p ≥ 0.6 | critical |
| DOEFF202 | 通信の手段(URL や query・HTTP の method や status・JSON の wire・SQL・宛先の address)を知っているか(jev-lint の J3) | `semantic.transport_knowledge.layers` | warning p ≥ 0.6・info p ≥ 0.4 | critical |
| DOEFF203 | deff の理由の註が受け入れる理由に当たるか(11 節) | `semantic.plain_callable` | warning_min 0.4・info_min 0.4 | 重さから |
| DOEFF204 | 処理を持つ method のある class が value / external-world / stateful / other のどれか(12 節) | `semantic.class_role` | warning_min 0.7・info_min 0.5 | 重さから |
| DOEFF205 | judgment / program の定義が形の検めと判断を混ぜているか(14 節) | `semantic.mixed_concerns` | warning_min 0.7・info_min 0.5 | critical |

- **全体の実行(text / json / editor-json の repo 全体)の既定は、cache を使い変わった定義だけを撃つ**(operator 2026-09-29 "jev involved lints are to be
  run everywhere every time with cached by default"・agora-redesign #1160): 対象 = path の引数の file、無ければ git で変わった file。そのうち手元の
  cache に答えの無い定義だけを撃ち、残りの答えの無い定義は代理が設定されていれば代理に「覚えている時だけ」問う(下の「Jev の呼び出しを覚える代理」)。
  問うはずだった定義に答えを得られなければ `semantic.unmeasured`(測れなかった)に数え、error の違反が無くても終了コード 3。
- **1 file の `--stdin`(書いた直後の hook・エディタ)の既定は cache を読むだけ**(hook の全体 3 秒の上限の中で層の規則の知らせを失わないため・#1190 の
  決定 A)。`--semantic-changed` を名指せば cache に答えの無い定義だけを撃つ。
- `--semantic` = 対象の定義を cache に答えが在っても撃ち直す・`--semantic-all` = 設定した層の全定義を撃つ・`--semantic-cache-only` = 撃たない(cache を読むだけ・
  測れなかったに数えない — 網の無い所の実行)。
- **書きかけで読めない定義(閉じない括弧・対応しない閉じ括弧・閉じない文字列)は、どの実行でも問わない**(未判定に数える)。
- 問う定義 = 設定した層の Hy の最上位の defn・defk・deff・defp・defpp・defhandler・defeffect・defclass・defrecord・defenum。
- state = 定義の名・kind・file・申告の `:tags` を消した source(`semantic.source_limit` 字 = 既定 1,800 で切る)・置かれた層の説明(architecture.hy の layer の説明か、
  `layers.describe`)。
- cache = repo の根の `.doeff-linter/semantic-cache/<鍵>.json`(git の外に置く — `.gitignore` に足すかは repo ごと)。鍵 = sha256(model・問いの JSON・層の説明・
  タグを消した source)。申告の役は鍵に入れず、判定の後にコードで比べる。cache の答えが無い定義は違反にせず、最上位の `semantic.unjudged` に数える(合格に倒さない)。
  cache の答えは確率と choice のほかに、答えに載った費用 `reported_cost_usd`(上流が載せた時だけ・載せない答えは null で 0 と区別する)と
  `input_tokens`・`output_tokens`(usage に在る時だけ)を持つ。以前の欄 `cost_usd`(載らない時も 0 と書いていた)は読まない — 前の答えの費用は不明と
  して読む(agora-redesign #1892)。
- 重さは warning か info だけ(当たり外れを測り終えるまで error にしない — 設定にも error の欄は無い)。外れは誤判定の一覧に載せる(下の「誤判定の一覧と正例の一覧」)。
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
(`model`・`wire`・`judged`・`unjudged`・`unmeasured`(測れなかった数 — Jev に問えなかった定義と、proxy の覚えを読む束が待ち
`proxy_peek_timeout_ms` の内に返らなかった定義。後者は `unjudged` に入れない・agora-redesign #1885)・`asked`・`peeked`(proxy が覚えていた答えを受け取った数)・`cost_usd`(上流が答えに費用を載せた回の和 —
載せるのは Vercel の AI Gateway だけで、TypeSafe 直は載せない)・`cost_unreported`(上流を呼んだのに費用が載っていなかった回の数 — 0 でなければ
`cost_usd` は下限)・`remembered`(proxy の覚え・相乗りで答えた回の数 — 見出し `x-jev-proxy` が `hit` / `coalesced`。上流を呼んでいないので
費用と token を数えない)・`input_tokens`・`output_tokens`(上流を呼んだ回の usage の和)・
`served_model`・`calibration` = not-run / ok / drifted / failed・`false_positives`・`labeled` — 下の節)。`wire` は宛先の形と決め方(例 `direct(default)`・`direct(env)`・`direct(repo)` = repo の代理)。

### 誤判定の一覧と正例の一覧(agora-redesign #1039)

Jev の判定は確率で、高い確率の外れと低い確率の当たりが混ざる。外れを登録簿に載せると「既知の破れ」として重大さ(level)が残り続けるので、
人が外れと判定した当たりは登録簿とは別の置き場に置き、**違反として出さず、件数にも入れない**。

```toml
[tool.doeff-linter.semantic]
false_positives = ["scripts/doeff_lint/JEV-FALSE-POSITIVES"]   # 誤判定の一覧(反例)
true_positives = ["scripts/doeff_lint/JEV-TRUE-POSITIVES"]     # 人が本当の違反と判定した当たり(正例)
```

- どちらも repo の根からの dir の列。中の `*.txt` 1 つが判定 1 つで、1 行目が違反の鍵(5 節の綴り — editor-json の `key` をそのまま写す)、
  2 行目から後が人の判定の理由(空でない行を空白でつないで読む)。**理由の無い file は判定として読まず**、`errors` に理由を出す
  (理由の書けない判定を黙って効かせない)。読めない dir・file も `errors`。
- 効くのは意味の規則(DOEFF201〜205)の当たりだけ。鍵が誤判定の一覧に載った当たりは `violations` に出さず、最上位の
  `semantic.false_positives` に外した数を出す(text の出力は stderr の要約の行「意味の規則の誤判定 N 件」)。登録簿にも載っていても外す。
- 正例の一覧は違反の出し方を変えない(載っていても閾値に届かなければ出ない)。当たり外れを測る材料として読むだけ。
- 同じ鍵が両方の一覧に在れば食い違いとして `errors` に出し、どちらとしても読まない。
- `semantic.labeled` = 人の判定と Jev の答えの突き合わせ:
  `positives` / `negatives` = `{listed: 一覧に載った数, judged: そのうち Jev の答えのある数, flagged: そのうち今の閾値で当たりになる数}`、
  `items` = 答えのある判定ごとの `{key, rule, expect(true = 正例), probability, flagged}`(鍵の順)。閾値に届かない答えも載る
  (閾値を決め直す材料)。答えの無い判定(未判定の定義・もう無い定義)は `listed` にだけ数える。
- 較正の手順: 判定を付けた file を `--semantic <file>…`(か `--semantic-all`)で問い、`semantic.labeled` の正例の `flagged / judged`
  (当たりを拾えた割合)と反例の `flagged / judged`(外れを出す割合)を読む。同梱の較正の見張り(model の中身が変わったかの検め)とは別の物で、
  見張りの幅には入れない(人の判定は閾値の際の物が多く、幅から外れても model の変化とは限らないため)。
- 読みの限界: 鍵は定義の名で作るので、定義の中身が変わっても判定は載ったまま効く(登録簿と同じ)。中身が大きく変わった定義の判定は人が見直す。


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
- **`defsystem` は定義**(doeff-hy・agora-redesign #833 — 関数に展開され、job の行 `(名 (job の関数 foundation …) :needs … :environ …)` の
  式は「名 → Program」の Program の値そのもので、実行しない)。定義の中と同じ判定で下るので、job の行の式は拾わず、その中で答えとして
  使う所(`:environ {"X" (str (f …))}` など)だけを拾う。鍵の `<定義>` は系の名。事実: agora の本線で module の最上位の defsystem の
  job の行が 43 件 `<module>` として当たっていた(agora-redesign #913)。
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
              "inferred": [EffectRef],          // 推論(下)
              "complete": true},                // 推論が追いきれたか — 追えない呼び(repo の外の関数・deff・method)を撃っていれば false で、
                                                // inferred は見えた分だけ(エディタは空でも「effect なし」と描かない)
  "tags": {"context": "demo", "role": "program"}
}],
"bindings": [{
  "form": "<-",                          // <- | val | var | setv | :=
  "modifier": "lazy" | null,             // (lazy val …)・(session var …) の前の語(form は val / var)
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


## 19. (退役)DOEFF129

agora-redesign #955 で消した(#942 の決定 1 — judgment / program を effect の有無で分ける軸は責務の境界ではない)。番号は他の規則に使い回さない。


## 21. 翻訳の handler が業務の intent を出す — DOEFF130

agora-redesign #956(#942 の決定 2 — 責務の境界の規則)。翻訳の層(protocol)の handler は、受けた intent を doeff の汎用の effect
(HttpRequest・doeff-records の読み書き・時計・file・process・Ask …)へ出し直すだけで、業務の intent(層 intent の型)を出さない。
業務の intent を出すと、翻訳の handler が業務の判断と流れを抱え込む。

- **対象**: 設定の `translation_effects.handler_layers` の層(既定 protocol)の Hy の module の、最上位の handler — `(defhandler 名 …)` と、
  引数がちょうど `[effect k]` の `defk`・`deff`・`defn`。母集団は層の母集団(4 節)。handler でない defk は対象にしない(handler から
  呼ばれれば、その先として辿られる)。
- **業務の intent**: 実行する呼びの頭を module まで解いた名が、`translation_effects.intent_layers` の層(既定 intent)の module の、
  頭が大文字の名(型)であること。`defeffect` でも `defclass` でもよい(層 intent は型だけを置く — 型を実行すれば intent を出している)。
  module は層の母集団の索引で解く(package の `__init__` も)。数えるのは handler と**同じ service** の intent だけ — 他の service の intent
  (公開の契約)を出すのは翻訳の仕事として許す(DOEFF156 の「他の service の公開の効果」と同じ読み・agora-redesign #1134 の決め)。
  handler か intent のどちらかの service が決まらない(層が先の置き場)時は、今までどおり数える。
- **推論**: handler の本体で実行する呼び(`(<- …)`・`(! …)` — 18 節と同じ `signatures::World` の読み)を順に見て、頭が業務の intent なら
  当たり、頭が repo の defk ならその defk の実行する呼びへ進む(import した defk の先も)。進む defk の数の上限は
  `translation_effects.max_depth`(既定 8・0 なら handler の本体で直に出す intent だけ)。同じ handler の中で同じ defk は 1 度だけ見る
  (呼びの輪で止まる)。追えない呼び(repo の外の関数・deff・method)の先は数えない。
  - 定義の本体の名だけで判じる検査(DOEFF201・agora の `check_business_fakes.hy`)は、handler が import した関数を経由して intent を
    出す形をすり抜ける。この規則はその穴を塞ぐために defk の先まで辿る(#942 の独立レビュー)。
- **場所**: handler の本体の、その intent に至る最初の呼びの頭。文に handler・層・経由した defk の道(`a → b`)・intent を書く。
  handler ごと・intent ごとに 1 件。
- **重さ**: error(決まった規則 — handler の本体と repo の定義だけで判じる)。重大さの宣言(`rules.DOEFF130.level`)は使う側の設定で書く
  (agora は critical — #957)。
- **鍵**: `<path>::<law か DOEFF130>::<handler>::<intent>`(handler は mangle した綴り、intent は module まで含めた名)。
- **設定**: 節 `[tool.doeff-linter.translation_effects]` を書かなければ既定(protocol・intent・8)で、その名の層が両方とも在る時だけ当たる。
  節を書いたのに層の名が無ければ設定の誤り(終了コード 2)。
- **作らなかった側**: repo で定義した土台(foundation)の effect は判じない(土台は汎用の effect の置き場)。Python の handler は読まない。


## 16. 読めない Hy の file — DOEFF128

- 読み取り器(doeff-indexer の hy_index::reader)が括弧か文字列の閉じない所を見つけた file は、規則の判定が読めた所までで違反が欠ける。
  前は理由の文を全体の `errors` に積むだけで、エディタのその file の違反の欄は空、hook は何も言わなかった(2026-09-28 — agora の
  controllers/durable/protocol/contract.hy を読み取り器の誤りで読めなかった)。
- 規則が読む file(層の置き場・定義の規則の母集団・検の置き場・業務の名の母集団)のうち読めない物は、有効な規則の一覧に関わらず、
  最初の読めない所に error の違反 DOEFF128(鍵 `<path>::DOEFF128`・登録簿の外)として出す。エディタ・hook・text の全部に出る。
- 読み取り器は f 文字列の置き換えの欄 `{…}` の中を Hy の式として飛ばす(欄の中の文字列・入れ子の f 文字列・括弧で文字列を閉じない
  — Hy の `hy.read_many` と同じ)。`{{` は字面の `{`。

## 20. 定義の本体の文字 — editor-json の `bodies`(agora-redesign #910)

doeff-runner の読む面(webview)は定義などの実体を HTML のカードで見せ、**文字で出すのは本体だけ**。その本体の行の材料。本体の文字の
形は operator が承認済み("yeah val var when match is perfect.")で、表の正本は `docs/design/hy-reading-plane/artifacts/v2/design.md`
2.2 節と `v3/design.md` 2 節(意味の正本 ADR-DOE-HY-006)。Hy の form の読み方は `src/project/body_view.rs` の 1 か所に置き、面は行と
字の範囲を描くだけにする(#849 の決定「読み方の写しを持たない」)。呼びの読み(何を呼びと読むか・括弧の要否)は 17 節の
`call_view.rs` の読み手を式 1 つずつ呼んで使う。版 2 への欄の追加(古いエディタは読み飛ばす)。`--stdin --path <file>` の時だけその
file の defk / deff の分を出す(全体の実行では空)。

```jsonc
"bodies": [{
  "kind": "defk",                   // defk | deff(signatures と同じ定義)
  "name": "judged", "path": "/abs/core/conversation_input.hy",
  "range": {…}, "full_range": {…},  // 名の範囲・定義の form 全体(signatures と同じ)
  "lines": [{
    "line": 49,                     // source の行(0 始まり — 面は 1 を足して見せる)。1 つの行に文が 2 つあれば同じ番号が続く
    "depth": 0,                     // 字下げの段(本体の一番外 = 0・when / if / match / for の中身で 1 つ深い)
    "pad": 0,                       // 段の後ろに足す空白(面は "  " × depth + " " × pad の後ろに字を並べる)
    "segments": [                   // 行の字 = text をつないだ物
      {"text": "val", "role": "keyword", "range": {…}, "effect": null, "definition": null},
      {"text": " ", "role": "text", "range": null, "effect": null, "definition": null},
      {"text": "str | None", "role": "type", "range": {…}, …},        // range = (<- x T e) の T(注釈が無ければ null)
      {"text": "target", "role": "name", "range": {…}, …},
      {"text": "⇐", "role": "bind", "range": null, …},
      {"text": "target-of", "role": "call", "range": {…}, "definition": {"path", "range"} | null},
      {"text": "SettleIntake", "role": "effect", "range": {…}, "effect": "SettleIntake", …}
    ],
    "binding": 3 | null,            // この行が描く束縛(同じ出力の bindings の番号)
    "warning": {"kind": "setv", "message": "setv の代わりに (val x …)、…"} | null
  }]
}]
```

- **字の役**(閉じた集合): `keyword`(`val`・`var`・`lazy`・`session`・`setv`・`return`・`resume`・`when`・`while`・`if`・`else`・`match`・`cond`・`for`・`in`・`try`・`except`・`as`・`finally`・`raise`・`from`・`continue`・`break`)・`type`(束縛の型)・
  `unknown-type`(型が分からない印 `?`)・`name`(束ねる名)・`bind`(`⇐`)・`assign`(`=` と `:=`)・`effect`(effect の値を作る呼びの頭 —
  面が `effect` の名で絵を選ぶ)・`call`(それ以外の呼びの頭)・`text`(引数・演算子・字面・区切り)・`lisp`(表に無い form)・`comment`(本体の途中の行全体の註 `;; …` を `# …` にした行)。
- **range**: source から来た字は source の範囲を持ち、区切り・`⇐`・`?` のように source に無い字は null。`keyword` と `type` は綴りが
  変わりうる(`<-` → `val`・`(| A B)` → `A | B`)。それ以外の役は範囲の字と text が一字一句同じ。
- **型**: 16 節の `bindings` の型と同じ物(`binding` の番号の束縛の `type` を `A | B`・`H[a, b]` と綴る)。分からなければ `?`(別の型で
  埋めない)。

| 元の form | 本体の文字 |
|---|---|
| `(<- x T e)` / `(<- x e)` | `val T x ⇐ e` |
| `(val x e)` / `(var x e)` | `val T x = e` / `var T x = e` |
| `(val x ! e)` / `(val x (! e))` | `val T x ⇐ e`(撃つ値 — `(<- x e)` と同じ意味) |
| `(lazy val x e)` / `(lazy var x e)` / `(session val x e)` / `(session var x e)` | `lazy val T x = e` … |
| `(:= x v)` | `x := v` |
| 本体の `(setv x e)` | `setv x = e` + `warning`(val / var へ) |
| `(E a)` / `(! (E a))` / `(<- (E a))`(E が effect) | `E(a)`(E が `effect` の役・`!` は出さない) |
| `(! (f a))` / `(<- (f a))`(effect でない) | `!f(a)` |
| `(f a b)`・`(.m o a)`・`(get d k)`・`(. o a)`・`(f a :k v)`・演算 | 17 節の置き換えと同じ(`f(a, b)`・`o.m(a)`・`d[k]`・`o.a`・`f(a, k=v)`・`a + b`) |
| `(return v)` / `(resume v)` | `return v` / `resume v` |
| `(<- x T e :absent F)` | `val T x ⇐ e` の後ろに `:absent F` を `lisp` のまま |
| `(when c …)` | `when c` + 1 つ深い段の中身 |
| `(if c a b)` / `(if c a)` | `if c` + a、`else` + b(else は if の列に揃え、行の番号は b の行) |
| `(match v P x P :if g y …)` | `match v` + 腕ごとに `P → x` / `P if g → y`(`→` の前は腕の pattern の最大幅 + 空白 1 つで揃える)。pattern は `(C)` → `C`・`(C a :k p)` → `C(a, k=p)`・`(\| p q)` → `p \| q`・`[p q]` → `[p, q]`・名と字面はそのまま |
| `(for [x xs] …)` / `(for [[a b] xs] …)` | `for x in xs` / `for a, b in xs` + 1 つ深い段の中身 |
| `(lfor x xs :if c e)` / `gfor` / `sfor` | `[e for x in xs if c]` / `(e for …)` / `{e for …}`(節の重ねは `for … for …`) |
| `(do a b …)` | `do` の字は出さず中身を並べる(文の場所なら今の段に。腕の `→` の後ろなら 1 つ目をその行に続け、残りをその列に揃える) |
| `(cond c x … True z)` | `cond` + 腕ごとに `c → x`(条件の幅を揃える・最後の `True` は `else →`)。1 行に収まらない条件があれば cond 全体を lisp |
| `(while c …)` | `while c` + 1 つ深い段の中身 |
| `(try … (except [e T] …) (except [[A B]] …) (except [] …) (else …) (finally …))` | `try` / `except T as e` / `except (A, B)` / `except` / `else` / `finally`(節の語は try の列・中身は 1 つ深い段) |
| `(continue)` / `(break)` / `(raise)` / `(raise e)` / `(raise e :from c)` | `continue` / `break` / `raise` / `raise e` / `raise e from c` |
| `(setv (get x k) v)` / `(setv (. o a) v)` / `(setv o.a v)` | `x[k] = v` / `o.a = v`(中身の書き換え — 束縛ではないので setv の警告を付けない・ADR-DOE-HY-006 の対象外) |
| 塊の中の文と文の間の行全体の `;; …` | `# …` の行(`comment` の役・source の範囲つき) |
| `(del x …)` / `(assert c m)` | `del x, …` / `assert c, m` |
| 式の中の `(if c a b)` | `a if c else b`(演算の項・method の的では括弧で包む)。束縛・return の値と腕の `→` の後ろでは、100 字を超えれば縦の `if` / `else` に開く |
| 式の中の `(fn [x] e)` / `(fn [a b] e)` / `(fn [] e)` | `x ⇒ e` / `(a, b) ⇒ e` / `() ⇒ e`(引数は名だけ・本体は式 1 つ。`#*`・既定値は lisp のまま) |
| `(cut xs a b)` / `(cut xs b)` / `(cut xs)` / `(cut xs a b s)` | `xs[a:b]`(`None` の端は空)/ `xs[:b]` / `xs[:]` / `xs[a:b:s]` |
| 式の中の内包(`lfor` / `gfor` / `sfor` / `dfor`) | `[e for …]` / `(e for …)` / `{e for …}` / `{k: v for …}`(`:setv` / `:do` の節は lisp のまま) |
| 字面 `#(a b)` / `#(a)` / `[a b]` / `#{a b}` / `{k v}` | `(a, b)` / `(a,)` / `[a, b]` / `{a, b}` / `{k: v}`(鍵が keyword の辞書は元の字のまま) |
| 束縛・return の値の場所の `cond` / `match` / `do` / `try` | 縦に開く(1 行目は束縛の行に続け、中身は塊の語の列に揃える) |
| 表に無い form(知らない macro・`unless`・内包の `:setv` / `:do`・名が組でない `for`・腕の欠けた `match`・表に無い pattern・組への分解の setv・行の途中に註のある式) | 元の lisp のまま(`lisp` の役)。推測で描かない |

- **平らにする**: 式の中の形(3 項・lambda・cut・内包・字面)と、cond の条件・match の pattern と `:if`・except の頭は、source で複数行
  でも改行と字下げを空白 1 つにして 1 行に描く。行の途中に註(文字列の外の `;`)があれば平らにせず lisp のまま(註が後ろの字を飲むため)。

- **lisp の島**: 式の中で置き換えなかった括弧(知らない頭)は、その括弧だけ元の lisp のまま `lisp` の役で出す(中の呼びも置き換えない)。
- **複数行**: 式や lisp が次の行へ続く所は新しい行(`line` = その source の行・`depth` は同じ)にし、source の字下げの文の頭からの差を
  `pad` に足す。
- **腕の中で始まる塊**: `A → match v` のように `→` の後ろで始まる when / if / match / for の中身は、腕の段 + 1 の段と、塊の語の列に揃える
  `pad` で持つ(字の数え方は面と同じ — 段 1 つ = 空白 2 つ・字 1 つ = 1 列)。
- **内包**: 部分(本体・名・元・条件)がそれぞれ 1 行に収まれば、source で複数行でも 1 行に並べる(行の番号は内包の頭の行。後ろの字の
  range は後ろの source の行を指す)。収まらなければ lisp の島のまま。
- **本体の頭の文字列**: 後ろに文がある時は説明として出さない(文字列 1 つだけの本体はその文字列が答えなので出す)。
- 16 節の束縛の読みも同じ変更で直した: `(val x ! e)`(Hy の reader は `!(e)` も `!` と `(e)` の 2 つの要素に読む — ADR-DOE-HY-006 §3)
  の 2 つ組を撃つ値に畳み、`(lazy val x e)`・`(session var x e)` を束縛として読んで `bindings` の欄 `modifier`(`lazy` | `session` | null)
  に前の語を載せる(版 2 への欄の追加)。

### 種つきの疑似乱数と外部 I/O（DOEFF133・agora-redesign #1564）

生の I/O の索引は、種を明示した `random.Random(seed)`（Hy の `(random.Random seed)`・`:x seed`）を外部 I/O に数えない。数値定数だけでなく、変数の種も明示された引数として扱う。生成した instance の method、instance を別名へ代入してからの method、`random.Random` の型注釈、関数内の import も、それだけでは外部 I/O ではない。module の import と同じ綴りの method（`(.random rng)`・`(. rng (random))`）は module 自体の参照と区別する。

種を省略した `random.Random()`、明示的な `None`、展開引数だけで種が確認できない生成、module 大域の `random.random()` 等、`SystemRandom`、`uuid.uuid4`、`secrets` は外部 I/O のまま。種を作る式自体が時計などを読む場合も、その式の I/O は残す。変数の実行時の値が `None` かどうかまでは推論しない。

索引は呼び出しの引数の形と型専用の参照を内部の事実として持ち、file の cache にも運ぶ。公開 JSON の形は変えない。外部 I/O の抽出元を直すため、DOEFF133 のみの例外表は追加しない。
