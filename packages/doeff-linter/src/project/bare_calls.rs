//! DOEFF126: defk の定義を素で呼んでいる所を拾う。
//!
//! defk を素で呼ぶと、答えではなく Program が返る。型の誤りで落ちずに、静かに間違った値として流れる(
//! defk に改めた `latest-by-ref` を deff と検 4 file が素のまま呼んでいた — 検で見つかった)。defn / deff を defk に改める便が何百と
//! 続くので、呼び手の取り残しを見張る。決まり(戻せる)。
//!
//! defk と分かる定義 = repo の Hy の file の最上位の `(defk 名 …)`(`do` と `eval-and-compile` の中も)。呼びの頭の綴りは file の import と
//! 定義の場所で module まで解き(`smells::Scope`)、defk の集合と比べる。追えない呼び(引数で受けた関数・method)は拾わない。
//!
//! 拾うのは、呼びの答えを値として使う所だけ(Program が値の代わりに流れて静かに間違う所): 比べ・演算・真偽の組み合わせ
//! (`=`・`+`・`in`・`not`・`and` …)、答えを読む組み込みの関数(`len`・`str`・`get`・`sorted`・`isinstance` …)、method の的と引数
//! (`(.get (f …) "欄")`・`(.append out (f …))`)と属性(`(. (f …) 欄)`)、条件(`if`・`when`・`while` の頭・`cond` の条件)、繰り返しの元(`for` の束ねと
//! 内包表記の元)、record の欄(頭が大文字の型を作る呼び — doeff の package の effect は除く・repo の `defrecord` で宣言の型が Program か
//! EffectBase を名指す欄も除く — `ProgramFields`)。その位置の中の `if`・`when`・`cond`・`do`・`let` の枝も
//! 答えとして使う所のまま。
//! repo の defk の呼びの引数のうち、受ける引数の `:pre` の型が Program を受け得ない型(`ValueParams` — 組み込みの値の型・None・repo の
//! `defrecord`・`defwire`・`defenum` と、その `(get T …)`・`(of T …)`・`(| …)`)の位置も答えとして使う所(`(counts-sources-of (measure-rows-of standing))`
//! — Call が渡り、:pre の型の検に実行の時に初めて当たって落ちる。実弾 = agora-controllers の job patrol が 2026-11-04 に起動の約 5 分後に毎回
//! 落ちた。段が開く周期まで実行されず、merge と宣言を素通りした)。型の註の無い引数・Program・object・Any・知らない別名を受ける引数は拾わない。
//! 拾わない: `(<- …)` の右辺・`(! …)`・`(return …)`、Program を受ける呼びの引数(repo の関数に渡す形も — Program を受けて走らせる
//! 関数(run-on など)かもしれず、追えない)、名への束ね(後で Program として渡すかもしれない)。
//!
//! 定義の外(module の最上位の式 — `(val TABLE [(entry "a" (f …)) …])`・`(setv PAIRS #(…))`)は、位置を問わず答えとして使う所として拾う。
//! 定義の外には Program を走らせる所が無く、表の行や名に束ねた Program は値の代わりに流れる(実弾 = agora-controllers c6271008a が
//! scripts/land_focus_gate.hy の最上位の表に defk の tests-of を run 無しで足し、`--all` が起動で落ちた)。ただし `run`・`<-`・`!`・
//! doeff の package の呼びの中、defk の呼びの引数、関数の本体(`fn`・`fnk`・`defmacro`・`defclass` …)、quote / quasiquote の中は定義の中と同じ判定で下る。
//! `defsystem` は定義(関数に展開される)で、job の行の Program の式は答えとして使う所ではない。

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Prefix, Reader};

use super::facts::ByteSpan;
use super::facts_cache;
use super::names::{hy_mangle, module_of};
use super::rule::ProjectRule;
use super::smells::{children, live, span_of, Hy, Scope};

/// defk の集合(module まで含めた名・mangle 済み)と、各 defk の引数のうち `:pre` の型を名指した物(`ValueParams`)。
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct DefkNames {
    names: BTreeSet<String>,
    /// module まで含めた defk の名 → 引数の並びと `:pre` の型(引数の型を 1 つも名指さない defk は載せない)。
    #[serde(default)]
    params: BTreeMap<String, ValueParams>,
}

/// defk 1 つの引数の並びと、`:pre` の `(: 名 型)` が名指す型。型は union の枝ごとに、Program を受け得ない組み込みの型・None なら空の綴り、
/// それ以外は定義の file の import で module まで解いた名(呼びの所で repo の record の名と比べる — `ProgramFields::is_record`)。
/// 枝の 1 つでも読めない形(文字列の註・型の引数・Program を名指すかもしれない式)の引数は載せない。
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ValueParams {
    /// 位置で渡せる引数の名(mangle 済み・`*` か `#*` の前まで)。
    positional: Vec<String>,
    /// 引数の名(mangle 済み)→ 型の union の枝。
    types: BTreeMap<String, Vec<String>>,
}

impl ValueParams {
    /// 引数の名(mangle 済み)が、Program を受け得ない型だけを名指すか(`records` = repo の record の型の名)。
    fn refuses_program(&self, name: &str, records: &ProgramFields) -> bool {
        self.types.get(name).is_some_and(|arms| !arms.is_empty() && arms.iter().all(|arm| arm.is_empty() || records.is_record(arm)))
    }
}

impl DefkNames {
    /// module まで含めた名の列から作る(検のため)。
    pub fn of(names: &[&str]) -> Self {
        DefkNames { names: names.iter().map(|n| n.to_string()).collect(), params: BTreeMap::new() }
    }

    /// 別の集合を足す。
    pub fn extend(&mut self, other: DefkNames) {
        self.names.extend(other.names);
        self.params.extend(other.params);
    }

    /// defk(module まで含めた名)の引数の並びと型。
    fn params_of(&self, qualified: &str) -> Option<&ValueParams> {
        self.params.get(qualified)
    }

    /// module まで含めた名が defk か。
    pub fn contains(&self, qualified: &str) -> bool {
        self.names.contains(qualified)
    }

    /// 数(報告のため)。
    pub fn len(&self) -> usize {
        self.names.len()
    }

    /// 空か。
    pub fn is_empty(&self) -> bool {
        self.names.is_empty()
    }

