//! Hy の読み取り器 — Hy の source を form の木にする。括弧が閉じていなくても落ちない。
//!
//! 出自: `vscode-semantic-highlighting/rust-highlighter/src/hy.rs` の `Reader`(色付け用)を写し、
//! 索引のために次を変えた — すべての form に byte の範囲(`Span`)を持たせる(定義の form 全体の
//! 範囲を出すため)、読めなかった箇所を `ReadIssue` として積む(`errors` に出すため)、
//! reader macro の tag の範囲を持つ。`#^` 注釈・`#_`・`#(`・`#{`・bracket 文字列・f 文字列・
//! `#*` / `#**` の扱いは写し元と同じ。

/// source の中の byte の範囲 `[start, end)`。境界は必ず UTF-8 の文字の境目に来る。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Span {
    pub start: usize,
    pub end: usize,
}

/// 括弧の種類。`#(` は tuple、`#{` は set。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Delim {
    Paren,
    Bracket,
    Brace,
    Tuple,
    Set,
}

impl Delim {
    /// 閉じ括弧の byte を返す(括弧の対応を確かめるため)。
    fn closer(self) -> u8 {
        match self {
            Delim::Paren | Delim::Tuple => b')',
            Delim::Bracket => b']',
            Delim::Brace | Delim::Set => b'}',
        }
    }

    /// 開き括弧の綴りを返す(errors の文言のため)。
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

/// 文字列の種類。docstring として読めるのは `Plain` と `Bracket` だけ。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StrKind {
    Plain,
    Format,
    Raw,
    Bytes,
    Bracket,
    FormatBracket,
}

/// form の前に付く記号(quote・unquote・unpack)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Prefix {
    Quote,
    Quasiquote,
    Unquote,
    UnquoteSplice,
    Unpack,
    UnpackMapping,
}

/// 読んだ form の 1 つ。`span` は form 全体(前置の記号・括弧を含む)。
#[derive(Debug)]
pub struct Form {
    pub span: Span,
    pub node: Node,
}

/// form の中身の種類。
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
    /// `( … )` の中身を返す(それ以外は None)。
    pub fn paren_items(&self) -> Option<&[Form]> {
        match &self.node {
            Node::Seq { delim: Delim::Paren, items } => Some(items),
            _ => None,
        }
    }

    /// `[ … ]` の中身を返す(それ以外は None)。
    pub fn bracket_items(&self) -> Option<&[Form]> {
        match &self.node {
            Node::Seq { delim: Delim::Bracket, items } => Some(items),
            _ => None,
        }
    }

    /// `{ … }`(dict)かを返す(defk の `{:pre … :post …}` を読み飛ばすため)。
    pub fn is_brace(&self) -> bool {
        matches!(self.node, Node::Seq { delim: Delim::Brace, .. })
    }
}

/// 読み取りで見つけた壊れた箇所(読めた分は読み続ける)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ReadIssue {
    /// 開き括弧が file の終わりまで閉じない。
    Unclosed { delim: Delim, open: usize },
    /// 対応しない閉じ括弧で列が終わった。
    Mismatched { delim: Delim, open: usize, at: usize },
    /// top level に余った閉じ括弧。
    StrayCloser { at: usize },
    /// 文字列が file の終わりまで閉じない。
    UnterminatedString { start: usize },
}

/// source の一部 `[pos, end)` を読む読み取り器。
pub struct Reader<'a> {
    src: &'a str,
    bytes: &'a [u8],
    pos: usize,
    end: usize,
    pub issues: Vec<ReadIssue>,
}

/// 記号の終わりになる byte かを返す。
fn is_delimiter(b: u8) -> bool {
    b.is_ascii_whitespace() || matches!(b, b'(' | b')' | b'[' | b']' | b'{' | b'}' | b'"' | b';')
}

/// 閉じ括弧の byte かを返す。
fn is_closer(b: u8) -> bool {
    matches!(b, b')' | b']' | b'}')
}

impl<'a> Reader<'a> {
    /// `src` の `[start, end)` を読む読み取り器を作る(f 文字列の `{…}` の中を読む時は部分を渡す)。
    pub fn new(src: &'a str, start: usize, end: usize) -> Self {
        let end = end.min(src.len());
        Reader { src, bytes: src.as_bytes(), pos: start.min(end), end, issues: Vec::new() }
    }

    /// 今の位置から `offset` 先の byte を返す(範囲の外は None)。
    fn peek(&self, offset: usize) -> Option<u8> {
        let at = self.pos + offset;
        if at < self.end {
            Some(self.bytes[at])
        } else {
            None
        }
    }

    /// 範囲の終わりまで top level の form を全部読む。
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

    /// 行末までの註を読み飛ばす。
    fn skip_line_comment(&mut self) {
        while self.pos < self.end && self.bytes[self.pos] != b'\n' {
            self.pos += 1;
        }
    }

    /// 空白と註を読み飛ばす。
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

    /// form を 1 つ読む。範囲の終わりか閉じ括弧(消費しない)なら None。
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

    /// 括弧の列を読む。閉じない・食い違う閉じ括弧は issue に積んで列を終える。
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

    /// 前置の記号の付いた form を読む。
    fn read_prefixed(&mut self, start: usize, prefix: Prefix, len: usize) -> Form {
        self.pos += len;
        let inner = self.read_form().map(Box::new);
        Form { span: Span { start, end: self.pos }, node: Node::Prefixed { prefix, inner } }
    }

    /// `#` で始まる form(tuple・set・bracket 文字列・unpack・注釈・読み捨て・reader macro)を読む。
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

    /// reader macro の `#tag form` を読む(tag は記号として数えない)。
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

    /// `#[delim[ … ]delim]` の bracket 文字列を読む。形が合わなければ reader macro として読む。
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

    /// `"…"` の文字列を読む(`quote` は開きの `"` の位置、`start` は接頭辞を含む始まり)。
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
        // `\` の後の 2 byte 飛ばしが多 byte 文字の途中に落ちても、終わりは `"` か範囲の終わりなので境目に来る。
        Form { span: Span { start, end: self.pos }, node: Node::Str { kind, body: Span { start: quote + 1, end: body_end } } }
    }

    /// 開きの `"` の位置 quote から文字列の終わりを探す — (閉じの `"` の位置・その次の位置)。閉じが無ければ (None・範囲の終わり)。
    /// f 文字列(format)は、置き換えの欄 `{…}` の中が Hy の式なので、欄の中の文字列(入れ子の `f"…"` も)と括弧を飛ばしてから
    /// 閉じの `"` を探す — Hy の読み手と同じ(`f"{(.join "; " xs)}"` の中の `"` で文字列を閉じない)。`{{` は字面の `{`。
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

    /// f 文字列の置き換えの欄の中身(`{` の次)から、対応する `}` の次の位置を返す。中の文字列は `string_end` で飛ばし
    /// (接頭辞 `f` の付いた入れ子の f 文字列も)、`(`・`[`・`{` の入れ子を数える。閉じが無ければ範囲の終わり。
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

    /// 記号・keyword・数・接頭辞つき文字列(`f"…"` など)を読む。
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

/// 数の literal かを返す(先頭が数字、または符号・`.` の次が数字)。
fn is_number(text: &str) -> bool {
    let mut chars = text.chars();
    match chars.next() {
        Some(c) if c.is_ascii_digit() => true,
        Some('+' | '-' | '.') => chars.next().is_some_and(|c| c.is_ascii_digit()),
        _ => false,
    }
}

/// f 文字列の中で `open` の `{` を閉じる `}` を探す(入れ子の括弧と文字列を飛ばす)。見つからなければ `end`。
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
