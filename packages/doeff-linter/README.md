# doeff-linter

A high-performance linter for enforcing code quality and immutability patterns in Python.

## Features

- **27 specialized rules** for code quality, immutability, and workflow replay safety
- **層の規則 DOEFF101〜108**(Hy と Python の両方): 純粋な層と外の世界に触る層の決まりを repo ごとの設定で判じる(下の「層の規則」)
- **エディタ向けの出力** `--output-format editor-json`(doeff-runner が表示する。判定の正本はこの linter)
- **Configurable via pyproject.toml**
- **noqa comments** for per-line rule suppression
- **Fast** - written in Rust for maximum performance
- **JSON Lines logging** for violation tracking and statistics

## Installation

### From Source (Rust)

```bash
cd packages/doeff-linter
cargo install --path .
```

## Quick Start

```bash
# Lint all Python files in the current directory
doeff-linter

# Lint specific files or directories
doeff-linter src/ tests/test_specific.py

# Show detailed output
doeff-linter --verbose

# Output as JSON
doeff-linter --output-format json
```

## Configuration

Configure the linter in your `pyproject.toml`:

```toml
[tool.doeff-linter]
# Enable all rules except specific ones
enable = ["ALL"]
disable = ["DOEFF004"]

# Or enable only specific rules
# enable = ["DOEFF001", "DOEFF002", "DOEFF007"]

# Exclude paths (applies to directory scanning by default)
exclude = [".venv", "build", "tests/fixtures"]

# Log violations to a file for later analysis (JSON Lines format)
log_file = ".doeff-lint.jsonl"

# Rule-specific configuration
[tool.doeff-linter.rules.DOEFF003]
max_mutable_attributes = 3

[tool.doeff-linter.rules.DOEFF009]
skip_private_functions = true
skip_test_functions = true
```

## Available Rules

| Rule ID | Name | Description |
|---------|------|-------------|
| DOEFF001 | Builtin Shadowing | Functions should not shadow Python builtin names |
| DOEFF002 | Mutable Attribute Naming | Mutable attributes must use `mut_` or `_mut` prefix |
| DOEFF003 | Max Mutable Attributes | Limit the number of mutable attributes in a class |
| DOEFF004 | No os.environ Access | Forbid direct access to environment variables |
| DOEFF005 | No Setter Methods | Classes should not have setter methods |
| DOEFF006 | No Tuple Returns | Functions should not return tuples (use dataclasses) |
| DOEFF007 | No Mutable Argument Mutations | Functions should not mutate dict/list/set arguments |
| DOEFF008 | No Dataclass Attribute Mutation | Dataclass instances should be immutable |
| DOEFF009 | Missing Return Type Annotation | Functions should have return type annotations |
| DOEFF010 | Test File Placement | Test files must be under `tests/` directory |
| DOEFF011 | No Flag/Mode Arguments | Use callbacks or protocol objects instead of flag/mode arguments |
| DOEFF012 | No Append Loop Pattern | Use list comprehension instead of empty list + for loop append |
| DOEFF013 | Prefer Maybe Monad | Use `Maybe[T]` instead of `Optional[T]` or `T \| None` |
| DOEFF014 | No Try-Except Blocks | Use doeff's error handling effects instead of try-except |
| DOEFF101 | Layer Import Direction | 層の module は設定で許した層の module だけを import する |
| DOEFF102 | Layer Forbidden Module | 層ごとに禁じた module(I/O の module など)を直に import しない |
| DOEFF103 | Types-Only Layer | 型だけの層に関数と handler を定めない |
| DOEFF104 | Module Declares Tags | 層の module の定義はタグで文脈(context)と役(role)を名乗る |
| DOEFF105 | Role Matches Layer | タグの role はその層で許された物 |
| DOEFF106 | Raw Side Effect Placement | 生の副作用に直に触る定義は許された層にだけ置く(Hy) |
| DOEFF107 | Raw Side Effect Via Call | 呼ぶ定義を通して生の副作用に届く定義の知らせ(info・Hy) |
| DOEFF108 | Environment Name In Business Code | 業務の file・handler・組み立ての関数の名に環境の語を付けない |
| DOEFF109 | Service Boundary | service A の判断と翻訳の層は、service B の同じ層を読まない(B の intent と共有の置き場は読める) |
| DOEFF110 | No defn | Hy の定義は defn / defn/a ではなく defk で書く |
| DOEFF111 | deff Needs A Reason | deff には `; defk にできない: <理由>` の註を付ける |
| DOEFF112 | Definition Tags Required | defk・deff・defp・defhandler・defeffect は :tags で必須の鍵を名乗る |
| DOEFF113 | Context Matches Service | タグの :context と置き場の service が食い違う(info) |
| DOEFF119 | Class Touches The World Or Holds State | 外の世界に触る class は error・self を書き換える class は warning・欄だけの class は defrecord を勧める info |
| DOEFF118 | Tests Are deftest | 検の置き場の検は deftest で書く(名が test の defn・deff・defk・fn の束縛を置かない) |
| DOEFF121 | Shape Check In Judgment | 判断の層の定義が文字列の鍵の `(.get x "欄")` をその欄への isinstance で検める(JSON の形の検めは protocol の defwire へ) |
| DOEFF122 | Hand-Written Failure Rethrow | match の腕が宣言した失敗の型を受け、受けた値か包み直した値を return するだけ(`(<- (Raise …))` と on-raise へ) |
| DOEFF123 | Bind Then Return | `(<- x T (f …))` の直後の `(return x)` で x を他で使わない |
| DOEFF124 | Fields Joined Into Text | 同じ値の 2 つ以上の欄を文字列と一緒に `+` か f 文字列でつなぐ |
| DOEFF125 | Rebuilt Accumulator | for / while の中の `(:= xs (+ xs #(…)))`(内包表記へ) |
| DOEFF120 | JsonValue Outside Wire Modules | JsonValue・JsonObject を使ってよいのは汎用の解き手と、architecture.hy の `:wire-modules` に挙げた foundation の送受信の module だけ |

