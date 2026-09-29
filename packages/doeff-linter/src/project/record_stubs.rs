//! DOEFF145: 型の宣言(.pyi)が、同じ dir の同じ名の .hy の実行時の形を偽らない(agora-redesign #1191 — agora-controllers の
//! scripts/check_record_stubs.hy の判定の移し先。判定の意味は元と同じ)。
//!
//! 食い違いの形: .hy の class が kw-only の record(最上位の `(defrecord 名 …)` か、飾りに `(dataclass … :kw-only True …)` を持つ
//! 最上位の `(defclass [飾り …] 名 …)`)なのに、同じ dir の同じ名の .pyi がその class を kw_only=True の無い `@dataclass` で宣言する。
//! 型検査は .pyi を読むので位置の引数の呼びを通すが、実行時の `__init__` は欄を名でしか受けないので TypeError になる。
//!
//! 読む .pyi は repo の architecture.hy の `:record-stubs {:files [..] :except [..]}` に当たり、同じ dir に同じ名の .hy が在る物。
//! .pyi の側は最上位の class の飾り `@dataclass`・`@dataclasses.dataclass`(呼びの形も)だけを読み、`kw_only=True` の literal だけを
//! kw-only と読む(無い・False・literal でない値は kw-only でない)。`@dataclass` の無い class は読まない(`__init__` を宣言しないので
//! 型検査は位置の引数の呼びを通さない — 危ない向きではない)。名は Hy の名を Python の名へ mangle してから突き合わせる。

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use doeff_indexer::hy_index::reader::{Form, Node, Reader};
use rustpython_ast::{Constant, Expr, Mod, Ranged, Stmt};
use rustpython_parser::{parse, Mode};

use super::architecture::FileSelection;
use super::retired::{judge_files, selected};

const DATACLASS_NAMES: &[&str] = &["dataclass", "dataclasses.dataclass"];
/// .hy の側の書き方(知らせの文の語)。
pub const DEFRECORD_FORM: &str = "defrecord";
pub const KW_ONLY_DEFCLASS_FORM: &str = "飾り (dataclass :kw-only True) の defclass";

/// 食い違い 1 つ。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Mismatch {
    /// .pyi の飾りの byte の範囲。
    pub start: usize,
    pub end: usize,
    /// class の名。
    pub name: String,
    /// .hy の側の書き方。
    pub form: &'static str,
}

/// 読んだ .pyi 1 つの食い違い。
pub struct FileMismatches {
    pub rel: String,
    pub path: PathBuf,
    pub source: String,
    pub mismatches: Vec<Mismatch>,
}

/// Hy の名を Python の名にする(hy.mangle と同じ — 頭の `_` は残し、2 字目から先の `-` を `_` に、識別子にならなければ `hyx_` の形)。
pub fn mangle(name: &str) -> String {
    if name.contains('.') && !name.trim_matches('.').is_empty() {
        return name.split('.').map(|part| if part.is_empty() { String::new() } else { mangle(part) }).collect::<Vec<_>>().join(".");
    }
    let rest = name.trim_start_matches('_');
    let leading = &name[..name.len() - rest.len()];
    let mut chars = rest.chars();
    let body: String = match chars.next() {
        Some(first) => std::iter::once(first).chain(chars.map(|c| if c == '-' { '_' } else { c })).collect(),
        None => String::new(),
    };
    let identifier = |text: &str| {
        let mut cs = text.chars();
        cs.next().is_some_and(|c| c == '_' || c.is_alphabetic()) && cs.all(|c| c == '_' || c.is_alphanumeric())
    };
    if identifier(&format!("{}{}", leading, body)) {
        return format!("{}{}", leading, body);
    }
    let encoded: String = body
        .chars()
        .map(|c| {
            if c != 'X' && (c == '_' || c.is_alphanumeric()) {
                c.to_string()
            } else {
                format!("X{}X", char_name(c))
            }
        })
        .collect();
    format!("{}hyx_{}", leading, encoded)
}

/// hy.mangle が使う文字の名(unicodedata.name を小文字にし `-` を H・空白を _ に — 表に無い文字は U<16 進>)。
fn char_name(c: char) -> String {
    let name = match c {
        '!' => "exclamation mark",
        '?' => "question mark",
        '*' => "asterisk",
        '+' => "plus sign",
        '-' => "hyphen-minus",
        '/' => "solidus",
        '<' => "less-than sign",
        '>' => "greater-than sign",
        '=' => "equals sign",
        '%' => "percent sign",
        '&' => "ampersand",
        '$' => "dollar sign",
        '@' => "commercial at",
        '^' => "circumflex accent",
        '~' => "tilde",
        '|' => "vertical line",
        ':' => "colon",
        '\'' => "apostrophe",
        'X' => "latin capital letter x",
        _ => return format!("U{:x}", c as u32),
    };
    name.replace('-', "H").replace(' ', "_")
}

