//! `doeff-linter fix defn-to-defk` — Hy の defn を defk に直し、repo の中の呼び手を `<-` / `!` へ書き換える変換(agora-redesign #828)。
//!
//! 出自: operator 2026-09-28 逐語 "in my understanding linter fixes are somewhat simple and easy,,, but why is it taking this long time?"
//! — LLM の係が file ごとに呼び手の書き換えまで手でしていたので、機械的な書き換えをこの変換へ移し、係は変換が直せなかった
//! 「判断の要る物」だけを直す(coordinator の戻せる決定)。
//!
//! 読み方は linter と同じ doeff-indexer の読み取り器(form の木と byte の範囲)で、書き換えは byte の範囲の置き換えだけ
//! (書いた人の改行・註・字下げは残す)。変換の後に DOEFF126(defk の素の呼び)の判定を同じ読み手で撃ち、残りを報告する。
//!
//! 定義の書き換え:
//! - `(defn #^ R 名 [#^ T x …] "doc" 本体)` → `(defk 名 [x …] {:pre [(: x T) …] :post [(: % R)]} "doc" 本体)`。defk は :pre に全部の引数の
//!   型、:post に戻り値の型を要る(doeff-hy の macro が確かめる)ので、書いてある `#^` の注記から組む。注記が無い・`object`・`Any` の物は
//!   組めない(判断の要る物)。`(get list str)` のような型の引数つきの型は isinstance が受けないので、契約には元の型(`list`)を書き、
//!   引数の `#^` の注記は残す(静的な型は注記が持つ)。既定値が None の引数は `(| T None)`、`float` は `(| float int)`(int を渡す呼び手が
//!   普通なので — 注記は検査されなかった)。
//! - 既に `{:pre … :post …}` の辞書があれば残し、足りない鍵だけ足す。`:tags` は足さない(別の規則 DOEFF112 が見る)。
//!
//! 呼び手の書き換え(repo の Hy の file 全部):
//! - `(setv x (f a))`・`(val x (f a))`(名 1 つへの束ね)→ `(<- x (f a))`。
//! - それ以外の式の途中・末尾の呼び → `(! (f a))`(末尾も — defk の本体が Program を返すと :post の型で落ちる。実測 2026-09-28)。
//! - 書き換えられない所(内包表記・素の関数(defn・deff・fn)の中・method・module の最上位・macro の中・`->` の中・関数そのものを値として
//!   渡す所・Python からの参照・import の別名・除いた path の file)は書き換えず、「判断の要る物」に出す(係が手で直す — coordinator の
//!   指示 2026-09-28)。`--strict` の時は、そういう所が 1 つでもある関数を変換しない(呼び手を壊れたまま残さない)。関数の中の呼び手は、
//!   その関数も変換されるなら書き換えられる(不動点で決める)。
//! - 既に `(<- x (f a))`・`(! (f a))`・`(run (f a))` の形で呼ばれている defn は Program を返す組み立てなので変換しない(判断の要る物)。
//!
//! 当てない形(報告の「当てなかった物」): dunder の名、`:async`、定義の行か直前の註の行に deff の理由の印(設定の
//! `definitions.deff_reason_marker`)か「defn のまま」がある物、`eval-and-compile` の中、class の method、除いた path(既定は
//! `clients/hy/acp_client`・`docs/design-checks` — 報告にも出さない)。

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use doeff_indexer::hy_index::{
    self,
    reader::{Delim, Form, Node, Prefix, Reader, StrKind},
};
use serde::Serialize;

use super::bare_calls;
use super::facts::{hy_bindings, ByteSpan};
use super::names::{absolute_module, hy_mangle, module_of};
use super::paths::relative_path;
use super::smells::{children, live, live_items, span_of, Hy, Scope};

/// 既定で除く path(repo の根からの前方一致)— ACP の client の写しと、設計の検証の見本。
pub const DEFAULT_EXCLUDES: &[&str] = &["clients/hy/acp_client", "docs/design-checks"];

/// 設定に印が無い時の deff の理由の印(settings.rs の既定と同じ)。
pub const DEFAULT_REASON_MARKER: &str = "defk にできない:";

/// defn のまま残す理由の註の、印のほかの綴り(agora の既存の註)。
const KEEP_MARKERS: &[&str] = &["defn のまま"];

/// 変換の入力。
#[derive(Debug, Clone)]
pub struct FixOptions {
    /// repo の根(呼び手を探す範囲・module の綴りの基準)。
    pub root: PathBuf,
    /// 変換する定義を探す file か dir(絶対の path)。
    pub targets: Vec<PathBuf>,
    /// 除く path(根からの前方一致)。
    pub excludes: Vec<String>,
    /// deff の理由の印。
    pub reason_marker: String,
    /// file を書き換えるか(偽なら計画だけを報告する)。
    pub write: bool,
    /// 呼び手を全部書き換えられる関数だけを変換する(偽なら変換して、書き換えられない呼び手を判断の要る物に出す)。
    pub strict: bool,
}

// --- 報告 ---------------------------------------------------------------------------------

/// file の中の位置(1 始まり)。
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize)]
pub struct Location {
    pub file: String,
    pub line: usize,
    pub column: usize,
}

impl std::fmt::Display for Location {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}:{}:{}", self.file, self.line, self.column)
    }
}

/// defk に直した定義 1 つ。
#[derive(Debug, Clone, Serialize)]
pub struct ConvertedDefinition {
    pub location: Location,
    pub name: String,
    /// 足した契約の辞書(既にあって足さなかったら空)。
    pub contract: String,
}

/// 呼びの書き換えの形。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "kebab-case")]
pub enum CallRewrite {
    /// `(setv x (f a))` / `(val x (f a))` → `(<- x (f a))`。
    Bind { from: String },
    /// `(f a)` → `(! (f a))`。
    Bang,
}

/// 書き換えた呼び 1 つ。
#[derive(Debug, Clone, Serialize)]
pub struct RewrittenCall {
    pub location: Location,
    pub callee: String,
    /// 呼びを含む定義の名(module の最上位なら空)。
    pub container: String,
    pub rewrite: CallRewrite,
}

/// 呼び手を書き換えられない理由。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "kebab-case")]
pub enum CallerBlock {
    /// 内包表記(lfor・gfor・dfor・sfor)の中 — yield を置けない。
    Comprehension { head: String },
    /// 変換しない素の関数(defn・deff・fn・defclass)の中。
    PlainFunction { head: String, name: String },
    /// module の最上位(import の時に走る)。
    ModuleLevel,
    /// 定義の見出し(既定値・契約の辞書)— 定義の時か素の検査として走る。
    Signature,
    /// macro の中(defmacro)。
    Macro,
    /// `->`・`->>`・`as->`・`doto` の段 — 呼びの形が書いた形と違う。
    ThreadingMacro { head: String },
    /// 関数そのものを値として渡している(`sorted` の `:key`・`map` の引数・表の値など)。
    PassedAsValue,
    /// 既に Program として束ねている(`<-` の右辺・`!`・`yield`)— Program を返す組み立て。
    AlreadyBound,
    /// Program を受ける関数(run・Gather …)へ渡している — Program を返す組み立て。
    ProgramArgument { head: String },
    /// Python の file からの参照。
    Python,
    /// Hy と Python の外の file(pyproject の入口など)からの参照。
    OtherFile,
    /// `(import m [f :as g])` の別名 — 別名の呼びを追えない。
    ImportAlias { alias: String },
    /// 除いた path の file の中。
    ExcludedFile,
}

impl std::fmt::Display for CallerBlock {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CallerBlock::Comprehension { head } => write!(f, "内包表記 {} の中で呼んでいる", head),
            CallerBlock::PlainFunction { head, name } => write!(f, "変換しない素の関数({} {})の中で呼んでいる", head, name),
            CallerBlock::ModuleLevel => write!(f, "module の最上位で呼んでいる"),
            CallerBlock::Signature => write!(f, "定義の見出し(既定値・契約)で呼んでいる"),
            CallerBlock::Macro => write!(f, "macro の中で使っている"),
            CallerBlock::ThreadingMacro { head } => write!(f, "{} の段として呼んでいる", head),
            CallerBlock::PassedAsValue => write!(f, "関数そのものを値として渡している"),
            CallerBlock::AlreadyBound => write!(f, "既に <- / ! で束ねている(Program を返す組み立て)"),
            CallerBlock::ProgramArgument { head } => write!(f, "Program を受ける {} へ渡している(Program を返す組み立て)", head),
            CallerBlock::Python => write!(f, "Python の file から参照している"),
            CallerBlock::OtherFile => write!(f, "Hy と Python の外の file から参照している"),
            CallerBlock::ImportAlias { alias } => write!(f, "import の別名 {} で取り込んでいる", alias),
            CallerBlock::ExcludedFile => write!(f, "除いた path の file の中で使っている"),
        }
    }
}

/// 判断の要る物の理由。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "kebab-case")]
pub enum JudgementReason {
    /// 引数に型の注記が無い(defk の :pre に書く型が決まらない)。
    MissingParamType { param: String },
    /// 戻り値の型の注記が無い(:post に書く型が決まらない)。
    MissingReturnType,
    /// `object`・`Any` — defk の契約が拒む広すぎる型。
    BroadType { target: String, written: String },
    /// isinstance に写せない型(`Literal`・文字列の説明など)。
    UnconvertibleType { target: String, written: String },
    /// 飾り(`(defn [装飾] …)`)がある。
    Decorated { decorators: String },
    /// 本体に yield がある(生成器)。
    Generator,
    /// 引数の名が effect の handler の形(effect・eff・k)— defk は拒む。
    HandlerLikeParams { params: Vec<String> },
    /// 検の関数(test- で始まる)— defk ではなく deftest にする。
    TestFunction,
    /// 関数の中の defn(class の method ではない)。
    NestedDefn { outer: String },
    /// 書き換えられない呼び手・参照がある(この所)。
    BlockedCaller { callee: String, why: CallerBlock },
    /// 関数は defk にした — この呼び手・参照は書き換えられないので手で直す。
    CallerLeft { callee: String, why: CallerBlock },
}