## 層の規則(Hy と Python)

DOEFF101〜108 は、repo の module の一覧と設定を見て判じる規則です。「業務の判断を持つ純粋な層」と「外の世界に触る層」を
分け、import の向き・タグ・生の副作用の置き場・環境の語を検査します。層の名前・role・環境の語・登録簿の置き場は Rust に
書き込まず、すべて repo ごとの設定から受け取ります。設定の節が無い規則は何も出しません。

Hy の file は doeff-indexer の hy-index の解析(関数として呼ぶ)と読み取り器で読みます。Python の file は rustpython で読みます。
詳しい決まり(鍵の綴り・重さ・読み方)は [docs/SPECIFICATION.md](docs/SPECIFICATION.md)、規則ごとの例は
[docs/rules/DOEFF101-108.md](docs/rules/DOEFF101-108.md) にあります。

```toml
[tool.doeff-linter.layers]
order = ["core", "intent", "protocol", "foundation", "entry"]   # 外の世界からの遠さの順
paths = { core = "controllers/core", intent = "controllers/intent", protocol = "controllers/protocol", foundation = "controllers/foundation", entry = "controllers/entry" }
exclude = ["tests", "__pycache__", "conftest.py"]              # 層の規則の外に置く dir や file の名
types_only = ["intent"]                                        # 関数と handler を定めない層(DOEFF103)

[tool.doeff-linter.layers.allow_imports]                       # 層ごとに import してよい層(DOEFF101)
core = ["core", "intent"]
intent = ["intent"]
protocol = ["protocol", "intent"]
foundation = ["foundation"]
entry = ["core", "intent", "protocol", "foundation", "entry"]

[tool.doeff-linter.layers.forbid_modules]                      # 層ごとに直に import しない module(DOEFF102)
core = ["subprocess", "socket", "http", "urllib", "httpx"]

[tool.doeff-linter.roles]                                      # role の閉じた一覧と、層ごとに許す role(DOEFF105)
names = ["type", "judgment", "program", "intent", "protocol", "foundation", "entry"]
[tool.doeff-linter.roles.by_layer]
core = ["type", "judgment", "program"]
intent = ["intent", "type"]

[tool.doeff-linter.roles.describe]                             # role の説明(違反の理由の文に差し込む)
translation = "翻訳の handler — 今は層 protocol の役"

[tool.doeff-linter.layers.describe.core]                       # 層の説明(出力の layers と、違反の「なぜ」の文に使う)
summary = "業務の判断と Program"
knows = "業務の判断(いつ・誰に・何を)"
does_not_know = "相手が誰か、どう通信するか"
question = "通信の方法が変わってもこのコードは変わらないか?"

[tool.doeff-linter.raw_side_effects]                           # 生の副作用に直に触ってよい層(DOEFF106・107)
allowed_layers = ["foundation", "entry"]

[tool.doeff-linter.environment_names]                          # 業務の名に付けない環境の語(DOEFF108)
words = ["production", "emulated", "fake", "local", "machine", "wire"]
paths = ["controllers/kanban", "controllers/intent/daily_verify_*"]   # 業務の file の置き場(末尾 * は前方一致)
exclude = ["controllers/entry"]
exclude_parts = ["tests"]
assembly_files = ["handler_sets.hy"]                           # 最上位の定義の名を全部見る組み立ての file

[[tool.doeff-linter.laws]]                                     # 規則と ADR の law の対応(出力の law・adr と鍵の綴り)
name = "core-imports-only-intent"
adr = "ADR-CONTROLLERS-CHOOSE-ENVIRONMENT-BY-HANDLER-SET"
rules = ["DOEFF101", "DOEFF102"]
layers = ["core"]
statement = "module ∈ 層 core の module ⇒ …"

[tool.doeff-linter.registry]                                   # 既知の破れの登録簿と、照合中の規則
dirs = ["scripts/layer_imports/BREACHES"]                      # 1 鍵 1 file(*.txt の 1 行目が鍵)
files = []                                                     # 1 行 1 鍵
reconciling = ["DOEFF104"]                                     # 照合中の規則は info に下げる
```

