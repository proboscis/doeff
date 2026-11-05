//! Hy reader — turns Hy source into a tree of forms. It does not fail even if parentheses are unclosed.
//!
//! Origin: copied from `Reader` in `vscode-semantic-highlighting/rust-highlighter/src/hy.rs` (for coloring),
//! with these changes for the index — every form carries a byte range (`Span`) (to report the range of the whole
//! definition form), unreadable spots are accumulated as `ReadIssue` (to report in `errors`),
//! and the range of a reader macro's tag is kept. Handling of `#^` annotations, `#_`, `#(`, `#{`, bracket strings, f-strings and
//! `#*` / `#**` is the same as the original.

/// A byte range `[start, end)` in the source. Boundaries always fall on UTF-8 character boundaries.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Span {
    pub start: usize,
    pub end: usize,
}

/// Kind of bracket. `#(` is a tuple, `#{` is a set.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Delim {
    Paren,
    Bracket,
    Brace,
    Tuple,
    Set,
}

impl Delim {
    /// Returns the byte of the closing bracket (to check bracket matching).
    fn closer(self) -> u8 {
        match self {
            Delim::Paren | Delim::Tuple => b')',
            Delim::Bracket => b']',
            Delim::Brace | Delim::Set => b'}',
        }
    }

    /// Returns the spelling of the opening bracket (for error messages).
    pub fn opener_text(self) -> &'static str {
        match self {
            Delim::Paren => "(",
            Delim::Bracket => "[",
            Delim::Brace => "{",
            Delim::Tuple => "#(",
            Delim::Set => "#{",
        }
    }
}

/// Kind of string. Only `Plain` and `Bracket` can be read as docstrings.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StrKind {
    Plain,
    Format,
    Raw,
    Bytes,
    Bracket,
    FormatBracket,
}

/// Marks placed before a form (quote, unquote, unpack).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Prefix {
    Quote,
    Quasiquote,
    Unquote,
    UnquoteSplice,
    Unpack,
    UnpackMapping,
}

/// One form read. `span` covers the whole form (including prefix marks and brackets).
#[derive(Debug)]
pub struct Form {
    pub span: Span,
    pub node: Node,
}

/// Kind of form content.
#[derive(Debug)]
pub enum Node {
    Seq { delim: Delim, items: Vec<Form> },
    Symbol,
    Keyword,
    Str { kind: StrKind, body: Span },
    Number,
    Prefixed { prefix: Prefix, inner: Option<Box<Form>> },
    Annotated { annotation: Option<Box<Form>>, target: Option<Box<Form>> },
    Discarded,
    Tagged { inner: Option<Box<Form>> },
}

impl Form {
    /// Returns the contents of `( … )` (None for anything else).
    pub fn paren_items(&self) -> Option<&[Form]> {
        match &self.node {
            Node::Seq { delim: Delim::Paren, items } => Some(items),
            _ => None,
        }
    }

    /// Returns the contents of `[ … ]` (None for anything else).
    pub fn bracket_items(&self) -> Option<&[Form]> {
        match &self.node {
            Node::Seq { delim: Delim::Bracket, items } => Some(items),
            _ => None,
        }
    }

    /// Returns whether this is `{ … }` (a dict) (to skip defk's `{:pre … :post …}`).
    pub fn is_brace(&self) -> bool {
        matches!(self.node, Node::Seq { delim: Delim::Brace, .. })
    }
}

/// A broken spot found while reading (reading continues for what can be read).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ReadIssue {
    /// An opening bracket is not closed by the end of the file.
    Unclosed { delim: Delim, open: usize },
    /// The sequence ended with a non-matching closing bracket.
    Mismatched { delim: Delim, open: usize, at: usize },
    /// A stray closing bracket at top level.
    StrayCloser { at: usize },
    /// A string is not closed by the end of the file.
    UnterminatedString { start: usize },
}

/// A reader for part of the source `[pos, end)`.
pub struct Reader<'a> {
    src: &'a str,
    bytes: &'a [u8],
    pos: usize,
    end: usize,
    pub issues: Vec<ReadIssue>,
}

