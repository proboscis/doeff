//! DOEFF150・151: 使わないと決めた綴りと呼び(agora-redesign #1193 — agora-controllers の check_vocabulary・check_controller_clock の
//! 移し先)。
//!
//! 語と呼びの表は repo の architecture.hy の `:retired-words`・`:retired-calls` にだけ在り、ここには書かない。判定は file 1 つを
//! 読めば決まるので repo 全体の索引を組まない — 名指しの path(`focus`)が在ればその下の file だけを読み、無ければ宣言の glob の
//! 頭の dir だけを歩く。
//!   * DOEFF150 `:in lines` — 行ごとに、語として単独で在る :words(前後が英字・`_`・`-` でない所)と :patterns の正規表現。
//!     :rule-lines の綴りを含む行(規則そのものを述べる行)は数えない。数えるのは実際に使う code の中の綴りだけ(agora-redesign
//!     #1794・#1762 の決定 Q2-3 — 註・docstring・文書の中の綴りは使っているわけではない):
//!       - `.md` の file は数えない(file ごと)。
//!       - Hy は `;` の註(文字列の外の `;` から行末)と、`def…` の形の docstring(名と引数の列・任意の {…} の meta の後の最初の
//!         文字列で、後にまだ form が在る物)を数えない。
//!       - Python は `#` の註(文字列の外)と docstring(行の最初の非空白から始まる三重引用符の文字列)を数えない。
//!       - shell・toml・ほかの file は引用符の外の `#` の註(行頭か空白の後の `#` から行末 — `$#`・`${#…}` は註でない)を数えない。
//!       - どの種類でも、1 行目の shebang(`#!`)の行は数える(退役語 direct-shebang の対象)。
//!     記号・欄名と、docstring でない文字列は数える — command の文字列(`"cd x && PYTHONPATH=. hy"`)や env の key は実行される綴りなので。
//!     群に :contract-files が在れば、契約の綴り(契約の file のキーの名と enum / const の値)に在る :words の語は、次の 2 か所でだけ
//!     数えない(agora-redesign #1893 — 契約と wire の欄名は契約の綴りのまま書く):
//!       - 文字列で中身がその語ちょうどの物 — Hy の普通の文字列(`"mail"`)と、Python(`.py`・`.pyi`)の接頭辞の無い 1 行の文字列
//!         (`"mail"`・`'mail'`)。
//!       - Hy の defwire の本体の欄の定義の名(`(#^ str mail)`・`(setv #^ T mail v)` — 欄の読み方は doeff-indexer の hy_index::fields)。
//!     変数・引数・defrecord / defclass の欄・loop の変数・属性の読み(`x.mail`)は今どおり数え、:patterns は塗らない中身に当てる。
//!   * DOEFF150 `:in names` — 定義の名だけ(Hy は `def…` の形と `setv`・`val`・`var` の左辺・Python は def と class の名)。
//!   * DOEFF150 `:in paths` — file の名だけ(最後の `.` より前・dir の名と中身は見ない)。退役した名の file を置き直さない(#1369)。
//!   * DOEFF151 — Hy の file の `(呼び …)` の形の呼び(頭の記号が :calls のどれか)。註・文字列・`#_` で読み捨てた form は数えない。
//!     境目の部品(architecture.hy の `:boundary-parts`)の module の中では、呼びの綴りが生の副作用の目録で分類でき、その分類の
//!     触れる先を部品が `:touches` に宣言している呼びだけを当てない(agora-redesign #1894 — 生の時計の呼びを通す所は部品の宣言
//!     1 か所で決まり、DOEFF106 と同じ写し方なので 2 つの規則が食い違わない)。

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use doeff_indexer::hy_index::fields::record_field_targets;
use doeff_indexer::hy_index::reader::{Form, Node, Reader, StrKind};
use doeff_indexer::hy_index::{matches_pattern, RawCatalog};
use rayon::prelude::*;
use regex::Regex;
use walkdir::WalkDir;

use super::architecture::{mangle_dotted, RetiredCalls, RetiredWords, WordPlace, WorldTouch};
use super::names::module_of;
use super::relative_path;
use super::world_catalog::touch_of_raw;

/// 歩かない dir(隠し dir と生成物 — 宣言の glob の頭の dir より下で)。
const SKIPPED_DIRS: &[&str] = &["node_modules", "target", "__pycache__", "venv", "site-packages"];

/// 使わないと決めた綴りの当たり 1 つ。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WordHit {
    pub rel: String,
    /// 当たった綴りの byte の範囲。
    pub start: usize,
    pub end: usize,
    pub group: String,
    /// 当たった綴り(語か、正規表現に当たった字面)。
    pub spelling: String,
    /// 登録簿の鍵の細目(:words は語・:patterns は群の名)。
    pub detail: String,
    pub instead: String,
    pub place: WordPlace,
    /// :in names の時の定義の名。
    pub name: Option<String>,
}

/// 使わないと決めた呼びの当たり 1 つ。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CallHit {
    pub rel: String,
    pub start: usize,
    pub end: usize,
    pub group: String,
    pub call: String,
    pub instead: String,
}

/// 読んだ file 1 つの当たり(読めなければ理由)。
pub struct FileHits {
    pub rel: String,
    pub path: PathBuf,
    pub source: String,
    pub words: Vec<WordHit>,
    pub calls: Vec<CallHit>,
}

/// 境目の部品の中で当てない呼び(DOEFF151・agora-redesign #1894)— 部品の module(mangle した dotted の綴り)→ その中で当てない
/// 呼びの綴り。呼びの綴りが生の副作用の目録で分類でき、その分類の触れる先(DOEFF106 の boundary_allows と同じ `touch_of_raw`)を
/// 部品が `:touches` に宣言している時だけ当てない。目録で分類できない呼び(効果の Now など)は、どの部品の中でも当てる。
#[derive(Debug, Default)]
pub struct PartCalls {
    by_module: BTreeMap<String, BTreeSet<String>>,
}

impl PartCalls {
    /// 呼びの群・部品の module → 触れる先(`Architecture::boundary_touches`)・生の副作用の目録から組む。
    pub fn new(calls: &[RetiredCalls], boundary: &BTreeMap<String, Vec<WorldTouch>>, catalog: &RawCatalog) -> PartCalls {
        let classified: Vec<(&str, WorldTouch)> =
            calls.iter().flat_map(|group| group.calls.iter()).filter_map(|call| raw_touch(call, catalog).map(|touch| (call.as_str(), touch))).collect();
        let by_module = boundary
            .iter()
            .map(|(module, touches)| {
                let allowed: BTreeSet<String> =
                    classified.iter().filter(|(_, touch)| touches.contains(touch)).map(|(call, _)| call.to_string()).collect();
                (module.clone(), allowed)
            })
            .filter(|(_, allowed)| !allowed.is_empty())
            .collect();
        PartCalls { by_module }
    }

    /// file rel(根からの path)の中で呼び call を当てないか。
    fn allows(&self, rel: &str, call: &str) -> bool {
        self.by_module.get(&mangle_dotted(&module_of(rel))).is_some_and(|allowed| allowed.contains(call))
    }
}

