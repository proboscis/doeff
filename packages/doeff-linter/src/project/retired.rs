//! DOEFF150, 151: spellings and calls decided not to be used (the destination of agora-controllers' check_vocabulary and check_controller_clock,
//! which were moved here).
//!
//! The word and call tables live only in the repo's architecture.hy as `:retired-words` and `:retired-calls`, and are not written here. A verdict depends on a single file,
//! so no repo-wide index is built — if a named path (`focus`) is given only files under it are read, otherwise only the leading dir of the declared glob
//! is walked.
//!   * DOEFF150 `:in lines` — per line, :words standing alone as words (not preceded or followed by a letter, `_`, or `-`) and :patterns regular expressions.
//!     Lines containing a :rule-lines spelling (lines that state the rule itself) are not counted. Only spellings in code that is actually used are counted (
//!     decision Q2-3 of #1762 — spellings in comments, docstrings, and documents are not usage):
//!       - `.md` files are not counted (per file).
//!       - In Hy, `;` comments (from a `;` outside a string to end of line) and docstrings of `def…` forms (the first string after the name, the argument list, and an optional {…} meta,
//!         when more forms follow it) are not counted.
//!       - In Python, `#` comments (outside strings) and docstrings (triple-quoted strings starting at the first non-blank of a line) are not counted.
//!       - In shell, toml, and other files, `#` comments outside quotes (from a `#` at line start or after whitespace to end of line — `$#` and `${#…}` are not comments) are not counted.
//!       - For every kind, a first-line shebang (`#!`) is counted (target of the retired word direct-shebang).
//!     Symbols, field names, and strings that are not docstrings are counted — command strings (`"cd x && PYTHONPATH=. hy"`) and env keys are spellings that get executed.
//!     If a group has :contract-files, the :words that appear in contract spellings (key names of contract files and enum / const values) are not
//!     counted in the following 3 places only (contract and wire field names are written in the contract's spelling):
//!       - A string whose content is exactly that word — an ordinary Hy string (`"mail"`) and a Python (`.py`, `.pyi`) unprefixed one-line string
//!         (`"mail"`, `'mail'`).
//!       - The name of a field definition in the body of a Hy defwire (`(#^ str mail)`, `(setv #^ T mail v)` — how fields are read is doeff-indexer's hy_index::fields).
//!       - In the strings of the `:reads` / `:writes` sets on a Hy defsystem job line (`#{"record:mail"}` — spelled `<store>:<name>`), the name part after the first
//!         `:` that is exactly that word (the name is a record kind or table name, written in the contract's spelling).
//!     Variables, arguments, defrecord / defclass fields, loop variables, and attribute reads (`x.mail`) are counted as before; :patterns are matched against the content with nothing blanked.
//!   * DOEFF150 `:in names` — definition names only (in Hy, `def…` forms and the left side of `setv`, `val`, `var`; in Python, def and class names).
//!   * DOEFF150 `:in paths` — file names only (before the last `.`; dir names and contents are not looked at). Files with retired names must not be put back (#1369).
//!   * DOEFF151 — calls of the form `(call …)` in Hy files (the head symbol is one of :calls). Comments, strings, and forms discarded by `#_` are not counted.
//!     Inside modules of boundary parts (architecture.hy's `:boundary-parts`), only calls whose spelling can be classified by the raw side-effect catalog, and whose classification's
//!     target the part declares in `:touches`, are not matched (where raw clock calls are allowed is decided in one place, the part's declaration,
//!     and it is copied the same way as DOEFF106, so the two rules do not disagree).

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use doeff_indexer::hy_index::fields::record_field_targets;
use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader, StrKind};
use doeff_indexer::hy_index::{matches_pattern, RawCatalog};
use rayon::prelude::*;
use regex::Regex;
use walkdir::WalkDir;

use super::architecture::{mangle_dotted, RetiredCalls, RetiredWords, WordPlace, WorldTouch};
use super::names::module_of;
use super::paths::relative_path;
use super::world_catalog::touch_of_raw;

/// Dirs not walked (hidden dirs and generated output — below the leading dir of the declared glob).
const SKIPPED_DIRS: &[&str] = &["node_modules", "target", "__pycache__", "venv", "site-packages"];

/// One hit of a spelling decided not to be used.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WordHit {
    pub rel: String,
    /// Byte range of the matched spelling.
    pub start: usize,
    pub end: usize,
    pub group: String,
    /// The matched spelling (a word, or the text matched by a regular expression).
    pub spelling: String,
    /// Detail of the registry key (:words is the word, :patterns is the group name).
    pub detail: String,
    pub instead: String,
    pub place: WordPlace,
    /// The definition name for :in names.
    pub name: Option<String>,
}