impl std::fmt::Display for JudgementReason {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            JudgementReason::MissingParamType { param } => write!(f, "引数 {} に型の注記が無い", param),
            JudgementReason::MissingReturnType => write!(f, "戻り値の型の注記が無い"),
            JudgementReason::BroadType { target, written } => write!(f, "{} の型 {} は defk の契約が拒む広い型", target, written),
            JudgementReason::UnconvertibleType { target, written } => write!(f, "{} の型 {} を isinstance の型に写せない", target, written),
            JudgementReason::Decorated { decorators } => write!(f, "飾り {} がある", decorators),
            JudgementReason::Generator => write!(f, "本体に yield がある(生成器)"),
            JudgementReason::HandlerLikeParams { params } => write!(f, "引数の名 {} が handler の形", params.join("・")),
            JudgementReason::TestFunction => write!(f, "検の関数 — deftest にする"),
            JudgementReason::NestedDefn { outer } => write!(f, "{} の中の defn", outer),
            JudgementReason::BlockedCaller { callee, why } => write!(f, "{} の呼び手: {}", callee, why),
            JudgementReason::CallerLeft { callee, why } => write!(f, "{} は defk にした — この呼び手を手で直す: {}", callee, why),
        }
    }
}

/// 判断の要る物 1 件。
#[derive(Debug, Clone, Serialize)]
pub struct NeedsJudgement {
    pub location: Location,
    /// 変換しなかった定義の名。
    pub definition: String,
    pub reason: JudgementReason,
}

/// 当てなかった理由(直す対象の外)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "kebab-case")]
pub enum SkipReason {
    Dunder,
    Async,
    ReasonComment { comment: String },
    CompileTime,
    Method,
}

impl std::fmt::Display for SkipReason {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SkipReason::Dunder => write!(f, "dunder の名"),
            SkipReason::Async => write!(f, ":async"),
            SkipReason::ReasonComment { comment } => write!(f, "理由の註: {}", comment),
            SkipReason::CompileTime => write!(f, "eval-and-compile の中"),
            SkipReason::Method => write!(f, "class の method"),
        }
    }
}

/// 当てなかった定義 1 つ。
#[derive(Debug, Clone, Serialize)]
pub struct SkippedDefinition {
    pub location: Location,
    pub name: String,
    pub reason: SkipReason,
}

/// 変換の後に残った素の呼び(DOEFF126)1 件。位置は書き換えた後の source の行(ほかの報告の位置は書き換える前の行)。
#[derive(Debug, Clone, Serialize)]
pub struct ResidualBareCall {
    pub location: Location,
    pub definition: String,
    pub callee: String,
}

/// 実行 1 回の報告。
#[derive(Debug, Clone, Default, Serialize)]
pub struct FixReport {
    pub root: String,
    /// file を書き換えたか(偽 = 計画だけ)。
    pub written: bool,
    pub converted: Vec<ConvertedDefinition>,
    pub rewritten_calls: Vec<RewrittenCall>,
    pub needs_judgement: Vec<NeedsJudgement>,
    pub skipped: Vec<SkippedDefinition>,
    pub residual_bare_calls: Vec<ResidualBareCall>,
    pub changed_files: Vec<String>,
}

impl FixReport {
    /// 人が読む表(Markdown の表)。
    pub fn table(&self) -> String {
        let mut out = String::new();
        let binds = self.rewritten_calls.iter().filter(|c| matches!(c.rewrite, CallRewrite::Bind { .. })).count();
        out.push_str(&format!(
            "defn → defk({}): 変換 {} 件・呼びの書き換え {} 件(<- {}・! {})・判断の要る物 {} 件・当てなかった物 {} 件・残った素の呼び {} 件・書き換えた file {} 本\n",
            if self.written { "書き換えた" } else { "計画だけ" },
            self.converted.len(),
            self.rewritten_calls.len(),
            binds,
            self.rewritten_calls.len() - binds,
            self.needs_judgement.len(),
            self.skipped.len(),
            self.residual_bare_calls.len(),
            self.changed_files.len()
        ));
        if !self.converted.is_empty() {
            out.push_str("\n## 変換した定義\n\n| 所(書き換える前の行) | 名 | 足した契約 |\n|---|---|---|\n");
            for c in &self.converted {
                out.push_str(&format!("| {} | {} | `{}` |\n", c.location, c.name, c.contract));
            }
        }
        if !self.rewritten_calls.is_empty() {
            out.push_str("\n## 書き換えた呼び\n\n| 所(書き換える前の行) | 呼び先 | 呼びを含む定義 | 形 |\n|---|---|---|---|\n");
            for c in &self.rewritten_calls {
                let form = match &c.rewrite {
                    CallRewrite::Bind { from } => format!("{} → <-", from),
                    CallRewrite::Bang => "!".to_string(),
                };
                out.push_str(&format!("| {} | {} | {} | {} |\n", c.location, c.callee, c.container, form));
            }
        }
        if !self.needs_judgement.is_empty() {
            out.push_str("\n## 判断の要る物\n\n| 所(書き換える前の行) | 定義 | 理由 |\n|---|---|---|\n");
            for j in &self.needs_judgement {
                out.push_str(&format!("| {} | {} | {} |\n", j.location, j.definition, j.reason));
            }
        }
        if !self.skipped.is_empty() {
            out.push_str("\n## 当てなかった物\n\n| 所(書き換える前の行) | 名 | 理由 |\n|---|---|---|\n");
            for s in &self.skipped {
                out.push_str(&format!("| {} | {} | {} |\n", s.location, s.name, s.reason));
            }
        }
        if !self.residual_bare_calls.is_empty() {
            out.push_str("\n## 残った素の呼び(DOEFF126)\n\n| 所(書き換えた後の行) | 定義 | 呼び先 |\n|---|---|---|\n");
            for r in &self.residual_bare_calls {
                out.push_str(&format!("| {} | {} | {} |\n", r.location, r.definition, r.callee));
            }
        }
        out
    }
}

// --- 読んだ file ------------------------------------------------------------------------------

/// 読んだ Hy の file 1 本。
struct HyFile {
    rel: String,
    path: PathBuf,
    module: String,
    source: String,
    forms: Vec<Form>,
    bindings: BTreeMap<String, String>,
    lines: Vec<usize>,
    /// 変換する定義を探す file か。
    target: bool,
    /// 除いた path の file か。
    excluded: bool,
}

impl HyFile {
    fn read(root: &Path, path: &Path, source: String, targets: &[PathBuf], excludes: &[String]) -> Option<HyFile> {
        let rel = relative_path(root, path)?;
        let module = module_of(&rel);
        let excluded = excludes.iter().any(|e| rel == *e || rel.starts_with(&format!("{}/", e.trim_end_matches('/'))));
        let target = !excluded && targets.iter().any(|t| path.starts_with(t));
        let forms = Reader::new(&source, 0, source.len()).read_all();
        let bindings = hy_bindings(&source, &module);
        let lines = line_starts(&source);
        Some(HyFile { rel, path: path.to_path_buf(), module, source, forms, bindings, lines, target, excluded })
    }

    fn location(&self, offset: usize) -> Location {
        location_in(&self.rel, &self.lines, &self.source, offset)
    }
}

fn line_starts(source: &str) -> Vec<usize> {
    let mut starts = vec![0];
    starts.extend(source.match_indices('\n').map(|(at, _)| at + 1));
    starts
}

fn location_in(rel: &str, lines: &[usize], source: &str, offset: usize) -> Location {
    let line = lines.partition_point(|&start| start <= offset).max(1);
    let start = lines[line - 1];
    let column = source.get(start..offset).map(|s| s.chars().count()).unwrap_or(0) + 1;
    Location { file: rel.to_string(), line, column }
}

// --- 定義の読み ------------------------------------------------------------------------------

/// byte の範囲の置き換え 1 つ。
#[derive(Debug, Clone, PartialEq, Eq)]
struct Edit {
    start: usize,
    end: usize,
    text: String,
}

/// 変換の候補の定義 1 つ。
struct Candidate {
    file: usize,
    name: String,
    qualified: String,
    start: usize,
    edits: Vec<Edit>,
    contract: String,
}

/// 型の注記を契約の型へ写した物。
struct ContractType {
    text: String,
    /// 型の引数を落とした(注記を残す)。
    reduced: bool,
}

/// 型を写せない理由。
enum TypeProblem {
    Broad(String),
    Unconvertible(String),
}

/// 広すぎて defk の契約が拒む型。
const BROAD_TYPES: &[&str] = &["object", "Any", "typing.Any"];