- 鍵: `<repo の根からの path>::<law の名、無ければ規則の ID>[::<細目>]`(細目 = import の先・`definitions`・role など)。
- 重さ: 新しい破れは error、登録簿に載った破れは warning(`registered: true`)、照合中の規則は info。DOEFF106 の弱い証拠
  (method 名だけで見つけた物)は warning、DOEFF107 は常に info。
- 終了コード: 0 = error なし、1 = error あり、2 = 引数・設定の誤り(設定の名前の食い違いは黙って捨てない)。
- 説明: 層の規則の違反は「これは何か(subject)」「なぜ違反か(reason)」「law の文」を持ち、editor-json の `explanation`・text の
  出力・agent の hook の文に出ます。各 module の `layer_reason` は層を何で決めたか(path の置き場所・タグとの食い違い)です。
- `noqa` の註は Python の文ごとの規則だけに効きます。層の規則の既知の破れは登録簿に鍵を置きます。

### 置き場のパターンと service(DOEFF109・113)

層の置き場は綴りの列で書け、段 `*` が service の名に当たります(移行の途中は層が先の形と service が先の形を並べる)。

```toml
[tool.doeff-linter.layers]
paths = { core = ["controllers/*/core", "controllers/core"], intent = ["controllers/*/intent", "controllers/intent"], foundation = "controllers/foundation" }

[tool.doeff-linter.services]
shared = ["shared"]                 # どの service からも読める置き場(controllers/shared/…)
guarded_layers = ["core", "protocol"]
open_layers = ["intent"]            # 別の service から読んでよい層
exceptions = [{ from = "kanban", to = "automation" }]
check_context = true                # タグの :context と service を照らす(DOEFF113・info)
```

2 つの置き場に当たる file は段の多い方(細かい方)を採ります。module の `service` と、説明の文(「service billing の層 core」)に出ます。

### 定義の書き方(DOEFF110〜112)

```toml
[tool.doeff-linter.definitions]     # 母集団(paths が空なら repo の Hy の全部)
paths = ["controllers", "services"]
exclude = ["packages/doeff-hy/src/doeff_hy/macros"]   # 例: macro の持ち主
deff_reason_marker = "defk にできない:"

[tool.doeff-linter.tags]
required = ["context", "role"]      # DOEFF112 の必須の鍵
module_default = true               # module の頭の MODULE-TAGS で補えるか
```

登録簿に載った破れの重さは規則ごとに決められます(既定 warning・新しい破れは常に error)。設定と一緒に持ち運ぶ登録簿は
`config_files`(設定 file の dir からの相対)に書きます。

```toml
[tool.doeff-linter.rules.DOEFF110]
registered_severity = "info"

[tool.doeff-linter.registry]
config_files = ["definition-breaches.txt"]
```