    /// 集まりの指紋(file ごとの事実の cache の印に使う — この集まりに依る事実は、集まりが変われば作り直す)。
    pub fn digest(&self) -> String {
        use sha2::{Digest, Sha256};
        let mut hasher = Sha256::new();
        for name in &self.names {
            hasher.update(name.as_bytes());
            hasher.update([0u8]);
        }
        for (name, params) in &self.params {
            hasher.update(name.as_bytes());
            hasher.update([1u8]);
            hasher.update(params.positional.join(" ").as_bytes());
            for (param, arms) in &params.types {
                hasher.update([2u8]);
                hasher.update(param.as_bytes());
                hasher.update(arms.join(" ").as_bytes());
            }
        }
        format!("{:x}", hasher.finalize())
    }
}

/// 1 つの source の最上位の defk の名(`<module>.<名>`)。
pub fn defk_names_in(source: &str, module: &str) -> DefkNames {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let hy = Hy { src: source };
    let bindings = super::facts::hy_bindings(source, module);
    let scope = Scope { module, bindings: &bindings };
    let mut names = BTreeSet::new();
    let mut params = BTreeMap::new();
    let mut pending: Vec<&Form> = forms.iter().collect();
    while let Some(form) = pending.pop() {
        let Some(items) = live(form) else { continue };
        match items.first().and_then(|h| hy.symbol(h)) {
            Some("do" | "eval-and-compile" | "eval-when-compile") => pending.extend(items[1..].iter().copied()),
            Some("defk") => {
                let name_form = match items.get(1) {
                    Some(first) if first.bracket_items().is_some() => items.get(2).copied(),
                    other => other.copied(),
                };
                let name = name_form.map(|f| match &f.node {
                    Node::Annotated { target: Some(target), .. } => hy.text(target),
                    _ => hy.text(f),
                });
                if let Some(name) = name.filter(|n| !n.is_empty()) {
                    let qualified = format!("{}.{}", module, hy_mangle(name));
                    if let Some(found) = value_params_of(&hy, scope, &items).filter(|p| !p.types.is_empty()) {
                        params.insert(qualified.clone(), found);
                    }
                    names.insert(qualified);
                }
            }
            _ => {}
        }
    }
    DefkNames { names, params }
}

/// Program を受け得ない組み込みの型(Call はどれの instance でもない — `:pre` の isinstance が必ず断る)。
const VALUE_BUILTINS: &[&str] = &[
    "None", "str", "int", "float", "bool", "bytes", "bytearray", "complex", "tuple", "list", "dict", "set", "frozenset",
];

/// `(defk 名 [引数] {:pre [(: 名 型) …] …} …)` の引数の並びと型(`defk` の items — 名の前の decorator の列は読まない)。
fn value_params_of(hy: &Hy<'_>, scope: Scope<'_>, items: &[&Form]) -> Option<ValueParams> {
    let params = items.get(2).and_then(|p| p.bracket_items())?;
    let mut positional = Vec::new();
    let mut open = true;
    for param in super::smells::live_items(params) {
        let name = match &param.node {
            Node::Symbol => hy.symbol(param),
            Node::Annotated { target: Some(target), .. } => hy.symbol(target),
            Node::Seq { delim: Delim::Bracket, .. } => param.bracket_items().and_then(|i| i.first()).and_then(|first| hy.symbol(first)),
            _ => {
                // `#* rest`・`#** kw` — その先は位置で渡せない。
                open = false;
                continue;
            }
        };
        match name {
            Some("*") => open = false,
            Some("/") | None => {}
            Some(name) if open && !name.starts_with('&') => positional.push(hy_mangle(name)),
            Some(_) => {}
        }
    }
    let contract = items.iter().skip(3).take(2).find(|f| f.is_brace())?;
    let entries = match &contract.node {
        Node::Seq { delim: Delim::Brace, items } => super::smells::live_items(items),
        _ => return None,
    };
    let pre = entries.chunks(2).find(|pair| pair.first().is_some_and(|k| hy.text(k) == ":pre")).and_then(|pair| pair.get(1))?;
    let mut types = BTreeMap::new();
    for check in pre.bracket_items().map(super::smells::live_items).unwrap_or_default() {
        let Some(parts) = live(check) else { continue };
        let (Some(":"), Some(name), Some(ty)) = (parts.first().map(|h| hy.text(h)), parts.get(1).and_then(|n| hy.symbol(n)), parts.get(2)) else {
            continue;
        };
        if let (Some(arms), 3) = (type_arms(hy, scope, ty), parts.len()) {
            types.insert(hy_mangle(name), arms);
        }
    }
    Some(ValueParams { positional, types })
}

/// 型の式の union の枝(組み込みの値の型・None は空の綴り、それ以外の名は module まで解いた名)。読めない形は None。
fn type_arms(hy: &Hy<'_>, scope: Scope<'_>, ty: &Form) -> Option<Vec<String>> {
    if let Some(name) = hy.symbol(ty) {
        return Some(vec![if VALUE_BUILTINS.contains(&name) { String::new() } else { scope.qualify(name) }]);
    }
    let parts = live(ty)?;
    match parts.first().and_then(|h| hy.symbol(h))? {
        "|" => {
            let mut arms = Vec::new();
            for arm in &parts[1..] {
                arms.extend(type_arms(hy, scope, arm)?);
            }
            Some(arms)
        }
        // `(get tuple #(X ...))`・`(of dict K V)` — isinstance が見るのは外側の型だけ。
        "get" | "of" => type_arms(hy, scope, parts.get(1)?),
        _ => None,
    }
}

/// repo の Hy の file の defk の集合 — 集める file(`files`)と引き方(`reach`)を受ける(規則が無効なら空)。`(defk` の綴りを含む file だけを読み、最上位の defk の名を
/// module まで含めた名にする。1 file の実行は書いた file が名指す module の file だけを渡す(defk の集合は module まで含めた名で引かれ、引く名は書いた file の
/// 記号を解いた名 — その module の file だけで答えが同じ)。
pub fn defk_names_over(root: &Path, enabled: &BTreeSet<ProjectRule>, reach: facts_cache::Reach, files: &[(String, PathBuf)]) -> DefkNames {
    let mut all = DefkNames::default();
    if !enabled.contains(&ProjectRule::DefkCalledBare) {
        return all;
    }
    // file ごとの defk の名はその file の中身だけで決まるので、変わった file だけ読み直す(保存ごとの 1 file の実行でも
    // repo 全体の名が要るため — #1025)。
    let found: Vec<DefkNames> = facts_cache::per_file_in(reach, root, "defk-names", "", files, |rel, path| {
        let source = std::fs::read_to_string(path).ok()?;
        source.contains("(defk").then(|| defk_names_in(&source, &module_of(rel)))
    });
    for names in found {
        all.extend(names);
    }
    all
}