/// .hy の最上位の form → 実行時に欄を名でしか受けない class の索引(Python の名 → 書き方)。
fn hy_kw_only_classes(source: &str) -> Result<BTreeMap<String, &'static str>, String> {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    if let Some(issue) = reader.issues.first() {
        return Err(format!("Hy の読み取り器が読めない所が {} か所(最初 = {:?})", reader.issues.len(), issue));
    }
    let text = |f: &Form| source.get(f.span.start..f.span.end).unwrap_or("").to_string();
    let kept = |items: &[Form]| -> Vec<usize> { items.iter().enumerate().filter(|(_, f)| !matches!(f.node, Node::Discarded)).map(|(i, _)| i).collect() };
    let mut out = BTreeMap::new();
    for form in &forms {
        let Some(all) = form.paren_items() else { continue };
        let items: Vec<&Form> = kept(all).into_iter().map(|i| &all[i]).collect();
        let symbol = |f: &Form| matches!(f.node, Node::Symbol);
        let head = if items.len() > 1 && symbol(items[0]) { text(items[0]) } else { String::new() };
        let decorators: Vec<&Form> = match items.get(1).and_then(|f| f.bracket_items()) {
            Some(list) if head == "defclass" && items.len() > 2 => list.iter().filter(|f| !matches!(f.node, Node::Discarded)).collect(),
            _ => Vec::new(),
        };
        let kw_only_decorated = decorators.iter().any(|d| {
            let Some(parts) = d.paren_items() else { return false };
            let parts: Vec<&Form> = parts.iter().filter(|f| !matches!(f.node, Node::Discarded)).collect();
            !parts.is_empty()
                && DATACLASS_NAMES.contains(&text(parts[0]).as_str())
                && (1..parts.len().saturating_sub(1)).any(|i| {
                    matches!(parts[i].node, Node::Keyword)
                        && mangle(text(parts[i]).trim_start_matches(':')) == "kw_only"
                        && symbol(parts[i + 1])
                        && text(parts[i + 1]) == "True"
                })
        });
        if head == "defrecord" && symbol(items[1]) {
            out.insert(mangle(&text(items[1])), DEFRECORD_FORM);
        } else if kw_only_decorated && symbol(items[2]) {
            out.insert(mangle(&text(items[2])), KW_ONLY_DEFCLASS_FORM);
        }
    }
    Ok(out)
}

fn dotted(node: &Expr) -> String {
    match node {
        Expr::Name(name) => name.id.to_string(),
        Expr::Attribute(attribute) => format!("{}.{}", dotted(&attribute.value), attribute.attr),
        _ => String::new(),
    }
}

/// .hy の中身と同じ名の .pyi の中身 → 食い違い(.pyi の最上位の @dataclass の class のうち、.hy で kw-only なのに kw_only=True が無い物)。
pub fn judge(hy_source: &str, stub_source: &str, stub_rel: &str) -> Result<Vec<Mismatch>, String> {
    let classes = hy_kw_only_classes(hy_source).map_err(|reason| format!("同じ名の .hy を読めない: {}", reason))?;
    let module = parse(stub_source, Mode::Module, stub_rel).map_err(|error| format!("構文木にならない: {}", error))?;
    let Mod::Module(module) = module else { return Ok(Vec::new()) };
    let mut out = Vec::new();
    for statement in &module.body {
        let Stmt::ClassDef(class) = statement else { continue };
        for decorator in &class.decorator_list {
            let (target, keywords) = match decorator {
                Expr::Call(call) => (call.func.as_ref(), Some(&call.keywords)),
                other => (other, None),
            };
            if !DATACLASS_NAMES.contains(&dotted(target).as_str()) {
                continue;
            }
            let kw_only = keywords.is_some_and(|keywords| {
                keywords.iter().any(|k| {
                    k.arg.as_ref().is_some_and(|a| a.as_str() == "kw_only") && matches!(&k.value, Expr::Constant(c) if matches!(c.value, Constant::Bool(true)))
                })
            });
            if let Some(form) = classes.get(class.name.as_str()).filter(|_| !kw_only) {
                let range = decorator.range();
                out.push(Mismatch { start: range.start().to_usize(), end: range.end().to_usize(), name: class.name.to_string(), form });
            }
        }
    }
    Ok(out)
}

/// 同じ dir の同じ名の .hy の path。
fn sibling_hy(path: &Path) -> PathBuf {
    path.with_extension("hy")
}

/// 判じる .pyi か(宣言の glob に当たり、同じ dir に同じ名の .hy が在る)。
pub fn wants(rel: &str, path: &Path, selection: &FileSelection) -> bool {
    rel.ends_with(".pyi") && selected(rel, &selection.files, &selection.except) && sibling_hy(path).is_file()
}

/// .pyi 1 つ(中身は渡した物)と、同じ名の .hy を disk から読んで判じる。
pub fn judge_file(rel: &str, path: &Path, stub_source: &str) -> Result<Vec<Mismatch>, String> {
    let hy = sibling_hy(path);
    let hy_source = std::fs::read_to_string(&hy).map_err(|error| format!("同じ名の .hy を読めない: {}", error))?;
    judge(&hy_source, stub_source, rel)
}