/// One hit of a call decided not to be used.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CallHit {
    pub rel: String,
    pub start: usize,
    pub end: usize,
    pub group: String,
    pub call: String,
    pub instead: String,
}

/// The hits of one file read (or the reason it could not be read).
pub struct FileHits {
    pub rel: String,
    pub path: PathBuf,
    pub source: String,
    pub words: Vec<WordHit>,
    pub calls: Vec<CallHit>,
}

/// Calls not matched inside boundary parts (DOEFF151) — part module (mangled dotted spelling) -> the call
/// spellings not matched inside it. A call is not matched only when its spelling can be classified by the raw side-effect catalog and the part declares the classification's target (the same `touch_of_raw` as DOEFF106's boundary_allows)
/// in `:touches`. Calls the catalog cannot classify (such as the effect Now) are matched inside any part.
#[derive(Debug, Default)]
pub struct PartCalls {
    by_module: BTreeMap<String, BTreeSet<String>>,
}

impl PartCalls {
    /// Built from the call groups, the part modules -> targets (`Architecture::boundary_touches`), and the raw side-effect catalog.
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

    /// Whether call `call` is not matched inside file rel (path from the root).
    fn allows(&self, rel: &str, call: &str) -> bool {
        self.by_module.get(&mangle_dotted(&module_of(rel))).is_some_and(|allowed| allowed.contains(call))
    }
}

/// Classify a call spelling by the raw side-effect catalog and map it to a target word (a dotted-name pattern or a builtin name — names matching the catalog's ignored,
/// and names that cannot be classified, give None).
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

/// Match a glob anchored at the repo root (`**` is zero or more levels, `*` is any spelling within a level, a pattern without `/` is a file directly under the root).
fn anchored(glob: &str, rel: &str) -> bool {
    let parts: Vec<&str> = glob.split('/').filter(|p| !p.is_empty()).collect();
    let path: Vec<&str> = rel.split('/').collect();
    super::paths::segments_match(&parts, &path)
}

pub(super) fn selected(rel: &str, files: &[String], except: &[String]) -> bool {
    files.iter().any(|g| anchored(g, rel)) && !except.iter().any(|g| anchored(g, rel))
}

/// The leading level(s) of a glob that contain no `*` (where the walk starts).
fn literal_head(glob: &str) -> String {
    glob.split('/').filter(|p| !p.is_empty()).take_while(|p| !p.contains('*')).collect::<Vec<_>>().join("/")
}

/// Files under path (just the file if path is a file). Hidden dirs and generated dirs are not walked.
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

/// Candidate files for the declared glob (files) (in order of path from the root, no overlap) — only under focus if given, otherwise only the leading dir of the glob is
/// walked (the common reading for rules judged per file — DOEFF144, 145, 150, 151). The glob selection (selected) is done by the caller.
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

/// Whether it stands alone as a word (the characters before and after are not letters, `_`, or `-`).
fn stands_alone(line: &str, at: usize, word: &str) -> bool {
    let glued = |c: char| c.is_ascii_alphabetic() || c == '_' || c == '-';
    let before = line[..at].chars().next_back().is_some_and(glued);
    let after = line[at + word.len()..].chars().next().is_some_and(glued);
    !before && !after
}

/// The first position in spelling text where word stands alone.
fn find_word(text: &str, word: &str) -> Option<usize> {
    text.match_indices(word).map(|(at, _)| at).find(|&at| stands_alone(text, at, word))
}

/// Match a group against one line (or a definition name). Per hit: (relative start, end, spelling, detail). :words are matched against words_text (the content with the contract-spelling places blanked
/// — the same as text for groups that blank nothing), :patterns against text. The two have the same length and aligned byte positions.
fn match_group(text: &str, words_text: &str, group: &RetiredWords, patterns: &[Regex]) -> Vec<(usize, usize, String, String)> {
    let mut out = Vec::new();
    for word in &group.words {
        if let Some(at) = find_word(words_text, word) {
            out.push((at, at + word.len(), word.clone(), word.clone()));
        }
    }
    // A regular expression counts once per group (even if two patterns match the same line it counts as one line — the key detail is the group name, so the count is the number of lines).
    if let Some(m) = patterns.iter().find_map(|pattern| pattern.find(text)) {
        out.push((m.start(), m.end(), m.as_str().to_string(), group.name.clone()));
    }
    out
}