/// record の型の欄のうち、宣言の型が Program か EffectBase を名指す欄(module まで含めた型の名 → 欄の名・どちらも mangle 済み)。
/// その欄は Program を運ぶ宣言なので、型を作る呼びのその欄に置いた素の defk 呼びは答えとして使う所ではない(
/// doeff-records の RecordsServing の :prepare は (| Program EffectBase) で、serve-records が用意の task として走らせる)。
/// 入れないのは、型の註の無い欄・object / Any の欄・別名で隠れた欄(別名の名は Program と綴られない)— どれも今までどおり答えとして数える。
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ProgramFields {
    fields: BTreeMap<String, BTreeSet<String>>,
    /// repo の `defrecord`・`defwire`・`defenum` の型の名(module まで含めた名・mangle 済み)— Program を受け得ない型(defk の引数の
    /// `:pre` の型がこれだけなら、その引数に置いた素の defk 呼びは答えとして使う所 — `ValueParams`)。
    #[serde(default)]
    records: BTreeSet<String>,
}

impl ProgramFields {
    /// 別の表を足す。
    pub fn extend(&mut self, other: ProgramFields) {
        for (record, fields) in other.fields {
            self.fields.entry(record).or_default().extend(fields);
        }
        self.records.extend(other.records);
    }

    /// module まで含めた型の名の欄(mangle 済み)が Program を運ぶ宣言か。
    pub fn carries_program(&self, record: &str, field: &str) -> bool {
        self.fields.get(record).is_some_and(|names| names.contains(field))
    }

    /// module まで含めた型の名が repo の record(`defrecord`・`defwire`・`defenum`)か。
    pub fn is_record(&self, qualified: &str) -> bool {
        self.records.contains(qualified)
    }

    /// 空か。
    pub fn is_empty(&self) -> bool {
        self.fields.is_empty() && self.records.is_empty()
    }
}

/// 欄の型の宣言が名指すと Program を運ぶ欄とする型の名(module を付けた綴りは最後の段で比べる — `doeff.Program` も同じ)。
const PROGRAM_TYPES: &[&str] = &["Program", "EffectBase"];

/// 欄の型の註(綴り)が Program か EffectBase を名指すか — 註を語に区切り、どれかの語の最後の段が PROGRAM_TYPES の名なら真
/// (`(| Program EffectBase)`・`Program`・`doeff.Program`)。`object`・`Any`・別名は名指さない。
fn names_program_type(annotation: &str) -> bool {
    annotation
        .split(|c: char| !(c.is_alphanumeric() || c == '_' || c == '.' || c == '-'))
        .any(|word| PROGRAM_TYPES.contains(&word.rsplit('.').next().unwrap_or(word)))
}

/// 1 つの source の最上位の `defrecord` の、宣言の型が Program か EffectBase を名指す欄(`<module>.<型の名>` → 欄の名)。
/// 欄は `(#^ 型 名)` と `#^ 型 名` の 2 つの綴り(doeff-hy の defrecord)。最上位の `defrecord`・`defwire`・`defenum` の型の名も控える。
pub fn program_fields_in(source: &str, module: &str) -> ProgramFields {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let hy = Hy { src: source };
    let mut found = ProgramFields::default();
    let mut pending: Vec<&Form> = forms.iter().collect();
    while let Some(form) = pending.pop() {
        let Some(items) = live(form) else { continue };
        match items.first().and_then(|h| hy.symbol(h)) {
            Some("do" | "eval-and-compile" | "eval-when-compile") => pending.extend(items[1..].iter().copied()),
            Some("defwire" | "defenum") => {
                if let Some(name) = items.get(1).and_then(|f| hy.symbol(f)) {
                    found.records.insert(format!("{}.{}", module, hy_mangle(name)));
                }
            }
            Some("defrecord") => {
                let Some(name) = items.get(1).and_then(|f| hy.symbol(f)) else { continue };
                let record = format!("{}.{}", module, hy_mangle(name));
                found.records.insert(record.clone());
                for item in items.iter().skip(2) {
                    let annotated = match &item.node {
                        Node::Annotated { .. } => Some(*item),
                        Node::Seq { delim: Delim::Paren, .. } => live(item).and_then(|inner| inner.first().copied()),
                        _ => None,
                    };
                    if let Some(Form { node: Node::Annotated { annotation: Some(annotation), target: Some(target) }, .. }) = annotated {
                        if let (true, Some(field)) = (names_program_type(hy.text(annotation)), hy.symbol(target)) {
                            found.fields.entry(record.clone()).or_default().insert(hy_mangle(field));
                        }
                    }
                }
            }
            _ => {}
        }
    }
    found
}

/// 素の呼び 1 件。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BareCall {
    /// 呼びを含む最上位の定義の名(mangle 済み — 定義の外なら `<module>`)。
    pub definition: String,
    /// 呼びを含む定義の頭(defk・deff・defn・deftest …)— 説明の文のため。
    pub container: String,
    /// 呼んだ defk の綴り(書かれたとおり)。
    pub callee: String,
    pub span: ByteSpan,
}

impl BareCall {
    /// 登録簿の鍵の細目(`<定義>::<呼んだ defk>`)。
    pub fn detail(&self) -> String {
        format!("{}::{}", self.definition, hy_mangle(&self.callee))
    }
}

/// 答えを値として読む Python の演算子と組み込みの関数(引数の全部が答えとして使う所)。
const VALUE_HEADS: &[&str] = &[
    "=", "!=", "<", ">", "<=", ">=", "+", "-", "*", "/", "//", "%", "**", "in", "not-in", "not", "and", "or", "is", "is-not", "len", "str",
    "int", "float", "bool", "tuple", "list", "dict", "set", "frozenset", "sorted", "reversed", "any", "all", "sum", "min", "max", "repr",
    "get", "isinstance", "print", "enumerate", "zip", "iter", "next", "hash", "format", "abs", "round", "assert",
];

/// 名に束ねる形(`(setv 名 式 …)` の組・`(val 名 式)`・`(var 名 式)`・`(:= 名 式)`)— 素の呼びを名に束ねて後で答えとして使う形を追うため。
const PLAIN_BINDING_HEADS: &[&str] = &["setv", "setx", "val", "var", ":="];

/// 答えとして使う位置を中の枝へ受け継ぐ形(条件の式は別に答えとして使う所)。
const BRANCHING_FORMS: &[&str] = &["if", "when", "unless", "cond", "do", "let"];

/// 内包表記(元は items[2])。
const COMPREHENSIONS: &[&str] = &["lfor", "sfor", "gfor", "dfor"];