/// 呼びの綴りを生の副作用の目録で分類し、触れる先の語へ写す(dotted の名前の pattern か組み込みの名 — 目録の ignored に当たる名と、
/// 分類できない名は None)。
fn raw_touch(call: &str, catalog: &RawCatalog) -> Option<WorldTouch> {
    if catalog.ignored.iter().any(|pattern| matches_pattern(call, pattern)) {
        return None;
    }
    catalog
        .categories
        .iter()
        .find(|entry| entry.patterns.iter().any(|pattern| matches_pattern(call, pattern)) || entry.builtins.iter().any(|name| name == call))
        .map(|entry| touch_of_raw(entry.category))
}

/// glob を repo の根に錨を下ろして当てる(`**` は 0 個以上の段・`*` は段の中の任意の綴り・`/` の無い型は根の直下の file)。
fn anchored(glob: &str, rel: &str) -> bool {
    let parts: Vec<&str> = glob.split('/').filter(|p| !p.is_empty()).collect();
    let path: Vec<&str> = rel.split('/').collect();
    super::segments_match(&parts, &path)
}

pub(super) fn selected(rel: &str, files: &[String], except: &[String]) -> bool {
    files.iter().any(|g| anchored(g, rel)) && !except.iter().any(|g| anchored(g, rel))
}

/// glob の頭の、`*` を含まない段(歩き始める所)。
fn literal_head(glob: &str) -> String {
    glob.split('/').filter(|p| !p.is_empty()).take_while(|p| !p.contains('*')).collect::<Vec<_>>().join("/")
}

/// path の下の file(path が file ならそれだけ)。隠し dir と生成物の dir は歩かない。
fn files_under(path: &Path) -> Vec<PathBuf> {
    if path.is_file() {
        return vec![path.to_path_buf()];
    }
    if !path.is_dir() {
        return Vec::new();
    }
    WalkDir::new(path)
        .follow_links(false)
        .into_iter()
        .filter_entry(|entry| {
            let name = entry.file_name().to_string_lossy();
            entry.depth() == 0 || !entry.file_type().is_dir() || !(name.starts_with('.') || SKIPPED_DIRS.contains(&name.as_ref()))
        })
        .filter_map(Result::ok)
        .filter(|entry| entry.file_type().is_file())
        .map(|entry| entry.into_path())
        .collect()
}

/// 宣言の glob(files)の候補の file(根からの path の順・重なりなし)— focus が在ればその下だけ、無ければ glob の頭の dir だけを
/// 歩く(file 1 つで判じる規則の共通の読み — DOEFF144・145・150・151)。glob の選別(selected)は呼び手がする。
pub(super) fn candidate_files<'g>(root: &Path, files: impl Iterator<Item = &'g String>, focus: Option<&[PathBuf]>) -> Vec<(String, PathBuf)> {
    let starts: Vec<PathBuf> = match focus {
        Some(paths) => paths.to_vec(),
        None => files.map(|g| root.join(literal_head(g))).collect(),
    };
    let mut found: Vec<(String, PathBuf)> = starts
        .iter()
        .flat_map(|start| files_under(start))
        .filter_map(|path| relative_path(root, &path))
        .map(|rel| {
            let path = root.join(&rel);
            (rel, path)
        })
        .collect();
    found.sort();
    found.dedup_by(|a, b| a.0 == b.0);
    found
}

/// 語として単独で在るか(前後の文字が英字・`_`・`-` でない)。
fn stands_alone(line: &str, at: usize, word: &str) -> bool {
    let glued = |c: char| c.is_ascii_alphabetic() || c == '_' || c == '-';
    let before = line[..at].chars().next_back().is_some_and(glued);
    let after = line[at + word.len()..].chars().next().is_some_and(glued);
    !before && !after
}

/// 綴り text の中の、語 word が単独で在る最初の位置。
fn find_word(text: &str, word: &str) -> Option<usize> {
    text.match_indices(word).map(|(at, _)| at).find(|&at| stands_alone(text, at, word))
}

/// 群を 1 つの行(か定義の名)に当てる。当たりごとに (相対の開始・終了・綴り・細目)。:words は words_text(契約の綴りの所を塗った
/// 中身 — 塗らない群は text と同じ)に、:patterns は text に当てる。2 つは同じ長さで byte の位置が揃う。
fn match_group(text: &str, words_text: &str, group: &RetiredWords, patterns: &[Regex]) -> Vec<(usize, usize, String, String)> {
    let mut out = Vec::new();
    for word in &group.words {
        if let Some(at) = find_word(words_text, word) {
            out.push((at, at + word.len(), word.clone(), word.clone()));
        }
    }
    // 正規表現は群で 1 件(同じ行に 2 つの型が当たっても 1 行と数える — 鍵の細目が群の名なので、件数は行の数)。
    if let Some(m) = patterns.iter().find_map(|pattern| pattern.find(text)) {
        out.push((m.start(), m.end(), m.as_str().to_string(), group.name.clone()));
    }
    out
}

/// 1 行目が shebang(`#!`)なら、その行の終わり(註を探し始める所)。shebang の行は、どの種類の file でも数える。
fn after_shebang(source: &str) -> usize {
    if source.starts_with("#!") {
        source.find('\n').map_or(source.len(), |at| at + 1)
    } else {
        0
    }
}

/// 行の終わり(`\n` の位置か source の終わり)。
fn line_end(source: &str, from: usize) -> usize {
    source[from..].find('\n').map_or(source.len(), |at| from + at)
}

/// Hy の form の木から、文字列の範囲(註を探す時に飛ばす)と定義の docstring の範囲を集める。`#_` で読み捨てた form は読み直す
/// (読み捨てた文字列の中の `;` を註と取り違えない)。
fn hy_strings_and_docstrings(source: &str, form: &Form, strings: &mut Vec<(usize, usize)>, docstrings: &mut Vec<(usize, usize)>) {
    if let Some(items) = form.paren_items() {
        if let Some(doc) = hy_docstring(source, items) {
            docstrings.push((doc.span.start, doc.span.end));
        }
    }
    match &form.node {
        Node::Str { .. } => strings.push((form.span.start, form.span.end)),
        Node::Seq { items, .. } => items.iter().for_each(|item| hy_strings_and_docstrings(source, item, strings, docstrings)),
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => hy_strings_and_docstrings(source, inner, strings, docstrings),
        Node::Annotated { annotation, target } => [annotation, target].into_iter().flatten().for_each(|part| hy_strings_and_docstrings(source, part, strings, docstrings)),
        Node::Discarded => {
            let mut reader = Reader::new(source, form.span.start + 2, form.span.end);
            for inner in &reader.read_all() {
                hy_strings_and_docstrings(source, inner, strings, docstrings);
            }
        }
        Node::Prefixed { inner: None, .. } | Node::Tagged { inner: None } | Node::Symbol | Node::Keyword | Node::Number => {}
    }
}