/// If the first line is a shebang (`#!`), the end of that line (where the comment search starts). The shebang line is counted for every kind of file.
fn after_shebang(source: &str) -> usize {
    if source.starts_with("#!") {
        source.find('\n').map_or(source.len(), |at| at + 1)
    } else {
        0
    }
}

/// End of line (the position of `\n`, or the end of the source).
fn line_end(source: &str, from: usize) -> usize {
    source[from..].find('\n').map_or(source.len(), |at| from + at)
}

/// From the Hy form tree, collect string ranges (skipped when looking for comments) and definition docstring ranges. Forms discarded by `#_` are re-read
/// (so a `;` inside a discarded string is not mistaken for a comment).
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

/// The docstring of a `def…` form (head symbol starting with def — defk, defn, deff, defclass, defhandler, deftest, defadr …). The first form after skipping the decorator `[…]` before the name,
/// the name (a symbol or `#^ type name`), and one argument `[…]` and meta `{…}` after the name, is a docstring only if it is an ordinary string or bracket string
/// and more forms follow it (if the string is the whole body, it is the answer value).
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

/// Uncounted ranges for Hy — `;` comments (from a `;` outside a string to end of line) and definition docstrings.
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

/// Uncounted ranges for Python — `#` comments (outside strings) and docstrings (triple-quoted strings starting at the first non-blank of a line — a string statement at the head of a module, def, or class
/// ).
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
            // Do not mistake letters in the middle of a name for a string prefix (there is no `attr"…"`, but skip spellings like `bar'`).
            while pos < bytes.len() && (bytes[pos].is_ascii_alphanumeric() || bytes[pos] == b'_') {
                pos += 1;
            }
        } else {
            pos += 1;
        }
    }
    out
}

/// The position of the quote after a Python string prefix (1-2 characters of r, b, u, f) starting at pos (None if it is not a string). The caller skips mid-name positions.
fn python_string_prefix(bytes: &[u8], pos: usize) -> Option<usize> {
    let prefix = bytes[pos..].iter().take_while(|c| matches!(c, b'r' | b'R' | b'b' | b'B' | b'u' | b'U' | b'f' | b'F')).count();
    (prefix <= 2 && matches!(bytes.get(pos + prefix), Some(b'"' | b'\''))).then_some(pos + prefix)
}

/// The end (after the closing quote) of the Python string starting at quote at quote_at. A single-quoted string ends at end of line (when unclosed).
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

