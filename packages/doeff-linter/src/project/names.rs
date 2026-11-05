//! Name spelling conversions — Hy mangle (same result as `hy.mangle` in Hy 1.x), module spelling, and relative import resolution.
//! The registry keys are spellings made by the Python check (agora's scripts) with `hy.mangle`, so the same rules are applied here to align the keys.

/// Converts a Hy name to a Python name (same as `hy.mangle` — dotted names are handled per segment, `-` becomes `_`, characters that cannot be in an identifier become `XnameX`).
pub fn hy_mangle(name: &str) -> String {
    if name.is_empty() {
        return String::new();
    }
    if name.contains('.') && !name.trim_matches('.').is_empty() {
        return name.split('.').map(|part| if part.is_empty() { String::new() } else { hy_mangle(part) }).collect::<Vec<_>>().join(".");
    }
    let stripped = name.trim_start_matches('_');
    let leading = "_".repeat(name.len() - stripped.len());
    let mut chars = stripped.chars();
    let converted: String = match chars.next() {
        Some(first) => std::iter::once(first).chain(chars.map(|c| if c == '-' { '_' } else { c })).collect(),
        None => String::new(),
    };
    let candidate = format!("{}{}", leading, converted);
    if is_identifier(&candidate) {
        return candidate;
    }
    let escaped: String = converted
        .chars()
        .map(|c| {
            if c != 'X' && is_identifier_continue(c) {
                c.to_string()
            } else {
                format!("X{}X", char_name(c))
            }
        })
        .collect();
    format!("{}hyx_{}", leading, escaped)
}

/// Whether the character can start a Python identifier (outside ASCII, letter/digit categories are treated as identifier characters).
fn is_identifier_start(c: char) -> bool {
    c == '_' || c.is_alphabetic()
}

/// Whether the character can appear as the 2nd or later character of a Python identifier.
fn is_identifier_continue(c: char) -> bool {
    c == '_' || c.is_alphanumeric()
}

/// Whether the string is a Python identifier.
pub(crate) fn is_identifier(text: &str) -> bool {
    let mut chars = text.chars();
    match chars.next() {
        Some(first) => is_identifier_start(first) && chars.all(is_identifier_continue),
        None => false,
    }
}

/// The name in mangle's `XnameX` (the Unicode character name lowercased, with `-` replaced by `H` and whitespace by `_`).
/// ASCII symbols are looked up in a table; characters not in the table become `U` plus the hex code point.
fn char_name(c: char) -> String {
    let name = match c {
        ' ' => "space",
        '!' => "exclamation mark",
        '"' => "quotation mark",
        '#' => "number sign",
        '$' => "dollar sign",
        '%' => "percent sign",
        '&' => "ampersand",
        '\'' => "apostrophe",
        '(' => "left parenthesis",
        ')' => "right parenthesis",
        '*' => "asterisk",
        '+' => "plus sign",
        ',' => "comma",
        '-' => "hyphen-minus",
        '.' => "full stop",
        '/' => "solidus",
        ':' => "colon",
        ';' => "semicolon",
        '<' => "less-than sign",
        '=' => "equals sign",
        '>' => "greater-than sign",
        '?' => "question mark",
        '@' => "commercial at",
        '[' => "left square bracket",
        '\\' => "reverse solidus",
        ']' => "right square bracket",
        '^' => "circumflex accent",
        '`' => "grave accent",
        '{' => "left curly bracket",
        '|' => "vertical line",
        '}' => "right curly bracket",
        '~' => "tilde",
        'X' => "latin capital letter x",
        _ => return format!("U{:x}", c as u32),
    };
    name.to_lowercase().replace('-', "H").replace(' ', "_")
}

/// Turns a path from the repo root (`a/b/c.hy`) into a module spelling (`a.b.c`). Drops everything after the last `.` (the extension).
pub fn module_of(rel: &str) -> String {
    let stem = match rel.rfind('.') {
        Some(at) => &rel[..at],
        None => rel,
    };
    stem.replace('/', ".")
}

/// Resolves a relative module spelling (number of leading dots = levels up) to an absolute spelling from the base module.
pub fn absolute_module(base: &str, name: &str) -> String {
    let rest = name.trim_start_matches('.');
    let dots = name.len() - rest.len();
    if dots == 0 {
        return name.to_string();
    }
    let parts: Vec<&str> = base.split('.').collect();
    let keep = parts.len().saturating_sub(dots);
    let mut out: Vec<&str> = parts[..keep].to_vec();
    if !rest.is_empty() {
        out.push(rest);
    }
    out.join(".")
}

/// Words from splitting the name on `-`, `_`, `.` that are in `words` (lowercase, in order, non-overlapping).
pub fn environment_words_of(name: &str, words: &std::collections::BTreeSet<String>) -> Vec<String> {
    let lowered = name.to_lowercase().replace(['-', '.'], "_");
    let found: std::collections::BTreeSet<String> = lowered.split('_').filter(|w| words.contains(*w)).map(str::to_string).collect();
    found.into_iter().collect()
}

/// Whether the name is all uppercase (Python's `str.isupper` — at least one cased character, and all of them uppercase). To exclude constant names.
pub fn is_upper_name(name: &str) -> bool {
    let cased: Vec<char> = name.chars().filter(|c| c.is_lowercase() || c.is_uppercase()).collect();
    !cased.is_empty() && cased.iter().all(|c| c.is_uppercase())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mangle_matches_hy() {
        // Expected values are the actual output of Hy 1.3.0's hy.mangle.
        let cases = [
            ("stale?", "hyx_staleXquestion_markX"),
            ("-foo", "hyx_XhyphenHminusXfoo"),
            ("a-b.c-d", "a_b.c_d"),
            ("->x", "hyx_XhyphenHminusXXgreaterHthan_signXx"),
            ("foo!", "hyx_fooXexclamation_markX"),
            ("*x*", "hyx_XasteriskXxXasteriskX"),
            (".foo", ".foo"),
            ("..a.b", "..a.b"),
            ("foo-bar?", "hyx_foo_barXquestion_markX"),
            ("local-machine-handlers", "local_machine_handlers"),
            ("_-a", "_hyx_XhyphenHminusXa"),
            ("MODULE-TAGS", "MODULE_TAGS"),
        ];
        for (input, expected) in cases {
            assert_eq!(hy_mangle(input), expected, "{}", input);
        }
    }

    #[test]
    fn module_and_relative_import() {
        assert_eq!(module_of("controllers/core/goal.hy"), "controllers.core.goal");
        assert_eq!(absolute_module("controllers.core.goal", ".types"), "controllers.core.types");
        assert_eq!(absolute_module("controllers.core.goal", ".."), "controllers");
        assert_eq!(absolute_module("controllers.core.goal", "doeff.x"), "doeff.x");
    }

    #[test]
    fn environment_words_split_on_separators() {
        let words = ["local", "wire"].iter().map(|s| s.to_string()).collect();
        assert_eq!(environment_words_of("handlers_machine_local", &words), vec!["local"]);
        assert_eq!(environment_words_of("wire-classifier.local", &words), vec!["local", "wire"]);
        assert!(environment_words_of("wireless", &words).is_empty());
        assert!(is_upper_name("TRANSLATION_HANDLERS"));
        assert!(!is_upper_name("emulated_handlers"));
    }
}