/// 宣言の .pyi を読んで判じる(focus が在ればその下の file だけ)。読めない file は理由を返す。
/// 名指しの .hy の隣の、判じる同じ名の .pyi(根からの path と path)— .hy を直した時も、その実行時の形を偽る型の宣言を判じるため。
pub fn stub_of_hy(hy_rel: &str, hy_path: &Path, selection: &FileSelection) -> Option<(String, PathBuf)> {
    let stem = hy_rel.strip_suffix(".hy")?;
    let rel = format!("{}.pyi", stem);
    let path = hy_path.with_extension("pyi");
    (path.is_file() && wants(&rel, &path, selection)).then_some((rel, path))
}

/// 名指しの path の列に、名指しの .hy の隣の同じ名の .pyi を足す(重なりは除く)— .hy を直した時も、その実行時の形を偽る型の宣言を
/// 読み(focus)、その当たりを出力に残す(出力の絞り)ため。命令の行の名指しを読む 1 か所(main の only_paths と --modified)で使う。
pub fn with_sibling_stubs(paths: Vec<PathBuf>) -> Vec<PathBuf> {
    let stubs: Vec<PathBuf> =
        paths.iter().filter(|p| p.extension().is_some_and(|e| e == "hy") && p.is_file()).map(|p| p.with_extension("pyi")).filter(|p| p.is_file()).collect();
    let mut out = paths;
    for stub in stubs {
        if !out.contains(&stub) {
            out.push(stub);
        }
    }
    out
}

/// 宣言の .pyi を読んで判じる(focus が在ればその下の file だけ — 名指しの .hy の隣の .pyi は with_sibling_stubs が focus に足す)。
/// 読めない file は理由を返す。
pub fn find(root: &Path, selection: &FileSelection, focus: Option<&[PathBuf]>) -> (Vec<FileMismatches>, Vec<String>) {
    judge_files(
        root,
        selection.files.iter(),
        focus,
        |rel, path| wants(rel, path, selection),
        |rel, path, source| {
            let mismatches = judge_file(&rel, &path, &source).map_err(|reason| format!("{}: DOEFF145 の判定が読めない({})", rel, reason))?;
            Ok((!mismatches.is_empty()).then(|| FileMismatches { rel, path, source, mismatches }))
        },
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn names(hy: &str, stub: &str) -> Vec<(String, &'static str)> {
        judge(hy, stub, "a.pyi").expect("読める").into_iter().map(|m| (m.name, m.form)).collect()
    }

    #[test]
    fn mangle_matches_hy() {
        assert_eq!(mangle("Foo-bar"), "Foo_bar");
        assert_eq!(mangle("_a-b"), "_a_b");
        assert_eq!(mangle("foo?"), "hyx_fooXquestion_markX");
        assert_eq!(mangle("日本"), "日本");
    }

    #[test]
    fn kw_only_records_need_kw_only_in_the_stub() {
        let hy = "(defrecord Row (#^ str a))\n(defrecord row-two (#^ str b))\n\
                  (defclass [(dataclass :frozen True :kw-only True)] Manual [] (#^ int n))\n\
                  (defclass [(dataclass :frozen True)] Positional [] (#^ int n))\n\
                  (defrecord Fine (#^ str c))\n(defrecord NoDeco (#^ str d))\n#_(defrecord Gone (#^ str e))\n";
        let stub = "import dataclasses\nfrom dataclasses import dataclass\n\
                    @dataclass(frozen=True)\nclass Row:\n    a: str\n\
                    @dataclasses.dataclass\nclass row_two:\n    b: str\n\
                    @dataclass(frozen=True, kw_only=False)\nclass Manual:\n    n: int\n\
                    @dataclass(frozen=True)\nclass Positional:\n    n: int\n\
                    @dataclass(frozen=True, kw_only=True)\nclass Fine:\n    c: str\n\
                    class NoDeco:\n    d: str\n\
                    @dataclass\nclass Gone:\n    e: str\n";
        assert_eq!(
            names(hy, stub),
            vec![("Row".to_string(), DEFRECORD_FORM), ("row_two".to_string(), DEFRECORD_FORM), ("Manual".to_string(), KW_ONLY_DEFCLASS_FORM)]
        );
    }

    #[test]
    fn kw_only_must_be_the_literal_true() {
        let hy = "(defrecord A (#^ str a))\n(defrecord B (#^ str b))\n";
        let stub = "KW = True\n@dataclass(kw_only=KW)\nclass A: ...\n@dataclass(kw_only=True)\nclass B: ...\n";
        assert_eq!(names(hy, stub), vec![("A".to_string(), DEFRECORD_FORM)]);
    }

    #[test]
    fn unreadable_sides_are_reasons() {
        assert!(judge("(defrecord A", "class A: ...\n", "a.pyi").is_err());
        assert!(judge("(defrecord A)", "class A(:\n", "a.pyi").is_err());
    }
}