/// 型の注記の form を isinstance に渡せる契約の型の綴りにする。
fn contract_type(hy: &Hy<'_>, form: &Form) -> Result<ContractType, TypeProblem> {
    let written = hy.text(form).to_string();
    match &form.node {
        Node::Symbol if BROAD_TYPES.contains(&written.as_str()) => Err(TypeProblem::Broad(written)),
        Node::Symbol if written == "float" => Ok(ContractType { text: "(| float int)".to_string(), reduced: false }),
        Node::Symbol => Ok(ContractType { text: written, reduced: false }),
        Node::Str { kind: StrKind::Plain, body } => {
            let inner = hy.src.get(body.start..body.end).unwrap_or("");
            let identifier = !inner.is_empty()
                && inner.split('.').all(|part| part.chars().next().is_some_and(|c| c.is_alphabetic() || c == '_') && part.chars().all(|c| c.is_alphanumeric() || c == '_' || c == '-'));
            if identifier {
                Ok(ContractType { text: inner.to_string(), reduced: false })
            } else {
                Err(TypeProblem::Unconvertible(written))
            }
        }
        Node::Seq { delim: Delim::Tuple, .. } => Ok(ContractType { text: written, reduced: false }),
        Node::Seq { delim: Delim::Paren, .. } => {
            let items = live(form).unwrap_or_default();
            match items.first().and_then(|h| hy.symbol(h)) {
                Some("|") => {
                    let parts: Vec<ContractType> = items[1..].iter().map(|p| contract_type(hy, p)).collect::<Result<_, _>>()?;
                    let reduced = parts.iter().any(|p| p.reduced);
                    Ok(ContractType { text: format!("(| {})", parts.iter().map(|p| p.text.as_str()).collect::<Vec<_>>().join(" ")), reduced })
                }
                Some("get" | "of") => {
                    let origin = items.get(1).ok_or_else(|| TypeProblem::Unconvertible(written.clone()))?;
                    let args: Vec<&Form> = match items.get(2) {
                        Some(tuple) if matches!(tuple.node, Node::Seq { delim: Delim::Tuple, .. }) => children(tuple),
                        Some(one) => vec![*one],
                        None => Vec::new(),
                    };
                    let origin_text = hy.text(origin);
                    match origin_text.rsplit('.').next().unwrap_or(origin_text) {
                        "Optional" => {
                            let inner = args.first().ok_or_else(|| TypeProblem::Unconvertible(written.clone()))?;
                            let inner = contract_type(hy, inner)?;
                            Ok(ContractType { text: format!("(| {} None)", inner.text), reduced: true })
                        }
                        "Union" => {
                            let parts: Vec<ContractType> = args.iter().map(|p| contract_type(hy, p)).collect::<Result<_, _>>()?;
                            Ok(ContractType { text: format!("(| {})", parts.iter().map(|p| p.text.as_str()).collect::<Vec<_>>().join(" ")), reduced: true })
                        }
                        "Annotated" => {
                            let inner = args.first().ok_or_else(|| TypeProblem::Unconvertible(written.clone()))?;
                            let inner = contract_type(hy, inner)?;
                            Ok(ContractType { text: inner.text, reduced: true })
                        }
                        "Literal" | "ClassVar" | "Final" => Err(TypeProblem::Unconvertible(written)),
                        "Type" => Ok(ContractType { text: "type".to_string(), reduced: true }),
                        _ => {
                            let inner = contract_type(hy, origin)?;
                            Ok(ContractType { text: inner.text, reduced: true })
                        }
                    }
                }
                _ => Ok(ContractType { text: written, reduced: false }),
            }
        }
        _ => Err(TypeProblem::Unconvertible(written)),
    }
}

/// 契約の型が None を受けるか(既定値 None の引数に `(| T None)` を足すか決めるため)。
fn accepts_none(text: &str) -> bool {
    text == "None" || text.split(|c: char| c.is_whitespace() || c == '(' || c == ')').any(|w| w == "None")
}

/// 定義の form の部品。
struct DefinitionParts<'f> {
    decorators: Option<&'f Form>,
    is_async: bool,
    name_form: &'f Form,
    params: &'f Form,
    body: Vec<&'f Form>,
}

/// `(defn [装飾]? :async? #^ R? 名 [引数] 本体…)` を部品に分ける(形が違えば None)。
fn definition_parts<'f>(hy: &Hy<'_>, items: &[&'f Form]) -> Option<DefinitionParts<'f>> {
    let mut at = 1;
    let mut decorators = None;
    let mut is_async = hy.head_text(items[0]) == Some("defn/a");
    while at < items.len() {
        let item = items[at];
        match &item.node {
            Node::Keyword if hy.text(item) == ":async" => {
                is_async = true;
                at += 1;
            }
            Node::Keyword if hy.text(item) == ":tp" => at += 2,
            Node::Seq { delim: Delim::Bracket, .. }
                if decorators.is_none() && items.get(at + 1).is_some_and(|n| matches!(n.node, Node::Symbol | Node::Annotated { .. } | Node::Keyword)) =>
            {
                decorators = Some(item);
                at += 1;
            }
            _ => break,
        }
    }
    let name_form = *items.get(at)?;
    let params = *items.get(at + 1)?;
    params.bracket_items()?;
    Some(DefinitionParts { decorators, is_async, name_form, params, body: items[at + 2..].to_vec() })
}

/// 定義の名(注記を外した綴り)と戻り値の注記。
fn name_and_return<'f>(hy: &Hy<'_>, name_form: &'f Form) -> (String, Option<&'f Form>) {
    match &name_form.node {
        Node::Annotated { annotation, target: Some(target) } => (hy.text(target).to_string(), annotation.as_deref()),
        _ => (hy.text(name_form).to_string(), None),
    }
}

/// 候補を調べた結果。
enum Examined {
    Candidate(Candidate),
    Skipped(SkipReason),
    Judgement(Vec<JudgementReason>),
}

/// 1 つの最上位の defn を調べる。
fn examine(file: &HyFile, index: usize, form: &Form, items: &[&Form], compile_time: bool, marker: &str) -> Option<(String, Examined)> {
    let hy = Hy { src: &file.source };
    let parts = definition_parts(&hy, items)?;
    let (name, return_form) = name_and_return(&hy, parts.name_form);
    if name.starts_with("__") && name.ends_with("__") {
        return Some((name, Examined::Skipped(SkipReason::Dunder)));
    }
    if parts.is_async {
        return Some((name, Examined::Skipped(SkipReason::Async)));
    }
    if compile_time {
        return Some((name, Examined::Skipped(SkipReason::CompileTime)));
    }
    if let Some(comment) = reason_comment(&file.source, form.span.start, parts.params.span.end, marker) {
        return Some((name, Examined::Skipped(SkipReason::ReasonComment { comment })));
    }
    let mut problems = Vec::new();
    if let Some(decorators) = parts.decorators.filter(|d| d.bracket_items().is_some_and(|i| !live_items(i).is_empty())) {
        problems.push(JudgementReason::Decorated { decorators: hy.text(decorators).to_string() });
    }
    if name.starts_with("test-") || name.starts_with("test_") {
        problems.push(JudgementReason::TestFunction);
    }
    if parts.body.iter().any(|b| has_yield(&hy, b)) {
        problems.push(JudgementReason::Generator);
    }
    let mut edits = vec![Edit { start: items[0].span.start, end: items[0].span.end, text: "defk".to_string() }];
    if let (Node::Annotated { target: Some(target), .. }, true) = (&parts.name_form.node, return_form.is_some()) {
        edits.push(Edit { start: parts.name_form.span.start, end: parts.name_form.span.end, text: hy.text(target).to_string() });
    }
    let mut pre = Vec::new();
    let mut handler_like = Vec::new();
    for param in parts.params.bracket_items().map(live_items).unwrap_or_default() {
        let (annotation, target) = match &param.node {
            Node::Annotated { annotation, target: Some(target) } => (annotation.as_deref(), target.as_ref()),
            _ => (None, param),
        };
        let (param_name, default) = match &target.node {
            Node::Symbol if matches!(hy.text(target), "*" | "/") => continue,
            Node::Symbol => (hy.text(target).to_string(), None),
            Node::Seq { delim: Delim::Bracket, .. } => {
                let pair = target.bracket_items().map(live_items).unwrap_or_default();
                match pair.first() {
                    Some(first) => (hy.text(first).to_string(), pair.get(1).map(|d| hy.text(d))),
                    None => continue,
                }
            }
            // `#* args`・`#** kw` — 契約は要らない(注記はそのまま)。
            Node::Prefixed { prefix: Prefix::Unpack | Prefix::UnpackMapping, .. } => continue,
            _ => (hy.text(target).to_string(), None),
        };
        if matches!(param_name.as_str(), "effect" | "eff" | "k") {
            handler_like.push(param_name.clone());
        }
        let Some(annotation) = annotation else {
            problems.push(JudgementReason::MissingParamType { param: param_name });
            continue;
        };
        match contract_type(&hy, annotation) {
            Ok(ty) => {
                let text = if default == Some("None") && !accepts_none(&ty.text) { format!("(| {} None)", ty.text) } else { ty.text };
                pre.push(format!("(: {} {})", param_name, text));
                if !ty.reduced {
                    edits.push(Edit { start: param.span.start, end: param.span.end, text: hy.text(target).to_string() });
                }
            }
            Err(TypeProblem::Broad(written)) => problems.push(JudgementReason::BroadType { target: param_name, written }),
            Err(TypeProblem::Unconvertible(written)) => problems.push(JudgementReason::UnconvertibleType { target: param_name, written }),
        }
    }
    if !handler_like.is_empty() {
        problems.push(JudgementReason::HandlerLikeParams { params: handler_like });
    }
    let post = match return_form.map(|r| contract_type(&hy, r)) {
        None => {
            problems.push(JudgementReason::MissingReturnType);
            None
        }
        Some(Ok(ty)) => Some(format!("(: % {})", ty.text)),
        Some(Err(TypeProblem::Broad(written))) => {
            problems.push(JudgementReason::BroadType { target: "戻り値".to_string(), written });
            None
        }
        Some(Err(TypeProblem::Unconvertible(written))) => {
            problems.push(JudgementReason::UnconvertibleType { target: "戻り値".to_string(), written });
            None
        }
    };
    if !problems.is_empty() {
        return Some((name, Examined::Judgement(problems)));
    }
    let pre_text = format!(":pre [{}]", pre.join(" "));
    let post_text = format!(":post [{}]", post.unwrap_or_default());
    let existing = existing_contract(&hy, &parts.body);
    let contract = match existing {
        Some(dict) => {
            let keys = contract_keys(&hy, dict);
            let mut missing = Vec::new();
            if !keys.contains(":pre") {
                missing.push(pre_text);
            }
            if !keys.contains(":post") {
                missing.push(post_text);
            }
            if !missing.is_empty() {
                let text = missing.join(" ");
                edits.push(Edit { start: dict.span.start + 1, end: dict.span.start + 1, text: format!("{} ", text) });
                text
            } else {
                String::new()
            }
        }
        None => {
            let text = format!("{{{} {}}}", pre_text, post_text);
            edits.push(contract_insertion(&file.source, form.span.start, parts.params.span.end, &text));
            text
        }
    };
    let qualified = format!("{}.{}", file.module, hy_mangle(&name));
    Some((name.clone(), Examined::Candidate(Candidate { file: index, name, qualified, start: form.span.start, edits, contract })))
}

