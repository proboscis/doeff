//! Converts byte positions into contract positions (0-based line, column in UTF-16 code units).

use serde::{Deserialize, Serialize};

/// A contract position. The same unit as VS Code's Position (line is 0-based, column is in UTF-16 code units).
/// Ordered by line, then column (to compare range containment).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct Position {
    pub line: u32,
    pub character: u32,
}

/// A contract range `[start, end)`.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Range {
    pub start: Position,
    pub end: Position,
}

/// Table of byte positions where lines start. Used to look up the line and UTF-16 column from a byte position.
pub struct LineIndex<'a> {
    src: &'a str,
    line_starts: Vec<usize>,
}

impl<'a> LineIndex<'a> {
        /// Builds the line table from the newline positions of the source.
    pub fn new(src: &'a str) -> Self {
        let mut line_starts = vec![0];
        line_starts.extend(src.match_indices('\n').map(|(at, _)| at + 1));
        LineIndex { src, line_starts }
    }

        /// Converts a byte position into a contract position. A position that is not on a character boundary is moved to the previous boundary (no panic).
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

        /// Converts a byte range into a contract range.
    pub fn range(&self, start: usize, end: usize) -> Range {
        Range { start: self.position(start), end: self.position(end) }
    }

        /// Converts a contract position back into a byte position (the inverse of `position`). If the line is outside the source, moves to the end of the source; if the column is outside the line, to the end of that line.
        /// The start of a line is looked up from the table, so one call costs only the length of that line (it does not recount from the start of the source — callers converting many positions of one
        /// file back used to become heavy as the square of the file length).
    pub fn offset(&self, position: Position) -> usize {
        let Some(&line_start) = self.line_starts.get(position.line as usize) else { return self.src.len() };
        let line_end = self.src[line_start..].find('\n').map(|at| line_start + at).unwrap_or(self.src.len());
        let mut units = 0u32;
        for (index, ch) in self.src[line_start..line_end].char_indices() {
            if units >= position.character {
                return line_start + index;
            }
            units += ch.len_utf16() as u32;
        }
        line_end
    }
}

/// Converts usize to u32 (there is no file over 4G, but even if there were, it clamps to the limit instead of failing).
fn to_u32(value: usize) -> u32 {
    u32::try_from(value).unwrap_or(u32::MAX)
}
