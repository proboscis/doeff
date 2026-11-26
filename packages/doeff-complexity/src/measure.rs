//! Hy complexity measurement on `doeff_indexer::hy_index::reader`.
//!
//! Counting rules (design section 2, `code-quality/docs/design/wmc-gate/design.md`):
//! - Cyclomatic complexity (CC) of a function/method/clause = 1 + branch decisions.
//! - Branches: `if`, `when`, `unless`, `while`, `until`, `except`, `except*`, `guard`, `if-let`,
//!   `when-let`, `absent-as` (+1); `and` / `or` (+(terms - 1)); `cond`, `case`, `match` (+1 per
//!   non-wildcard arm, and +1 per `:if` guard in `match`); comprehensions `for`, `lfor`, `gfor`,
//!   `sfor`, `dfor`, `for/do`, `traverse` (+1 per iterator pair and +1 per `:if` filter);
//!   `on-raise` (+1 per clause).
//! - Quoted/quasiquoted forms and direct-child `{:pre ... :post ...}` contracts are not counted.
//! - Branches inside anonymous `fn` forms are added to the enclosing function (no +1 for `fn`).
//! - Production files only: excludes test dirs (`tests`, `test`, `e2e`, `__tests__`), test file
//!   names (`test_*`, `conftest*`, `*_test.*`, `*.test.*`, `*.spec.*`), `docs/`, `.agents/`, and
//!   `generated/`. `deftest` forms are not counted.
//! - Any Hy reader issue (unclosed/mismatched bracket, stray closer, unterminated string) makes
//!   the file unreadable (exit 2).

use std::collections::BTreeMap;

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Prefix, ReadIssue, Reader};

/// One measured Hy function, class/record method, or handler clause.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FnMetric {
    pub kind: String,
    pub owner: String,
    pub name: String,
    pub line: usize,
    pub cc: usize,
}

/// Complexity measurements for one Hy source file.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FileMetrics {
    /// Sum of CC of all counted functions, methods, clauses, and loose top-level branches.
    pub module_wmc: usize,
    /// Own-method / own-clause WMC sum per type/handler name (only types with WMC > 0).
    pub types: BTreeMap<String, usize>,
    /// All counted functions, methods, and handler clauses (excludes `deftest` and loose top-level).
    pub fns: Vec<FnMetric>,
    /// Loose top-level branch decisions (contributes to `module_wmc`, not to `fns`).
    pub loose_top_cc: usize,
}

impl FileMetrics {
    pub fn type_count(&self) -> usize {
        self.types.len()
    }

    pub fn type_sum(&self) -> usize {
        self.types.values().sum()
    }

    pub fn type_max(&self) -> usize {
        self.types.values().copied().max().unwrap_or(0)
    }

    pub fn fn_count(&self) -> usize {
        self.fns.len()
    }

    pub fn fn_sum(&self) -> usize {
        self.fns.iter().map(|f| f.cc).sum()
    }

    pub fn fn_max(&self) -> usize {
        self.fns.iter().map(|f| f.cc).max().unwrap_or(0)
    }
}

/// Normalizes a repo-relative path (strips leading `./`, converts `\` to `/`).
pub fn normalize_path(path: &str) -> String {
    let mut s = path.replace('\\', "/");
    while let Some(stripped) = s.strip_prefix("./") {
        s = stripped.to_string();
    }
    s
}

/// Returns whether a repo-relative path is a production Hy file subject to complexity measurement.
pub fn is_prod_hy(path: &str) -> bool {
    let norm = normalize_path(path);
    if !norm.ends_with(".hy") {
        return false;
    }
    if norm.starts_with("docs/") || norm.starts_with(".agents/") {
        return false;
    }
    let parts: Vec<&str> = norm.split('/').collect();
    let Some((&file_name, dirs)) = parts.split_last() else {
        return false;
    };
    if dirs.iter().any(|d| matches!(*d, "tests" | "test" | "e2e" | "__tests__" | "generated")) {
        return false;
    }
    if file_name.starts_with("test_")
        || file_name.starts_with("conftest")
        || file_name.contains("_test.")
        || file_name.contains(".test.")
        || file_name.contains(".spec.")
    {
        return false;
    }
    true
}

struct LineIndex(Vec<usize>);

impl LineIndex {
    fn of(src: &str) -> Self {
        Self(src.match_indices('\n').map(|(idx, _)| idx).collect())
    }

    fn line_at(&self, offset: usize) -> usize {
        self.0.partition_point(|&nl| nl < offset) + 1
    }
}