`eval-and-compile` / `eval-when-compile` の中の defn(マクロの展開の時の関数)は DOEFF110 の外です。既存の破れは登録簿
(`registry.files` の 1 行 1 鍵・鍵 = `<path>::<規則>::<定義の名>`)に載せると warning になり、新しい破れだけが error になります。

### architecture.hy(service と層の唯一の宣言 — DOEFF114〜117)

repo の一番上の `architecture.hy` に `defarchitecture`(root・層・shared・foundation)と `defservice`(説明・`:depends-on`・`:layers`)を
書くと、doeff-linter が実行せずに読みます。在れば層・role・service は ここだけに書き、TOML には規則の入り切りと重さと登録簿を残します。
形と、Tach・import-linter・Nx・Deptrac との対応は [docs/SPECIFICATION.md](docs/SPECIFICATION.md) の 9 節。

### 意味の規則(DOEFF201・202 — Jev)

`--semantic`(指定の file か git で変わった file)と `--semantic-all` の時だけ Jev(TypeSafe)に問い、答えを repo の根の
`.doeff-linter/semantic-cache/` に残します。普段の実行(エディタ・hook)は cache を読むだけです。重さは warning か info だけ。
宛先とキーは doeff-jev と同じ決め方(`JEV_*` → `~/.config/jev/client.json` → TypeSafe 直・`TYPESAFE_API_KEY`)。詳しくは
[docs/SPECIFICATION.md](docs/SPECIFICATION.md) の 10 節。

### 素の関数の理由と検の書き方(DOEFF110・111・118・203)

deff には `; defk にできない: <自由な理由>` を書きます。決定的に違反にするのは註が無い・理由が空・「同上」だけ(DOEFF111)。
理由を受け入れるかは Jev(DOEFF203)が、architecture.hy の `:plain-callable-reasons`(受け入れる理由)と
`:rejected-plain-callable-reasons`(受け入れない型と直し方)から選んで決めます。検の置き場(`definitions.test_paths`)では検は deftest だけ(DOEFF118)。
詳しくは [docs/SPECIFICATION.md](docs/SPECIFICATION.md) の 11 節。

### class の中身(DOEFF119・DOEFF204)

defclass は名前ではなく中身で分けます。method か欄の初期値が生の副作用に触る class は error(土台の handler へ)、method が self の欄を
書き換える class は warning(状態は handler の `(session var …)` へ)、欄だけの class は defrecord を勧める info。例外・Enum・Protocol・
外の library の基底を継ぐ class は出しません。どれにも当たらない、処理を持つ method のある class だけを Jev(DOEFF204)に
value / external-world / stateful / other で問います。詳しくは [docs/SPECIFICATION.md](docs/SPECIFICATION.md) の 12 節。

### JsonValue の使い場所(DOEFF120)

`JsonValue`・`JSONValue`・`JsonObject`・`JSONObject`(素の dict を名で包んだだけの型)を使ってよいのは、汎用の解き手(`doeff_hy.wire`・
`doeff_records.wire`)と、architecture.hy の `:wire-modules`(module の綴りの pattern)に挙げ、かつ foundation の層に在る送受信の module だけです。
ほかの module は解き手が形を確かめた型のある値だけを見ます。module ごとに 1 件(鍵 `<path>::DOEFF120`)。`:wire-modules` に挙げても
foundation の外なら許さず、説明に訳を書きます。許す場所の決め方は `json_value_allowance` の 1 か所で、差し替えられます。
詳しくは [docs/rules/DOEFF120.md](docs/rules/DOEFF120.md) と [docs/SPECIFICATION.md](docs/SPECIFICATION.md) の 13 節。

### 臭いの規則(DOEFF121〜125・DOEFF205)

型と effect で書けるのに手で書いた形を拾います(operator 2026-09-28 "lets add them"・題材は agora の decide-tag)。重さの既定は warning
(`[tool.doeff-linter.rules.<ID>] severity = "info"` で下げられる・error にはしない)。DOEFF121 は `[tool.doeff-linter.smells]
shape_check_layers` に挙げた判断の層の file だけ、DOEFF122〜125 は `definitions` の母集団の全部に当たります。DOEFF122 の「失敗の型」は
名前で決め打ちせず、defrecord の頭の辞書の `{:failure True}` と defeffect の `:failure` / `:absent` の宣言から取ります(import で
module まで解く)。DOEFF205 は役が judgment / program の定義に形の検めと判断が混ざっているかを Jev に問います
(`[tool.doeff-linter.semantic] mixed_concerns = { layer = "core" }`)。詳しくは [docs/SPECIFICATION.md](docs/SPECIFICATION.md) の 14 節。

