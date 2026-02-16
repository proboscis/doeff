pub fn line_col_at(source: &str, byte_offset: usize) -> (u32, u32) {
    let mut line = 1u32;
    let mut column = 1u32;

    for ch in source[..byte_offset.min(source.len())].chars() {
        if ch == '\n' {
            line += 1;
            column = 1;
        } else {
            column += 1;
        }
    }

    (line, column)
}

pub fn slice_text(source: &str, start: usize, end: usize) -> String {
    source
        .get(start..end)
        .map(|text| text.to_string())
        .unwrap_or_default()
}