fn format_issue(lines: &LineIndex, issue: &ReadIssue) -> String {
    match issue {
        ReadIssue::Unclosed { delim, open } => {
            format!("unclosed '{}' at line {}", delim.opener_text(), lines.line_at(*open))
        }
        ReadIssue::Mismatched { delim, open, at } => {
            format!(
                "mismatched closer at line {} for '{}' opened at line {}",
                lines.line_at(*at),
                delim.opener_text(),
                lines.line_at(*open)
            )
        }
        ReadIssue::StrayCloser { at } => {
            format!("stray closing bracket at line {}", lines.line_at(*at))
        }
        ReadIssue::UnterminatedString { start } => {
            format!("unterminated string at line {}", lines.line_at(*start))
        }
    }
}

fn text<'a>(src: &'a str, form: &Form) -> &'a str {
    &src[form.span.start..form.span.end]
}

fn sym<'a>(src: &'a str, form: &Form) -> Option<&'a str> {
    match &form.node {
        Node::Symbol => Some(text(src, form)),
        Node::Annotated { target: Some(t), .. } => sym(src, t),
        _ => None,
    }
}

fn seq(form: &Form) -> Option<&[Form]> {
    match &form.node {
        Node::Seq { items, .. } => Some(items),
        _ => None,
    }
}

fn is_kw(form: &Form) -> bool {
    matches!(form.node, Node::Keyword)
}

fn is_wild(src: &str, form: &Form) -> bool {
    sym(src, form).is_some_and(|x| matches!(x, "_" | "True" | "else")) || is_kw(form)
}

fn is_comp(head: &str) -> bool {
    matches!(head, "for" | "lfor" | "gfor" | "sfor" | "dfor" | "for/do" | "traverse")
}

/// Counts iterator pairs and `:if` filters in comprehension clauses or a `for` bracket.
fn clauses_cc(src: &str, items: &[Form], bracketed: bool) -> usize {
    let mut n = 0;
    let mut i = 0;
    let end = if bracketed { items.len() } else { items.len().saturating_sub(1) };
    while i < end {
        if is_kw(&items[i]) {
            if text(src, &items[i]) == ":if" {
                n += 1;
            }
            i += 2;
            continue;
        }
        if i + 1 < end {
            n += 1;
        }
        i += 2;
    }
    n
}

/// Counts branch decisions inside `form` (without the function's base +1).
fn form_cc(src: &str, form: &Form) -> usize {
    match &form.node {
        Node::Prefixed { prefix: Prefix::Quote | Prefix::Quasiquote, .. } => 0,
        Node::Prefixed { inner: Some(inner), .. } => form_cc(src, inner),
        Node::Annotated { target: Some(target), .. } => form_cc(src, target),
        Node::Tagged { inner: Some(inner) } => form_cc(src, inner),
        Node::Seq { delim: Delim::Paren, items } => {
            let below: usize = items.iter().map(|item| form_cc(src, item)).sum();
            let head = items.first().and_then(|f| sym(src, f)).unwrap_or("");
            let here = match head {
                "if" | "when" | "unless" | "while" | "until" | "except" | "except*" | "guard"
                | "if-let" | "when-let" | "absent-as" => 1,
                "and" | "or" => items.len().saturating_sub(2),
                "cond" => {
                    let mut n = 0;
                    let mut i = 1;
                    while i + 1 < items.len() {
                        if !is_wild(src, &items[i]) {
                            n += 1;
                        }
                        i += 2;
                    }
                    n
                }
                "case" => {
                    let mut n = 0;
                    let mut i = 2;
                    while i + 1 < items.len() {
                        if !is_wild(src, &items[i]) {
                            n += 1;
                        }
                        i += 2;
                    }
                    n
                }
                "match" => {
                    let mut n = 0;
                    let mut i = 2;
                    while i + 1 < items.len() {
                        let wild = is_wild(src, &items[i]);
                        let mut has_guard = false;
                        let mut j = i + 1;
                        while j + 1 < items.len()
                            && is_kw(&items[j])
                            && matches!(text(src, &items[j]), ":if" | ":as")
                        {
                            if text(src, &items[j]) == ":if" {
                                has_guard = true;
                            }
                            j += 2;
                        }
                        if !wild {
                            n += 1;
                        }
                        if has_guard {
                            n += 1;
                        }
                        i = j + 1;
                    }
                    n
                }
                "on-raise" => items.iter().skip(2).filter(|item| seq(item).is_some()).count(),
                _ if is_comp(head) => {
                    if let Some(Form { node: Node::Seq { delim: Delim::Bracket, items: b }, .. }) =
                        items.get(1)
                    {
                        if matches!(head, "for" | "for/do") {
                            clauses_cc(src, b, true)
                        } else {
                            clauses_cc(src, &items[1..], false)
                        }
                    } else {
                        let e = if head == "dfor" {
                            items.len().saturating_sub(1)
                        } else {
                            items.len()
                        };
                        clauses_cc(src, &items[1..e.max(1)], false)
                    }
                }
                _ => 0,
            };
            here + below
        }
        Node::Seq { items, .. } => items.iter().map(|item| form_cc(src, item)).sum(),
        _ => 0,
    }
}

