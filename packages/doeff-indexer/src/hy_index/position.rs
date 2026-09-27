//! byte の位置を、契約の位置(0 始まりの行・UTF-16 の code unit の列)へ直す。

use serde::Serialize;

/// 契約の位置。VS Code の Position と同じ単位(行は 0 始まり、列は UTF-16 の code unit)。
/// 順序は行、次に列(範囲の包含を比べるため)。
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize)]
pub struct Position {
    pub line: u32,
    pub character: u32,
}

/// 契約の範囲 `[start, end)`。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct Range {
    pub start: Position,
    pub end: Position,
}

/// 行の始まりの byte の位置の表。byte の位置から行と UTF-16 の列を引くためのもの。
pub struct LineIndex<'a> {
    src: &'a str,
    line_starts: Vec<usize>,
}

impl<'a> LineIndex<'a> {
    /// source の改行の位置から行の表を作る。
    pub fn new(src: &'a str) -> Self {
        let mut line_starts = vec![0];
        line_starts.extend(src.match_indices('\n').map(|(at, _)| at + 1));
        LineIndex { src, line_starts }
    }

    /// byte の位置を契約の位置へ直す。文字の境目でない位置は手前の境目へ寄せる(panic しない)。
    pub fn position(&self, offset: usize) -> Position {
        let mut offset = offset.min(self.src.len());
        while !self.src.is_char_boundary(offset) {
            offset -= 1;
        }
        let line = self.line_starts.partition_point(|&start| start <= offset).saturating_sub(1);
        let line_start = self.line_starts.get(line).copied().unwrap_or(0);
        let character = self.src[line_start..offset].encode_utf16().count();
        Position { line: to_u32(line), character: to_u32(character) }
    }

    /// byte の範囲を契約の範囲へ直す。
    pub fn range(&self, start: usize, end: usize) -> Range {
        Range { start: self.position(start), end: self.position(end) }
    }
}

/// usize を u32 へ直す(4G を越える file は無いが、越えても落とさず上限に丸める)。
fn to_u32(value: usize) -> u32 {
    u32::try_from(value).unwrap_or(u32::MAX)
}
