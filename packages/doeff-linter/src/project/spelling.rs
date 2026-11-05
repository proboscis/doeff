//! A tool that finds a name's spelling in a file's text: a one-file run selects only the files that spell a name, without parsing all the Hy in the repo.
//!
//! The repo facts that a one-file check looks up (the Program a caller passes as an argument, the declaration of a failure type) have fixed names to look up. A file that holds such a fact always includes the spelling of the name's last segment in
//! its text: a symbol is resolved to its module by `Scope::qualify` (the resolution lives in one place, `smells::Scope::qualify`), the last segment of the resolved name is the mangle of the symbol's last segment,
//! and neither import bindings, aliases, nor module segments affect the last segment. Mangle only turns `-` into `_` (it leaves the run of leading `_` and the one character after it alone),
//! so a `_` in a name can also be spelled `-`. When a name contains non-identifier characters (a name mangle escaped to `hyx_`), its spelling cannot be determined, so the text is not used to narrow (every file matches).

use regex::Regex;

use super::names::is_identifier;

/// How to look for the spelling of one name.
pub struct Spelling {
    /// A regex made of the name's spelling plus the trailing delimiter. None = a name whose spelling cannot be determined, treated as matching every file.
    pattern: Option<Regex>,
    /// For a name whose spelling can be determined, the spelling with `-` folded to `_` (= the name itself, since a mangled name has no `-`). Used by the memo lookup (`blob_spell`).
    folded: Option<String>,
}

impl Spelling {
    /// The spelling of a mangled name (the last segment, without module segments).
    pub fn of(name: &str) -> Spelling {
        let pattern = spelling_pattern(name).and_then(|spelling| Regex::new(&format!("{spelling}{SYMBOL_END}")).ok());
        let folded = pattern.as_ref().map(|_| name.to_string());
        Spelling { pattern, folded }
    }

    /// The spelling of a name whose spelling can be determined (None for one that cannot, which matches every file).
    pub fn folded_name(&self) -> Option<&str> {
        self.folded.as_deref()
    }

    /// Whether the source has a place that spells the name as the last segment of a symbol. A regex that starts with the spelling has a fixed first character and searches fast (putting the preceding delimiter
    /// into the regex would leave the start undetermined), so find the matches first and then check the preceding delimiter per match. A match includes the trailing delimiter character, but the spelling
    /// contains no delimiter character, so a match discarded because its preceding delimiter did not fit never eats the start of the next match.
    pub fn in_source(&self, source: &str) -> bool {
        let Some(pattern) = &self.pattern else { return true };
        pattern.find_iter(source).any(|found| starts_a_segment(source, found.start()))
    }
}

/// What follows the last segment of a symbol. A symbol ends at whitespace or `( ) [ ] { } " ;` (the reader's `is_delimiter`).
/// In the middle of a longer symbol (`rows-by-text` in `rows-by-text-all`) or in a module path segment (`entry` in `controllers.entry.system`), the spelling of the name is not the last segment either.
/// Whitespace is the ASCII kind (the reader's `is_ascii_whitespace` plus vertical tab). Nothing is missed; an extra match only increases the files to parse and does not change the answer.
const SYMBOL_END: &str = r#"(?:[ \t\n\x0B\x0C\r()\[\]{}";]|$)"#;

/// Whether `at` (the start of the name's spelling) is the start of a symbol, or right after a `.`, i.e. before the last segment.
fn starts_a_segment(source: &str, at: usize) -> bool {
    match at.checked_sub(1).map(|before| source.as_bytes()[before]) {
        None => true,
        Some(byte) => byte.is_ascii_whitespace() || matches!(byte, 0x0B | b'.' | b'(' | b')' | b'[' | b']' | b'{' | b'}' | b'"' | b';'),
    }
}

/// A regex matching every spelling that mangles to the mangled name `name`. None for a name whose spelling cannot be determined.
fn spelling_pattern(name: &str) -> Option<String> {
    let stripped = name.trim_start_matches('_');
    // Mangle escapes non-identifier characters to `hyx_` (the spelling before escaping cannot be recovered from the name).
    if !is_identifier(name) || stripped.starts_with("hyx_") {
        return None;
    }
    let mut pattern = "_".repeat(name.len() - stripped.len());
    let mut rest = stripped.chars();
    if let Some(first) = rest.next() {
        pattern.push_str(&regex::escape(&first.to_string()));
    }
    for c in rest {
        match c {
            '_' => pattern.push_str("[-_]"),
            c => pattern.push_str(&regex::escape(&c.to_string())),
        }
    }
    Some(pattern)
}

#[cfg(test)]
mod tests {
    use super::super::names::hy_mangle;
    use super::*;

    /// The name-spelling regex picks up every spelling (mixes of `-` and `_`) that mangles to the name.
    #[test]
    fn the_spelling_pattern_matches_every_spelling_that_mangles_to_the_name() {
        for name in ["rows_by_text", "a", "a_b", "a__b", "_private_fn", "__dunder__", "x_1_y", "é_è", "ab_"] {
            let pattern = Regex::new(&format!("^{}$", spelling_pattern(name).expect(name))).unwrap();
            let underscores: Vec<usize> = name.char_indices().filter(|(_, c)| *c == '_').map(|(at, _)| at).collect();
            for mask in 0..(1u32 << underscores.len()) {
                let mut spelling = name.to_string();
                for (bit, at) in underscores.iter().enumerate() {
                    if mask >> bit & 1 == 1 {
                        spelling.replace_range(*at..*at + 1, "-");
                    }
                }
                if hy_mangle(&spelling) == name {
                    assert!(pattern.is_match(&spelling), "does not pick up {spelling}, which mangles to {name}");
                }
            }
        }
        assert!(spelling_pattern("hyx_checkXquestion_markX").is_none());
        assert!(spelling_pattern("_hyx_a").is_none());
        assert!(spelling_pattern("").is_none());
    }

    /// Picks up only places spelled as the last segment of a symbol: it picks up a symbol's start, right after `.`, and next to various brackets and quotes, but not the middle of a longer symbol or a module path segment.
    #[test]
    fn only_the_last_segment_of_a_symbol_counts() {
        let spelling = Spelling::of("rows_by_text");
        for hit in [
            "(rows-by-text x)",
            "(rows_by_text)",
            "(app.hof.rows-by-text x)",
            "[rows-by-text]",
            "{:f rows_by_text}",
            "rows-by-text",
            "  (  rows-by-text x)",
            "\"rows_by_text\"",
            ";rows-by-text\n",
            "(a)\n(rows-by-text\n x)",
            "xrows-by-text rows-by-text ",
        ] {
            assert!(spelling.in_source(hit), "{hit:?}");
        }
        for miss in ["(rows-by-text-all x)", "(all-rows-by-text x)", "(import controllers.rows-by-text.system)", "(rowsXby-text x)", "(x.rows-by-textx)", "xrows-by-text "] {
            assert!(!spelling.in_source(miss), "{miss:?}");
        }
        // A name whose spelling cannot be determined matches every file.
        assert!(Spelling::of("hyx_checkXquestion_markX").in_source("nothing here"));
    }
}