/// Returns whether the byte ends a symbol.
fn is_delimiter(b: u8) -> bool {
    b.is_ascii_whitespace() || matches!(b, b'(' | b')' | b'[' | b']' | b'{' | b'}' | b'"' | b';')
}

/// Returns whether the byte is a closing bracket.
fn is_closer(b: u8) -> bool {
    matches!(b, b')' | b']' | b'}')
}

impl<'a> Reader<'a> {
    /// Creates a reader for `[start, end)` of `src` (a sub-range is passed when reading inside an f-string's `{…}`).
    pub fn new(src: &'a str, start: usize, end: usize) -> Self {
        let end = end.min(src.len());
        Reader { src, bytes: src.as_bytes(), pos: start.min(end), end, issues: Vec::new() }
    }

    /// Returns the byte `offset` ahead of the current position (None outside the range).
    fn peek(&self, offset: usize) -> Option<u8> {
        let at = self.pos + offset;
        if at < self.end {
            Some(self.bytes[at])
        } else {
            None
        }
    }

    /// Reads all top-level forms up to the end of the range.
    pub fn read_all(&mut self) -> Vec<Form> {
        let mut forms = Vec::new();
        if self.src[self.pos..self.end].starts_with("#!") {
            self.skip_line_comment();
        }
        loop {
            self.skip_trivia();
            match self.peek(0) {
                None => break,
                Some(b) if is_closer(b) => {
                    self.issues.push(ReadIssue::StrayCloser { at: self.pos });
                    self.pos += 1;
                }
                Some(_) => {
                    if let Some(form) = self.read_form() {
                        forms.push(form);
                    }
                }
            }
        }
        forms
    }

    /// Skips a comment to the end of the line.
    fn skip_line_comment(&mut self) {
        while self.pos < self.end && self.bytes[self.pos] != b'\n' {
            self.pos += 1;
        }
    }

    /// Skips whitespace and comments.
    fn skip_trivia(&mut self) {
        while let Some(b) = self.peek(0) {
            if b == b';' {
                self.skip_line_comment();
            } else if b.is_ascii_whitespace() {
                self.pos += 1;
            } else {
                break;
            }
        }
    }

    /// Reads one form. Returns None at the end of the range or at a closing bracket (not consumed).
    pub fn read_form(&mut self) -> Option<Form> {
        self.skip_trivia();
        let start = self.pos;
        let b = self.peek(0)?;
        match b {
            b'(' => Some(self.read_seq(start, Delim::Paren, 1)),
            b'[' => Some(self.read_seq(start, Delim::Bracket, 1)),
            b'{' => Some(self.read_seq(start, Delim::Brace, 1)),
            b')' | b']' | b'}' => None,
            b'"' => Some(self.read_string(start, start, StrKind::Plain)),
            b'\'' => Some(self.read_prefixed(start, Prefix::Quote, 1)),
            b'`' => Some(self.read_prefixed(start, Prefix::Quasiquote, 1)),
            b'~' => {
                if self.peek(1) == Some(b'@') {
                    Some(self.read_prefixed(start, Prefix::UnquoteSplice, 2))
                } else {
                    Some(self.read_prefixed(start, Prefix::Unquote, 1))
                }
            }
            b'#' => Some(self.read_dispatch(start)),
            _ => Some(self.read_atom()),
        }
    }

    /// Reads a sequence of brackets. An unclosed or mismatched closing bracket is accumulated as an issue and ends the sequence.
    fn read_seq(&mut self, start: usize, delim: Delim, open_len: usize) -> Form {
        self.pos += open_len;
        let close = delim.closer();
        let mut items = Vec::new();
        loop {
            self.skip_trivia();
            match self.peek(0) {
                None => {
                    self.issues.push(ReadIssue::Unclosed { delim, open: start });
                    break;
                }
                Some(b) if b == close => {
                    self.pos += 1;
                    break;
                }
                Some(b) if is_closer(b) => {
                    self.issues.push(ReadIssue::Mismatched { delim, open: start, at: self.pos });
                    self.pos += 1;
                    break;
                }
                Some(_) => {
                    if let Some(form) = self.read_form() {
                        items.push(form);
                    }
                }
            }
        }
        Form { span: Span { start, end: self.pos }, node: Node::Seq { delim, items } }
    }