/// 定義の頭(呼びを含む定義の名を取るため)。defsystem(doeff-hy)は関数に展開され、job の行の式は「名 → Program」の
/// Program の値そのもの(実行しない — 宣言の道具と sim-cluster が読む)なので、定義の中と同じ判定で下る(job の式は答えとして使う所ではない・#913)。
const DEFINITION_HEADS: &[&str] = &["defk", "deff", "defn", "defn/a", "defp", "defpp", "defhandler", "deftest", "defeffect", "defsystem"];

/// 定義の外で、中の Program を受ける形(`run` と effect として出す形)— 中は定義の中と同じ判定で下る。doeff の package の呼びも同じ扱い。
const MODULE_LEVEL_PROGRAM_HEADS: &[&str] = &["run", "<-", "!", "yield", "yield-from"];

/// 定義の外に書いた関数の本体・macro・class・import — 中は定義の中と同じ判定で下る(呼ばれた時に答えを返す所で、定義の外ではない)。
const MODULE_LEVEL_BODY_HEADS: &[&str] = &[
    "fn", "fn/a", "fnk", "defmacro", "defmacro/g!", "defreader", "defclass", "defrecord", "defmain", "import", "require", "quote", "quasiquote",
];

/// 1 つの source の、答えとして使う所で呼んだ defk を全部拾う(同じ定義の同じ呼び先は最初の 1 件だけ)。record の欄の型は知らない
/// (型を作る呼びの欄は全部答えとして数える — 欄の型を使うのは `bare_calls_with`)。
pub fn bare_calls_in(source: &str, scope: Scope<'_>, defks: &DefkNames) -> Vec<BareCall> {
    bare_calls_with(source, scope, defks, &ProgramFields::default())
}

/// `bare_calls_in` と同じ — ただし型を作る呼びの `:欄 値` で、その欄の宣言の型が Program か EffectBase を名指すなら(`fields`)、値を
/// 答えとして数えない(欄が Program を運ぶ宣言)。
pub fn bare_calls_with(source: &str, scope: Scope<'_>, defks: &DefkNames, fields: &ProgramFields) -> Vec<BareCall> {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let walker = Walker { hy: Hy { src: source }, scope, defks, fields };
    let mut found: Vec<BareCall> = Vec::new();
    for form in &forms {
        walker.top(form, &mut found);
    }
    let mut seen = BTreeSet::new();
    found.retain(|call| seen.insert(call.detail()));
    found
}

/// 素の呼びを探して木を下る道具。
struct Walker<'a> {
    hy: Hy<'a>,
    scope: Scope<'a>,
    defks: &'a DefkNames,
    fields: &'a ProgramFields,
}