## エディタ向けの出力(editor-json)

```bash
doeff-linter --output-format editor-json                              # repo 全体(cwd = repo の根)
doeff-linter --output-format editor-json --stdin --path <file>        # 保存前の内容の 1 file
doeff-linter --output-format editor-json --config lint.toml --root .  # 設定 file と repo の根を指定
```

出力は契約 lint-contract 版 1 の JSON 1 つです(`version`・`root`・`violations`・`modules`・`rules`・`errors`)。違反ごとに
`rule`(DOEFF の ID)・`law`・`adr`・`severity`・`path`・`range`(0 始まりの行・UTF-16 の列)・`message`・`hint`・`key`・
`registered` を持ちます。行だけの規則(DOEFF001〜031)も、その行の範囲を出します。形の詳細は
[docs/SPECIFICATION.md](docs/SPECIFICATION.md)。既存の `--output-format json` の形は変えていません(層の規則の違反も同じ形で並びます)。

## Inline Suppression

Use `noqa` comments to suppress rules on specific lines:

```python
# Suppress specific rule
def dict():  # noqa: DOEFF001
    return {}

# Suppress all rules on a line
data["key"] = value  # noqa
```

### File-Level Suppression

Suppress rules for an entire file by placing `noqa: file` at the top of the file (before any code):

```python
# noqa: file=DOEFF001
"""This module intentionally uses builtin names as function names."""

def dict():  # No violation reported
    return {}

def list():  # No violation reported
    return []
```

File-level noqa variants:

```python
# noqa: file              # Suppress ALL rules for entire file
# noqa: file=DOEFF001     # Suppress specific rule for entire file
# noqa: file=DOEFF001,DOEFF002  # Suppress multiple rules for entire file
```

**Note:** File-level noqa must appear before any code. Only comments, blank lines, and module docstrings are allowed to precede it.

## CLI Options

```
Usage: doeff-linter [OPTIONS] [PATHS]...

Arguments:
  [PATHS]...  Files or directories to lint

Options:
      --enable <RULES>       Enable specific rules (comma-separated)
      --disable <RULES>      Disable specific rules (comma-separated)
      --exclude <PATTERNS>   Exclude paths matching patterns
      --force-exclude        Apply exclusion rules to explicit file paths
      --output-format <FMT>  Output format: text, json, editor-json [default: text]
      --config <FILE>        設定 file(pyproject.toml の形か、[tool.doeff-linter] の中身だけの TOML)
      --root <DIR>           repo の根(層の置き場・登録簿・鍵の path の基準)
      --stdin                保存前の内容を stdin から読む(editor-json・--path と一緒に)
      --path <FILE>          --stdin の内容をどの file として判じるか
      --log-file <PATH>      Log violations to file [default: .doeff-lint.jsonl]
      --no-log               Disable logging to file
      --modified             Only lint git-modified files
      --no-config            Ignore pyproject.toml configuration
      --hook                 Run as Cursor stop hook
  -v, --verbose              Show verbose output
  -h, --help                 Print help
  -V, --version              Print version
```

## Exclusion Behavior

By default, exclusion patterns from `pyproject.toml` (and `--exclude`) only apply when **scanning directories**. When you explicitly specify file paths, those files are linted regardless of exclusion patterns.

Use `--force-exclude` to apply exclusion rules even to explicitly specified files:

```bash
# Without --force-exclude: .venv/lib/foo.py will be linted
doeff-linter .venv/lib/foo.py

# With --force-exclude: .venv/lib/foo.py will be excluded (if .venv is in exclude list)
doeff-linter --force-exclude .venv/lib/foo.py
```

This is useful when piping file lists from external tools (like `git diff`, IDE file watchers, etc.) that may include files you want to exclude.

**Note:** The `--modified` mode always applies exclusions, similar to `--force-exclude`.

## Logging and Statistics

The linter logs all detected violations to a file in JSON Lines format by default for later analysis and statistics tracking.

