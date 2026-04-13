//! Minimal S-expression reader for Hy source files.
//!
//! Parses just enough Hy syntax to extract:
//! - `(defk name [params] ...body...)`
//! - `(<- x (SomeEffect ...))` effect bindings
//! - `(import module [name1 name2])` imports
//! - `(require module [name1 name2])` macro imports
//! - `(defclass Name [Base] ...)` class definitions
//! - Function calls `(some-fn ...)`

use crate::SourceSpan;

/// A parsed S-expression node with source location.
#[derive(Debug, Clone)]
pub struct SExpr {
    pub kind: SExprKind,
    pub start: usize, // byte offset in source
    pub end: usize,
}

#[derive(Debug, Clone)]
pub enum SExprKind {
    /// `(a b c)` — parenthesized expression
    List(Vec<SExpr>),
    /// `[a b c]` — square bracket list
    Vector(Vec<SExpr>),
    /// `{a b c d}` — curly brace dict
    Dict(Vec<SExpr>),
    /// `#(a b c)` — tuple literal
    Tuple(Vec<SExpr>),
    /// Identifier / symbol
    Symbol(String),
    /// `:keyword`
    Keyword(String),
    /// `"string literal"`
    Str(String),
    /// Numeric literal (kept as string)
    Number(String),
    /// `'expr` — quote
    Quote(Box<SExpr>),
    /// `` `expr `` — quasiquote
    Quasiquote(Box<SExpr>),
    /// `~expr` — unquote
    Unquote(Box<SExpr>),
    /// `~@expr` — unquote-splice
    UnquoteSplice(Box<SExpr>),
}

impl SExpr {
    pub fn as_symbol(&self) -> Option<&str> {
        match &self.kind {
            SExprKind::Symbol(s) => Some(s.as_str()),
            _ => None,
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match &self.kind {
            SExprKind::Str(s) => Some(s.as_str()),
            _ => None,
        }
    }

    pub fn as_list(&self) -> Option<&[SExpr]> {
        match &self.kind {
            SExprKind::List(items) => Some(items.as_slice()),
            _ => None,
        }
    }

    pub fn as_vector(&self) -> Option<&[SExpr]> {
        match &self.kind {
            SExprKind::Vector(items) => Some(items.as_slice()),
            _ => None,
        }
    }

    /// Check if this is a list whose first element is the given symbol.
    pub fn is_form(&self, name: &str) -> bool {
        self.as_list()
            .and_then(|items| items.first())
            .and_then(|first| first.as_symbol())
            .map(|s| s == name)
            .unwrap_or(false)
    }
}

/// Parse errors.
#[derive(Debug)]
pub struct ParseError {
    pub message: String,
    pub offset: usize,
}

impl std::fmt::Display for ParseError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "parse error at byte {}: {}", self.offset, self.message)
    }
}

impl std::error::Error for ParseError {}

/// Parse a Hy source string into a list of top-level S-expressions.
pub fn parse_hy(source: &str) -> Result<Vec<SExpr>, ParseError> {
    let mut reader = Reader::new(source);
    let mut exprs = Vec::new();
    loop {
        reader.skip_whitespace_and_comments();
        if reader.is_eof() {
            break;
        }
        exprs.push(reader.read_expr()?);
    }
    Ok(exprs)
}

/// Compute a SourceSpan from a byte offset.
pub fn span_at(source: &str, offset: usize, file: &str) -> SourceSpan {
    let (line, column) = crate::source::line_col_at(source, offset);
    SourceSpan {
        file: file.to_string(),
        line,
        column,
    }
}

struct Reader<'a> {
    source: &'a str,
    pos: usize,
}

impl<'a> Reader<'a> {
    fn new(source: &'a str) -> Self {
        Self { source, pos: 0 }
    }

    fn is_eof(&self) -> bool {
        self.pos >= self.source.len()
    }

    fn peek(&self) -> Option<char> {
        self.source[self.pos..].chars().next()
    }

    fn advance(&mut self) -> Option<char> {
        let ch = self.source[self.pos..].chars().next()?;
        self.pos += ch.len_utf8();
        Some(ch)
    }