impl Walker<'_> {
    /// 最上位の form 1 つ — 定義なら名と頭を控えて本体を下り、`do` などは中へ入る。
    fn top(&self, form: &Form, out: &mut Vec<BareCall>) {
        let head = self.hy.head(form);
        match head {
            Some("do" | "eval-and-compile" | "eval-when-compile") => {
                for child in live(form).unwrap_or_default().into_iter().skip(1) {
                    self.top(child, out);
                }
            }
            Some(h) if DEFINITION_HEADS.contains(&h) => {
                let items = live(form).unwrap_or_default();
                let name_form = match items.get(1) {
                    Some(first) if first.bracket_items().is_some() => items.get(2).copied(),
                    other => other.copied(),
                };
                let name = name_form
                    .map(|f| match &f.node {
                        Node::Annotated { target: Some(target), .. } => self.hy.text(target),
                        _ => self.hy.text(f),
                    })
                    .unwrap_or("");
                let context = Context {
                    definition: hy_mangle(name),
                    container: h.to_string(),
                    bound: self.bare_bindings(form),
                    shadowed: self.local_names(form),
                    module_level: false,
                };
                for child in items.into_iter().skip(1) {
                    match live(child) {
                        // defsystem の job の行 `(名 Program の式 :欄 値 …)` の頭は job の名で、呼びではない(同じ名の defk が在っても)— 中だけを下る。
                        Some(row) if h == "defsystem" && row.first().is_some_and(|head| matches!(head.node, Node::Symbol)) => {
                            for part in row.into_iter().skip(1) {
                                self.walk(part, false, &context, out);
                            }
                        }
                        _ => self.walk(child, false, &context, out),
                    }
                }
            }
            _ => self.walk(
                form,
                false,
                &Context {
                    definition: format!("<{}>", self.scope.module),
                    container: "module".to_string(),
                    bound: BTreeMap::new(),
                    shadowed: BTreeSet::new(),
                    module_level: true,
                },
                out,
            ),
        }
    }

    /// form を下る。`value` はこの form の答えが値として使われる位置か。
    fn walk(&self, form: &Form, value: bool, context: &Context, out: &mut Vec<BareCall>) {
        self.walk_at(form, value, false, context, out);
    }

    /// form を下る。`yielded` はこの form が effect として出される位置(`(<- … form)` の右辺・`(! form)`・`(yield form)`)か。
    fn walk_at(&self, form: &Form, value: bool, yielded: bool, context: &Context, out: &mut Vec<BareCall>) {
        match &form.node {
            Node::Seq { delim: Delim::Paren, .. } => self.call(form, value, yielded, context, out),
            // defk の素の呼びを束ねた名(`all`・`all.x`)を答えとして使う所 — 束ねた呼びを違反にする。
            Node::Symbol if value => {
                let text = self.hy.text(form);
                let name = text.split('.').next().unwrap_or(text);
                if let Some((callee, span)) = context.bound.get(name) {
                    out.push(BareCall { definition: context.definition.clone(), container: context.container.clone(), callee: callee.clone(), span: *span });
                }
            }
            // 定義の外の quote / quasiquote(`'(f …)`・`` `(f …) ``)と reader の tag の中は呼びではない — 定義の中と同じ判定で下る。
            Node::Prefixed { prefix: Prefix::Quote | Prefix::Quasiquote, .. } | Node::Tagged { .. } if context.module_level => {
                let inner = context.inside_definition();
                for child in children(form) {
                    self.walk(child, false, &inner, out);
                }
            }
            _ => {
                for child in children(form) {
                    self.walk(child, false, context, out);
                }
            }
        }
    }

    /// `( … )` 1 つ — 自分が defk の呼びで答えとして使われているなら積み、子へ「答えとして使う位置か」を配る。
    fn call(&self, form: &Form, value: bool, yielded: bool, context: &Context, out: &mut Vec<BareCall>) {
        let items = live(form).unwrap_or_default();
        let head = items.first().and_then(|h| self.hy.head_text(h));
        let qualified = head.filter(|h| !h.starts_with(':')).map(|h| self.scope.qualify(h));
        let is_defk = qualified.as_deref().is_some_and(|q| self.defks.contains(q)) && !head.is_some_and(|h| context.shadowed.contains(h));
        // `(all.get k)` — 束ねた名の method を呼ぶのも、その名を答えとして使う所。
        if let Some((callee, span)) = head.and_then(|h| h.split_once('.')).and_then(|(name, _)| context.bound.get(name)) {
            out.push(BareCall { definition: context.definition.clone(), container: context.container.clone(), callee: callee.clone(), span: *span });
        }
        if let (true, true, Some(callee)) = (is_defk, value || context.module_level, head) {
            out.push(BareCall { definition: context.definition.clone(), container: context.container.clone(), callee: callee.to_string(), span: span_of(form) });
        }
        let doeff = qualified.as_deref().is_some_and(|q| q.starts_with("doeff"));
        // 定義の外で、中が Program を受ける所・関数の本体・defk の呼びの引数(その defk が Program を受けるかもしれない — 呼び自体は積んだ)
        // なら、中は定義の中と同じ判定で下る。
        // `run` を値として受け取る呼び(`(map run #((f …) …))`)も、中の Program を走らせる所。
        let passes_runner =
            items.iter().skip(1).filter_map(|item| self.hy.symbol(item)).any(|s| s == "run" || self.scope.qualify(s) == "doeff.run");
        let leaves_module_level = context.module_level
            && (doeff
                || is_defk
                || passes_runner
                || head.is_some_and(|h| {
                    MODULE_LEVEL_PROGRAM_HEADS.contains(&h) || MODULE_LEVEL_BODY_HEADS.contains(&h) || DEFINITION_HEADS.contains(&h)
                }));
        let inner;
        let context = if leaves_module_level {
            inner = context.inside_definition();
            &inner
        } else {
            context
        };
        // record の欄は答えとして使う所 — ただし effect として出す型(`(<- (AnswerLater :answer (f …)))`)の欄は Program を運ぶことがあるので除く。
        let constructor = head.is_some_and(|h| h.chars().next().is_some_and(|c| c.is_ascii_uppercase())) && !doeff && !is_defk && !yielded;
        // その欄の宣言の型が Program か EffectBase を名指す `:欄 値` の値は、Program を運ぶ欄に置いた物 — 答えとして使う所ではない(#2877)。
        let program_field = |index: usize| -> bool {
            let record = qualified.as_deref().filter(|_| constructor);
            let keyword = items.get(index.wrapping_sub(1)).filter(|prev| index >= 2 && matches!(prev.node, Node::Keyword));
            match (record, keyword) {
                (Some(record), Some(prev)) => self.fields.carries_program(record, &hy_mangle(self.hy.text(prev).trim_start_matches(':'))),
                _ => false,
            }
        };
        let binds = head == Some("<-");
        // repo の defk の呼びの引数のうち、受ける引数の `:pre` の型が Program を受け得ない位置は答えとして使う所(Call が :pre の検で落ちる)。
        let value_slots = qualified
            .as_deref()
            .filter(|_| is_defk)
            .and_then(|q| self.defks.params_of(q))
            .map(|params| self.value_slots(&items, params))
            .unwrap_or_default();
        let last = items.len().saturating_sub(1);
        for (index, child) in items.iter().enumerate().skip(1) {
            let child_value = match head {
                _ if value_slots.get(index).copied().unwrap_or(false) => true,
                Some(h) if VALUE_HEADS.contains(&h) => true,
                Some(".") => index == 1,
                // method の的も引数も答えとして使う所(`(.get (f …) "欄")`・`(.append out (f …))`)。
                Some(h) if h.starts_with('.') && h.len() > 1 => true,
                Some("if" | "when" | "unless" | "while") if index == 1 => true,
                Some("cond") => index % 2 == 1 || value,
                Some(h) if BRANCHING_FORMS.contains(&h) => value,
                Some(h) if COMPREHENSIONS.contains(&h) => index == 2,
                Some("for") if index == 1 => {
                    // `(for [x (f …)] …)` — 束ねの列の元(奇数の位置)が繰り返しの元。
                    let sources = child.bracket_items().map(super::smells::live_items).unwrap_or_default();
                    for (at, source) in sources.iter().enumerate() {
                        self.walk(source, at % 2 == 1, context, out);
                    }
                    continue;
                }
                _ => constructor && !matches!(child.node, Node::Keyword) && !program_field(index),
            };
            let child_yielded = match head {
                Some("!" | "yield" | "yield-from") => true,
                _ => binds && (index == last || items.get(index + 1).is_some_and(|next| matches!(next.node, Node::Keyword))),
            };
            self.walk_at(child, child_value, child_yielded, context, out);
        }
    }
}

/// 下っている所の定義(鍵と説明のため)と、その定義の中で defk の素の呼びを束ねた名(名 → 呼んだ defk・束ねた呼びの範囲)。
struct Context {
    definition: String,
    container: String,
    bound: BTreeMap<String, (String, ByteSpan)>,
    /// 定義の中で局所に束ねた名(綴りのまま — 入れ子の deff・defn・defk の名・引数・束ねの的)。同じ綴りの呼びは局所の物を呼ぶので defk の呼びと読まない
    /// (agora-controllers の検の中の `(deff mail-of …)` が、同じ file の最上位の defk mail-of を隠す)。
    shadowed: BTreeSet<String>,
    /// 定義の外(module の最上位の式)を下っているか。定義の外には Program を走らせる所が無いので、`run` などの Program を受ける形
    /// (`MODULE_LEVEL_PROGRAM_HEADS`・doeff の package の呼び)と関数の本体(`MODULE_LEVEL_BODY_HEADS`)と quote の外にある defk の呼びは、
    /// 位置を問わず答えとして使う所(表の行・`+` の引数・repo の関数の引数・名への束ね)。
    module_level: bool,
}

impl Context {
    /// 定義の外の判定を外した写し(Program を受ける形・関数の本体・quote の中を、定義の中と同じ判定で下るため)。
    fn inside_definition(&self) -> Context {
        Context {
            definition: self.definition.clone(),
            container: self.container.clone(),
            bound: self.bound.clone(),
            shadowed: self.shadowed.clone(),
            module_level: false,
        }
    }
}