/// Uncounted ranges for shell, toml, and other files — `#` comments outside quotes (from a `#` at line start or after whitespace to end of line — `$#`, `${#…}`, and `a#b` are not comments).
/// In shell files (`.sh`) quotes span lines, and the content of a here-document (`<<EOF` … `EOF`) is counted. In other files quotes are within a line only.
fn hash_uncounted(rel: &str, source: &str) -> Vec<(usize, usize)> {
    static HEREDOC: OnceLock<Regex> = OnceLock::new();
    let heredoc = HEREDOC.get_or_init(|| Regex::new(r#"(?:^|[^<])<<-?[ \t]*['"]?([A-Za-z_][A-Za-z0-9_]*)"#).expect("fixed regular expression"));
    let shell = rel.ends_with(".sh");
    let bytes = source.as_bytes();
    let mut out = Vec::new();
    let mut pos = after_shebang(source);
    let mut quote: Option<u8> = None;
    let mut heredoc_tag: Option<String> = None;
    let mut line_start = pos;
    // The head of the comment on the current line (here-document markers are looked for only in the code before the comment).
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

/// Whether the file is read as Python source — `.py` and the type-declaration `.pyi` (comment and docstring blanking, contract strings, and definition names
/// each looked at the extension separately, and the places that treated `.pyi` as Python disagreed with those that did not. The decision is in this one place).
fn is_python(rel: &str) -> bool {
    rel.ends_with(".py") || rel.ends_with(".pyi")
}

/// Content read for :in lines — the source with uncounted ranges (comments, docstrings) blanked with spaces (byte positions and lines unchanged). None for `.md` files, which are not counted.
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
        // Range boundaries are character boundaries (`;`, `#`, quotes, end of line), so blanking the inside with ASCII spaces keeps it valid UTF-8. Newlines are kept.
        bytes[start..end].iter_mut().filter(|b| **b != b'\n').for_each(|b| *b = b' ');
    }
    Some(String::from_utf8(bytes).expect("blanked at character boundaries"))
}

/// From the Hy form tree, collect ranges of contract-spelling candidates — the content of ordinary strings (`"…"`), the names of field definitions in a defwire body, and
/// the name part of the `:reads` / `:writes` strings on a defsystem job line.
fn hy_contract_sites(source: &str, form: &Form, out: &mut Vec<(usize, usize)>) {
    if let Some(fields) = form.paren_items().and_then(|items| defwire_fields(source, items)) {
        out.extend(record_field_targets(source, &fields).into_iter().map(|target| (target.name.start, target.name.end)));
    }
    if let Some(items) = form.paren_items() {
        system_io_name_sites(source, items, out);
    }
    match &form.node {
        Node::Str { kind: StrKind::Plain, body } => out.push((body.start, body.end)),
        Node::Seq { items, .. } => items.iter().for_each(|item| hy_contract_sites(source, item, out)),
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => hy_contract_sites(source, inner, out),
        Node::Annotated { annotation, target } => [annotation, target].into_iter().flatten().for_each(|part| hy_contract_sites(source, part, out)),
        Node::Str { .. } | Node::Prefixed { inner: None, .. } | Node::Tagged { inner: None } | Node::Symbol | Node::Keyword | Node::Number | Node::Discarded => {}
    }
}

/// The list of field forms of `(defwire name [base]? "doc"? {meta}? field …)` (None if not a defwire) — the head is read the same as hy-index's record_def.
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

/// For a defsystem job line `(defsystem name [args] "doc"? (job …) …)`, the `:reads` / `:writes` sets (`#{"<store>:<name>" …}` — a doeff field),
/// the range of the name after the first `:` in each string (adds nothing if not a defsystem). The store spelling (records, record,
/// ledger) is not a contract word, so only the name part becomes a contract-spelling candidate (#3506 — the name mail in `"record:mail"` is a contract spelling of a record kind).
fn system_io_name_sites(source: &str, items: &[Form], out: &mut Vec<(usize, usize)>) {
    let Some(head) = items.first() else { return };
    if !matches!(head.node, Node::Symbol) || source.get(head.span.start..head.span.end) != Some("defsystem") {
        return;
    }
    for row in items.iter().filter_map(Form::paren_items) {
        for pair in row.windows(2) {
            let key = source.get(pair[0].span.start..pair[0].span.end);
            if !matches!(pair[0].node, Node::Keyword) || !matches!(key, Some(":reads" | ":writes")) {
                continue;
            }
            let Node::Seq { delim: Delim::Set, items: spellings } = &pair[1].node else { continue };
            for spelling in spellings {
                if let Node::Str { kind: StrKind::Plain, body } = &spelling.node {
                    if let Some(colon) = source.get(body.start..body.end).and_then(|text| text.find(':')) {
                        out.push((body.start + colon + 1, body.end));
                    }
                }
            }
        }
    }
}

/// Content ranges of Python unprefixed one-line strings (`"…"`, `'…'`) — excluding comments (`#` to end of line), triple-quoted strings, and prefixed strings.
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

/// The content with the places that do not count the contract-spelling words blanked with spaces (same length and lines as counted) — in Hy, ordinary strings whose content is exactly that word, and
/// names of field definitions in a defwire body and the name part of defsystem `:reads` / `:writes` strings that are exactly that word; in Python (`.py`, `.pyi`), unprefixed one-line strings whose content is exactly that word. None for groups
/// with no contract-spelling words and for other kinds of file (nothing blanked).
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
        // Ranges are exactly a word (no newline, on character boundaries), so blanking with ASCII spaces keeps it valid UTF-8.
        bytes[start..end].iter_mut().for_each(|b| *b = b' ');
    }
    Some(String::from_utf8(bytes).expect("blanked the whole word range"))
}

/// Per-line hits (:in lines). Matched against the content with comments and docstrings blanked (counted_text), with :rule-lines checked against the original lines. For words in the group's :contract-files
/// spellings, search further in the content with the contract-spelling places (string content, defwire field definition names) blanked (contract_blanked).
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

/// From the Hy form tree, collect the symbols of definition names (the first name of `def…` forms and `setv`, `val`, `var` — the decorator `[…]` and `#^ type` are skipped).
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

/// Ranges of definition names (Hy and Python; none for other files).
fn definition_names(rel: &str, source: &str) -> Vec<(usize, usize)> {
    let mut out = Vec::new();
    if rel.ends_with(".hy") {
        let mut reader = Reader::new(source, 0, source.len());
        for form in &reader.read_all() {
            hy_definition_names(source, form, &mut out);
        }
    } else if is_python(rel) {
        static PYTHON_DEFINITION: OnceLock<Regex> = OnceLock::new();
        let python = PYTHON_DEFINITION.get_or_init(|| Regex::new(r"(?m)^[ \t]*(?:async[ \t]+)?(?:def|class)[ \t]+([A-Za-z_][A-Za-z0-9_]*)").expect("fixed regular expression"));
        out.extend(python.captures_iter(source).filter_map(|c| c.get(1)).map(|m| (m.start(), m.end())));
    }
    out.sort();
    out
}