/// 本体の頭の契約の辞書(docstring の後 — 鍵が :pre・:post・:tags・:effects のどれかで始まる物)。
fn existing_contract<'f>(hy: &Hy<'_>, body: &[&'f Form]) -> Option<&'f Form> {
    let mut rest = body.iter();
    let mut first = rest.next()?;
    if matches!(first.node, Node::Str { .. }) {
        first = rest.next()?;
    }
    let keys = contract_keys(hy, first);
    (first.is_brace() && keys.iter().next().is_some_and(|k| matches!(k.as_str(), ":pre" | ":post" | ":tags" | ":effects"))).then_some(*first)
}

/// 辞書の鍵(keyword だけ・偶数の位置)。
fn contract_keys(hy: &Hy<'_>, dict: &Form) -> BTreeSet<String> {
    match &dict.node {
        Node::Seq { delim: Delim::Brace, items } => live_items(items).iter().step_by(2).filter(|k| matches!(k.node, Node::Keyword)).map(|k| hy.text(k).to_string()).collect(),
        _ => BTreeSet::new(),
    }
}

/// 契約の辞書を置く所 — 引数の `]` の後が行の終わり(か註)なら次の行に字下げして、同じ行に本体が続くなら `]` の直後に置く。
fn contract_insertion(source: &str, form_start: usize, params_end: usize, contract: &str) -> Edit {
    let line_end = source[params_end..].find('\n').map(|at| params_end + at).unwrap_or(source.len());
    let rest = source[params_end..line_end].trim();
    let line_start = source[..form_start].rfind('\n').map(|at| at + 1).unwrap_or(0);
    let column = source[line_start..form_start].chars().count();
    if rest.is_empty() || rest.starts_with(';') {
        Edit { start: line_end, end: line_end, text: format!("\n{}{}", " ".repeat(column + 2), contract) }
    } else {
        Edit { start: params_end, end: params_end, text: format!(" {}", contract) }
    }
}

/// 定義の行(頭から引数の終わりまで)か直前の註だけの行に、理由の註があればその註の文。
fn reason_comment(source: &str, form_start: usize, params_end: usize, marker: &str) -> Option<String> {
    let line_start = source[..form_start].rfind('\n').map(|at| at + 1).unwrap_or(0);
    let line_end = source[params_end..].find('\n').map(|at| params_end + at).unwrap_or(source.len());
    let own = &source[line_start..line_end];
    let previous = source[..line_start.saturating_sub(1)].rsplit('\n').next().filter(|l| l.trim_start().starts_with(';')).unwrap_or("");
    [own, previous].into_iter().filter_map(|text| {
        let comment = text.find(';').map(|at| text[at..].trim_start_matches(';').trim())?;
        (comment.contains(marker) || KEEP_MARKERS.iter().any(|m| comment.contains(m))).then(|| comment.to_string())
    }).next()
}

/// form の中に yield があるか(入れ子の fn の中は数えない)。
fn has_yield(hy: &Hy<'_>, form: &Form) -> bool {
    match hy.head(form) {
        Some("yield" | "yield-from") => true,
        Some("fn" | "fn/a" | "defn" | "defn/a" | "defk" | "deff" | "fnk" | "defclass") => false,
        _ => children(form).into_iter().any(|c| has_yield(hy, c)),
    }
}

// --- 呼び手 ------------------------------------------------------------------------------

/// 呼びのある所の囲み(書き換えられるか)。
#[derive(Debug, Clone)]
enum Frame {
    /// Program の本体(defk・deftest・defhandler・fnk・defp・do! …)。
    Program,
    /// 変換の候補の本体(候補が変換されれば Program)。
    Candidate { candidate: usize, name: String },
    /// 書き換えられない所。
    Blocked(CallerBlock),
}

/// 呼びの形。
#[derive(Debug, Clone)]
enum Shape {
    /// 呼び — Some なら名 1 つへの束ね(setv / val の頭の範囲)。
    Call { bind: Option<ByteSpan> },
    /// 既に <- / ! / yield で束ねている。
    Yielded,
    /// Program を受ける関数の引数。
    ProgramArgument { head: String },
    /// `->` の段。
    Threaded { head: String },
    /// 呼びの頭ではない参照。
    Reference,
    /// 読めない所からの参照(Python・別名・ほかの file)。
    Foreign(CallerBlock),
}

/// 候補の関数を使う所 1 つ。
struct Site {
    /// Hy の file(Python などの参照は None)。
    file: Option<usize>,
    location: Location,
    span: ByteSpan,
    callee: usize,
    frame: Frame,
    container: String,
    shape: Shape,
}

/// 呼びの子の位置の種類。
#[derive(Debug, Clone)]
enum Slot {
    Value,
    Yielded,
    Bind(ByteSpan),
    ProgramArgument(String),
    Threaded(String),
}

/// Program を受けて走らせる・組み合わせる関数(候補がここへ渡されていれば Program を返す組み立て)。
const PROGRAM_TAKERS: &[&str] = &[
    "run", "run-on", "run_on", "async-run", "async_run", "Gather", "Spawn", "Race", "Safe", "Try", "Local", "Listen", "Intercept", "WithHandler",
    "with-handler", "with_handler", "with-handlers", "with_handlers", "maybe", "result", "on-raise", "absent-as",
];

const COMPREHENSIONS: &[&str] = &["lfor", "gfor", "dfor", "sfor"];
const THREADING: &[&str] = &["->", "->>", "as->", "doto"];
const PROGRAM_DEFINITIONS: &[&str] = &["defk", "deftest", "defp", "defpp", "defhandler", "defmcp-tool"];
const PROGRAM_BLOCKS: &[&str] = &["do!", "for/do", "traverse", "handle", "fnk"];
const PLAIN_DEFINITIONS: &[&str] = &["deff", "defclass"];
const PLAIN_FUNCTIONS: &[&str] = &["fn", "fn/a"];
const MACROS: &[&str] = &["defmacro", "defmacro/g!", "defreader"];

/// 呼び手を探して 1 つの file を下る道具。
struct SiteWalker<'a> {
    hy: Hy<'a>,
    file: &'a HyFile,
    index: usize,
    scope: Scope<'a>,
    candidates: &'a BTreeMap<String, usize>,
    /// この file の最上位の候補(form の始まり → 候補の番号)。
    tops: &'a BTreeMap<usize, usize>,
}