    fn skip_whitespace_and_comments(&mut self) {
        loop {
            // Skip whitespace
            while let Some(ch) = self.peek() {
                if ch.is_whitespace() {
                    self.advance();
                } else {
                    break;
                }
            }
            // Skip line comments
            if self.peek() == Some(';') {
                while let Some(ch) = self.advance() {
                    if ch == '\n' {
                        break;
                    }
                }
            } else {
                break;
            }
        }
    }

    fn read_expr(&mut self) -> Result<SExpr, ParseError> {
        self.skip_whitespace_and_comments();

        let start = self.pos;
        let ch = self.peek().ok_or_else(|| ParseError {
            message: "unexpected end of input".to_string(),
            offset: self.pos,
        })?;

        match ch {
            '(' => self.read_delimited('(', ')', start, |items| SExprKind::List(items)),
            '[' => self.read_delimited('[', ']', start, |items| SExprKind::Vector(items)),
            '{' => self.read_delimited('{', '}', start, |items| SExprKind::Dict(items)),
            '#' => {
                self.advance(); // consume '#'
                match self.peek() {
                    Some('(') => {
                        self.read_delimited('(', ')', start, |items| SExprKind::Tuple(items))
                    }
                    _ => {
                        // #_ discard, or other hash dispatch — treat as symbol
                        self.read_symbol_from(start)
                    }
                }
            }
            '"' => self.read_string(start),
            '\'' => {
                self.advance();
                let inner = self.read_expr()?;
                Ok(SExpr {
                    end: inner.end,
                    kind: SExprKind::Quote(Box::new(inner)),
                    start,
                })
            }
            '`' => {
                self.advance();
                let inner = self.read_expr()?;
                Ok(SExpr {
                    end: inner.end,
                    kind: SExprKind::Quasiquote(Box::new(inner)),
                    start,
                })
            }
            '~' => {
                self.advance();
                if self.peek() == Some('@') {
                    self.advance();
                    let inner = self.read_expr()?;
                    Ok(SExpr {
                        end: inner.end,
                        kind: SExprKind::UnquoteSplice(Box::new(inner)),
                        start,
                    })
                } else {
                    let inner = self.read_expr()?;
                    Ok(SExpr {
                        end: inner.end,
                        kind: SExprKind::Unquote(Box::new(inner)),
                        start,
                    })
                }
            }
            ':' => self.read_keyword(start),
            _ if ch == '-' || ch == '+' || ch.is_ascii_digit() => {
                self.read_number_or_symbol(start)
            }
            _ => self.read_symbol_from(start),
        }
    }

    fn read_delimited(
        &mut self,
        open: char,
        close: char,
        start: usize,
        make: impl FnOnce(Vec<SExpr>) -> SExprKind,
    ) -> Result<SExpr, ParseError> {
        self.advance(); // consume open
        let mut items = Vec::new();
        loop {
            self.skip_whitespace_and_comments();
            if self.is_eof() {
                return Err(ParseError {
                    message: format!("unclosed '{open}', expected '{close}'"),
                    offset: start,
                });
            }
            if self.peek() == Some(close) {
                self.advance();
                break;
            }
            items.push(self.read_expr()?);
        }
        Ok(SExpr {
            kind: make(items),
            start,
            end: self.pos,
        })
    }

    fn read_string(&mut self, start: usize) -> Result<SExpr, ParseError> {
        self.advance(); // consume opening "
        let mut s = String::new();
        loop {
            match self.advance() {
                None => {
                    return Err(ParseError {
                        message: "unterminated string".to_string(),
                        offset: start,
                    })
                }
                Some('\\') => match self.advance() {
                    Some('n') => s.push('\n'),
                    Some('t') => s.push('\t'),
                    Some('\\') => s.push('\\'),
                    Some('"') => s.push('"'),
                    Some(ch) => {
                        s.push('\\');
                        s.push(ch);
                    }
                    None => {
                        return Err(ParseError {
                            message: "unterminated escape in string".to_string(),
                            offset: self.pos,
                        })
                    }
                },
                Some('"') => break,
                Some(ch) => s.push(ch),
            }
        }
        Ok(SExpr {
            kind: SExprKind::Str(s),
            start,
            end: self.pos,
        })
    }