/// Hits of definition names (:in names).
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

/// Hits of file names (:in paths). The name is before the last `.` (a name without `.` is used as is), and dir names are not looked at. The hit position is the head of the file.
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

/// From the Hy form tree, collect calls whose head symbol is one of calls.
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

/// Groups with regular expressions already built (not rebuilt per file — built only once across all of agora).
pub struct Prepared<'a> {
    words: Vec<(&'a RetiredWords, Vec<Regex>)>,
    calls: &'a [RetiredCalls],
    parts: &'a PartCalls,
}

impl<'a> Prepared<'a> {
    pub fn new(words: &'a [RetiredWords], calls: &'a [RetiredCalls], parts: &'a PartCalls) -> Prepared<'a> {
        // These are regular expressions already checked at read time (architecture.rs), so failing to read them here cannot happen.
        let words = words.iter().map(|g| (g, g.patterns.iter().filter_map(|p| Regex::new(p).ok()).collect())).collect();
        Prepared { words, calls, parts }
    }

    /// Whether any group looks at file rel.
    fn wants(&self, rel: &str) -> bool {
        self.words.iter().any(|(g, _)| selected(rel, &g.files, &g.except)) || self.calls.iter().any(|g| rel.ends_with(".hy") && selected(rel, &g.files, &g.except))
    }
}

/// Match one file (path rel from the root and its content) against all groups (a single-file run — the regular expressions are built here).
pub fn judge(rel: &str, source: &str, words: &[RetiredWords], calls: &[RetiredCalls], parts: &PartCalls) -> (Vec<WordHit>, Vec<CallHit>) {
    judge_prepared(rel, source, &Prepared::new(words, calls, parts))
}

/// Match one file against the groups already built.
fn judge_prepared(rel: &str, source: &str, prepared: &Prepared) -> (Vec<WordHit>, Vec<CallHit>) {
    let mut word_hits = Vec::new();
    // The content with comments and docstrings blanked is built only once per file (only when there is an :in lines group).
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

/// Read and judge the files matching the declared groups (only files under focus if given). Files that cannot be read return a reason.
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

/// The common reading for rules judged per file (DOEFF144, 145, 150, 151) — read in parallel the files that wants selected among the declared glob candidates (only under focus if given),
/// and pass (path from the root, path, content) to judge. Return only the files that have an answer, in candidate order; files that cannot be read and
/// reasons returned by judge are pushed onto errors.
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
            Err(error) => Err(format!("{}: cannot read: {}", rel, error)),
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
            instead: "instead".into(),
        }
    }

    /// An :in lines group with contract spelling contract (the contract file path is a sample for the test — spellings are passed in their post-read form).
    fn with_contract(words_list: &[&str], contract: &[&str]) -> RetiredWords {
        let mut group = words(words_list, &[], WordPlace::Lines, &[]);
        group.contracts = super::super::architecture::ContractSpellings {
            files: vec!["docs/contracts/wire.json".into()],
            spellings: contract.iter().map(|s| s.to_string()).collect(),
        };
        group
    }

    /// Words in contract spellings are not counted only in Hy strings whose content is exactly that word and in defwire body field definition names.
    /// Variables, arguments, defrecord fields, loop variables, attribute reads, and long strings containing the word are counted. Words not in the contract are counted as before.
    #[test]
    fn contract_spellings_skip_exact_strings_and_defwire_fields_only() {
        let group = with_contract(&["mail", "letter"], &["mail", "kind"]);
        let quiet = "(defwire Carrier \"a received mark\" {:names :camel} (#^ str job) (#^ str mail))\n\
                     (defwire Seen {:unknown :ignore} (setv #^ (| str None) mail None))\n\
                     (.append record chat id rows \"mail\")\n(setv row {\"kind\" \"mail\"})\n";
        assert!(spelled("a.hy", quiet, &group).is_empty(), "{:?}", spelled("a.hy", quiet, &group));
        let loud = "(setv mail 1)\n(defk send [mail] mail)\n(defrecord Row (#^ str mail))\n(lfor mail rows mail.id)\n\
                    (setv s status.carrier.mail)\n(setv t \"mail recipient\")\n(defwire W (#^ str letter))\n(setv u \"letter\")\n";
        assert_eq!(spelled("a.hy", loud, &group), vec!["mail", "mail", "mail", "mail", "mail", "mail", "letter", "letter"]);
        // Even if a contract string on the same line is skipped, inner names are counted (even if the first hit is a string, the later name is searched for).
        assert_eq!(spelled("a.hy", "(setv mail \"mail\")\n(get row \"mail\" mail)\n", &group), vec!["mail", "mail"]);
        let hits = judge("a.hy", "(get row \"mail\" mail)\n", &[group.clone()], &[], &PartCalls::default()).0;
        assert_eq!((hits[0].start, hits[0].end), (16, 20), "the position is that of the name after the string");
        // A group with no contract spelling counts strings and defwire fields as before.
        let plain = words(&["mail"], &[], WordPlace::Lines, &[]);
        assert_eq!(spelled("a.hy", "(defwire C (#^ str mail))\n(f \"mail\")\n", &plain), vec!["mail", "mail"]);
    }

    /// For the `:reads` / `:writes` strings (`<store>:<name>`) on a defsystem job line, if the name part is exactly a contract-spelling word,
    /// it is not counted. Names of words not in the contract, the same strings outside fields, and the same fields in a non-defsystem form are counted as before.
    #[test]
    fn contract_spellings_skip_the_name_part_of_system_io_spellings_only() {
        let group = with_contract(&["mail", "letter"], &["mail"]);
        let quiet = "(defsystem s [#^ T f]\n  \"doc\"\n  (a (job-a f) :needs #{\"agent\"}\n     :reads #{\"record:mail\" \"records:message\"}\n     :writes #{\"record:mail\"}))\n";
        assert!(spelled("a.hy", quiet, &group).is_empty(), "{:?}", spelled("a.hy", quiet, &group));
        let loud = "(defsystem s [f]\n  (a (job-a f)\n     :reads #{\"record:letter\"}\n     :environ {\"record:mail\" \"\"}))\n\
                    (val k \"record:mail\")\n(defk t [f] (a (job f) :reads #{\"record:mail\"}))\n";
        assert_eq!(spelled("a.hy", loud, &group), vec!["letter", "mail", "mail", "mail"]);
    }

    /// Python (.py, .pyi) unprefixed one-line strings whose content is exactly a contract-spelling word are not counted. Names, attributes, fields,
    /// prefixed strings, and long strings containing the word are counted. Other files such as md and json are not blanked.
    #[test]
    fn contract_spellings_skip_exact_python_strings_only() {
        let group = with_contract(&["mail"], &["mail"]);
        let quiet = "MESSAGE_SOURCE_MAIL = \"mail\"\nINPUT_CARRIER_MAIL = 'mail'  # mail is a comment\nsend(kind=\"mail\")\n";
        for rel in ["a.py", "a.pyi"] {
            assert!(spelled(rel, quiet, &group).is_empty(), "{} {:?}", rel, spelled(rel, quiet, &group));
        }
        let loud = "mail = 1\nx = row.mail\nclass V:\n    mail: str\ny = f\"mail\"\nz = \"mail box\"\n";
        assert_eq!(spelled("a.py", loud, &group), vec!["mail", "mail", "mail", "mail", "mail"]);
        assert_eq!(spelled("a.json", "{\"kind\": \"mail\"}\n", &group), vec!["mail"], "json is not blanked");
    }

    #[test]
    fn globs_are_anchored_at_the_root() {
        assert!(anchored("README.md", "README.md"));
        assert!(!anchored("README.md", "docs/README.md"), "a pattern without / matches only directly under the root");
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
        assert_eq!(hits.len(), 1, "two patterns matching the same line still count as one line");
        assert_eq!(hits[0].detail, "g");
    }

    #[test]
    fn names_are_read_from_definitions_only() {
        let group = words(&[], &["(?i)conversation"], WordPlace::Names, &[]);
        let source = "(defk attend-conversation [x] \"conversation\")\n(defclass [(dataclass :frozen True)] ConversationRow [])\n\
                      (setv CONVERSATION-KIND \"conversation\")\n(defn #^ str chat-of [row] row.chat) ; spelling of conversation only in a comment\n\
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
        let hit = |rel: &str| judge(rel, "(setv worker 1) ; spelling of worker only in contents\n", &[group.clone()], &[], &PartCalls::default()).0;
        let details = |rel: &str| hit(rel).iter().map(|h| h.detail.clone()).collect::<Vec<_>>();
        assert_eq!(details("controllers/automation/core/worker.hy"), vec!["worker"]);
        assert_eq!(details("controllers/automation/core/design_request.hy"), vec!["design_request"]);
        assert_eq!(details("controllers/automation/core/dispatch_probe.hy"), vec!["g"], ":patterns build the key from the group name");
        assert!(hit("controllers/automation/core/worker_pool.hy").is_empty(), "part of another word is not counted");
        assert!(hit("controllers/worker/program.hy").is_empty(), "dir names are not looked at");
        assert!(hit("controllers/automation/core/program.hy").is_empty(), "spellings in contents are not looked at");
        let found = hit("controllers/automation/core/worker.hy");
        assert_eq!((found[0].start, found[0].end, found[0].place), (0, 0, WordPlace::Paths));
    }

    /// The sequence of matched spellings as text (match the :in lines group group against content source of file rel).
    fn spelled(rel: &str, source: &str, group: &RetiredWords) -> Vec<String> {
        judge(rel, source, &[group.clone()], &[], &PartCalls::default()).0.iter().map(|h| source[h.start..h.end].to_string()).collect()
    }

    /// Spellings in code that is actually used — Hy symbols, field names, command strings, other strings — are counted.
    #[test]
    fn hy_symbols_fields_and_command_strings_are_counted() {
        let group = words(&["ACP_CHECKOUT", "mail"], &[r"\bPYTHONPATH=", r#""PYTHONPATH""#], WordPlace::Lines, &[]);
        let source = "(setv ACP_CHECKOUT 1)\n(defrecord Row (#^ str mail))\n(setv row.mail 2)\n\
                      (defk run [] (RunProcess \"cd x && PYTHONPATH=. hy\"))\n(setv env {\"PYTHONPATH\" \".\"})\n";
        assert_eq!(spelled("a.hy", source, &group), vec!["ACP_CHECKOUT", "mail", "mail", "PYTHONPATH=", "\"PYTHONPATH\""]);
        // Even a string at the head of a definition is not a docstring if it is the whole body (the answer value).
        assert_eq!(spelled("a.hy", "(defk mail-of [] \"mail\")\n", &group), vec!["mail"]);
        // The first string of a non-definition form and string values of keywords are counted.
        assert_eq!(spelled("a.hy", "(print \"mail\" 1)\n(defadr x :title \"mail recipient\" :body 1)\n", &group), vec!["mail", "mail"]);
    }

    /// A first-line shebang is counted for every kind of file (target of the retired word direct-shebang).
    #[test]
    fn shebang_lines_are_counted_in_every_kind_of_file() {
        let group = words(&[], &[r"^#!\s*/usr/bin/env\s+(hy|python[0-9.]*)\b"], WordPlace::Lines, &[]);
        for rel in ["scripts/run.hy", "scripts/run.py", "scripts/run.sh", "scripts/run"] {
            assert_eq!(spelled(rel, "#!/usr/bin/env hy\n(print 1)\n", &group), vec!["#!/usr/bin/env hy"], "{}", rel);
        }
        assert!(spelled("scripts/run.sh", "echo 1\n#!/usr/bin/env hy\n", &group).is_empty(), "a #! on line 2 is a shell comment");
    }

    /// Hy `;` comments and definition docstrings are not counted (a `;` inside a string is not a comment).
    #[test]
    fn hy_comments_and_docstrings_are_not_counted() {
        let group = words(&["mail"], &[], WordPlace::Lines, &[]);
        let source = ";;; leading comment about mail\n(defk send [x]\n  {:pre [(: x int)]}\n  \"for sending mail\n  (mail on line 2)\"\n  (setv y x) ; mail is a comment\n  y)\n\
                      (defclass [(dataclass)] Box [Base] \"mail box\" (#^ int n))\n(defhandler h {:tags {}} \"mail meaning\" (Ask [k] (resume k)))\n\
                      (deftest test-mail-free \"mail check\" (assert 1))\n(defn #^ int f [] \"mail\" 1)\n#_(defk g [] \"mail\" 1) ; mail\n";
        assert!(spelled("a.hy", source, &group).is_empty(), "{:?}", spelled("a.hy", source, &group));
        // A `;` inside a string does not start a comment.
        assert_eq!(spelled("a.hy", "(setv s \"a ; mail\")\n", &group), vec!["mail"]);
    }

    /// `.md` files are not counted by :in lines (per file).
    #[test]
    fn markdown_files_are_not_counted() {
        let group = words(&["mail", "ACP_CHECKOUT"], &[], WordPlace::Lines, &[]);
        assert!(spelled("docs/a.md", "# mail\nbody mail and `ACP_CHECKOUT`\n```sh\nexport ACP_CHECKOUT=1\n```\n", &group).is_empty());
        // :in paths looks at the file name even for md (unchanged).
        let paths = words(&["mail"], &[], WordPlace::Paths, &[]);
        assert_eq!(judge("docs/mail.md", "", &[paths], &[], &PartCalls::default()).0.len(), 1);
    }

    /// Python `#` comments and docstrings (triple-quoted strings at line start) are not counted. Other strings and names are counted.
    #[test]
    fn python_comments_and_docstrings_are_not_counted() {
        let group = words(&["mail"], &[], WordPlace::Lines, &[]);
        let source = "\"\"\"mail module\n\nmail on line 2\"\"\"\nimport os  # mail is a comment\n\ndef f(x):\n    r'''mail function'''\n    s = \"# mail\"\n    return x.mail\n\n\
                      class C:\n    \"\"\"mail class\"\"\"\n    t = 'it''s # not mail'\n";
        assert_eq!(spelled("a.py", source, &group), vec!["mail", "mail", "mail"], "a # inside a string is not a comment; attribute names are counted");
        assert_eq!(spelled("a.py", "x = f(\"\"\"mail\"\"\")\n", &group), vec!["mail"], "a triple quote mid-line is not a docstring");
    }

    /// The type-declaration `.pyi` is also read as Python — words in docstrings and `#` comments are not counted, while field, variable, and definition names are.
    #[test]
    fn python_stub_files_are_read_as_python() {
        let group = words(&["mail"], &[], WordPlace::Lines, &[]);
        let source = "\"\"\"mail type declaration\"\"\"\nfrom typing import Any  # mail is a comment\n\nclass View:\n    \"\"\"has a mail field\"\"\"\n    mail: int\n\nmail_default: Any\nmail = 1\n";
        assert_eq!(spelled("a.pyi", source, &group), vec!["mail", "mail"], "docstrings and comments are not counted; field mail and variable mail are");
        assert_eq!(spelled("a.pyi", source, &group), spelled("a.py", source, &group), ".pyi and .py are read the same");
        let names = words(&[], &["(?i)conversation"], WordPlace::Names, &[]);
        let (stub, _) = judge("controllers/chat/rows.pyi", "def read_conversation(x: int) -> None: ...\nclass ChatRow: ...\n", &[names], &[], &PartCalls::default());
        assert_eq!(stub.iter().map(|h| h.name.as_deref().unwrap_or("")).collect::<Vec<_>>(), vec!["read_conversation"], ".pyi definition names are read too");
    }

    /// `#` comments (outside quotes) in shell, toml, and other files are not counted. `$#`, `${#…}`, and `#` inside quotes are not comments.
    #[test]
    fn hash_comments_are_not_counted_in_shell_and_other_files() {
        let group = words(&["ACP_CHECKOUT"], &[r"\bPYTHONPATH="], WordPlace::Lines, &[]);
        let source = "# leading comment about ACP_CHECKOUT\nexport PYTHONPATH=. # ACP_CHECKOUT is a comment\necho \"# ACP_CHECKOUT\"\necho $# ACP_CHECKOUT\n\
                      echo ${#ACP_CHECKOUT}\necho a#ACP_CHECKOUT\ncat <<EOF\n# ACP_CHECKOUT is here-document content\nEOF\n";
        assert_eq!(spelled("run.sh", source, &group), vec!["PYTHONPATH=", "ACP_CHECKOUT", "ACP_CHECKOUT", "ACP_CHECKOUT", "ACP_CHECKOUT", "ACP_CHECKOUT"]);
        let toml = "# ACP_CHECKOUT comment\ncommand = \"PYTHONPATH=. hy x.hy\" # ACP_CHECKOUT\nkey = 'ACP_CHECKOUT'\n";
        assert_eq!(spelled("declaration.toml", toml, &group), vec!["PYTHONPATH=", "ACP_CHECKOUT"]);
    }

    #[test]
    fn calls_are_heads_of_forms_not_comments_or_strings() {
        let group = RetiredCalls { name: "clock".into(), calls: vec!["Now".into(), "time.time".into()], files: vec!["**/*.hy".into()], except: Vec::new(), instead: "x".into() };
        let source = ";; the effect (Now) is retired\n(setv a (Now))\n(setv b \"(Now)\")\n(setv c (time.time))\n#_(Now)\n(setv d Now)\n";
        let (_, hits) = judge("a.hy", source, &[], &[group.clone()], &PartCalls::default());
        assert_eq!(hits.iter().map(|h| h.call.as_str()).collect::<Vec<_>>(), vec!["Now", "time.time"]);
        let (_, none) = judge("a.py", "Now()\n", &[], &[group], &PartCalls::default());
        assert!(none.is_empty(), "Python files are not counted");
    }
}