impl SiteWalker<'_> {
    fn resolve(&self, spelled: &str) -> Option<usize> {
        if spelled.starts_with(':') || spelled.starts_with('.') || !spelled.chars().any(|c| c.is_alphabetic()) {
            return None;
        }
        self.candidates.get(&self.scope.qualify(spelled)).copied()
    }

    fn push(&self, span: ByteSpan, callee: usize, frame: &Frame, container: &str, shape: Shape, out: &mut Vec<Site>) {
        let frame = if self.file.excluded { Frame::Blocked(CallerBlock::ExcludedFile) } else { frame.clone() };
        out.push(Site { file: Some(self.index), location: self.file.location(span.start), span, callee, frame, container: container.to_string(), shape });
    }

    fn walk(&self, form: &Form, frame: &Frame, container: &str, slot: Slot, top: bool, out: &mut Vec<Site>) {
        match &form.node {
            Node::Seq { delim: Delim::Paren, .. } => self.list(form, frame, container, slot, top, out),
            Node::Symbol => {
                if let Some(callee) = self.resolve(self.hy.text(form)) {
                    self.push(span_of(form), callee, frame, container, Shape::Reference, out);
                }
            }
            Node::Prefixed { prefix: Prefix::Quote | Prefix::Quasiquote, .. } => {}
            Node::Annotated { target: Some(target), .. } => self.walk(target, frame, container, Slot::Value, false, out),
            _ => {
                for child in children(form) {
                    self.walk(child, frame, container, Slot::Value, false, out);
                }
            }
        }
    }

    fn list(&self, form: &Form, frame: &Frame, container: &str, slot: Slot, top: bool, out: &mut Vec<Site>) {
        let items = live(form).unwrap_or_default();
        let Some(first) = items.first() else { return };
        let head = self.hy.head_text(first);
        match head {
            Some("import" | "require") => return self.import_aliases(&items, frame, container, out),
            Some("do" | "eval-and-compile" | "eval-when-compile") if top => {
                for child in &items[1..] {
                    self.walk(child, frame, container, Slot::Value, true, out);
                }
                return;
            }
            Some(h @ ("defn" | "defn/a")) => {
                let (name, body_frame) = match (top, self.tops.get(&form.span.start)) {
                    (true, Some(&candidate)) => {
                        let name = self.definition_name(&items);
                        (name.clone(), Frame::Candidate { candidate, name })
                    }
                    _ => {
                        let name = self.definition_name(&items);
                        (name.clone(), Frame::Blocked(CallerBlock::PlainFunction { head: h.to_string(), name }))
                    }
                };
                return self.definition(&items, frame, container, &name, body_frame, out);
            }
            Some(h) if PROGRAM_DEFINITIONS.contains(&h) => {
                let name = self.definition_name(&items);
                return self.definition(&items, frame, container, &name, Frame::Program, out);
            }
            Some(h) if PLAIN_DEFINITIONS.contains(&h) => {
                let name = self.definition_name(&items);
                let blocked = Frame::Blocked(CallerBlock::PlainFunction { head: h.to_string(), name: name.clone() });
                return self.definition(&items, frame, container, &name, blocked, out);
            }
            Some(h) if MACROS.contains(&h) => {
                for child in &items[1..] {
                    self.walk(child, &Frame::Blocked(CallerBlock::Macro), container, Slot::Value, false, out);
                }
                return;
            }
            Some(h) if PLAIN_FUNCTIONS.contains(&h) => {
                let blocked = Frame::Blocked(CallerBlock::PlainFunction { head: h.to_string(), name: container.to_string() });
                for child in &items[1..] {
                    self.walk(child, &blocked, container, Slot::Value, false, out);
                }
                return;
            }
            Some(h) if PROGRAM_BLOCKS.contains(&h) => {
                for child in &items[1..] {
                    self.walk(child, &Frame::Program, container, Slot::Value, false, out);
                }
                return;
            }
            Some(h) if COMPREHENSIONS.contains(&h) => {
                let blocked = Frame::Blocked(CallerBlock::Comprehension { head: h.to_string() });
                for child in &items[1..] {
                    self.walk(child, &blocked, container, Slot::Value, false, out);
                }
                return;
            }
            _ => {}
        }
        // 呼びの頭が候補なら、この呼びを積む。
        match (&first.node, head) {
            (Node::Symbol, Some(spelled)) => {
                if let Some(callee) = self.resolve(spelled) {
                    let shape = match &slot {
                        Slot::Value => Shape::Call { bind: None },
                        Slot::Bind(head) => Shape::Call { bind: Some(*head) },
                        Slot::Yielded => Shape::Yielded,
                        Slot::ProgramArgument(head) => Shape::ProgramArgument { head: head.clone() },
                        Slot::Threaded(head) => Shape::Threaded { head: head.clone() },
                    };
                    self.push(span_of(form), callee, frame, container, shape, out);
                }
            }
            (Node::Keyword, _) => {}
            _ => self.walk(first, frame, container, Slot::Value, false, out),
        }
        let last = items.len() - 1;
        for (index, child) in items.iter().enumerate().skip(1) {
            let child_slot = match head {
                Some("<-") if index == last => Slot::Yielded,
                Some("!" | "yield" | "yield-from" | "await") => Slot::Yielded,
                Some("setv" | "val") if items.len() == 3 && index == 2 && self.plain_name(items[1]) => Slot::Bind(span_of(first)),
                Some(h) if PROGRAM_TAKERS.contains(&h) => Slot::ProgramArgument(h.to_string()),
                Some(h) if THREADING.contains(&h) && index >= 2 => Slot::Threaded(h.to_string()),
                _ => Slot::Value,
            };
            self.walk(child, frame, container, child_slot, false, out);
        }
    }

    /// `(<- x …)` へ直せる名(点の無い記号)か。
    fn plain_name(&self, form: &Form) -> bool {
        self.hy.symbol(form).is_some_and(|s| !s.contains('.') && !s.starts_with(':'))
    }

    fn definition_name(&self, items: &[&Form]) -> String {
        match definition_parts(&self.hy, items) {
            Some(parts) => name_and_return(&self.hy, parts.name_form).0,
            None => items.get(1).map(|n| name_and_return(&self.hy, n).0).unwrap_or_default(),
        }
    }

    /// 定義の form — 見出し(飾り・引数の既定値・契約の辞書)は外の囲みで、本体は body_frame で下る。
    fn definition(&self, items: &[&Form], outer: &Frame, outer_container: &str, name: &str, body_frame: Frame, out: &mut Vec<Site>) {
        let head = self.hy.head_text(items[0]).unwrap_or("");
        let (header, body): (Vec<&Form>, Vec<&Form>) = match head {
            "defn" | "defn/a" | "defk" | "deff" | "defmacro" => match definition_parts(&self.hy, items) {
                Some(parts) => (parts.decorators.into_iter().chain([parts.params]).collect(), parts.body),
                None => (Vec::new(), items[1..].to_vec()),
            },
            // 名の後が全部本体(deftest・defhandler・defp・defclass …)。
            _ => (Vec::new(), items.get(2..).map(|r| r.to_vec()).unwrap_or_default()),
        };
        let header_frame = match outer {
            Frame::Blocked(_) => outer.clone(),
            _ => Frame::Blocked(CallerBlock::Signature),
        };
        for form in header {
            self.walk(form, &header_frame, outer_container, Slot::Value, false, out);
        }
        let contract = existing_contract(&self.hy, &body).map(|c| c.span.start);
        for form in body {
            let frame = if Some(form.span.start) == contract { &header_frame } else { &body_frame };
            self.walk(form, frame, name, Slot::Value, false, out);
        }
    }

    /// `(import m [f :as g])` の別名で候補を取り込む所。
    fn import_aliases(&self, items: &[&Form], frame: &Frame, container: &str, out: &mut Vec<Site>) {
        if self.hy.head_text(items[0]) != Some("import") {
            return;
        }
        let mut index = 1;
        while index < items.len() {
            let module = absolute_module(&self.file.module, &hy_mangle(self.hy.text(items[index])));
            match items.get(index + 1).and_then(|n| n.bracket_items()) {
                Some(names) => {
                    let names = live_items(names);
                    for at in 0..names.len() {
                        let is_alias = names.get(at + 1).is_some_and(|k| self.hy.text(k) == ":as");
                        let qualified = format!("{}.{}", module, hy_mangle(self.hy.text(names[at])));
                        if let (true, Some(&callee)) = (is_alias, self.candidates.get(&qualified)) {
                            let alias = names.get(at + 2).map(|a| self.hy.text(a).to_string()).unwrap_or_default();
                            self.push(span_of(names[at]), callee, frame, container, Shape::Foreign(CallerBlock::ImportAlias { alias }), out);
                        }
                    }
                    index += 2;
                }
                None => index += 1,
            }
        }
    }
}

/// Python と toml の file からの参照(名が綴りとして現れ、module の綴りも現れる file)。
fn foreign_sites(root: &Path, candidates: &[Candidate], files: &[HyFile]) -> Vec<Site> {
    let mut by_name: BTreeMap<String, Vec<usize>> = BTreeMap::new();
    for (index, candidate) in candidates.iter().enumerate() {
        by_name.entry(hy_mangle(&candidate.name)).or_default().push(index);
    }
    let skipped = [".venv", "node_modules", "target", ".git", "__pycache__"];
    let walker = walkdir::WalkDir::new(root).follow_links(false).into_iter().filter_entry(|e| e.depth() == 0 || !e.file_type().is_dir() || !skipped.contains(&e.file_name().to_string_lossy().as_ref()));
    let mut out = Vec::new();
    for entry in walker.filter_map(Result::ok).filter(|e| e.file_type().is_file()) {
        let path = entry.path();
        let block = match path.extension().and_then(|e| e.to_str()) {
            Some("py" | "pyi") => CallerBlock::Python,
            Some("toml") => CallerBlock::OtherFile,
            _ => continue,
        };
        let Ok(source) = std::fs::read_to_string(path) else { continue };
        let Some(rel) = relative_path(root, path) else { continue };
        let lines = line_starts(&source);
        let words = identifier_offsets(&source);
        for (word, offsets) in &words {
            let Some(indexes) = by_name.get(*word) else { continue };
            for &index in indexes {
                let candidate = &candidates[index];
                let module = &files[candidate.file].module;
                let last = module.rsplit('.').next().unwrap_or(module);
                if source.contains(module.as_str()) || source.contains(last) {
                    let offset = offsets[0];
                    out.push(Site {
                        file: None,
                        location: location_in(&rel, &lines, &source, offset),
                        span: ByteSpan { start: offset, end: offset + word.len() },
                        callee: index,
                        frame: Frame::Blocked(block.clone()),
                        container: String::new(),
                        shape: Shape::Foreign(block.clone()),
                    });
                }
            }
        }
    }
    out
}

/// source の識別子の綴り → 現れた byte の位置。
fn identifier_offsets(source: &str) -> BTreeMap<&str, Vec<usize>> {
    let mut out: BTreeMap<&str, Vec<usize>> = BTreeMap::new();
    let bytes = source.as_bytes();
    let mut at = 0;
    while at < bytes.len() {
        let c = bytes[at];
        if c.is_ascii_alphabetic() || c == b'_' {
            let start = at;
            while at < bytes.len() && (bytes[at].is_ascii_alphanumeric() || bytes[at] == b'_') {
                at += 1;
            }
            out.entry(&source[start..at]).or_default().push(start);
        } else {
            at += 1;
        }
    }
    out
}

/// 使う所が関数の変換を止めるか — 厳格なら書き換えられない所の全部、そうでなければ Program を返す組み立ての印(既に束ねている・
/// Program を受ける関数へ渡している)だけ(変換すると呼び手の意味が変わる)。
fn blocks(site: &Site, alive: &BTreeSet<usize>, strict: bool) -> bool {
    match verdict(site, alive) {
        Ok(_) => false,
        Err(CallerBlock::AlreadyBound | CallerBlock::ProgramArgument { .. }) => true,
        Err(_) => strict,
    }
}

