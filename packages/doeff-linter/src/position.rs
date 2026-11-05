//! Position conversion — moving between byte positions and editor positions (0-based line, column in UTF-16 code units).
//! byte -> UTF-16 uses doeff-indexer's `LineIndex` (the same unit as hy-index positions).

pub use doeff_indexer::hy_index::{LineIndex, Position, Range};

/// Converts an editor position back to a byte position (if the line or column is outside the source, clamps to the end of that line or the end of the source).
pub fn offset_of(src: &str, position: Position) -> usize {
    let mut line_start = 0usize;
    for _ in 0..position.line {
        match src[line_start..].find('\n') {
            Some(at) => line_start += at + 1,
            None => return src.len(),
        }
    }
    let line_end = src[line_start..].find('\n').map(|at| line_start + at).unwrap_or(src.len());
    let mut units = 0u32;
    for (index, ch) in src[line_start..line_end].char_indices() {
        if units >= position.character {
            return line_start + index;
        }
        units += ch.len_utf16() as u32;
    }
    line_end
}

/// The range from a byte position to the end of its line (the violation position for rules that cannot get a column).
pub fn line_range(src: &str, offset: usize) -> Range {
    let lines = LineIndex::new(src);
    let mut offset = offset.min(src.len());
    while !src.is_char_boundary(offset) {
        offset -= 1;
    }
    let line_end = src[offset..].find('\n').map(|at| offset + at).unwrap_or(src.len());
    let line_end = if line_end > offset && src.as_bytes()[line_end - 1] == b'\r' { line_end - 1 } else { line_end };
    lines.range(offset, line_end)
}

/// The range from the start of the first line to its end (the violation position for violations that apply to the whole file).
pub fn first_line_range(src: &str) -> Range {
    line_range(src, 0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn utf16_columns_on_japanese_lines() {
        let src = "(setv 名前 1)\n(import 外.世界 [httpx])\n";
        let lines = LineIndex::new(src);
        let at = src.find("httpx").unwrap();
        let position = lines.position(at);
        // Line 2: "(import 外.世界 [" is 14 UTF-16 code units.
        assert_eq!(position, Position { line: 1, character: 14 });
        assert_eq!(offset_of(src, position), at);
        let emoji = "x = \"😀\"; y\n";
        let y = emoji.find('y').unwrap();
        let p = LineIndex::new(emoji).position(y);
        assert_eq!(p.character, 10);
        assert_eq!(offset_of(emoji, p), y);
    }

    #[test]
    fn line_range_runs_to_line_end_and_clamps() {
        let src = "abc\r\ndef";
        let range = line_range(src, 0);
        assert_eq!(range.start, Position { line: 0, character: 0 });
        assert_eq!(range.end, Position { line: 0, character: 3 });
        let past = line_range(src, 999);
        assert_eq!(past.start, Position { line: 1, character: 3 });
        assert_eq!(offset_of(src, Position { line: 9, character: 0 }), src.len());
    }

    /// `LineIndex::offset`, which maps back from the line table, returns the same answer as `offset_of`, which counts from the start of the source —
    /// including columns of Japanese text and emoji, columns beyond the line, lines beyond the source, and the line after a trailing newline.
    #[test]
    fn line_index_offset_agrees_with_offset_of() {
        for src in ["(setv 名前 1)\n(import 外.世界 [httpx])\n", "x = \"😀\"; y\nz", "abc\r\ndef", "", "\n\n", "末尾に改行なし"] {
            let lines = LineIndex::new(src);
            for line in 0..5u32 {
                for character in 0..20u32 {
                    let p = Position { line, character };
                    assert_eq!(lines.offset(p), offset_of(src, p), "{src:?} at {p:?}");
                }
            }
        }
    }
}
