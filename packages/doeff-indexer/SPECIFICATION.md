# doeff-indexer Specification

## Table of Contents

1. [Purpose and Overview](#purpose-and-overview)
2. [Type Definitions](#type-definitions)
3. [Detection Logic](#detection-logic)
4. [CLI Commands](#cli-commands)
   - [Hy Index (`hy-index`)](#hy-index-hy-index)
5. [Type Filtering Rules](#type-filtering-rules)
6. [@do Decorator Handling](#do-decorator-handling)
7. [Integration with IDE Plugins](#integration-with-ide-plugins)
8. [Examples and Edge Cases](#examples-and-edge-cases)

## Purpose and Overview

`doeff-indexer` is a static analysis tool that indexes Python source code to identify and categorize functions related to the `doeff` effects system. It provides language server capabilities for IDE integration and command-line tools for developers to discover doeff-related functions in their codebase.

### Core Responsibilities

1. **Static Analysis**: Parse Python source files to extract function definitions, decorators, type annotations, and comments
2. **Pattern Recognition**: Identify doeff-specific patterns like `@do` decorators and marker comments
3. **Type Classification**: Categorize functions into four main types: Interpreters, Transforms, KleisliProgram, and Interceptors
4. **CLI Interface**: Provide command-line tools for querying the indexed functions
5. **IDE Integration**: Support language server protocol for real-time IDE assistance

### Key Features

- **Signature-based categorization**: Analyzes function signatures to determine categories
- **Marker-based filtering**: Uses `# doeff: <type>` comments for explicit marking
- **@do decorator handling**: Special logic for functions decorated with `@do`
- **Type filtering**: Support for filtering functions by parameter types
- **Module path resolution**: Handles various Python project structures (UV projects, regular packages)

## Type Definitions

### Core Function Categories

The indexer categorizes functions into four primary types based on their signatures and purpose:

#### 1. Interpreter
```python
def interpreter_function(program: Program[T]) -> T:
    """
    Interprets a Program[T] and returns the unwrapped value T.
    Does NOT return Program type.
    """
```

**Characteristics:**
- First parameter: `Program[T]` (any generic Program type)
- Return type: Any type except `Program` 
- Purpose: Execute/interpret programs to produce concrete values

#### 2. Transform
```python
def transform_function(program: Program[T]) -> Program[U]:
    """
    Transforms one Program into another Program.
    Input and output are both Program types.
    """
```

**Characteristics:**
- First parameter: `Program[T]` (any generic Program type)
- Return type: `Program[U]` (any generic Program type)
- Purpose: Transform programs while maintaining the Program wrapper

#### 3. KleisliProgram
```python
@do
def kleisli_function(value: T) -> U:
    """
    Creates a Program[U] from a value T using @do notation.
    The @do decorator wraps the return value in Program.
    """
```

**Characteristics:**
- First parameter: Any type except `Program` or `Effect`
- Decorator: `@do` (automatic categorization)
- Alternative: Manual marking with `# doeff: kleisli`
- Purpose: Lift regular values into the Program monad

#### 4. Interceptor
```python
def interceptor_function(effect: Effect) -> Effect | Program:
    """
    Intercepts and potentially modifies effects during program execution.
    First parameter is always an Effect type.
    """
```

**Characteristics:**
- First parameter: `Effect` (any Effect subtype)
- Return type: `Effect` or `Program` (flexible)
- Purpose: Intercept and modify effects during execution

### EntryCategory Enumeration

```rust
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum EntryCategory {
    // Primary categories (mutually exclusive for core types)
    ProgramInterpreter,    // Executes Program[T] -> T
    ProgramTransformer,    // Transforms Program[T] -> Program[U]  
    KleisliProgram,        // @do functions or T -> Program[U]
    Interceptor,           // Effect -> Effect | Program
    
    // Secondary categories (can be combined)
    DoFunction,            // Has @do decorator
    AcceptsProgramParam,   // First param is Program[T]
    ReturnsProgram,        // Return type is Program[T]
    AcceptsEffectParam,    // First param is Effect
    
    // Marker categories
    HasMarker,             // Has any doeff: marker comment
}
```

### Index Entry Structure

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IndexEntry {
    pub name: String,                    // Function name
    pub module_path: String,             // Fully qualified module path
    pub file_path: String,               // Absolute file path
    pub line_number: u32,                // Line where function is defined
    pub decorators: Vec<String>,         // List of decorator names
    pub return_annotation: Option<String>, // Return type annotation
    pub all_parameters: Vec<Parameter>,  // All function parameters
    pub markers: Vec<String>,            // Extracted doeff markers
    pub categories: Vec<EntryCategory>,  // Assigned categories
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Parameter {
    pub name: String,                    // Parameter name
    pub annotation: Option<String>,      // Type annotation
    pub default_value: Option<String>,   // Default value if any
    pub is_vararg: bool,                 // *args parameter
    pub is_kwarg: bool,                  // **kwargs parameter
}
```

## Detection Logic

The indexer employs two complementary detection strategies:

### 1. Signature-based Categorization

This is the primary method for categorizing functions based on their type signatures:

```rust
fn categorize_by_signature(entry: &mut IndexEntry) {
    let first_param = entry.all_parameters.first();
    let return_type = &entry.return_annotation;
    let has_do = entry.decorators.contains(&"do".to_string());
    
    if let Some(param) = first_param {
        if let Some(annotation) = &param.annotation {
            if annotation.contains("Program") {
                // Program as first parameter
                entry.categories.push(EntryCategory::AcceptsProgramParam);
                
                if has_do {
                    // @do with Program -> Transform
                    entry.categories.push(EntryCategory::ProgramTransformer);
                } else if let Some(ret) = return_type {
                    if ret.contains("Program") {
                        entry.categories.push(EntryCategory::ProgramTransformer);
                    } else {
                        entry.categories.push(EntryCategory::ProgramInterpreter);
                    }
                }
            } else if annotation.contains("Effect") {
                // Effect as first parameter
                entry.categories.push(EntryCategory::AcceptsEffectParam);
                entry.categories.push(EntryCategory::Interceptor);
            } else if has_do {
                // @do with non-Program/Effect -> Kleisli
                entry.categories.push(EntryCategory::KleisliProgram);
            }
        }
    }
    
    // Check return type
    if let Some(ret) = return_type {
        if ret.contains("Program") {
            entry.categories.push(EntryCategory::ReturnsProgram);
        }
    }
    
    // Mark @do functions
    if has_do {
        entry.categories.push(EntryCategory::DoFunction);
    }
}
```

#### Detection Rules

1. **Interpreter Detection**:
   - First parameter: `Program[T]` 
   - Return type: NOT `Program`
   - Category: `ProgramInterpreter`

2. **Transform Detection**:
   - First parameter: `Program[T]`
   - Return type: `Program[U]` OR `@do` decorator
   - Category: `ProgramTransformer`

3. **Kleisli Detection**:
   - `@do` decorator with non-Program/Effect first parameter
   - OR manual `# doeff: kleisli` marker
   - Category: `KleisliProgram`

4. **Interceptor Detection**:
   - First parameter: `Effect` (any subtype)
   - Category: `Interceptor`

### 2. Marker-based Filtering

The indexer extracts explicit markers from comments to override or supplement signature-based categorization:

#### Marker Format

```python
def my_function(param: Type) -> ReturnType:  # doeff: interpreter
def another_function(x: int) -> str:  # doeff: kleisli, transform
```

#### Supported Markers

- `interpreter`: Mark function as interpreter
- `transform`: Mark function as transform  
- `kleisli`: Mark function as Kleisli program
- `interceptor`: Mark function as interceptor

#### Marker Extraction Algorithm

```rust
fn extract_markers_from_source(
    source: &str, 
    line_number: u32, 
    function_name: &str,
    args: &Arguments
) -> Vec<String> {
    let lines: Vec<&str> = source.lines().collect();
    let mut markers = Vec::new();
    
    // Search for markers in function signature lines
    let start_line = line_number as usize - 1;
    let end_line = find_function_end(source, start_line, args);
    
    for i in start_line..=end_line {
        if let Some(line) = lines.get(i) {
            if let Some(comment_start) = line.find('#') {
                let comment = &line[comment_start..];
                if let Some(doeff_start) = comment.to_lowercase().find("doeff:") {
                    let marker_text = &comment[doeff_start + 6..];
                    for marker in marker_text.split(',') {
                        let cleaned = marker.trim();
                        if !cleaned.is_empty() {
                            markers.push(cleaned.to_string());
                        }
                    }
                }
            }
        }
    }
    
    markers
}
```

#### Marker Precedence

- Markers take precedence over signature analysis for `find-*` commands
- Signature analysis is used for categorization regardless of markers
- Functions without markers are categorized but not returned by `find-*` commands

## CLI Commands

### Core Commands

#### `doeff-indexer --root <path>`

Builds a complete index and outputs JSON containing all discovered functions.

```bash
doeff-indexer --root /project/path
```

**Output Format:**
```json
{
  "entries": [
    {
      "name": "exec_program",
      "module_path": "myproject.interpreter", 
      "file_path": "/project/myproject/interpreter.py",
      "line_number": 15,
      "decorators": [],
      "return_annotation": "int",
      "all_parameters": [
        {
          "name": "program",
          "annotation": "Program[int]",
          "default_value": null,
          "is_vararg": false,
          "is_kwarg": false
        }
      ],
      "markers": ["interpreter"],
      "categories": ["ProgramInterpreter", "AcceptsProgramParam", "HasMarker"]
    }
  ]
}
```

#### `find-interpreters <root_path>`

Returns only functions marked with `# doeff: interpreter`.

```bash
find-interpreters /project/path
```

**Filtering Logic:**
- Must have `interpreter` in markers array
- Signature-based categorization is ignored
- Returns subset of index entries

#### `find-transforms <root_path>`  

Returns only functions marked with `# doeff: transform`.

```bash
find-transforms /project/path
```

#### `find-kleisli <root_path>`

Returns functions marked with `# doeff: kleisli`.

```bash
find-kleisli /project/path
```

**Filtering Logic:**
- Has `kleisli` in markers array

#### `find-kleisli --type-arg <type> <root_path>`

Returns Kleisli functions whose first parameter matches the specified type.

```bash
find-kleisli --type-arg str /project/path
find-kleisli --type-arg User /project/path
```

**Type Matching Rules:**
1. Exact match: `str` matches `str`
2. Generic match: `str` matches `Optional[str]`, `List[str]`  
3. Any match: `Any` type matches all type filters
4. Case-sensitive matching

#### `find-interceptors <root_path>`

Returns only functions marked with `# doeff: interceptor`.

```bash
find-interceptors /project/path
```

### Command Behavior

All `find-*` commands:
- Build the index from scratch each time
- Rely exclusively on marker-based filtering
- Return JSON arrays of matching entries
- Exit with status 0 on success, non-zero on error

## Hy Index (`hy-index`)

Hy の file(`*.hy` / `*.hyk` / `*.hyp`)の定義・import・参照・呼び出しを JSON で出す。エディタの定義への移動・参照の検索・目次、effect・handler・defk の間の行き来のためのもの。Python の索引(上の各コマンド)とは独立していて、Python の索引は作らない。実装は `src/hy_index/`。

### 使い方

```
doeff-indexer hy-index --root <dir>                        # dir 以下の Hy の file を全部
doeff-indexer hy-index --root <dir> --file <path>…         # 指定した file だけ(root は module 名の基準)
doeff-indexer hy-index --root <dir> --stdin --path <path>  # 保存前の内容を stdin から読み、path の file として 1 件出す
```

- 探索では `.venv`・`node_modules`・`target`・`.git`・`__pycache__` の directory に降りない。
- `--file` と `--path` の相対 path は `--root` を基準に解く。`--output` と `--pretty` は他のコマンドと同じ。
- 読めない file・閉じていない括弧でも止まらない。読めた分を出し、その file の `errors` に理由(`行:列: …`、1 始まり)を積む。終了コードは 0。引数の誤り(`--stdin` に `--path` が無い・`--root` が directory でない等)だけ 2。

### 出力の形(契約の版 4)

版 3 で足した生の副作用の証拠(`raw`・`raw_via`・`raw_catalog_problems`)と定義のタグ(`tags`)は `experiments/hy-highlighter/hy-index-contract-v3.md` にある。版 4 で足した完全修飾名(`qualified_name`・`target`)はこの節が正本。

```jsonc
{
  "version": 4,
  "root": "/abs/root",
  "files": [{
    "path": "/abs/root/pkg/mod.hy",
    "module": "pkg.mod",            // root からの相対 path。__init__.hy は親の package 名
    "definitions": [{
      "name": "classifier-peers",   // 書かれたとおりの綴り
      "mangled": "classifier_peers",// - を _ に(先頭の - は残す)
      "qualified_name": "pkg.mod.classifier_peers", // 完全修飾名(版 4)= module + 入れ物(在れば)+ 名。どの区切りも mangle した綴り
      "kind": "defn",
      "range": {…},                 // 名前の位置
      "full_range": {…},            // form 全体
      "container": null,            // 入れ物の定義の名前(method・field・enum-member・effect-clause・入れ子の law 等)
      "docstring": "…",             // 無ければ null
      "params": ["jev-script", "store"],
      "bases": []                   // defclass / defrecord の基底の記号(書かれたとおり、dotted も 1 つ)。他の kind は常に []
      // "checks": ["(>= start 0)"] // defrecord の頭の辞書の :check の式(書かれたとおり・書いた順)。頭の辞書を持つ defrecord だけが持ち、他は欄ごと出さない(版 3 への追加)
    }],
    "imports": [{"module": "doeff_records.memory", "name": "MemoryStore", "alias": null, "range": {…}, "is_require": false}],
    "references": [{"name": "c", "mangled": "c", "qualifier": "a.b", "range": {…}}],
    "calls": [{"callee": "PutRow", "mangled": "PutRow", "qualifier": null, "range": {…}, "caller": 12, "performed": true,
               "target": "pkg.effects.PutRow"}],  // target(版 4)= 呼び先の完全修飾名。解決できなければ null
    "errors": []
  }]
}
```

- 位置は 0 始まりの行と UTF-16 の code unit の列(VS Code の Position と同じ。日本語は 1 文字 1、絵文字は 2)。
- kind: `defn` `defn/a` `defmacro` `defk` `deff` `defp` `defpp` `fnk-binding`(今は出さない)`defclass` `defrecord` `defenum` `enum-member` `field` `method` `defhandler` `effect-clause` `deftest` `defadr` `defsemgrep` `law` `defpipeline` `defworkflow` `defphase` `defmcp-tool` `deftype` `defmain` `variable`。
- `definitions` は file の top level の定義と、その直下の入れ子だけ。`(do …)`・`(eval-and-compile …)` の中は top level とみなす。関数の中の局所の束縛は入れない。
  - defclass の body: `(defn …)` は `method`、`#^ T x`・`(#^ T x 既定値)`・`(setv x …)` は `field`。
  - defrecord の field、defenum の member(`A` と `(A "値")` の両方)。
  - defhandler: `(Effect [fields] …)` は `effect-clause`(params = fields)、`(session val|var x …)`・旧い `(lazy-val …)` / `(lazy-var …)` は `variable`。どちらも container = handler 名。
  - defadr の中の `law`・`defsemgrep`・`deftest`、defworkflow の中の `defphase` は container = 外側の名前。
  - `variable` = top level の `setv` / `setx`(`[a b]` の分解も)・`val` / `var`・`(lazy val …)`・`(session val|var …)`・`lazy-val` / `lazy-var`。
- docstring: 関数の形は引数の後(defk / deff の `{:pre … :post …}` の後でもよい)の文字列で、後に form が続く時だけ。class・record・enum は body の先頭の文字列。defhandler は名前の後か引数の後。`law` は `:statement`、`defadr` は `:title`、`defmcp-tool` は説明の文字列。字下げは Python の `inspect.cleandoc` と同じく揃える。
- `imports`: `(import m)`・`(import m :as a)`・`(import m [x y :as z])`・`(import m *)`・`(require m [names])`・`(require m :macros [..] :readers [..])`。range は alias → name → module の順で在るものを指す。関数の中の import も入れる。
- `references`: すべての記号の出現を `.` で区切って 1 件ずつ入れる(`a.b.c` は a・b・c、c の qualifier は `"a.b"`)。入れないもの: 先頭に置かれた予約語(Hy の special form と doeff-hy の macro。`val` 等の普通の語の macro は file が require した時だけ予約語)・演算子・定数(`True` / `False` / `None` 等)・`_`・keyword(`:key`)・文字列の中身・註・`#_` で読み捨てた form・quote の中(quasiquote の中の `~x` は入れる)。f 文字列の `{…}` の中の記号は入れる。
- `bases`(版 2): defclass の `[…]` の中の記号だけを書かれたとおりに入れる(`:metaclass M` のような keyword とその値、`(get Generic T)` のような式は入れない)。defrecord は今の macro が基底を書かない形なので、ふつう `[]`。
- `checks`(版 3 への追加): `(defrecord Name "doc"? {:tags {…} :check […]} 欄 …)` の頭の辞書の `:check` の各式を書かれたとおりの綴りで書いた順に入れる。頭の辞書を持つ defrecord は `:check` が無ければ `[]`、頭の辞書の無い defrecord と他の kind は欄ごと出さない。頭の辞書の `:tags` は defk / defeffect と同じく `tags` に入る。頭の辞書は欄ではない(欄は辞書の後ろから読む)。
- `calls`(版 2): `(` の直後の記号が、予約語・演算子・定数でないものを 1 件ずつ入れる(関数の呼び出し・class の生成・effect の生成)。引数の中の入れ子の呼び出しも入れる。
  - `callee` は頭の記号の最後の区切り、`qualifier` はその前の区切り(`mod.sub.fn` なら `"mod.sub"`)、`range` は最後の区切りの位置。
  - `caller` は、呼び出しの位置を `full_range` に含む定義のうち最も狭いものの添字(同じ file の `definitions` の添字)。含む定義が無ければ(top level の式)`null`。effect 節の本体の中はその `effect-clause`、`(fn …)` や `let` の中の局所の関数の中は外側の定義になる。`(setv x (f))` の `f` は `x` の `variable` が caller。
  - `performed` は `(<- (X …))`・`(<- name (X …))`・`(<- name T (X …))` の X と、`yield` / `yield-from` / `!`(doeff-hy の引数の位置での effect の bind、`(! (X …))`)の直下の呼び出しで true。`(! x)` のような記号だけの形は呼び出しではない。その中の引数の入れ子の呼び出しは false。
  - `target`(版 4): 呼び先の完全修飾名。その file の中身(module・definitions・imports)だけで決める名前の解決の結果で、他の file を見ない。だから `--root`・`--file`・`--stdin` のどの実行でも同じ値になる。解決の順:
    1. 同じ file の定義 — 修飾の無い名前は top level の定義(`container` が null)、`q.name` は入れ物 q の中の定義(`Store.put` → `pkg.mod.Store.put`)。
    2. file の import(`require` は除く — macro は呼び先の定義にしない)。最初に束ねた import を使い、相対 import は書いた file の package を基準に直す。`(import m [x :as y])` の `(y)` は `m.x`、`(import m)` の `(m.sub.f)` は `m.sub.f`、`(import pkg [sub])` の `(sub.f)` と `(import m [C])` の `(C.f)` はどちらも `pkg.sub.f` / `m.C.f` の形。
    3. どれでもない(組み込み・局所の束縛・引数・module そのものを呼ぶ形)は null。
  - `target` は Hy の定義とは限らない(`json.dumps` のような Python の名前もその完全修飾名になる)。索引の `qualified_name` に一致すれば Hy の定義。`qualified_name` は同じ file の同名の再定義で重なり得るので、一致は複数として扱う。
  - 呼び手の逆引き(ある定義を呼ぶ定義・deftest の一覧)は索引に持たない。読む側が `target` と `qualified_name` の一致を逆に引く(1 file の実行 `--stdin` で差し替えた file の呼び出しも、他の file の逆引きに正しく効くため)。
  - 呼び出しは `caller` の定義(最も内側)にだけ割り当てる。外側の定義(defclass・defhandler・defadr 等)の呼び出しは、`full_range` に含まれる入れ子の定義の分を読む側が足す。
  - 生の副作用の経由の証拠(`raw.via`)も、呼び出しの行き先をこの `target` と `qualified_name` の一致で引く(名前の解決は `src/hy_index/qualify.rs` の 1 か所)。
  - 入れないもの: 予約語(require した普通の語の macro を含む)、doeff-hy の束縛の構文の頭(`val` / `var` / `lazy` / `session`。defk などの macro が読むので require が無くても構文)、`(.method obj)`、`(. obj (method …))` の method、defhandler / `handle` の effect 節の頭(`(PutRow [table key] …)` の `PutRow`)、型注釈の中(`#^ (of list int) x`)、match の pattern の中(`(Point :x px)`)、quote の中。これらの記号は `references` には今までどおり入る。

### 既知の制限

- mangle は契約の単純な形(`-` → `_`)だけで、Hy の `?`・`!` などの記号の変換(`hyx_…`)はしない。
- `(when …)` など条件の中の定義、`defdomain` など上の一覧に無い定義の形は `definitions` に入れない(参照には入る)。その中の呼び出しの `caller` は外側の定義か `null` になる。
- `->` / `->>` の中の `(f a)` は書かれた形の頭を呼び出しとして入れる(展開後の引数の並びは見ない)。
- f 文字列の `{x:>10}` の書式は `:` の前までを名前とみなす。`{x !r}` のように空白で区切る Hy の書き方は正しく読める。

## Type Filtering Rules

### Type Annotation Matching

The indexer supports sophisticated type matching for parameter filtering:

#### Exact Type Matching
```python
# find-kleisli --type-arg str matches:
@do
def process_string(value: str) -> int:  # doeff: kleisli  # ✅ Exact match
    return len(value)
```

#### Generic Type Matching  
```python
# find-kleisli --type-arg str matches:
@do  
def process_optional(value: Optional[str]) -> int:  # doeff: kleisli  # ✅ Contains str
    return len(value or "")

@do
def process_list(items: List[str]) -> int:  # doeff: kleisli  # ✅ Contains str  
    return len(items)
```

#### Any Type Special Handling
```python
@do
def process_any(value: Any) -> str:  # doeff: kleisli  # ✅ Matches ALL type filters
    return str(value)
```

**Any Matching Logic:**
- Functions with `Any` type parameter match all `--type-arg` filters
- This allows generic functions to appear in all type-specific searches

#### Union Type Handling
```python
@do
def process_union(value: Union[str, int]) -> str:  # doeff: kleisli  # ✅ Matches --type-arg str
    return str(value)
```

#### Complex Generics
```python  
@do  
def process_complex(data: Dict[str, List[User]]) -> Summary:  # doeff: kleisli  # ✅ Matches --type-arg User
    return analyze(data)
```

### Type Matching Algorithm

```rust
fn matches_type_filter(annotation: &str, type_filter: &str) -> bool {
    // Special case: Any matches everything
    if annotation.contains("Any") {
        return true;
    }
    
    // Direct match
    if annotation == type_filter {
        return true;
    }
    
    // Generic/container match (List[str], Optional[str], etc.)
    if annotation.contains(type_filter) {
        return true;
    }
    
    false
}
```

### Kleisli Type-Arg Constraints

When clients call `find-kleisli --type-arg Program[T]`, the indexer extracts the inner `T` and
returns only entries that model `Kleisli[T, _]`:

- Only functions marked with `# doeff: kleisli` are eligible for CLI results.
- Type filtering additionally requires the function to use `@do`.
- Exactly one positional parameter may lack a default value; it must be annotated as `T` or `Any`.
- Additional parameters are allowed only when they provide defaults, because the CLI cannot infer
  values for extra required arguments.
- Functions with multiple required parameters are excluded from Kleisli results.
- Parameters typed as `Any` continue to match every `--type-arg` filter after the `Program[T]`
  unwrapping step.

## @do Decorator Handling

The `@do` decorator receives special treatment in the indexer as it fundamentally changes function semantics.

### @do Detection

```rust
fn extract_decorators(function_def: &FunctionDef) -> Vec<String> {
    function_def.decorator_list
        .iter()
        .map(|decorator| match decorator {
            Expr::Name { id, .. } => id.to_string(),
            Expr::Attribute { attr, .. } => attr.to_string(),
            _ => "unknown".to_string(),
        })
        .collect()
}
```

### @do Categorization Logic

Functions with `@do` decorator are categorized based on their first parameter:

```python
# Case 1: @do with Program parameter -> Transform
@do
def transform_program(program: Program[int]) -> str:
    """Automatically categorized as ProgramTransformer"""
    result = yield program
    return str(result)

# Case 2: @do with Effect parameter -> Interceptor  
@do
def intercept_effect(effect: LogEffect) -> str:
    """Automatically categorized as Interceptor"""
    yield effect
    return "logged"

# Case 3: @do with other parameter -> KleisliProgram
@do  
def kleisli_function(user_id: str) -> User:
    """Automatically categorized as KleisliProgram"""
    yield Log(f"Fetching {user_id}")
    return User(user_id)
```

### @do Override Rules

1. **Transform Override**: `@do` + `Program` parameter = `ProgramTransformer` (even if return type is not `Program`)
2. **Interceptor Override**: `@do` + `Effect` parameter = `Interceptor`  
3. **Kleisli Default**: `@do` + other parameter = `KleisliProgram`

### Marker Requirements for CLI Commands

Specific CLI discovery commands (`find-interpreters`, `find-transforms`, `find-kleisli`,
`find-interceptors`) only return entries that carry an explicit marker comment. Categorization
still honours signature analysis and decorators, but discoverability via these commands requires a
matching marker:

- `find-interpreters` → `# doeff: interpreter`
- `find-transforms` → `# doeff: transform`
- `find-kleisli` → `# doeff: kleisli`
- `find-interceptors` → `# doeff: interceptor`

`@do` functions continue to be categorized automatically (Kleisli/Transform/Interceptor), yet they
must also be marked to appear in CLI results. This guarantees that IDE integrations and automation
only surface functions that were intentionally exposed by maintainers.

## Integration with IDE Plugins

### Language Server Protocol Support

The indexer is designed to integrate with IDE plugins via Language Server Protocol (LSP):

#### Capabilities

1. **Function Discovery**: Real-time discovery of doeff functions
2. **Type Hints**: Provide type information for function parameters  
3. **Documentation**: Show function purpose and categorization
4. **Navigation**: Jump to function definitions
5. **Completion**: Auto-complete for doeff function names

#### LSP Requests

**Index Request:**
```json
{
  "method": "doeff/index",
  "params": {
    "rootUri": "file:///project/path"
  }
}
```

**Find Request:**  
```json
{
  "method": "doeff/find",
  "params": {
    "type": "kleisli",
    "typeArg": "str",
    "rootUri": "file:///project/path"
  }
}
```

**Response Format:**
```json
{
  "result": {
    "entries": [
      {
        "name": "process_string",
        "moduleUri": "file:///project/mymod.py",
        "line": 15,
        "character": 4,
        "categories": ["KleisliProgram", "DoFunction"],
        "signature": "process_string(value: str) -> int"
      }
    ]
  }
}
```

### IDE Plugin Features

#### PyCharm Plugin
- **Gutter Icons**: Visual indicators for doeff functions
- **Quick Actions**: Convert between function types
- **Inspection**: Validate doeff patterns
- **Navigation**: "Go to" commands for related functions

#### VS Code Extension  
- **Tree View**: Sidebar showing categorized functions
- **Hover Information**: Type details on hover
- **Command Palette**: Quick access to find commands
- **Syntax Highlighting**: Special highlighting for `@do` and markers

### Configuration

IDE plugins can configure indexer behavior:

```json
{
  "doeff.indexer.includePaths": ["src/", "lib/"],
  "doeff.indexer.excludePaths": ["tests/", "build/"],
  "doeff.indexer.enableRealTime": true,
  "doeff.indexer.showSignaturePreview": true
}
```

## Examples and Edge Cases

### Basic Examples

#### Interpreter Example
```python
def run_program(program: Program[int]) -> int:  # doeff: interpreter
    """✅ Correctly marked interpreter"""
    return program.run()

# Categories: [ProgramInterpreter, AcceptsProgramParam, HasMarker]
# Found by: find-interpreters
```

#### Transform Example  
```python
def map_program(program: Program[int]) -> Program[str]:  # doeff: transform
    """✅ Correctly marked transform"""
    return program.map(str)

# Categories: [ProgramTransformer, AcceptsProgramParam, ReturnsProgram, HasMarker]  
# Found by: find-transforms
```

#### Kleisli Example
```python
@do
def fetch_user(user_id: str) -> User:
    """✅ @do function creating KleisliProgram[str, User]"""
    yield Log(f"Fetching {user_id}")
    return User(user_id)

# Categories: [KleisliProgram, DoFunction]
# Found by: find-kleisli, find-kleisli --type-arg str
```

#### Interceptor Example
```python
def log_interceptor(effect: LogEffect) -> LogEffect:  # doeff: interceptor
    """✅ Correctly marked interceptor"""
    return LogEffect(f"[LOGGED] {effect.message}")

# Categories: [Interceptor, AcceptsEffectParam, HasMarker]
# Found by: find-interceptors
```

### Edge Cases

#### Multiple Markers
```python
def hybrid(program: Program[Any]) -> Program[Any]:  # doeff: transform, interpreter
    """Function with multiple markers - appears in both find-transforms and find-interpreters"""
    return program

# Categories: [ProgramTransformer, AcceptsProgramParam, ReturnsProgram, HasMarker]
# Found by: find-transforms, find-interpreters  
```

#### Incorrect Markers
```python
def wrong_interpreter(program: Program[int]) -> Program[int]:  # doeff: interpreter
    """❌ Marked as interpreter but returns Program (should be transform)"""
    return program

# Categories: [ProgramTransformer, AcceptsProgramParam, ReturnsProgram, HasMarker]
# Found by: find-interpreters (marker takes precedence)
# Warning: Signature suggests transform but marked as interpreter
```

#### @do with Program Parameter
```python
@do
def do_transform(program: Program[int]) -> str:  # doeff: transform
    """@do with Program param -> automatically categorized as Transform"""
    result = yield program
    return str(result)

# Categories: [ProgramTransformer, DoFunction, AcceptsProgramParam]
# Found by: find-kleisli (due to @do), find-transforms (due to marker)
```

#### Unmarked Functions
```python
def unmarked_interpreter(program: Program[str]) -> str:
    """❌ Valid interpreter signature but no marker - not found by find-*"""
    return program.run()

# Categories: [ProgramInterpreter, AcceptsProgramParam]  
# Found by: (none - no markers)
```

#### Class Methods
```python
class Executor:
    def run(self, program: Program[int]) -> int:  # doeff: interpreter
        """✅ Class method interpreter"""
        return program.run()
    
    @do
    def fetch(self, key: str) -> Data:
        """✅ Class method Kleisli"""
        yield Log(f"Fetching {key}")
        return Data(key)

# Both functions are properly indexed and categorized
```

#### Type Filtering Edge Cases
```python
@do
def optional_param(value: Optional[str]) -> int:
    """Matches --type-arg str due to Optional[str] containing str"""
    return len(value or "")

@do  
def union_param(value: Union[str, int]) -> str:
    """Matches --type-arg str due to Union containing str"""
    return str(value)

@do
def any_param(value: Any) -> Result:
    """Matches ALL --type-arg filters due to Any"""
    return Result(value)

# All found by: find-kleisli --type-arg str
```

#### Complex Generics
```python
@do
def complex_generic(data: Dict[str, List[User]]) -> Summary:
    """Matches --type-arg User due to nested User type"""
    return analyze_users(data)

# Found by: find-kleisli --type-arg User
```

#### Async Functions
```python
async def async_interpreter(program: Program[str]) -> str:  # doeff: interpreter
    """✅ Async functions are supported"""
    return await program.async_run()

# Categories: [ProgramInterpreter, AcceptsProgramParam, HasMarker]
# Found by: find-interpreters
```

#### Property Methods
```python
class Manager:
    @property
    def interpreter(self) -> Callable:  # doeff: interpreter
        """✅ Property methods are supported"""
        return lambda p: p.run()

# Categories: [HasMarker] (limited categorization due to lambda return)
# Found by: find-interpreters
```

### Error Cases

#### Missing Markers
```python
def valid_but_unmarked(program: Program[int]) -> int:
    """Valid interpreter but missing marker - won't be found"""
    return program.run()

# Result: Categorized but not discoverable via find-interpreters
```

#### Invalid Syntax
```python
def broken_syntax(program: Program[int] -> int:  # Syntax error
    return program.run()

# Result: Skipped during parsing with warning logged
```

#### Missing Type Annotations
```python
def no_annotations(program):  # doeff: interpreter
    """Missing type annotations - limited categorization"""
    return program.run()

# Categories: [HasMarker]
# Found by: find-interpreters (marker-based)
```

## Implementation Notes

### Performance Considerations

1. **Caching**: Index results should be cached per project
2. **Incremental Updates**: Support incremental re-indexing for modified files
3. **Parallel Processing**: Parse multiple files concurrently
4. **Memory Usage**: Stream large projects instead of loading all in memory

### Error Handling

1. **Syntax Errors**: Skip malformed files with warnings
2. **Import Errors**: Continue indexing despite missing dependencies  
3. **Type Resolution**: Handle unresolved type annotations gracefully
4. **File Access**: Handle permission errors and missing files

### Extensibility

1. **Custom Markers**: Support project-specific marker prefixes
2. **Plugin System**: Allow custom categorization rules
3. **Export Formats**: Support multiple output formats (JSON, XML, etc.)
4. **IDE Integration**: Pluggable IDE adapters

This specification serves as the authoritative reference for implementing and maintaining the doeff-indexer tool.