/// 使う所 1 つの判定 — 書き換えの形か、書き換えられない理由。
fn verdict(site: &Site, alive: &BTreeSet<usize>) -> Result<Option<ByteSpan>, CallerBlock> {
    match &site.shape {
        Shape::Reference => Err(match &site.frame {
            Frame::Blocked(CallerBlock::ExcludedFile) => CallerBlock::ExcludedFile,
            _ => CallerBlock::PassedAsValue,
        }),
        Shape::Foreign(block) => Err(block.clone()),
        Shape::Yielded => Err(CallerBlock::AlreadyBound),
        Shape::ProgramArgument { head } => Err(CallerBlock::ProgramArgument { head: head.clone() }),
        Shape::Threaded { head } => Err(CallerBlock::ThreadingMacro { head: head.clone() }),
        Shape::Call { bind } => match &site.frame {
            Frame::Program => Ok(*bind),
            Frame::Candidate { candidate, .. } if alive.contains(candidate) => Ok(*bind),
            Frame::Candidate { name, .. } => Err(CallerBlock::PlainFunction { head: "defn".to_string(), name: name.clone() }),
            Frame::Blocked(block) => Err(block.clone()),
        },
    }
}

// --- 実行 ------------------------------------------------------------------------------------

/// 変換を 1 回走らせる(options.write が偽なら file を書かずに計画だけを返す)。
pub fn run(options: &FixOptions) -> Result<FixReport, String> {
    let root = &options.root;
    let mut files: Vec<HyFile> = Vec::new();
    for path in super::hy_files::collect(root) {
        let source = std::fs::read_to_string(&path).map_err(|e| format!("{} を読めない: {}", path.display(), e))?;
        if let Some(file) = HyFile::read(root, &path, source, &options.targets, &options.excludes) {
            files.push(file);
        }
    }
    let mut report = FixReport { root: root.display().to_string(), written: options.write, ..FixReport::default() };

    // 1. 候補の定義を集める。
    let mut candidates: Vec<Candidate> = Vec::new();
    for (index, file) in files.iter().enumerate() {
        if !file.target {
            continue;
        }
        let hy = Hy { src: &file.source };
        let mut pending: Vec<(&Form, bool)> = file.forms.iter().map(|f| (f, false)).collect();
        pending.reverse();
        while let Some((form, compile_time)) = pending.pop() {
            let Some(items) = live(form) else { continue };
            match items.first().and_then(|h| hy.head_text(h)) {
                Some("do") => pending.extend(items[1..].iter().rev().map(|f| (*f, compile_time))),
                Some("eval-and-compile" | "eval-when-compile") => pending.extend(items[1..].iter().rev().map(|f| (*f, true))),
                Some("defn" | "defn/a") => match examine(file, index, form, &items, compile_time, &options.reason_marker) {
                    Some((_, Examined::Candidate(candidate))) => candidates.push(candidate),
                    Some((name, Examined::Skipped(reason))) => report.skipped.push(SkippedDefinition { location: file.location(form.span.start), name, reason }),
                    Some((name, Examined::Judgement(reasons))) => {
                        for reason in reasons {
                            report.needs_judgement.push(NeedsJudgement { location: file.location(form.span.start), definition: name.clone(), reason });
                        }
                    }
                    None => {}
                },
                _ => {}
            }
        }
        nested_definitions(&hy, file, &mut report);
    }

    // 2. 候補を使う所を repo の全部の Hy の file と Python・toml から集める。
    let by_qualified: BTreeMap<String, usize> = candidates.iter().enumerate().map(|(i, c)| (c.qualified.clone(), i)).collect();
    let mut sites: Vec<Site> = Vec::new();
    for (index, file) in files.iter().enumerate() {
        if !file.source.contains('(') {
            continue;
        }
        let tops: BTreeMap<usize, usize> = candidates.iter().enumerate().filter(|(_, c)| c.file == index).map(|(i, c)| (c.start, i)).collect();
        let walker = SiteWalker {
            hy: Hy { src: &file.source },
            file,
            index,
            scope: Scope { module: &file.module, bindings: &file.bindings },
            candidates: &by_qualified,
            tops: &tops,
        };
        for form in &file.forms {
            walker.walk(form, &Frame::Blocked(CallerBlock::ModuleLevel), "", Slot::Value, true, &mut sites);
        }
    }
    if !candidates.is_empty() {
        sites.extend(foreign_sites(root, &candidates, &files));
    }

    // 3. 不動点 — 書き換えられない所がある候補を落とし、落ちた候補の本体の呼びも書き換えられなくなる。
    let mut alive: BTreeSet<usize> = (0..candidates.len()).collect();
    loop {
        let dropped: BTreeSet<usize> = sites.iter().filter(|s| alive.contains(&s.callee) && blocks(s, &alive, options.strict)).map(|s| s.callee).collect();
        if dropped.is_empty() {
            break;
        }
        alive.retain(|c| !dropped.contains(c));
    }
    for site in &sites {
        if let Err(why) = verdict(site, &alive) {
            let candidate = &candidates[site.callee];
            let callee = candidate.qualified.clone();
            let reason = if alive.contains(&site.callee) { JudgementReason::CallerLeft { callee, why } } else { JudgementReason::BlockedCaller { callee, why } };
            report.needs_judgement.push(NeedsJudgement { location: site.location.clone(), definition: candidate.name.clone(), reason });
        }
    }

    // 4. 書き換えを file ごとに集める。
    let mut edits: BTreeMap<usize, Vec<Edit>> = BTreeMap::new();
    let mut needs: BTreeMap<usize, BTreeSet<&str>> = BTreeMap::new();
    for &index in &alive {
        let candidate = &candidates[index];
        edits.entry(candidate.file).or_default().extend(candidate.edits.iter().cloned());
        needs.entry(candidate.file).or_default().insert("defk");
        let file = &files[candidate.file];
        report.converted.push(ConvertedDefinition { location: file.location(candidate.start), name: candidate.name.clone(), contract: candidate.contract.clone() });
    }
    for site in &sites {
        let (true, Some(file)) = (alive.contains(&site.callee), site.file) else { continue };
        let Ok(bind) = verdict(site, &alive) else { continue };
        let source = &files[file].source;
        let rewrite = match bind {
            Some(head) => {
                edits.entry(file).or_default().push(Edit { start: head.start, end: head.end, text: "<-".to_string() });
                needs.entry(file).or_default().insert("<-");
                CallRewrite::Bind { from: source[head.start..head.end].to_string() }
            }
            None => {
                let list = edits.entry(file).or_default();
                list.push(Edit { start: site.span.start, end: site.span.start, text: "(! ".to_string() });
                list.push(Edit { start: site.span.end, end: site.span.end, text: ")".to_string() });
                CallRewrite::Bang
            }
        };
        report.rewritten_calls.push(RewrittenCall {
            location: site.location.clone(),
            callee: candidates[site.callee].name.clone(),
            container: site.container.clone(),
            rewrite,
        });
    }
    for (file, names) in &needs {
        if let Some(edit) = require_edit(&files[*file], names) {
            edits.entry(*file).or_default().push(edit);
        }
    }

    // 5. 書き換えた source を作り、DOEFF126 を撃つ。
    let mut rewritten: BTreeMap<usize, String> = BTreeMap::new();
    for (file, list) in &edits {
        rewritten.insert(*file, apply_edits(&files[*file].source, list)?);
    }
    report.changed_files = rewritten.keys().map(|i| files[*i].rel.clone()).collect();
    report.residual_bare_calls = residual_bare_calls(&files, &rewritten, &sites);
    if options.write {
        for (file, text) in &rewritten {
            std::fs::write(&files[*file].path, text).map_err(|e| format!("{} を書けない: {}", files[*file].path.display(), e))?;
        }
    }
    report.converted.sort_by(|a, b| a.location.cmp(&b.location));
    report.rewritten_calls.sort_by(|a, b| a.location.cmp(&b.location));
    report.needs_judgement.sort_by(|a, b| a.location.cmp(&b.location));
    report.skipped.sort_by(|a, b| a.location.cmp(&b.location));
    Ok(report)
}

/// 入れ子の定義を調べる時の、外の定義。
struct Outer {
    name: String,
    /// 外が class か(中の defn は method)。
    class: bool,
}

/// 関数の中の defn(method は当てなかった物、ほかは判断の要る物)。
fn nested_definitions(hy: &Hy<'_>, file: &HyFile, report: &mut FixReport) {
    fn visit(hy: &Hy<'_>, file: &HyFile, form: &Form, outer: Option<&Outer>, top: bool, report: &mut FixReport) {
        let Some(items) = live(form) else {
            for child in children(form) {
                visit(hy, file, child, outer, false, report);
            }
            return;
        };
        let head = items.first().and_then(|h| hy.head_text(h));
        let inner = match head {
            Some("do" | "eval-and-compile" | "eval-when-compile") if top => {
                for child in &items[1..] {
                    visit(hy, file, child, outer, true, report);
                }
                return;
            }
            Some("defclass") => Outer { name: items.get(1).map(|n| hy.text(n)).unwrap_or("").to_string(), class: true },
            Some("defn" | "defn/a") => {
                let name = definition_parts(hy, &items).map(|p| name_and_return(hy, p.name_form).0).unwrap_or_default();
                let location = file.location(form.span.start);
                match outer {
                    Some(Outer { class: true, .. }) => report.skipped.push(SkippedDefinition { location, name: name.clone(), reason: SkipReason::Method }),
                    Some(Outer { name: outer_name, class: false }) => report.needs_judgement.push(NeedsJudgement {
                        location,
                        definition: name.clone(),
                        reason: JudgementReason::NestedDefn { outer: outer_name.clone() },
                    }),
                    None => {}
                }
                Outer { name, class: false }
            }
            Some(h) if DEFINITION_LIKE.contains(&h) => Outer { name: items.get(1).map(|n| name_and_return(hy, n).0).unwrap_or_default(), class: false },
            _ => {
                for child in &items {
                    visit(hy, file, child, outer, false, report);
                }
                return;
            }
        };
        for child in &items[1..] {
            visit(hy, file, child, Some(&inner), false, report);
        }
    }
    for form in &file.forms {
        visit(hy, file, form, None, true, report);
    }
}