    /// Reads a form with a prefix mark.
    fn read_prefixed(&mut self, start: usize, prefix: Prefix, len: usize) -> Form {
        self.pos += len;
        let inner = self.read_form().map(Box::new);
        Form { span: Span { start, end: self.pos }, node: Node::Prefixed { prefix, inner } }
    }

    /// Reads a form starting with `#` (tuple, set, bracket string, unpack, annotation, discard, reader macro).
    fn read_dispatch(&mut self, start: usize) -> Form {
        match self.peek(1) {
            Some(b'(') => self.read_seq(start, Delim::Tuple, 2),
            Some(b'{') => self.read_seq(start, Delim::Set, 2),
            Some(b'[') => self.read_bracket_string(start),
            Some(b'*') => {
                if self.peek(2) == Some(b'*') {
                    self.read_prefixed(start, Prefix::UnpackMapping, 3)
                } else {
                    self.read_prefixed(start, Prefix::Unpack, 2)
                }
            }
            Some(b'^') => {
                self.pos += 2;
                let annotation = self.read_form().map(Box::new);
                let target = self.read_form().map(Box::new);
                Form { span: Span { start, end: self.pos }, node: Node::Annotated { annotation, target } }
            }
            Some(b'_') => {
                self.pos += 2;
                self.read_form();
                Form { span: Span { start, end: self.pos }, node: Node::Discarded }
            }
            _ => self.read_tagged(start),
        }
    }

    /// Reads a reader macro `#tag form` (the tag is not counted as a symbol).
    fn read_tagged(&mut self, start: usize) -> Form {
        self.pos = start + 1;
        while let Some(b) = self.peek(0) {
            if is_delimiter(b) {
                break;
            }
            self.pos += 1;
        }
        let inner = self.read_form().map(Box::new);
        Form { span: Span { start, end: self.pos }, node: Node::Tagged { inner } }
    }

    /// Reads a `#[delim[ … ]delim]` bracket string. If the shape does not match, reads it as a reader macro.
    fn read_bracket_string(&mut self, start: usize) -> Form {
        let delim_start = start + 2;
        let mut cursor = delim_start;
        while cursor < self.end && self.bytes[cursor] != b'[' && !is_delimiter(self.bytes[cursor]) {
            cursor += 1;
        }
        if cursor >= self.end || self.bytes[cursor] != b'[' {
            return self.read_tagged(start);
        }
        let delim = &self.src[delim_start..cursor];
        let body_start = cursor + 1;
        let closing = format!("]{}]", delim);
        let (body_end, end) = match self.src[body_start..self.end].find(&closing) {
            Some(found) => (body_start + found, body_start + found + closing.len()),
            None => {
                self.issues.push(ReadIssue::UnterminatedString { start });
                (self.end, self.end)
            }
        };
        self.pos = end;
        let kind = if delim.starts_with('f') { StrKind::FormatBracket } else { StrKind::Bracket };
        Form { span: Span { start, end }, node: Node::Str { kind, body: Span { start: body_start, end: body_end } } }
    }

    /// Reads a `"…"` string (`quote` is the position of the opening `"`, `start` is the start including the prefix).
    fn read_string(&mut self, start: usize, quote: usize, kind: StrKind) -> Form {
        let (body_end, after) = self.string_end(quote, kind == StrKind::Format);
        self.pos = after.min(self.end);
        let body_end = match body_end {
            Some(at) => at,
            None => {
                self.issues.push(ReadIssue::UnterminatedString { start });
                self.end
            }
        };
        // Even if skipping 2 bytes after `\` lands in the middle of a multibyte character, the end is `"` or the end of the range, so it falls on a boundary.
        Form { span: Span { start, end: self.pos }, node: Node::Str { kind, body: Span { start: quote + 1, end: body_end } } }
    }