/// CC of a definition body starting at the definition's name token.
/// Direct-child `{...}` forms (contracts `{:pre ... :post ...}`) are excluded.
fn body_cc(src: &str, items_from_name: &[Form]) -> usize {
    1 + items_from_name
        .iter()
        .skip(1)
        .filter(|item| !item.is_brace())
        .map(|item| form_cc(src, item))
        .sum::<usize>()
}

/// Parses and measures one Hy source file. Returns `Err` if the Hy reader finds any syntax issue.
pub fn measure_source(path: &str, src: &str) -> Result<FileMetrics, String> {
    let mut reader = Reader::new(src, 0, src.len());
    let forms = reader.read_all();
    let lines = LineIndex::of(src);
    if let Some(first_issue) = reader.issues.first() {
        return Err(format!("{}: Hy syntax error — {}", path, format_issue(&lines, first_issue)));
    }

    let mut module_wmc = 0usize;
    let mut types: BTreeMap<String, usize> = BTreeMap::new();
    let mut fns: Vec<FnMetric> = Vec::new();
    let mut loose_top_cc = 0usize;

    for form in &forms {
        let Some(items) = form.paren_items() else {
            let n = form_cc(src, form);
            loose_top_cc += n;
            module_wmc += n;
            continue;
        };
        let head = items.first().and_then(|f| sym(src, f)).unwrap_or("");
        let name_at = |k: usize| items.get(k).and_then(|f| sym(src, f)).unwrap_or("?").to_string();

        match head {
            "deftest" => {
                // Tests are never counted (design section 2).
            }
            "defk" | "deff" | "defp" | "defpp" | "defn" | "defmacro" | "defmcp-tool"
            | "defpipeline" | "defn/a" => {
                let mut k = 1;
                while k < 3
                    && matches!(
                        items.get(k).map(|f| &f.node),
                        Some(Node::Seq { delim: Delim::Bracket, .. })
                    )
                {
                    k += 1;
                }
                let cc = if k < items.len() { body_cc(src, &items[k..]) } else { 1 };
                module_wmc += cc;
                fns.push(FnMetric {
                    kind: head.to_string(),
                    owner: String::new(),
                    name: name_at(k),
                    line: lines.line_at(form.span.start),
                    cc,
                });
            }
            "defhandler" => {
                let owner = name_at(1);
                for clause in items.iter().skip(2) {
                    let Some(ci) = clause.paren_items() else { continue };
                    if ci.len() >= 2
                        && matches!(ci[1].node, Node::Seq { delim: Delim::Bracket, .. })
                        && matches!(ci[0].node, Node::Symbol)
                    {
                        let cc = 1 + ci[2..].iter().map(|i| form_cc(src, i)).sum::<usize>();
                        module_wmc += cc;
                        *types.entry(owner.clone()).or_default() += cc;
                        fns.push(FnMetric {
                            kind: "clause".to_string(),
                            owner: owner.clone(),
                            name: sym(src, &ci[0]).unwrap_or("?").to_string(),
                            line: lines.line_at(clause.span.start),
                            cc,
                        });
                    }
                }
            }
            "defclass" | "defrecord" | "defwire" | "defeffect" | "defenum" => {
                let mut k = 1;
                if matches!(
                    items.get(1).map(|f| &f.node),
                    Some(Node::Seq { delim: Delim::Bracket, .. })
                ) {
                    k = 2;
                }
                let owner = name_at(k);
                for child in items.iter().skip(k + 1) {
                    let Some(ci) = child.paren_items() else { continue };
                    if matches!(
                        ci.first().and_then(|i| sym(src, i)),
                        Some("defn" | "defk" | "deff" | "defn/a")
                    ) {
                        let mut mk = 1;
                        while mk < 3
                            && matches!(
                                ci.get(mk).map(|f| &f.node),
                                Some(Node::Seq { delim: Delim::Bracket, .. })
                            )
                        {
                            mk += 1;
                        }
                        let cc = if mk < ci.len() { body_cc(src, &ci[mk..]) } else { 1 };
                        let method_name =
                            ci.get(mk).and_then(|f| sym(src, f)).unwrap_or("?").to_string();
                        module_wmc += cc;
                        *types.entry(owner.clone()).or_default() += cc;
                        fns.push(FnMetric {
                            kind: "method".to_string(),
                            owner: owner.clone(),
                            name: method_name,
                            line: lines.line_at(child.span.start),
                            cc,
                        });
                    }
                }
            }
            _ => {
                let n = form_cc(src, form);
                loose_top_cc += n;
                module_wmc += n;
            }
        }
    }

    Ok(FileMetrics { module_wmc, types, fns, loose_top_cc })
}