/// `def…` の形(頭の記号が def で始まる — defk・defn・deff・defclass・defhandler・deftest・defadr …)の docstring。名の前の decorator の
/// `[…]`、名(記号か `#^ 型 名`)、名の後の引数の `[…]` 1 つと meta の `{…}` を飛ばした最初の form が、普通の文字列か bracket 文字列で、
/// その後にまだ form が在る時だけ docstring とする(文字列だけが本体なら、それは答えの値)。
fn hy_docstring<'f>(source: &str, items: &'f [Form]) -> Option<&'f Form> {
    let items: Vec<&Form> = items.iter().filter(|f| !matches!(f.node, Node::Discarded)).collect();
    let (head, rest) = items.split_first()?;
    let defines = matches!(head.node, Node::Symbol) && source.get(head.span.start..head.span.end).is_some_and(|h| h.starts_with("def"));
    if !defines {
        return None;
    }
    let mut at = rest.iter().take_while(|f| f.bracket_items().is_some()).count();
    if !matches!(rest.get(at)?.node, Node::Symbol | Node::Annotated { .. }) {
        return None;
    }
    at += 1;
    if rest.get(at).is_some_and(|f| f.bracket_items().is_some()) {
        at += 1;
    }
    at += rest[at.min(rest.len())..].iter().take_while(|f| f.is_brace()).count();
    let doc = rest.get(at)?;
    let is_doc = matches!(doc.node, Node::Str { kind: StrKind::Plain | StrKind::Bracket, .. }) && rest.len() > at + 1;
    is_doc.then_some(*doc)
}

/// Hy の数えない範囲 — `;` の註(文字列の外の `;` から行末まで)と定義の docstring。
fn hy_uncounted(source: &str) -> Vec<(usize, usize)> {
    let mut strings = Vec::new();
    let mut out = Vec::new();
    let mut reader = Reader::new(source, 0, source.len());
    for form in &reader.read_all() {
        hy_strings_and_docstrings(source, form, &mut strings, &mut out);
    }
    strings.sort();
    let bytes = source.as_bytes();
    let mut pos = after_shebang(source);
    let mut next_string = 0;
    while pos < bytes.len() {
        while next_string < strings.len() && strings[next_string].1 <= pos {
            next_string += 1;
        }
        match strings.get(next_string) {
            Some(&(start, end)) if start <= pos => pos = end,
            _ if bytes[pos] == b';' => {
                let end = line_end(source, pos);
                out.push((pos, end));
                pos = end;
            }
            _ => pos += 1,
        }
    }
    out
}

/// Python の数えない範囲 — `#` の註(文字列の外)と docstring(行の最初の非空白から始まる三重引用符の文字列 — module・def・class の頭の
/// 文字列の文)。
fn python_uncounted(source: &str) -> Vec<(usize, usize)> {
    let bytes = source.as_bytes();
    let mut out = Vec::new();
    let mut pos = after_shebang(source);
    let mut line_start = pos;
    while pos < bytes.len() {
        let b = bytes[pos];
        if b == b'\n' {
            pos += 1;
            line_start = pos;
        } else if b == b'#' {
            let end = line_end(source, pos);
            out.push((pos, end));
            pos = end;
        } else if b == b'"' || b == b'\'' || (b.is_ascii_alphabetic() && python_string_prefix(bytes, pos).is_some()) {
            let start = pos;
            let quote_at = python_string_prefix(bytes, pos).unwrap_or(pos);
            let quote = bytes[quote_at];
            let triple = bytes.get(quote_at..quote_at + 3).is_some_and(|q| q.iter().all(|&c| c == quote));
            let end = python_string_end(bytes, quote_at, quote, triple);
            let at_line_head = source[line_start..start].trim().is_empty();
            if triple && at_line_head {
                out.push((start, end));
            }
            pos = end;
        } else if b.is_ascii_alphanumeric() || b == b'_' {
            // 名の途中の英字を文字列の接頭辞と取り違えない(`attr"…"` は無いが、`bar'` のような綴りを飛ばす)。
            while pos < bytes.len() && (bytes[pos].is_ascii_alphanumeric() || bytes[pos] == b'_') {
                pos += 1;
            }
        } else {
            pos += 1;
        }
    }
    out
}

/// pos から始まる Python の文字列の接頭辞(r・b・u・f の 1〜2 字)の後の引用符の位置(文字列でなければ None)。名の途中は呼び手が飛ばす。
fn python_string_prefix(bytes: &[u8], pos: usize) -> Option<usize> {
    let prefix = bytes[pos..].iter().take_while(|c| matches!(c, b'r' | b'R' | b'b' | b'B' | b'u' | b'U' | b'f' | b'F')).count();
    (prefix <= 2 && matches!(bytes.get(pos + prefix), Some(b'"' | b'\''))).then_some(pos + prefix)
}

/// 引用符 quote_at から始まる Python の文字列の終わり(閉じの後)。一重の文字列は行末で終わる(閉じない時)。
fn python_string_end(bytes: &[u8], quote_at: usize, quote: u8, triple: bool) -> usize {
    let mut pos = quote_at + if triple { 3 } else { 1 };
    while pos < bytes.len() {
        match bytes[pos] {
            b'\\' => pos += 2,
            b'\n' if !triple => return pos,
            c if c == quote && (!triple || bytes.get(pos..pos + 3).is_some_and(|q| q.iter().all(|&x| x == quote))) => {
                return pos + if triple { 3 } else { 1 };
            }
            _ => pos += 1,
        }
    }
    bytes.len()
}