    /// Finds the end of a string from the position quote of the opening `"` — (position of the closing `"`, the position after it). If there is no closing, (None, end of range).
    /// For an f-string (format), the replacement field `{…}` contains a Hy expression, so strings inside the field (including nested `f"…"`) and brackets are skipped
    /// before searching for the closing `"` — same as Hy's reader (a `"` inside `f"{(.join "; " xs)}"` does not close the string). `{{` is a literal `{`.
    fn string_end(&self, quote: usize, format: bool) -> (Option<usize>, usize) {
        let mut pos = quote + 1;
        while pos < self.end {
            match self.bytes[pos] {
                b'\\' => pos += 2,
                b'"' => return (Some(pos), pos + 1),
                b'{' if format && self.bytes.get(pos + 1) == Some(&b'{') => pos += 2,
                b'{' if format => pos = self.replacement_end(pos + 1),
                _ => pos += 1,
            }
        }
        (None, self.end)
    }

    /// From the contents of an f-string replacement field (after `{`), returns the position after the matching `}`. Strings inside are skipped with `string_end`
    /// (including nested f-strings with the `f` prefix), and nesting of `(`, `[`, `{` is counted. If there is no closing, the end of the range.
    fn replacement_end(&self, mut pos: usize) -> usize {
        let mut depth = 0usize;
        while pos < self.end {
            match self.bytes[pos] {
                b'"' => {
                    let nested_format = pos > 0 && self.bytes[pos - 1] == b'f' && (pos < 2 || is_delimiter(self.bytes[pos - 2]));
                    let (_, after) = self.string_end(pos, nested_format);
                    pos = after;
                    continue;
                }
                b'\\' => pos += 1,
                b'(' | b'[' | b'{' => depth += 1,
                b')' | b']' => depth = depth.saturating_sub(1),
                b'}' if depth == 0 => return pos + 1,
                b'}' => depth -= 1,
                _ => {}
            }
            pos += 1;
        }
        self.end
    }

    /// Reads a symbol, keyword, number, or prefixed string (such as `f"…"`).
    fn read_atom(&mut self) -> Form {
        let start = self.pos;
        while let Some(b) = self.peek(0) {
            if is_delimiter(b) {
                break;
            }
            self.pos += 1;
        }
        let text = &self.src[start..self.pos];
        if self.peek(0) == Some(b'"') {
            let kind = match text.to_ascii_lowercase().as_str() {
                "f" | "fr" | "rf" => Some(StrKind::Format),
                "r" => Some(StrKind::Raw),
                "b" | "br" | "rb" => Some(StrKind::Bytes),
                _ => None,
            };
            if let Some(kind) = kind {
                return self.read_string(start, self.pos, kind);
            }
        }
        let span = Span { start, end: self.pos };
        let node = if text.len() > 1 && text.starts_with(':') {
            Node::Keyword
        } else if is_number(text) {
            Node::Number
        } else {
            Node::Symbol
        };
        Form { span, node }
    }
}

/// Returns whether this is a number literal (starts with a digit, or a digit follows a sign or `.`).
fn is_number(text: &str) -> bool {
    let mut chars = text.chars();
    match chars.next() {
        Some(c) if c.is_ascii_digit() => true,
        Some('+' | '-' | '.') => chars.next().is_some_and(|c| c.is_ascii_digit()),
        _ => false,
    }
}

/// Finds the `}` closing the `{` at `open` inside an f-string (skipping nested brackets and strings). If not found, `end`.
pub fn matching_brace(bytes: &[u8], open: usize, end: usize) -> usize {
    let mut depth = 0usize;
    let mut i = open;
    while i < end {
        match bytes[i] {
            b'{' | b'(' | b'[' => depth += 1,
            b'}' | b')' | b']' => {
                depth = depth.saturating_sub(1);
                if depth == 0 {
                    return i;
                }
            }
            b'"' => {
                i += 1;
                while i < end && bytes[i] != b'"' {
                    if bytes[i] == b'\\' {
                        i += 1;
                    }
                    i += 1;
                }
            }
            b'\\' => i += 1,
            _ => {}
        }
        i += 1;
    }
    end
}