/// 名を持つ定義(入れ子の defn の外の名を取るため)。
const DEFINITION_LIKE: &[&str] = &["defk", "deff", "deftest", "defhandler", "defp", "defpp", "defmacro"];

/// file の require に要る macro を足す書き換え(既にあれば None)。
fn require_edit(file: &HyFile, needed: &BTreeSet<&str>) -> Option<Edit> {
    let hy = Hy { src: &file.source };
    let mut first_import: Option<usize> = None;
    for form in &file.forms {
        let Some(items) = live(form) else { continue };
        let head = items.first().and_then(|h| hy.head_text(h));
        if matches!(head, Some("require" | "import")) && first_import.is_none() {
            first_import = Some(form.span.start);
        }
        if head != Some("require") {
            continue;
        }
        let mut index = 1;
        while index < items.len() {
            let module = hy.text(items[index]);
            let Some(names) = items.get(index + 1).filter(|n| n.bracket_items().is_some()) else {
                index += 1;
                continue;
            };
            if module == "doeff-hy.macros" || module == "doeff_hy.macros" {
                let have: BTreeSet<&str> = live_items(names.bracket_items().unwrap_or_default()).iter().map(|n| hy.text(n)).collect();
                let missing: Vec<&str> = needed.iter().filter(|n| !have.contains(**n)).copied().collect();
                if missing.is_empty() {
                    return None;
                }
                let close = names.span.end - 1;
                let text = if have.is_empty() { missing.join(" ") } else { format!(" {}", missing.join(" ")) };
                return Some(Edit { start: close, end: close, text });
            }
            index += 2;
        }
    }
    let at = first_import.or_else(|| file.forms.first().map(|f| f.span.start)).unwrap_or(0);
    let names: Vec<&str> = needed.iter().copied().collect();
    Some(Edit { start: at, end: at, text: format!("(require doeff-hy.macros [{}])\n", names.join(" ")) })
}

/// 書き換えを後ろから当てる(重なる書き換えは誤り)。
fn apply_edits(source: &str, edits: &[Edit]) -> Result<String, String> {
    let mut sorted: Vec<&Edit> = edits.iter().collect();
    sorted.sort_by(|a, b| b.start.cmp(&a.start).then(b.end.cmp(&a.end)));
    sorted.dedup_by(|a, b| a == b);
    let mut out = source.to_string();
    let mut limit = usize::MAX;
    for edit in sorted {
        if edit.end > limit {
            return Err(format!("書き換えが重なった({}..{})", edit.start, edit.end));
        }
        out.replace_range(edit.start..edit.end, &edit.text);
        limit = edit.start;
    }
    Ok(out)
}