    fn read_keyword(&mut self, start: usize) -> Result<SExpr, ParseError> {
        self.advance(); // consume ':'
        let ident_start = self.pos;
        self.consume_ident_chars();
        let name = self.source[ident_start..self.pos].to_string();
        Ok(SExpr {
            kind: SExprKind::Keyword(name),
            start,
            end: self.pos,
        })
    }

    fn read_number_or_symbol(&mut self, start: usize) -> Result<SExpr, ParseError> {
        // Peek ahead: if starts with digit, or +/- followed by digit, it's a number
        let rest = &self.source[self.pos..];
        let is_number = if rest.starts_with('+') || rest.starts_with('-') {
            rest.len() > 1 && rest.as_bytes().get(1).map(|b| b.is_ascii_digit()) == Some(true)
        } else {
            rest.starts_with(|c: char| c.is_ascii_digit())
        };

        if is_number {
            self.consume_ident_chars();
            let text = self.source[start..self.pos].to_string();
            Ok(SExpr {
                kind: SExprKind::Number(text),
                start,
                end: self.pos,
            })
        } else {
            self.read_symbol_from(start)
        }
    }

    fn read_symbol_from(&mut self, start: usize) -> Result<SExpr, ParseError> {
        self.consume_ident_chars();
        if self.pos == start {
            return Err(ParseError {
                message: format!("unexpected character: {:?}", self.peek()),
                offset: self.pos,
            });
        }
        let text = self.source[start..self.pos].to_string();
        Ok(SExpr {
            kind: SExprKind::Symbol(text),
            start,
            end: self.pos,
        })
    }

    fn consume_ident_chars(&mut self) {
        while let Some(ch) = self.peek() {
            if ch.is_whitespace()
                || matches!(ch, '(' | ')' | '[' | ']' | '{' | '}' | '"' | ';' | ',' | '`' | '~')
            {
                break;
            }
            self.advance();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_simple_list() {
        let exprs = parse_hy("(defk foo [x] x)").unwrap();
        assert_eq!(exprs.len(), 1);
        assert!(exprs[0].is_form("defk"));
    }

    #[test]
    fn test_parse_bind() {
        let exprs = parse_hy(r#"(<- y (Ask "key"))"#).unwrap();
        assert_eq!(exprs.len(), 1);
        assert!(exprs[0].is_form("<-"));
    }

    #[test]
    fn test_parse_string() {
        let exprs = parse_hy(r#"(print "hello world")"#).unwrap();
        let list = exprs[0].as_list().unwrap();
        assert_eq!(list[1].as_str(), Some("hello world"));
    }

    #[test]
    fn test_parse_keyword() {
        let exprs = parse_hy("(Iterate items :label \"stage1\")").unwrap();
        let list = exprs[0].as_list().unwrap();
        match &list[2].kind {
            SExprKind::Keyword(k) => assert_eq!(k, "label"),
            _ => panic!("expected keyword"),
        }
    }

    #[test]
    fn test_parse_tuple() {
        let exprs = parse_hy("#(1 2 3)").unwrap();
        assert!(matches!(exprs[0].kind, SExprKind::Tuple(_)));
    }

    #[test]
    fn test_parse_comments() {
        let exprs = parse_hy(
            ";; comment\n(foo) ; inline\n;; another\n(bar)",
        )
        .unwrap();
        assert_eq!(exprs.len(), 2);
        assert!(exprs[0].is_form("foo"));
        assert!(exprs[1].is_form("bar"));
    }

    #[test]
    fn test_parse_nested() {
        let exprs =
            parse_hy(r#"(defk pipeline [items] (<- x (Ask "model")) (<- y (Compute x)) y)"#)
                .unwrap();
        assert_eq!(exprs.len(), 1);
        let list = exprs[0].as_list().unwrap();
        assert_eq!(list.len(), 6); // defk, pipeline, [items], (<- ...), (<- ...), y
    }
}