impl Walker<'_> {
    /// defk の呼び `(f a b :c d)` の items の位置ごとに、受ける引数の `:pre` の型が Program を受け得ないか(`#*`・`#**` の後の位置の引数は追わない)。
    fn value_slots(&self, items: &[&Form], params: &ValueParams) -> Vec<bool> {
        let mut slots = vec![false; items.len()];
        let mut position = Some(0usize);
        let mut index = 1;
        while index < items.len() {
            match &items[index].node {
                Node::Keyword => {
                    let name = hy_mangle(self.hy.text(items[index]).trim_start_matches(':'));
                    if let Some(slot) = slots.get_mut(index + 1) {
                        *slot = params.refuses_program(&name, self.fields);
                    }
                    index += 2;
                    continue;
                }
                Node::Prefixed { prefix: Prefix::Unpack | Prefix::UnpackMapping, .. } => position = None,
                _ => {
                    if let Some(at) = position {
                        slots[index] = params.positional.get(at).is_some_and(|name| params.refuses_program(name, self.fields));
                        position = Some(at + 1);
                    }
                }
            }
            index += 1;
        }
        slots
    }

    /// 定義の中で局所に束ねた名(引数・入れ子の定義の名と引数・束ねの的)— 同じ綴りの呼びは defk の呼びと読まない。
    fn local_names(&self, definition: &Form) -> BTreeSet<String> {
        let mut names = BTreeSet::new();
        let items = live(definition).unwrap_or_default();
        // 定義の自分の名は隠さない(自分を呼び直す defk は defk の呼び)— 引数と本体の中の束ねだけを集める。
        let mut body_at = if items.get(1).is_some_and(|first| first.bracket_items().is_some()) { 3 } else { 2 };
        // 引数の並びの無い定義(`(deftest 名 本体 …)`)もある — 本体の最初の form を引数と読まない。
        if let Some(params) = items.get(body_at).filter(|p| p.bracket_items().is_some()) {
            self.param_names(params, &mut names);
            body_at += 1;
        }
        let mut pending: Vec<&Form> = items.iter().skip(body_at).copied().collect();
        while let Some(form) = pending.pop() {
            pending.extend(children(form));
            let Some(parts) = live(form) else { continue };
            match parts.first().and_then(|h| self.hy.head_text(h)) {
                Some("deff" | "defn" | "defn/a" | "defk" | "defmacro") => {
                    if let Some(name) = parts.get(1).and_then(|f| self.bound_symbol(f)) {
                        names.insert(name.to_string());
                    }
                    if let Some(params) = parts.get(2) {
                        self.param_names(params, &mut names);
                    }
                }
                Some("fn" | "fnk" | "fn/a") => {
                    if let Some(params) = parts.get(1) {
                        self.param_names(params, &mut names);
                    }
                }
                Some(h) if PLAIN_BINDING_HEADS.contains(&h) => {
                    names.extend(parts[1..].iter().step_by(2).filter_map(|t| self.bound_symbol(t)).map(str::to_string));
                }
                Some("<-") if parts.len() >= 3 => names.extend(parts.get(1).and_then(|t| self.hy.symbol(t)).map(str::to_string)),
                Some("let" | "for") => {
                    let pairs = parts.get(1).and_then(|b| b.bracket_items()).map(super::smells::live_items).unwrap_or_default();
                    names.extend(pairs.iter().step_by(2).filter_map(|t| self.bound_symbol(t)).map(str::to_string));
                }
                _ => {}
            }
        }
        names
    }

    /// 引数の並び `[a #^ T b [c 1] #* rest]` の名(綴りのまま)。
    fn param_names(&self, params: &Form, names: &mut BTreeSet<String>) {
        for param in params.bracket_items().map(super::smells::live_items).unwrap_or_default() {
            let name = match &param.node {
                Node::Seq { delim: Delim::Bracket, .. } => param.bracket_items().and_then(|i| i.first()).and_then(|f| self.bound_symbol(f)),
                Node::Prefixed { inner: Some(inner), .. } => self.bound_symbol(inner),
                _ => self.bound_symbol(param),
            };
            names.extend(name.map(str::to_string));
        }
    }

    /// 束ねの的の名(記号か `#^ T 名` の名)。
    fn bound_symbol(&self, form: &Form) -> Option<&str> {
        match &form.node {
            Node::Annotated { target: Some(target), .. } => self.hy.symbol(target),
            _ => self.hy.symbol(form),
        }
    }

    /// 定義の中で、defk の素の呼びを `setv`・`val`・`var`・`:=`・`let` で名に束ねた所を集める。同じ名を `(<- 名 …)` でも束ねる定義では
    /// その名を追わない(どちらの値か決まらない)。追うのは 1 つの定義の中だけ。
    fn bare_bindings(&self, definition: &Form) -> BTreeMap<String, (String, ByteSpan)> {
        let mut bound = BTreeMap::new();
        let mut effect_bound = BTreeSet::new();
        let mut pending = vec![definition];
        while let Some(form) = pending.pop() {
            if let Some(items) = live(form) {
                let head = items.first().and_then(|h| self.hy.head_text(h));
                let pairs: Vec<(&Form, &Form)> = match head {
                    Some(h) if PLAIN_BINDING_HEADS.contains(&h) => items[1..].chunks(2).filter_map(|p| Some((*p.first()?, *p.get(1)?))).collect(),
                    Some("let") => items
                        .get(1)
                        .and_then(|b| b.bracket_items())
                        .map(super::smells::live_items)
                        .unwrap_or_default()
                        .chunks(2)
                        .filter_map(|p| Some((*p.first()?, *p.get(1)?)))
                        .collect(),
                    Some("<-") => {
                        if let Some(name) = items.get(1).and_then(|t| self.hy.symbol(t)) {
                            effect_bound.insert(name.to_string());
                        }
                        Vec::new()
                    }
                    _ => Vec::new(),
                };
                for (target, value) in pairs {
                    let (Some(name), Some(callee)) = (self.hy.symbol(target), self.hy.head(value)) else { continue };
                    if self.defks.contains(&self.scope.qualify(callee)) {
                        bound.entry(name.to_string()).or_insert((callee.to_string(), span_of(value)));
                    }
                }
            }
            pending.extend(children(form));
        }
        bound.retain(|name, _| !effect_bound.contains(name));
        bound
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    /// module m・import の束縛 bindings で素の呼びを (定義::呼び先) の列にする。defk は `m.fetch` と `lib.load`。
    fn found(source: &str, bindings: &[(&str, &str)]) -> Vec<String> {
        let bindings: BTreeMap<String, String> = bindings.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
        let defks = DefkNames::of(&["m.fetch", "lib.load"]);
        bare_calls_in(source, Scope { module: "m", bindings: &bindings }, &defks).iter().map(|c| c.detail()).collect()
    }

    #[test]
    fn defk_names_come_from_top_level_defk_definitions() {
        let names = defk_names_in("(defk fetch [x] x)\n(deff plain [x] x)\n(do (defk inner [] 1))\n(defk #^ int typed [] 1)\n", "app.core.x");
        assert!(names.contains("app.core.x.fetch") && names.contains("app.core.x.inner") && names.contains("app.core.x.typed"));
        assert!(!names.contains("app.core.x.plain"));
        assert_eq!(names.len(), 3);
    }

    /// record の欄の宣言の型が Program か EffectBase を名指す時だけ、型を作る呼びのその欄の素の呼びを数えない。
    /// 型の註の無い欄・object / Any の欄・別名で隠れた欄・位置で渡した値は今までどおり数える。
    #[test]
    fn record_fields_declared_as_programs_carry_bare_calls_and_others_do_not() {
        let source = r#"(defrecord Serving
  "設定"
  (#^ (| Program EffectBase) prepare)
  #^ Program later
  (#^ doeff.Program qualified)
  (#^ object anything)
  (#^ typing.Any whatever)
  (#^ PreparedProgram aliased)
  (#^ dict rows)
  plain)
(defk carries [x] (Serving :prepare (fetch x) :later (fetch x) :qualified (fetch x)))
(defk untyped [x] (Serving :plain (fetch x)))
(defk opaque [x] (Serving :anything (fetch x)))
(defk any-typed [x] (Serving :whatever (fetch x)))
(defk hidden [x] (Serving :aliased (fetch x)))
(defk data [x] (Serving :rows (fetch x)))
(defk positional [x] (Serving (fetch x)))"#;
        let fields = program_fields_in(source, "m");
        assert!(fields.carries_program("m.Serving", "prepare") && fields.carries_program("m.Serving", "later"));
        assert!(fields.carries_program("m.Serving", "qualified"));
        for field in ["anything", "whatever", "aliased", "rows", "plain"] {
            assert!(!fields.carries_program("m.Serving", field), "{}", field);
        }
        let bindings = BTreeMap::new();
        let defks = DefkNames::of(&["m.fetch"]);
        let got: Vec<String> =
            bare_calls_with(source, Scope { module: "m", bindings: &bindings }, &defks, &fields).iter().map(|c| c.detail()).collect();
        // Program を運ぶ欄(prepare・later・qualified)の carries は拾わない。註の無い欄・object・Any・別名・dict・位置の値は拾う。
        assert_eq!(got, vec!["untyped::fetch", "opaque::fetch", "any_typed::fetch", "hidden::fetch", "data::fetch", "positional::fetch"]);
        // 欄の型を知らない呼び(bare_calls_in)は今までどおり全部拾う。
        assert!(found(source, &[]).contains(&"carries::fetch".to_string()));
    }

    #[test]
    fn bare_calls_are_found_where_the_answer_is_used_as_a_value() {
        let source = r#"(deff text-at [ref] (.get (fetch ref) "text"))
(defn count-rows [] (len (load "a")))
(deff collect [out ref] (.append out (fetch ref)))
(defk checks [ref]
  (when (fetch ref) (return 1))
  (val same (= (fetch ref) ref))
  (val field (. (fetch ref) text))
  (val row (Row :text (fetch ref)))
  (<- later (AnswerLater :answer (fetch ref)))
  (for [x (load "b")] (print x))
  (val xs (lfor y (fetch ref) y))
  (return (+ 1 (if same (fetch 1) 2))))
(val MODULE-LEVEL (str (fetch 0)))"#;
        assert_eq!(found(source, &[("load", "lib")]), vec!["text_at::fetch", "count_rows::load", "collect::fetch", "checks::fetch", "checks::load", "<m>::fetch"]);
    }

    #[test]
    fn programs_that_are_passed_bound_or_returned_are_not_bare() {
        let source = r#"(defk top []
  (<- out (run (with_handlers [h] (fetch 1))))
  (<- many (Gather [(fetch 1) (fetch 2)]))
  (<- task (Spawn (fetch 3)))
  (<- r (result (maybe (fetch 4))))
  (<- chosen (if flag (fetch 6) (load 7)))
  (<- nested (fetch (load 8)))
  (val later (fetch 9))
  (return out))
(deff runner [x] (run-on (fetch x) handlers))
(deff builder [x] (fetch x))
(deff callback [x] (helper x) (len (other.fetch x)) (len (f x)))"#;
        // Program を受ける呼びの引数・名への束ね・関数の答えとして返す形は拾わない(Program として渡すかもしれない)。
        // 追えない呼び(helper・method・引数の f)も拾わない。
        let bindings = [("run", "doeff"), ("with_handlers", "doeff"), ("Gather", "doeff"), ("Spawn", "doeff"), ("load", "lib")];
        assert!(found(source, &bindings).is_empty(), "{:?}", found(source, &bindings));
    }

    /// 1 つの source を repo の全部として、defk の名と record の表をその source から作って素の呼びを拾う(module m・import は無し)。
    fn found_in_repo(source: &str) -> Vec<String> {
        let bindings = BTreeMap::new();
        let defks = defk_names_in(source, "m");
        let fields = program_fields_in(source, "m");
        bare_calls_with(source, Scope { module: "m", bindings: &bindings }, &defks, &fields).iter().map(|c| c.detail()).collect()
    }

    /// 受ける引数の `:pre` の型が Program を受け得ない(組み込みの値の型・None・repo の record)defk の呼びの引数に、defk を素で呼んで
    /// 渡す形は拾う — 実弾 = agora-controllers の job patrol(2026-11-04)の `(counts-sources-of (measure-rows-of standing))`。
    /// Call が渡り、:pre の型の検に実行の時に初めて当たって落ちた。位置の引数・keyword の引数・record の型の引数・枝の中も同じ。
    #[test]
    fn bare_defk_calls_passed_where_the_receiving_defk_refuses_programs_are_found() {
        let source = r#"(defrecord StandingState "控え" (#^ int n))
(defrecord ContinuingEntry "行" (#^ str word))
(defk measure-rows-of [standing]
  {:pre [(: standing (| StandingState None))] :post [(: % (get tuple #(ContinuingEntry ...)))]}
  #())
(defk counts-sources-of [measures]
  {:pre [(: measures (get tuple #(ContinuingEntry ...)))] :post [(: % tuple)]}
  measures)
(defk fresh-standing [] {:post [(: % StandingState)]} (StandingState :n 1))
(defk stage [standing]
  (<- counts (get tuple #(str ...)) (counts-sources-of (measure-rows-of standing)))
  counts)
(defk by-keyword [standing]
  (<- counts (counts-sources-of :measures (measure-rows-of standing)))
  counts)
(defk by-record []
  (<- rows (measure-rows-of (fresh-standing)))
  rows)
(defk in-branch [standing flag]
  (<- counts (counts-sources-of (if flag (measure-rows-of standing) #())))
  counts)"#;
        assert_eq!(
            found_in_repo(source),
            vec!["stage::measure_rows_of", "by_keyword::measure_rows_of", "by_record::fresh_standing", "in_branch::measure_rows_of"]
        );
    }

    /// 受ける引数が Program を受けるかもしれない時は拾わない: 型の註の無い引数・Program・object・知らない別名・型の引数のある union の枝、
    /// `#*` の後の位置、`<-` か `!` で値にしてから渡す形、局所の `deff` が同じ綴りの defk を隠す呼び、defsystem の job の行の頭(job の名)。
    #[test]
    fn bare_defk_calls_passed_where_the_receiving_defk_may_take_programs_are_not_found() {
        let source = r#"(defrecord Row "行" (#^ str word))
(defk rows-of [n] {:pre [(: n int)] :post [(: % tuple)]} #())
(defk count-of [rows] {:pre [(: rows tuple)]} (len rows))
(defk untyped [body] body)
(defk run-twice [body] {:pre [(: body Program)]} (<- a body) a)
(defk anything [value] {:pre [(: value object)]} value)
(defk aliased [value] {:pre [(: value ProgramLike)]} value)
(defk either [value] {:pre [(: value (| tuple Program))]} value)
(defk spread [first #* rest] {:pre [(: first int) (: rest tuple)]} first)
(defk caller [n]
  (<- a (untyped (rows-of n)))
  (<- b (run-twice (rows-of n)))
  (<- c (anything (rows-of n)))
  (<- d (aliased (rows-of n)))
  (<- e (either (rows-of n)))
  (<- f (spread #* xs (rows-of n)))
  (<- rows (rows-of n))
  (<- g (count-of rows))
  (<- h (count-of (! (rows-of n))))
  #(a b c d e f g h))
(deftest test-local-function-hides-the-defk
  (deff rows-of [n] {:pre [(: n int)] :post [(: % tuple)]} #())
  (<- counted (count-of (rows-of 1)))
  counted)
(defsystem jobs [foundation]
  (rows-of (rows-of foundation) :replicas 1))"#;
        assert!(found_in_repo(source).is_empty(), "{:?}", found_in_repo(source));
    }

    #[test]
    fn bound_bare_calls_used_as_values_and_assert_messages_are_found() {
        // agora の incident_placement.hy(直す前 9e2574a6^)の形: (.items (histories run)) は method の的、
        // (setv … all (histories run)) の後の (get all job) は束ねた名を答えとして使う所。assert の文 (describe …) も答えとして使う。
        let source = r#"(deff turn-survives-roll [run]
  (for [[job history] (.items (fetch run))] (print job)))
(deff waits-of-run [run]
  (setv out [] all (fetch run))
  (for [job (.keys all)] (.append out (get all job)))
  out)
(deff via-let [run] (let [rows (fetch run)] (len rows)))
(deftest test-laws (assert (in "TI1" laws) (fetch out.breaches)))
(defk fine [run]
  (setv program (fetch run))
  (<- got (run-on program))
  (val later (fetch run))
  (return later))
(defk rebound [run] (setv x (fetch run)) (<- x (fetch run)) (len x))"#;
        assert_eq!(found(source, &[]), vec!["turn_survives_roll::fetch", "waits_of_run::fetch", "via_let::fetch", "test_laws::fetch"]);
    }

    #[test]
    fn bare_calls_inside_module_level_tables_are_found() {
        // agora-controllers c6271008a の scripts/land_focus_gate.hy を縮めた形: module の最上位の表の行に defk の tests-of を run 無しで足し、
        // 表に Program が混ざって `--all` が起動で落ちた。表の行の組み立て(`+`・repo の関数 entry の引数)の中でも、定義の外では答えとして使う所。
        let source = r#"(defn entry [path cmds] [path cmds])
(setv SIM-TESTS [(run (fetch "sim"))])
(val TABLE [(entry "a" [(run (fetch "x"))])
            ["b" (+ [(fetch "y") (run (load "z"))] SIM-TESTS)]])
(setv PAIRS #((entry "c" (load "w"))))"#;
        assert_eq!(found(source, &[("load", "lib"), ("run", "doeff")]), vec!["<m>::fetch", "<m>::load"]);
    }

    #[test]
    fn module_level_programs_that_are_run_passed_or_quoted_are_not_bare() {
        // 定義の外でも拾わない: run・doeff の Program を受ける呼び・`<-`・`!` の中、defk を値として渡す所(呼んでいない)、
        // 関数の本体(fn・fnk・defmacro・defclass の method)、quote / quasiquote の中。
        let source = r#"(setv ANSWER (run (with_handlers [h] (fetch 1))))
(setv DIRECT (doeff.run (fetch 2)))
(setv REGISTRY {"fetch" fetch "load" load})
(setv LATER (fn [x] (fetch x)))
(setv LATER-K (fnk [x] (<- got (fetch x)) (return got)))
(defmacro fetch-all [#* xs] `(lfor x ~xs (fetch x)))
(defclass Holder [] (defn method [self] (fetch 3)))
(setv QUOTED '(fetch 4))
(val REQUESTS (tuple (map run #((fetch 6) (fetch 7)))))
(when (= __name__ "__main__") (run (fetch 5)))"#;
        let bindings = [("run", "doeff"), ("with_handlers", "doeff"), ("load", "lib")];
        assert!(found(source, &bindings).is_empty(), "{:?}", found(source, &bindings));
    }

    #[test]
    fn defsystem_job_programs_are_declared_not_bare() {
        // doeff-hy の defsystem は関数に展開され、job の行の式は「名 → Program」の Program の値そのもの(実行しない)。
        // job の名が defk の名と同じ形(controllers/kanban/entry/writes_service.hy の (fetch (fetch foundation)))も呼びではない。
        let source = r#"(defsystem sample-system [foundation]
  "見本の系"
  (fetch (fetch foundation 3 1.0) :needs #{"sim"})
  (placer (load foundation) :needs #{"sim"} :environ {"POLL" "5.0"}))
(defsystem wrong-system [foundation]
  (reader (fetch foundation) :environ {"X" (str (load "k"))}))
(val OUTSIDE (fetch 1))"#;
        // defsystem の中でも答えとして使う所(`str` の引数)の素の呼びと、定義の外の素の呼びは今までどおり拾う。
        assert_eq!(found(source, &[("load", "lib")]), vec!["wrong_system::load", "<m>::fetch"]);
    }
}