/// shell・toml・ほかの file の数えない範囲 — 引用符の外の `#` の註(行頭か空白の後の `#` から行末まで — `$#`・`${#…}`・`a#b` は註でない)。
/// shell の file(`.sh`)は引用符が行を跨ぎ、here-document(`<<EOF` … `EOF`)の中身は数える。ほかの file の引用符は行の中だけ。
fn hash_uncounted(rel: &str, source: &str) -> Vec<(usize, usize)> {
    static HEREDOC: OnceLock<Regex> = OnceLock::new();
    let heredoc = HEREDOC.get_or_init(|| Regex::new(r#"(?:^|[^<])<<-?[ \t]*['"]?([A-Za-z_][A-Za-z0-9_]*)"#).expect("固定の正規表現"));
    let shell = rel.ends_with(".sh");
    let bytes = source.as_bytes();
    let mut out = Vec::new();
    let mut pos = after_shebang(source);
    let mut quote: Option<u8> = None;
    let mut heredoc_tag: Option<String> = None;
    let mut line_start = pos;
    // 今の行の註の頭(here-document の印は註の前の code だけで探す)。
    let mut comment_at: Option<usize> = None;
    while pos < bytes.len() {
        if let Some(tag) = &heredoc_tag {
            let end = line_end(source, pos);
            if source[pos..end].trim() == tag {
                heredoc_tag = None;
            }
            pos = end + 1;
            line_start = pos;
            continue;
        }
        let b = bytes[pos];
        match (quote, b) {
            (_, b'\n') => {
                if shell && quote.is_none() {
                    heredoc_tag = heredoc.captures(&source[line_start..comment_at.unwrap_or(pos)]).map(|c| c[1].to_string());
                }
                comment_at = None;
                if !shell {
                    quote = None;
                }
                pos += 1;
                line_start = pos;
            }
            (Some(q), c) if c == q => {
                quote = None;
                pos += 1;
            }
            (Some(b'"'), b'\\') => pos += 2,
            (Some(_), _) => pos += 1,
            (None, b'\\') => pos += 2,
            (None, b'"' | b'\'') => {
                quote = Some(b);
                pos += 1;
            }
            (None, b'#') if pos == line_start || bytes[pos - 1].is_ascii_whitespace() => {
                let end = line_end(source, pos);
                comment_at = Some(pos);
                out.push((pos, end));
                pos = end;
            }
            (None, _) => pos += 1,
        }
    }
    out
}

/// Python の source として読む file か — `.py` と型の宣言の `.pyi`(agora-redesign #1905: 註と docstring の塗り・契約の文字列・定義の名の
/// 3 か所が別々に拡張子を見ていて、`.pyi` を Python として扱う所と扱わない所が食い違った。判定はこの 1 か所)。
fn is_python(rel: &str) -> bool {
    rel.ends_with(".py") || rel.ends_with(".pyi")
}

/// :in lines で読む中身 — 数えない範囲(註・docstring)を空白で塗った source(byte の位置と行は元のまま)。`.md` の file は数えないので None。
fn counted_text(rel: &str, source: &str) -> Option<String> {
    if rel.ends_with(".md") {
        return None;
    }
    let uncounted = if rel.ends_with(".hy") {
        hy_uncounted(source)
    } else if is_python(rel) {
        python_uncounted(source)
    } else {
        hash_uncounted(rel, source)
    };
    let mut bytes = source.as_bytes().to_vec();
    for (start, end) in uncounted {
        let end = end.min(bytes.len());
        // 範囲の境は文字の境目(`;`・`#`・引用符・行末)なので、中を ASCII の空白で塗っても UTF-8 のまま。改行は残す。
        bytes[start..end].iter_mut().filter(|b| **b != b'\n').for_each(|b| *b = b' ');
    }
    Some(String::from_utf8(bytes).expect("文字の境目で塗った"))
}

/// Hy の form の木から、契約の綴りの候補の範囲を集める — 普通の文字列(`"…"`)の中身と、defwire の本体の欄の定義の名。
fn hy_contract_sites(source: &str, form: &Form, out: &mut Vec<(usize, usize)>) {
    if let Some(fields) = form.paren_items().and_then(|items| defwire_fields(source, items)) {
        out.extend(record_field_targets(source, &fields).into_iter().map(|target| (target.name.start, target.name.end)));
    }
    match &form.node {
        Node::Str { kind: StrKind::Plain, body } => out.push((body.start, body.end)),
        Node::Seq { items, .. } => items.iter().for_each(|item| hy_contract_sites(source, item, out)),
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => hy_contract_sites(source, inner, out),
        Node::Annotated { annotation, target } => [annotation, target].into_iter().flatten().for_each(|part| hy_contract_sites(source, part, out)),
        Node::Str { .. } | Node::Prefixed { inner: None, .. } | Node::Tagged { inner: None } | Node::Symbol | Node::Keyword | Node::Number | Node::Discarded => {}
    }
}

/// `(defwire 名 [基底]? "doc"? {meta}? 欄 …)` の欄の form の列(defwire でなければ None)— 頭の読み方は hy-index の record_def と同じ。
fn defwire_fields<'f>(source: &str, items: &'f [Form]) -> Option<Vec<&'f Form>> {
    let items: Vec<&Form> = items.iter().filter(|f| !matches!(f.node, Node::Discarded)).collect();
    let (head, rest) = items.split_first()?;
    if !matches!(head.node, Node::Symbol) || source.get(head.span.start..head.span.end) != Some("defwire") {
        return None;
    }
    let mut rest = rest.get(1..)?;
    if rest.first().is_some_and(|f| f.bracket_items().is_some()) {
        rest = &rest[1..];
    }
    if rest.first().is_some_and(|f| matches!(f.node, Node::Str { .. })) {
        rest = &rest[1..];
    }
    if rest.first().is_some_and(|f| f.is_brace()) {
        rest = &rest[1..];
    }
    Some(rest.to_vec())
}

/// Python の、接頭辞の無い 1 行の文字列(`"…"`・`'…'`)の中身の範囲 — 註(`#` から行末)・三重引用符の文字列・接頭辞つきの文字列は除く。
fn python_contract_sites(source: &str) -> Vec<(usize, usize)> {
    let bytes = source.as_bytes();
    let mut out = Vec::new();
    let mut pos = 0;
    while pos < bytes.len() {
        let b = bytes[pos];
        if b == b'#' {
            pos = line_end(source, pos);
        } else if b == b'"' || b == b'\'' || (b.is_ascii_alphabetic() && python_string_prefix(bytes, pos).is_some()) {
            let quote_at = python_string_prefix(bytes, pos).unwrap_or(pos);
            let quote = bytes[quote_at];
            let triple = bytes.get(quote_at..quote_at + 3).is_some_and(|q| q.iter().all(|&c| c == quote));
            let end = python_string_end(bytes, quote_at, quote, triple);
            let closed = end >= quote_at + 2 && bytes[end - 1] == quote;
            if quote_at == pos && !triple && closed {
                out.push((quote_at + 1, end - 1));
            }
            pos = end;
        } else if b.is_ascii_alphanumeric() || b == b'_' {
            while pos < bytes.len() && (bytes[pos].is_ascii_alphanumeric() || bytes[pos] == b'_') {
                pos += 1;
            }
        } else {
            pos += 1;
        }
    }
    out
}

/// 契約の綴りの語 words を数えない所を空白で塗った中身(counted と同じ長さ・同じ行)— Hy は普通の文字列で中身がその語ちょうどの物と
/// defwire の本体の欄の定義の名、Python(`.py`・`.pyi`)は接頭辞の無い 1 行の文字列で中身がその語ちょうどの物。契約の綴りの語が無い
/// 群とほかの種類の file は None(塗らない)。
fn contract_blanked(rel: &str, source: &str, counted: &str, words: &BTreeSet<&str>) -> Option<String> {
    if words.is_empty() {
        return None;
    }
    let sites = if rel.ends_with(".hy") {
        let mut sites = Vec::new();
        let mut reader = Reader::new(source, 0, source.len());
        for form in &reader.read_all() {
            hy_contract_sites(source, form, &mut sites);
        }
        sites
    } else if is_python(rel) {
        python_contract_sites(source)
    } else {
        return None;
    };
    let mut bytes = counted.as_bytes().to_vec();
    for (start, end) in sites.into_iter().filter(|&(start, end)| source.get(start..end).is_some_and(|text| words.contains(text))) {
        // 範囲は語ちょうど(改行を含まない・文字の境目)なので、ASCII の空白で塗っても UTF-8 のまま。
        bytes[start..end].iter_mut().for_each(|b| *b = b' ');
    }
    Some(String::from_utf8(bytes).expect("語の範囲ごと塗った"))
}

/// 行ごとの当たり(:in lines)。註・docstring を塗った中身(counted_text)に当て、:rule-lines は元の行で見る。群の :contract-files の
/// 綴りに在る語は、さらに契約の綴りの所(文字列の中身・defwire の欄の定義の名)を塗った中身で探す(contract_blanked)。
fn line_hits(rel: &str, source: &str, counted: Option<&str>, group: &RetiredWords, patterns: &[Regex]) -> Vec<WordHit> {
    let mut out = Vec::new();
    let Some(counted) = counted else {
        return out;
    };
    let blanked = contract_blanked(rel, source, counted, &group.contracts.words_in(&group.words));
    let words_text = blanked.as_deref().unwrap_or(counted);
    let mut offset = 0;
    for ((line, masked), words_line) in source.split_inclusive('\n').zip(counted.split_inclusive('\n')).zip(words_text.split_inclusive('\n')) {
        let body = line.trim_end_matches(['\n', '\r']);
        if !group.rule_lines.iter().any(|marker| body.contains(marker.as_str())) {
            for (start, end, spelling, detail) in match_group(masked.trim_end_matches(['\n', '\r']), words_line.trim_end_matches(['\n', '\r']), group, patterns) {
                out.push(WordHit {
                    rel: rel.to_string(),
                    start: offset + start,
                    end: offset + end,
                    group: group.name.clone(),
                    spelling,
                    detail,
                    instead: group.instead.clone(),
                    place: WordPlace::Lines,
                    name: None,
                });
            }
        }
        offset += line.len();
    }
    out
}

