//! 位置の変換 — byte の位置と、エディタの位置(0 始まりの行・UTF-16 の code unit の列)の行き来。
//! byte → UTF-16 は doeff-indexer の `LineIndex`(hy-index の位置と同じ単位)を使う。

pub use doeff_indexer::hy_index::{LineIndex, Position, Range};

/// エディタの位置を byte の位置へ戻す(行や列が source の外なら、その行の終わりか source の終わりに寄せる)。
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

/// byte の位置を含む行の、その位置から行末までの範囲(列の取れない規則の違反の位置)。
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

/// 1 行目の頭から行末までの範囲(file 全体に掛かる違反の位置)。
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
        // 2 行目: "(import 外.世界 [" は UTF-16 で 14 code unit。
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

    /// 行の表から戻す `LineIndex::offset`(agora-redesign #1632)は、source の頭から数える `offset_of` と同じ答えを返す —
    /// 日本語・絵文字の列、行の外の列、source の外の行、末尾の改行の後の行も含めて。
    #[test]
    fn line_index_offset_agrees_with_offset_of() {
        for src in ["(setv 名前 1)\n(import 外.世界 [httpx])\n", "x = \"😀\"; y\nz", "abc\r\ndef", "", "\n\n", "末尾に改行なし"] {
            let lines = LineIndex::new(src);
            for line in 0..5u32 {
                for character in 0..20u32 {
                    let p = Position { line, character };
                    assert_eq!(lines.offset(p), offset_of(src, p), "{src:?} の {p:?}");
                }
            }
        }
    }
}