/// 書き換えた後の source で DOEFF126 を撃つ(書き換えた file と、候補を使う所のある file)。
fn residual_bare_calls(files: &[HyFile], rewritten: &BTreeMap<usize, String>, sites: &[Site]) -> Vec<ResidualBareCall> {
    let text_of = |index: usize| rewritten.get(&index).map(String::as_str).unwrap_or(&files[index].source);
    let mut defks = bare_calls::DefkNames::default();
    for (index, file) in files.iter().enumerate() {
        let text = text_of(index);
        if text.contains("(defk") {
            defks.extend(bare_calls::defk_names_in(text, &file.module));
        }
    }
    let mut checked: BTreeSet<usize> = rewritten.keys().copied().collect();
    checked.extend(sites.iter().filter_map(|s| s.file));
    let mut out = Vec::new();
    for index in checked {
        let file = &files[index];
        if file.excluded {
            continue;
        }
        let text = text_of(index);
        let bindings = hy_bindings(text, &file.module);
        let lines = line_starts(text);
        for call in bare_calls::bare_calls_in(text, Scope { module: &file.module, bindings: &bindings }, &defks) {
            out.push(ResidualBareCall { location: location_in(&file.rel, &lines, text, call.span.start), definition: call.definition, callee: call.callee });
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 一時の repo に file を置き、変換を走らせて (報告・書き換えた後の中身) を返す。
    struct Fixture {
        dir: tempfile::TempDir,
    }

    impl Fixture {
        fn new(files: &[(&str, &str)]) -> Fixture {
            let dir = tempfile::tempdir().expect("tempdir");
            for (rel, text) in files {
                let path = dir.path().join(rel);
                std::fs::create_dir_all(path.parent().expect("parent")).expect("mkdir");
                std::fs::write(path, text).expect("write");
            }
            Fixture { dir }
        }

        fn run(&self, target: &str) -> FixReport {
            self.run_with(target, false)
        }

        fn run_strict(&self, target: &str) -> FixReport {
            self.run_with(target, true)
        }

        fn run_with(&self, target: &str, strict: bool) -> FixReport {
            let root = self.dir.path().canonicalize().expect("root");
            run(&FixOptions {
                root: root.clone(),
                targets: vec![root.join(target)],
                excludes: DEFAULT_EXCLUDES.iter().map(|s| s.to_string()).collect(),
                reason_marker: DEFAULT_REASON_MARKER.to_string(),
                write: true,
                strict,
            })
            .expect("run")
        }

        fn read(&self, rel: &str) -> String {
            std::fs::read_to_string(self.dir.path().join(rel)).expect("read")
        }
    }

    fn reasons(report: &FixReport) -> Vec<String> {
        report.needs_judgement.iter().map(|j| format!("{}:{}", j.definition, j.reason)).collect()
    }

    #[test]
    fn definition_gets_contract_from_annotations_and_callers_are_rewritten() {
        let fx = Fixture::new(&[
            (
                "app/core.hy",
                r#"(require doeff-hy.macros [val])

(defn #^ int add-one [#^ int x #^ (| str None) [label None]]
  "1 を足す。"
  (+ x 1))

(defn #^ (get list int) twice [#^ (get list int) xs #^ float scale]
  (lfor x xs (* x scale)))
"#,
            ),
            (
                "app/use.hy",
                r#"(require doeff-hy.macros [defk <- val])
(import app.core [add-one twice])

(defk total [n]
  {:pre [(: n int)] :post [(: % int)]}
  (setv a (add-one n))
  (val b (twice [n] 2.0))
  (+ a (add-one (len b))))
"#,
            ),
        ]);
        let report = fx.run("app/core.hy");
        assert!(report.needs_judgement.is_empty(), "{:?}", reasons(&report));
        assert_eq!(report.converted.len(), 2);
        let core = fx.read("app/core.hy");
        assert!(core.contains("(require doeff-hy.macros [val defk])"), "{}", core);
        assert!(core.contains("(defk add-one [x [label None]]\n  {:pre [(: x int) (: label (| str None))] :post [(: % int)]}\n  \"1 を足す。\""), "{}", core);
        // 型の引数つきの型は契約に元の型、注記は残す。float は int も受ける。
        assert!(core.contains("(defk twice [#^ (get list int) xs scale]\n  {:pre [(: xs list) (: scale (| float int))] :post [(: % list)]}"), "{}", core);
        let used = fx.read("app/use.hy");
        assert!(used.contains("(<- a (add-one n))"), "{}", used);
        assert!(used.contains("(<- b (twice [n] 2.0))"), "{}", used);
        // 式の途中の呼びは !、既に require にある <- は足さない。
        assert!(used.contains("(+ a (! (add-one (len b))))"), "{}", used);
        assert!(used.starts_with("(require doeff-hy.macros [defk <- val])"), "{}", used);
        assert!(report.residual_bare_calls.is_empty(), "{:?}", report.residual_bare_calls);
    }

    #[test]
    fn tail_calls_and_recursion_inside_converted_functions_are_banged() {
        let fx = Fixture::new(&[(
            "m.hy",
            r#"(defn #^ int leaf [#^ int x] x)
(defn #^ int branch [#^ int x] (if (> x 0) (branch (- x 1)) (leaf x)))
(deftest test-branch (assert (= (branch 2) 0)))
"#,
        )]);
        let report = fx.run("m.hy");
        assert_eq!(report.converted.len(), 2, "{:?}", reasons(&report));
        let text = fx.read("m.hy");
        assert!(text.starts_with("(require doeff-hy.macros [defk])\n(defk leaf [x] {:pre [(: x int)] :post [(: % int)]} x)"), "{}", text);
        assert!(text.contains("(if (> x 0) (! (branch (- x 1))) (! (leaf x)))"), "{}", text);
        assert!(text.contains("(assert (= (! (branch 2)) 0))"), "{}", text);
        assert!(report.residual_bare_calls.is_empty(), "{:?}", report.residual_bare_calls);
    }

    #[test]
    fn functions_with_callers_that_cannot_be_rewritten_are_left_and_listed() {
        let fx = Fixture::new(&[
            (
                "m.hy",
                r#"(defn #^ int in-comprehension [#^ int x] x)
(defn #^ int in-plain [#^ int x] x)
(defn #^ int as-key [#^ int x] x)
(defn #^ int at-module [#^ int x] x)
(defn #^ int in-fn [#^ int x] x)
(defn #^ int builder [#^ int x] x)
(defn #^ int from-python [#^ int x] x)
(defn #^ int caller-of-blocked [#^ int x] (in-plain x))
(deff helper [x] {:pre [(: x int)] :post [(: % int)]} (in-plain x))
(defk k [xs]
  {:pre [(: xs list)] :post [(: % list)]}
  (val ys (lfor x xs (in-comprehension x)))
  (val zs (sorted xs :key as-key))
  (val f (fn [x] (in-fn x)))
  (<- b (builder 1))
  ys)
(setv CONSTANT (at-module 1))
"#,
            ),
            ("py_user.py", "from m import from_python\n"),
        ]);
        let report = fx.run_strict("m.hy");
        // caller-of-blocked は in-plain を呼ぶが、自分の呼び手は無いので変換される(in-plain の呼びは素の defn の中ではなくなる… が
        // in-plain は deff の中でも呼ばれるので落ちる)。
        let converted: Vec<&str> = report.converted.iter().map(|c| c.name.as_str()).collect();
        assert_eq!(converted, vec!["caller-of-blocked"]);
        let why: BTreeSet<String> = reasons(&report).into_iter().collect();
        for expected in [
            "in-comprehension:m.in_comprehension の呼び手: 内包表記 lfor の中で呼んでいる",
            "in-plain:m.in_plain の呼び手: 変換しない素の関数(deff helper)の中で呼んでいる",
            "as-key:m.as_key の呼び手: 関数そのものを値として渡している",
            "at-module:m.at_module の呼び手: module の最上位で呼んでいる",
            "in-fn:m.in_fn の呼び手: 変換しない素の関数(fn k)の中で呼んでいる",
            "builder:m.builder の呼び手: 既に <- / ! で束ねている(Program を返す組み立て)",
            "from-python:m.from_python の呼び手: Python の file から参照している",
        ] {
            assert!(why.contains(expected), "{} が無い: {:?}", expected, why);
        }
        let text = fx.read("m.hy");
        assert!(text.contains("(defn #^ int in-plain [#^ int x] x)"), "{}", text);
        // 変換した caller-of-blocked の本体の in-plain は defn のまま(defk の中で素の関数を呼ぶのは正しい)。
        assert!(text.contains("(defk caller-of-blocked [x] {:pre [(: x int)] :post [(: % int)]} (in-plain x))"), "{}", text);
    }

    #[test]
    fn a_blocked_caller_inside_a_candidate_cascades() {
        let fx = Fixture::new(&[(
            "m.hy",
            r#"(defn #^ int inner [#^ int x] x)
(defn #^ int outer [#^ int x] (inner x))
(setv X (outer 1))
"#,
        )]);
        let report = fx.run_strict("m.hy");
        // outer は module の最上位で呼ばれるので落ち、outer の中の inner の呼びは素の defn の中になるので inner も落ちる。
        assert!(report.converted.is_empty(), "{:?}", report.converted);
        assert!(reasons(&report).contains(&"inner:m.inner の呼び手: 変換しない素の関数(defn outer)の中で呼んでいる".to_string()), "{:?}", reasons(&report));
        assert!(report.changed_files.is_empty());
    }

    #[test]
    fn forms_that_are_not_targeted_are_skipped() {
        let fx = Fixture::new(&[
            (
                "m.hy",
                r#"(defn __getattr__ [name] name)
(defn :async fetch [url] url)
(defn #^ int kept [#^ int x] x)  ; defk にできない: 外の framework が素の関数として呼ぶ
(eval-and-compile (defn #^ int helper [#^ int x] x))
(defclass Box [] (defn #^ int size [self] 1))
"#,
            ),
            ("clients/hy/acp_client/a.hy", "(defn #^ int copied [#^ int x] x)\n"),
        ]);
        let report = fx.run(".");
        let skipped: Vec<String> = report.skipped.iter().map(|s| format!("{}:{}", s.name, s.reason)).collect();
        assert_eq!(
            skipped,
            vec![
                "__getattr__:dunder の名",
                "fetch::async",
                "kept:理由の註: defk にできない: 外の framework が素の関数として呼ぶ",
                "helper:eval-and-compile の中",
                "size:class の method",
            ]
        );
        // 除いた path の定義は数えも書き換えもしない。
        assert!(fx.read("clients/hy/acp_client/a.hy").starts_with("(defn"));
        assert!(report.converted.is_empty() && report.changed_files.is_empty());
    }

    #[test]
    fn definitions_that_need_judgement_are_listed_with_the_reason() {
        let fx = Fixture::new(&[(
            "m.hy",
            r#"(defn untyped [x] x)
(defn #^ int no-param-type [x] x)
(defn no-return [#^ int x] x)
(defn #^ int broad [#^ object x] 1)
(defn #^ int literal [#^ (get Literal "a") x] 1)
(defn [cache] #^ int decorated [#^ int x] x)
(defn #^ int gen [#^ int x] (yield x))
(defn #^ None test-thing [] None)
(defn #^ int handler-like [#^ int effect #^ int k] 1)
(defn #^ int outer [#^ int x] (defn #^ int inner [#^ int y] y) (inner x))
"#,
        )]);
        let report = fx.run("m.hy");
        let why: Vec<String> = reasons(&report);
        for expected in [
            "untyped:引数 x に型の注記が無い",
            "untyped:戻り値の型の注記が無い",
            "no-param-type:引数 x に型の注記が無い",
            "no-return:戻り値の型の注記が無い",
            "broad:x の型 object は defk の契約が拒む広い型",
            "literal:x の型 (get Literal \"a\") を isinstance の型に写せない",
            "decorated:飾り [cache] がある",
            "gen:本体に yield がある(生成器)",
            "test-thing:検の関数 — deftest にする",
            "handler-like:引数の名 effect・k が handler の形",
            "inner:outer の中の defn",
        ] {
            assert!(why.contains(&expected.to_string()), "{} が無い: {:?}", expected, why);
        }
        // outer 自身は変換される(中の defn は素の関数のまま)。
        assert_eq!(report.converted.iter().map(|c| c.name.as_str()).collect::<Vec<_>>(), vec!["outer"]);
    }

    #[test]
    fn existing_contract_dicts_are_kept_and_completed() {
        let fx = Fixture::new(&[(
            "m.hy",
            r#"(defn #^ int tagged [#^ int x]
  "doc"
  {:tags {:context "c" :role "judgment"}}
  x)
(defn #^ dict literal-dict [] {"a" 1})
"#,
        )]);
        let report = fx.run("m.hy");
        assert_eq!(report.converted.len(), 2, "{:?}", reasons(&report));
        let text = fx.read("m.hy");
        assert!(text.contains("{:pre [(: x int)] :post [(: % int)] :tags {:context \"c\" :role \"judgment\"}}"), "{}", text);
        // 答えの辞書は契約ではない — 契約をその前に置く。
        assert!(text.contains("(defk literal-dict [] {:pre [] :post [(: % dict)]} {\"a\" 1})"), "{}", text);
    }

    #[test]
    fn threading_and_import_aliases_block() {
        let fx = Fixture::new(&[
            ("a.hy", "(defn #^ int step [#^ int x #^ int y] (+ x y))\n(defn #^ int aliased [#^ int x] x)\n"),
            (
                "b.hy",
                "(import a [step aliased :as other])\n(defk go [n] {:pre [(: n int)] :post [(: % int)]} (-> n (step 1)))\n",
            ),
        ]);
        let report = fx.run_strict("a.hy");
        let why = reasons(&report);
        assert!(why.contains(&"step:a.step の呼び手: -> の段として呼んでいる".to_string()), "{:?}", why);
        assert!(why.contains(&"aliased:a.aliased の呼び手: import の別名 other で取り込んでいる".to_string()), "{:?}", why);
        assert!(report.converted.is_empty());
    }

    #[test]
    fn by_default_functions_are_converted_and_callers_left_are_listed() {
        let fx = Fixture::new(&[(
            "m.hy",
            r#"(defn #^ int leaf [#^ int x] x)
(defn #^ int builder [#^ int x] x)
(defk k [xs]
  {:pre [(: xs list)] :post [(: % list)]}
  (val ys (lfor x xs (leaf x)))
  (<- b (builder 1))
  (+ ys [(leaf 1)]))
(setv CONSTANT (leaf 1))
"#,
        )]);
        let report = fx.run("m.hy");
        // leaf は変換し、内包表記と module の最上位の呼びは手で直す物に出す。builder は Program を返す組み立てなので変換しない。
        assert_eq!(report.converted.iter().map(|c| c.name.as_str()).collect::<Vec<_>>(), vec!["leaf"]);
        let why: BTreeSet<String> = reasons(&report).into_iter().collect();
        for expected in [
            "leaf:m.leaf は defk にした — この呼び手を手で直す: 内包表記 lfor の中で呼んでいる",
            "leaf:m.leaf は defk にした — この呼び手を手で直す: module の最上位で呼んでいる",
            "builder:m.builder の呼び手: 既に <- / ! で束ねている(Program を返す組み立て)",
        ] {
            assert!(why.contains(expected), "{} が無い: {:?}", expected, why);
        }
        let text = fx.read("m.hy");
        assert!(text.contains("(+ ys [(! (leaf 1))])") && text.contains("(lfor x xs (leaf x))"), "{}", text);
        // 手で直す呼びの module の最上位の束ねは、書き換えずに残り、DOEFF126 の残りにも出る(定義の外の defk の呼びは位置を問わず素の呼び)。
        // 内包表記の中の呼び(defk の本体の中・答えを値として使う所ではない)は残りに出ない。
        assert!(text.contains("(setv CONSTANT (leaf 1))"), "{}", text);
        let residual: Vec<(&str, &str)> = report.residual_bare_calls.iter().map(|r| (r.definition.as_str(), r.callee.as_str())).collect();
        assert_eq!(residual, vec![("<m>", "leaf")]);
    }

    #[test]
    fn dry_run_writes_nothing() {
        let fx = Fixture::new(&[("m.hy", "(defn #^ int one [] 1)\n")]);
        let root = fx.dir.path().canonicalize().expect("root");
        let report = run(&FixOptions {
            root: root.clone(),
            targets: vec![root.join("m.hy")],
            excludes: Vec::new(),
            reason_marker: DEFAULT_REASON_MARKER.to_string(),
            write: false,
            strict: false,
        })
        .expect("run");
        assert_eq!(report.converted.len(), 1);
        assert_eq!(report.changed_files, vec!["m.hy".to_string()]);
        assert_eq!(fx.read("m.hy"), "(defn #^ int one [] 1)\n");
        assert!(report.table().contains("計画だけ"));
    }
}