**Default log file:** `.doeff-lint.jsonl`

### Disable Logging

```bash
doeff-linter --no-log
```

### Custom Log File Path

Via CLI:
```bash
doeff-linter --log-file custom-path.jsonl
```

Via `pyproject.toml`:
```toml
[tool.doeff-linter]
log_file = "custom-path.jsonl"
```

### Log Format

Each line in the log file is a JSON object containing:

```json
{
  "timestamp": 1733126639,
  "datetime": "2025-12-02T07:23:59Z",
  "files_scanned": 42,
  "total_violations": 15,
  "error_count": 3,
  "warning_count": 12,
  "info_count": 0,
  "run_mode": "normal",
  "enabled_rules": ["DOEFF001", "DOEFF002", ...],
  "violations": [
    {
      "rule_id": "DOEFF006",
      "file_path": "src/utils.py",
      "line": 42,
      "severity": "error",
      "message": "...",
      "source_line": "def parse() -> tuple:"
    }
  ]
}
```

### CLI Statistics

View statistics from the log file:

```bash
doeff-linter stats
doeff-linter stats --trend    # Include daily trend
doeff-linter stats custom.jsonl  # Use custom log file
```

Example output:
```
════════════════════════════════════════════════════════════
 DOEFF-LINTER STATISTICS 
════════════════════════════════════════════════════════════

📊 Overview
  Total lint runs:      42
  Total files scanned:  156
  Total violations:     234

🎯 By Severity
  Errors:   12 (5.1%)
  Warnings: 220 (94.0%)
  Info:     2 (0.9%)

📋 By Rule
  DOEFF014    89  ████████████████████
  DOEFF013    67  ███████████████
  DOEFF012    34  ████████
  ...
```

### HTML Report

Generate an interactive HTML dashboard:

```bash
doeff-linter report
doeff-linter report -o my-report.html
doeff-linter report --open  # Open in browser after generation
```

The report includes:
- Summary statistics cards
- Violations by rule (bar chart)
- Severity distribution (donut chart)
- Top files by violations
- Daily activity trend

### Raw Log Analysis

You can also analyze logs with `jq`:

```bash
# Count violations by rule
cat .doeff-lint.jsonl | jq -s '[.[].violations[]] | group_by(.rule_id) | map({rule: .[0].rule_id, count: length})'

# Get total violations over time
cat .doeff-lint.jsonl | jq '{date: .datetime, total: .total_violations}'

# Find most frequent violation locations
cat .doeff-lint.jsonl | jq -s '[.[].violations[]] | group_by(.file_path) | map({file: .[0].file_path, count: length}) | sort_by(-.count) | .[0:10]'
```

## Cursor Integration (Stop Hook)

The linter can run as a [Cursor stop hook](https://cursor.com/ja/docs/agent/hooks) to automatically check code quality after the AI agent completes its work.

### Setup

1. Build and install the linter:
```bash
cd packages/doeff-linter
cargo build --release
# Copy to a location in your PATH, or use the full path
cp target/release/doeff-linter ~/.local/bin/
```

2. Create `.cursor/hooks.json` in your project root:
```json
{
  "version": 1,
  "hooks": {
    "stop": [
      {
        "command": "doeff-linter --hook"
      }
    ]
  }
}
```

3. Restart Cursor.

### How it Works

When the Cursor agent completes a task:
1. The linter receives the workspace paths via stdin
2. It scans all Python files for violations (respecting `exclude` patterns from `pyproject.toml`)
3. If errors are found, it sends a `followup_message` asking the agent to fix them
4. The agent automatically continues to fix the identified issues

The hook only triggers follow-up for **errors** (not warnings), preventing infinite loops while ensuring critical issues are addressed.

**Note:** The hook mode automatically applies exclusion rules from `pyproject.toml` to all files (equivalent to `--force-exclude`).

### Example Output

When violations are found, the hook outputs:
```json
{
  "followup_message": "The doeff-linter found code quality issues...\n\n## DOEFF006 - No Tuple Returns\n**Problem:** Returning raw tuples reduces code readability...\n**How to fix:** Use a dataclass or NamedTuple...\n\n- `src/utils.py:42` → `def parse_result() -> tuple[str, int]:`\n\nPlease fix these issues following the suggestions above."
}
```

## License

MIT License - see LICENSE file for details.