/// Hy の form の木から定義の名の記号を集める(`def…` の形と `setv`・`val`・`var` の最初の名 — decorator の `[…]` と `#^ 型` は飛ばす)。
fn hy_definition_names(source: &str, form: &Form, out: &mut Vec<(usize, usize)>) {
    let text = |f: &Form| source.get(f.span.start..f.span.end).unwrap_or("");
    if let Some(items) = form.paren_items() {
        let items: Vec<&Form> = items.iter().filter(|f| !matches!(f.node, Node::Discarded)).collect();
        let defines = items.first().is_some_and(|head| {
            matches!(head.node, Node::Symbol) && {
                let h = text(head);
                h.starts_with("def") || matches!(h, "setv" | "val" | "var")
            }
        });
        if defines {
            let name = items.iter().skip(1).find_map(|f| match &f.node {
                Node::Symbol => Some((f.span.start, f.span.end)),
                Node::Annotated { target: Some(target), .. } if matches!(target.node, Node::Symbol) => Some((target.span.start, target.span.end)),
                Node::Seq { .. } | Node::Annotated { .. } => None,
                _ => Some((0, 0)),
            });
            if let Some((start, end)) = name.filter(|(s, e)| e > s) {
                out.push((start, end));
            }
        }
    }
    match &form.node {
        Node::Seq { items, .. } => items.iter().for_each(|item| hy_definition_names(source, item, out)),
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => hy_definition_names(source, inner, out),
        Node::Annotated { annotation, target } => [annotation, target].into_iter().flatten().for_each(|part| hy_definition_names(source, part, out)),
        Node::Prefixed { inner: None, .. } | Node::Tagged { inner: None } | Node::Symbol | Node::Keyword | Node::Str { .. } | Node::Number | Node::Discarded => {}
    }
}

/// 定義の名の範囲(Hy と Python。ほかの file は無し)。
fn definition_names(rel: &str, source: &str) -> Vec<(usize, usize)> {
    let mut out = Vec::new();
    if rel.ends_with(".hy") {
        let mut reader = Reader::new(source, 0, source.len());
        for form in &reader.read_all() {
            hy_definition_names(source, form, &mut out);
        }
    } else if is_python(rel) {
        static PYTHON_DEFINITION: OnceLock<Regex> = OnceLock::new();
        let python = PYTHON_DEFINITION.get_or_init(|| Regex::new(r"(?m)^[ \t]*(?:async[ \t]+)?(?:def|class)[ \t]+([A-Za-z_][A-Za-z0-9_]*)").expect("固定の正規表現"));
        out.extend(python.captures_iter(source).filter_map(|c| c.get(1)).map(|m| (m.start(), m.end())));
    }
    out.sort();
    out
}

/// 定義の名の当たり(:in names)。
fn name_hits(rel: &str, source: &str, group: &RetiredWords, patterns: &[Regex]) -> Vec<WordHit> {
    let mut out = Vec::new();
    for (start, end) in definition_names(rel, source) {
        let name = &source[start..end];
        for (s, e, spelling, detail) in match_group(name, name, group, patterns) {
            out.push(WordHit {
                rel: rel.to_string(),
                start: start + s,
                end: start + e,
                group: group.name.clone(),
                spelling,
                detail,
                instead: group.instead.clone(),
                place: WordPlace::Names,
                name: Some(name.to_string()),
            });
        }
    }
    out
}

/// file の名の当たり(:in paths)。名は最後の `.` より前(`.` の無い名はそのまま)で、dir の名は見ない。当たりの位置は file の頭。
fn path_hits(rel: &str, group: &RetiredWords, patterns: &[Regex]) -> Vec<WordHit> {
    let file_name = rel.rsplit('/').next().unwrap_or(rel);
    let stem = file_name.rsplit_once('.').map_or(file_name, |(stem, _)| stem);
    match_group(stem, stem, group, patterns)
        .into_iter()
        .map(|(_, _, spelling, detail)| WordHit {
            rel: rel.to_string(),
            start: 0,
            end: 0,
            group: group.name.clone(),
            spelling,
            detail,
            instead: group.instead.clone(),
            place: WordPlace::Paths,
            name: None,
        })
        .collect()
}

/// Hy の form の木から、頭の記号が calls のどれかの呼びを集める。
fn hy_calls(source: &str, form: &Form, group: &RetiredCalls, rel: &str, out: &mut Vec<CallHit>) {
    if let Some(head) = form.paren_items().and_then(|items| items.iter().find(|f| !matches!(f.node, Node::Discarded))) {
        let spelled = source.get(head.span.start..head.span.end).unwrap_or("");
        if matches!(head.node, Node::Symbol) && group.calls.iter().any(|c| c == spelled) {
            out.push(CallHit {
                rel: rel.to_string(),
                start: head.span.start,
                end: head.span.end,
                group: group.name.clone(),
                call: spelled.to_string(),
                instead: group.instead.clone(),
            });
        }
    }
    match &form.node {
        Node::Seq { items, .. } => items.iter().for_each(|item| hy_calls(source, item, group, rel, out)),
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => hy_calls(source, inner, group, rel, out),
        Node::Annotated { annotation, target } => [annotation, target].into_iter().flatten().for_each(|part| hy_calls(source, part, group, rel, out)),
        Node::Prefixed { inner: None, .. } | Node::Tagged { inner: None } | Node::Symbol | Node::Keyword | Node::Str { .. } | Node::Number | Node::Discarded => {}
    }
}

/// 正規表現を組み終えた群(file ごとに組み直さない — agora の全体で 1 度だけ組む)。
pub struct Prepared<'a> {
    words: Vec<(&'a RetiredWords, Vec<Regex>)>,
    calls: &'a [RetiredCalls],
    parts: &'a PartCalls,
}

impl<'a> Prepared<'a> {
    pub fn new(words: &'a [RetiredWords], calls: &'a [RetiredCalls], parts: &'a PartCalls) -> Prepared<'a> {
        // 読む時(architecture.rs)に確かめた正規表現なので、ここで読めないことは無い。
        let words = words.iter().map(|g| (g, g.patterns.iter().filter_map(|p| Regex::new(p).ok()).collect())).collect();
        Prepared { words, calls, parts }
    }

    /// file rel をどれかの群が見るか。
    fn wants(&self, rel: &str) -> bool {
        self.words.iter().any(|(g, _)| selected(rel, &g.files, &g.except)) || self.calls.iter().any(|g| rel.ends_with(".hy") && selected(rel, &g.files, &g.except))
    }
}

/// file 1 つ(根からの path rel と中身)を全部の群に当てる(1 file の実行 — 正規表現はここで組む)。
pub fn judge(rel: &str, source: &str, words: &[RetiredWords], calls: &[RetiredCalls], parts: &PartCalls) -> (Vec<WordHit>, Vec<CallHit>) {
    judge_prepared(rel, source, &Prepared::new(words, calls, parts))
}

/// file 1 つを組み終えた群に当てる。
fn judge_prepared(rel: &str, source: &str, prepared: &Prepared) -> (Vec<WordHit>, Vec<CallHit>) {
    let mut word_hits = Vec::new();
    // 註・docstring を塗った中身は file ごとに 1 度だけ作る(:in lines の群が在る時だけ)。
    let mut counted: Option<Option<String>> = None;
    for (group, patterns) in prepared.words.iter().filter(|(g, _)| selected(rel, &g.files, &g.except)) {
        word_hits.extend(match group.place {
            WordPlace::Lines => line_hits(rel, source, counted.get_or_insert_with(|| counted_text(rel, source)).as_deref(), group, patterns),
            WordPlace::Names => name_hits(rel, source, group, patterns),
            WordPlace::Paths => path_hits(rel, group, patterns),
        });
    }
    let mut call_hits = Vec::new();
    let call_groups: Vec<&RetiredCalls> = prepared.calls.iter().filter(|g| rel.ends_with(".hy") && selected(rel, &g.files, &g.except)).collect();
    if !call_groups.is_empty() {
        let mut reader = Reader::new(source, 0, source.len());
        let forms = reader.read_all();
        for group in call_groups {
            for form in &forms {
                hy_calls(source, form, group, rel, &mut call_hits);
            }
        }
        call_hits.retain(|hit| !prepared.parts.allows(rel, &hit.call));
    }
    word_hits.sort_by_key(|h| (h.start, h.detail.clone()));
    call_hits.sort_by_key(|h| h.start);
    (word_hits, call_hits)
}

/// 宣言の群に当たる file を読んで判じる(focus が在ればその下の file だけ)。読めない file は理由を返す。
pub fn find(root: &Path, words: &[RetiredWords], calls: &[RetiredCalls], parts: &PartCalls, focus: Option<&[PathBuf]>) -> (Vec<FileHits>, Vec<String>) {
    let prepared = Prepared::new(words, calls, parts);
    let globs = words.iter().flat_map(|g| g.files.iter()).chain(calls.iter().flat_map(|g| g.files.iter()));
    judge_files(
        root,
        globs,
        focus,
        |rel, _| prepared.wants(rel),
        |rel, path, source| {
            let (word_hits, call_hits) = judge_prepared(&rel, &source, &prepared);
            Ok((!word_hits.is_empty() || !call_hits.is_empty()).then(|| FileHits { rel, path, source, words: word_hits, calls: call_hits }))
        },
    )
}

/// file 1 つで判じる規則(DOEFF144・145・150・151)の共通の読み — 宣言の glob の候補(focus が在ればその下だけ)のうち wants が
/// 選んだ file を並列に読み、judge に (根からの path・path・中身) を渡す。答えの在る file だけを候補の順に返し、読めない file と
/// judge が返した理由は errors に積む。
pub(super) fn judge_files<'g, R: Send>(
    root: &Path,
    globs: impl Iterator<Item = &'g String>,
    focus: Option<&[PathBuf]>,
    wants: impl Fn(&str, &Path) -> bool,
    judge: impl Fn(String, PathBuf, String) -> Result<Option<R>, String> + Sync,
) -> (Vec<R>, Vec<String>) {
    let wanted: Vec<(String, PathBuf)> = candidate_files(root, globs, focus).into_iter().filter(|(rel, path)| wants(rel, path)).collect();
    let judged: Vec<Result<Option<R>, String>> = wanted
        .into_par_iter()
        .map(|(rel, path)| match std::fs::read_to_string(&path) {
            Ok(source) => judge(rel, path, source),
            Err(error) => Err(format!("{}: 読めない: {}", rel, error)),
        })
        .collect();
    let mut found = Vec::new();
    let mut errors = Vec::new();
    for result in judged {
        match result {
            Ok(Some(one)) => found.push(one),
            Ok(None) => {}
            Err(error) => errors.push(error),
        }
    }
    (found, errors)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn words(words: &[&str], patterns: &[&str], place: WordPlace, rule_lines: &[&str]) -> RetiredWords {
        RetiredWords {
            name: "g".into(),
            words: words.iter().map(|w| w.to_string()).collect(),
            patterns: patterns.iter().map(|p| p.to_string()).collect(),
            files: vec!["**/*".into()],
            except: Vec::new(),
            rule_lines: rule_lines.iter().map(|w| w.to_string()).collect(),
            contracts: Default::default(),
            place,
            instead: "代わり".into(),
        }
    }

    /// 契約の綴り contract を持つ :in lines の群(契約の file の path は検の見本 — 綴りは読んだ後の形で渡す)。
    fn with_contract(words_list: &[&str], contract: &[&str]) -> RetiredWords {
        let mut group = words(words_list, &[], WordPlace::Lines, &[]);
        group.contracts = super::super::architecture::ContractSpellings {
            files: vec!["docs/contracts/wire.json".into()],
            spellings: contract.iter().map(|s| s.to_string()).collect(),
        };
        group
    }

    /// agora-redesign #1893: 契約の綴りに在る語は、Hy の文字列で中身がその語ちょうどの物と defwire の本体の欄の定義の名だけを数えない。
    /// 変数・引数・defrecord の欄・loop の変数・属性の読み・語を含む長い文字列は数える。契約に無い語は今どおり数える。
    #[test]
    fn contract_spellings_skip_exact_strings_and_defwire_fields_only() {
        let group = with_contract(&["mail", "letter"], &["mail", "kind"]);
        let quiet = "(defwire Carrier \"取った印\" {:names :camel} (#^ str job) (#^ str mail))\n\
                     (defwire Seen {:unknown :ignore} (setv #^ (| str None) mail None))\n\
                     (.append record chat id rows \"mail\")\n(setv row {\"kind\" \"mail\"})\n";
        assert!(spelled("a.hy", quiet, &group).is_empty(), "{:?}", spelled("a.hy", quiet, &group));
        let loud = "(setv mail 1)\n(defk send [mail] mail)\n(defrecord Row (#^ str mail))\n(lfor mail rows mail.id)\n\
                    (setv s status.carrier.mail)\n(setv t \"mail の宛先\")\n(defwire W (#^ str letter))\n(setv u \"letter\")\n";
        assert_eq!(spelled("a.hy", loud, &group), vec!["mail", "mail", "mail", "mail", "mail", "mail", "letter", "letter"]);
        // 同じ行の契約の文字列を飛ばしても、内部の名は数える(最初の当たりが文字列でも、その後の名を探す)。
        assert_eq!(spelled("a.hy", "(setv mail \"mail\")\n(get row \"mail\" mail)\n", &group), vec!["mail", "mail"]);
        let hits = judge("a.hy", "(get row \"mail\" mail)\n", &[group.clone()], &[], &PartCalls::default()).0;
        assert_eq!((hits[0].start, hits[0].end), (16, 20), "位置は文字列の後の名");
        // 契約の綴りの無い群は、文字列も defwire の欄も今どおり数える。
        let plain = words(&["mail"], &[], WordPlace::Lines, &[]);
        assert_eq!(spelled("a.hy", "(defwire C (#^ str mail))\n(f \"mail\")\n", &plain), vec!["mail", "mail"]);
    }

    /// agora-redesign #1893: Python(.py・.pyi)の接頭辞の無い 1 行の文字列で中身が契約の綴りの語ちょうどの物は数えない。名・属性・欄・
    /// 接頭辞つきの文字列・語を含む長い文字列は数える。md・json などほかの file は塗らない。
    #[test]
    fn contract_spellings_skip_exact_python_strings_only() {
        let group = with_contract(&["mail"], &["mail"]);
        let quiet = "MESSAGE_SOURCE_MAIL = \"mail\"\nINPUT_CARRIER_MAIL = 'mail'  # mail は註\nsend(kind=\"mail\")\n";
        for rel in ["a.py", "a.pyi"] {
            assert!(spelled(rel, quiet, &group).is_empty(), "{} {:?}", rel, spelled(rel, quiet, &group));
        }
        let loud = "mail = 1\nx = row.mail\nclass V:\n    mail: str\ny = f\"mail\"\nz = \"mail box\"\n";
        assert_eq!(spelled("a.py", loud, &group), vec!["mail", "mail", "mail", "mail", "mail"]);
        assert_eq!(spelled("a.json", "{\"kind\": \"mail\"}\n", &group), vec!["mail"], "json は塗らない");
    }

    #[test]
    fn globs_are_anchored_at_the_root() {
        assert!(anchored("README.md", "README.md"));
        assert!(!anchored("README.md", "docs/README.md"), "/ の無い型は根の直下だけ");
        assert!(anchored("controllers/**/*.hy", "controllers/a/b.hy"));
        assert!(anchored("controllers/**/*.hy", "controllers/b.hy"));
        assert!(!anchored("controllers/**/*.hy", "scripts/b.hy"));
        assert_eq!(literal_head("controllers/**/*.hy"), "controllers");
        assert_eq!(literal_head(".agents/code-quality.json"), ".agents/code-quality.json");
    }

    #[test]
    fn words_stand_alone_and_rule_lines_are_skipped() {
        let group = words(&["mail", "席", "auth home"], &[], WordPlace::Lines, &["使わない"]);
        let source = "email address\nmail を送る\n使わない語 = mail\n席へ届ける\nmailbox と mail-box\nauth home は退役\n";
        let (hits, _) = judge("a.txt", source, &[group], &[], &PartCalls::default());
        let found: Vec<(&str, &str)> = hits.iter().map(|h| (&source[h.start..h.end], h.detail.as_str())).collect();
        assert_eq!(found, vec![("mail", "mail"), ("席", "席"), ("auth home", "auth home")]);
    }

    #[test]
    fn patterns_hit_once_per_line_and_key_by_group() {
        let group = words(&[], &[r"会話\s*[（(]\s*(?:意味は|=)\s*agent", r"(?i)semantically\s+agent"], WordPlace::Lines, &[]);
        let (hits, _) = judge("a.txt", "会話(意味は agent)と 会話(= agent)は semantically agent\n会話は agent の手番の列を持つ\n", &[group], &[], &PartCalls::default());
        assert_eq!(hits.len(), 1, "同じ行に 2 つの型が当たっても 1 行と数える");
        assert_eq!(hits[0].detail, "g");
    }

    #[test]
    fn names_are_read_from_definitions_only() {
        let group = words(&[], &["(?i)conversation"], WordPlace::Names, &[]);
        let source = "(defk attend-conversation [x] \"conversation\")\n(defclass [(dataclass :frozen True)] ConversationRow [])\n\
                      (setv CONVERSATION-KIND \"conversation\")\n(defn #^ str chat-of [row] row.chat) ; conversation の綴りは註だけ\n\
                      (defrecord ChatSlice (#^ str chat))\n";
        let (hits, _) = judge("controllers/chat/rows.hy", source, &[group.clone()], &[], &PartCalls::default());
        let names: Vec<&str> = hits.iter().map(|h| h.name.as_deref().unwrap_or("")).collect();
        assert_eq!(names, vec!["attend-conversation", "ConversationRow", "CONVERSATION-KIND"]);
        let (python, _) = judge("controllers/chat/rows.py", "def read_conversation(x):\n    pass\nclass ChatRow:\n    pass\n", &[group], &[], &PartCalls::default());
        assert_eq!(python.iter().map(|h| h.name.as_deref().unwrap_or("")).collect::<Vec<_>>(), vec!["read_conversation"]);
    }

    #[test]
    fn paths_are_file_names_not_dirs_or_contents() {
        let group = words(&["worker", "design_request"], &[r"^dispatch"], WordPlace::Paths, &[]);
        let hit = |rel: &str| judge(rel, "(setv worker 1) ; worker の綴りは中身だけ\n", &[group.clone()], &[], &PartCalls::default()).0;
        let details = |rel: &str| hit(rel).iter().map(|h| h.detail.clone()).collect::<Vec<_>>();
        assert_eq!(details("controllers/automation/core/worker.hy"), vec!["worker"]);
        assert_eq!(details("controllers/automation/core/design_request.hy"), vec!["design_request"]);
        assert_eq!(details("controllers/automation/core/dispatch_probe.hy"), vec!["g"], ":patterns は群の名で鍵を作る");
        assert!(hit("controllers/automation/core/worker_pool.hy").is_empty(), "別の語の一部は数えない");
        assert!(hit("controllers/worker/program.hy").is_empty(), "dir の名は見ない");
        assert!(hit("controllers/automation/core/program.hy").is_empty(), "中身の綴りは見ない");
        let found = hit("controllers/automation/core/worker.hy");
        assert_eq!((found[0].start, found[0].end, found[0].place), (0, 0, WordPlace::Paths));
    }

    /// 当たった綴りの字面の並び(:in lines の群 group を file rel の中身 source に当てる)。
    fn spelled(rel: &str, source: &str, group: &RetiredWords) -> Vec<String> {
        judge(rel, source, &[group.clone()], &[], &PartCalls::default()).0.iter().map(|h| source[h.start..h.end].to_string()).collect()
    }

    /// agora-redesign #1794: 実際に使う code の中の綴り — Hy の記号・欄名・command の文字列・ほかの文字列 — は数える。
    #[test]
    fn hy_symbols_fields_and_command_strings_are_counted() {
        let group = words(&["ACP_CHECKOUT", "mail"], &[r"\bPYTHONPATH=", r#""PYTHONPATH""#], WordPlace::Lines, &[]);
        let source = "(setv ACP_CHECKOUT 1)\n(defrecord Row (#^ str mail))\n(setv row.mail 2)\n\
                      (defk run [] (RunProcess \"cd x && PYTHONPATH=. hy\"))\n(setv env {\"PYTHONPATH\" \".\"})\n";
        assert_eq!(spelled("a.hy", source, &group), vec!["ACP_CHECKOUT", "mail", "mail", "PYTHONPATH=", "\"PYTHONPATH\""]);
        // 定義の頭の文字列でも、それだけが本体(答えの値)なら docstring ではない。
        assert_eq!(spelled("a.hy", "(defk mail-of [] \"mail\")\n", &group), vec!["mail"]);
        // 定義でない form の最初の文字列・keyword の値の文字列は数える。
        assert_eq!(spelled("a.hy", "(print \"mail\" 1)\n(defadr x :title \"mail の宛先\" :body 1)\n", &group), vec!["mail", "mail"]);
    }

    /// agora-redesign #1794: 1 行目の shebang は、どの種類の file でも数える(退役語 direct-shebang の対象)。
    #[test]
    fn shebang_lines_are_counted_in_every_kind_of_file() {
        let group = words(&[], &[r"^#!\s*/usr/bin/env\s+(hy|python[0-9.]*)\b"], WordPlace::Lines, &[]);
        for rel in ["scripts/run.hy", "scripts/run.py", "scripts/run.sh", "scripts/run"] {
            assert_eq!(spelled(rel, "#!/usr/bin/env hy\n(print 1)\n", &group), vec!["#!/usr/bin/env hy"], "{}", rel);
        }
        assert!(spelled("scripts/run.sh", "echo 1\n#!/usr/bin/env hy\n", &group).is_empty(), "2 行目の #! は shell の註");
    }

    /// agora-redesign #1794: Hy の `;` の註と定義の docstring は数えない(文字列の中の `;` は註でない)。
    #[test]
    fn hy_comments_and_docstrings_are_not_counted() {
        let group = words(&["mail"], &[], WordPlace::Lines, &[]);
        let source = ";;; mail の頭の註\n(defk send [x]\n  {:pre [(: x int)]}\n  \"mail を送るため\n  (2 行目の mail)\"\n  (setv y x) ; mail は註\n  y)\n\
                      (defclass [(dataclass)] Box [Base] \"mail の箱\" (#^ int n))\n(defhandler h {:tags {}} \"mail の訳\" (Ask [k] (resume k)))\n\
                      (deftest test-mail-free \"mail の検\" (assert 1))\n(defn #^ int f [] \"mail\" 1)\n#_(defk g [] \"mail\" 1) ; mail\n";
        assert!(spelled("a.hy", source, &group).is_empty(), "{:?}", spelled("a.hy", source, &group));
        // 文字列の中の `;` の後は註でない。
        assert_eq!(spelled("a.hy", "(setv s \"a ; mail\")\n", &group), vec!["mail"]);
    }

    /// agora-redesign #1794: `.md` の file は :in lines で数えない(file ごと)。
    #[test]
    fn markdown_files_are_not_counted() {
        let group = words(&["mail", "ACP_CHECKOUT"], &[], WordPlace::Lines, &[]);
        assert!(spelled("docs/a.md", "# mail\n本文の mail と `ACP_CHECKOUT`\n```sh\nexport ACP_CHECKOUT=1\n```\n", &group).is_empty());
        // :in paths は md でも file の名を見る(変えない)。
        let paths = words(&["mail"], &[], WordPlace::Paths, &[]);
        assert_eq!(judge("docs/mail.md", "", &[paths], &[], &PartCalls::default()).0.len(), 1);
    }

    /// agora-redesign #1794: Python の `#` の註と docstring(行頭の三重引用符の文字列)は数えない。ほかの文字列と名は数える。
    #[test]
    fn python_comments_and_docstrings_are_not_counted() {
        let group = words(&["mail"], &[], WordPlace::Lines, &[]);
        let source = "\"\"\"mail の module\n\n2 行目の mail\"\"\"\nimport os  # mail は註\n\ndef f(x):\n    r'''mail の関数'''\n    s = \"# mail\"\n    return x.mail\n\n\
                      class C:\n    \"\"\"mail の class\"\"\"\n    t = 'it''s # not mail'\n";
        assert_eq!(spelled("a.py", source, &group), vec!["mail", "mail", "mail"], "文字列の中の # は註でない・属性の名は数える");
        assert_eq!(spelled("a.py", "x = f(\"\"\"mail\"\"\")\n", &group), vec!["mail"], "行の途中の三重引用符は docstring でない");
    }

    /// agora-redesign #1905: 型の宣言の `.pyi` も Python として読む — docstring と `#` の註の中の語は数えず、欄・変数・定義の名は数える。
    #[test]
    fn python_stub_files_are_read_as_python() {
        let group = words(&["mail"], &[], WordPlace::Lines, &[]);
        let source = "\"\"\"mail の型の宣言\"\"\"\nfrom typing import Any  # mail は註\n\nclass View:\n    \"\"\"mail の欄を持つ\"\"\"\n    mail: int\n\nmail_default: Any\nmail = 1\n";
        assert_eq!(spelled("a.pyi", source, &group), vec!["mail", "mail"], "docstring・註は数えず、欄 mail と変数 mail は数える");
        assert_eq!(spelled("a.pyi", source, &group), spelled("a.py", source, &group), ".pyi と .py は同じに読む");
        let names = words(&[], &["(?i)conversation"], WordPlace::Names, &[]);
        let (stub, _) = judge("controllers/chat/rows.pyi", "def read_conversation(x: int) -> None: ...\nclass ChatRow: ...\n", &[names], &[], &PartCalls::default());
        assert_eq!(stub.iter().map(|h| h.name.as_deref().unwrap_or("")).collect::<Vec<_>>(), vec!["read_conversation"], ".pyi の定義の名も読む");
    }

    /// agora-redesign #1794: shell・toml・ほかの file の `#` の註(引用符の外)は数えない。`$#`・`${#…}` と引用符の中の `#` は註でない。
    #[test]
    fn hash_comments_are_not_counted_in_shell_and_other_files() {
        let group = words(&["ACP_CHECKOUT"], &[r"\bPYTHONPATH="], WordPlace::Lines, &[]);
        let source = "# ACP_CHECKOUT の頭の註\nexport PYTHONPATH=. # ACP_CHECKOUT は註\necho \"# ACP_CHECKOUT\"\necho $# ACP_CHECKOUT\n\
                      echo ${#ACP_CHECKOUT}\necho a#ACP_CHECKOUT\ncat <<EOF\n# ACP_CHECKOUT は here-document の中身\nEOF\n";
        assert_eq!(spelled("run.sh", source, &group), vec!["PYTHONPATH=", "ACP_CHECKOUT", "ACP_CHECKOUT", "ACP_CHECKOUT", "ACP_CHECKOUT", "ACP_CHECKOUT"]);
        let toml = "# ACP_CHECKOUT の註\ncommand = \"PYTHONPATH=. hy x.hy\" # ACP_CHECKOUT\nkey = 'ACP_CHECKOUT'\n";
        assert_eq!(spelled(".agents/land-queue.toml", toml, &group), vec!["PYTHONPATH=", "ACP_CHECKOUT"]);
    }

    #[test]
    fn calls_are_heads_of_forms_not_comments_or_strings() {
        let group = RetiredCalls { name: "clock".into(), calls: vec!["Now".into(), "time.time".into()], files: vec!["**/*.hy".into()], except: Vec::new(), instead: "x".into() };
        let source = ";; 効果 (Now) は退役した\n(setv a (Now))\n(setv b \"(Now)\")\n(setv c (time.time))\n#_(Now)\n(setv d Now)\n";
        let (_, hits) = judge("a.hy", source, &[], &[group.clone()], &PartCalls::default());
        assert_eq!(hits.iter().map(|h| h.call.as_str()).collect::<Vec<_>>(), vec!["Now", "time.time"]);
        let (_, none) = judge("a.py", "Now()\n", &[], &[group], &PartCalls::default());
        assert!(none.is_empty(), "Python の file は数えない");
    }
}
