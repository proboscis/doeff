//! 名前の綴りの変換 — Hy の mangle(Hy 1.x の `hy.mangle` と同じ結果)・module の綴り・相対 import の解決。
//! 登録簿の鍵は Python の検査(agora の scripts)が `hy.mangle` で作った綴りなので、鍵を揃えるために同じ規則で直す。

/// Hy の名前を Python の名前へ直す(`hy.mangle` と同じ — dotted は区切りごと、`-` は `_`、識別子にならない文字は `XnameX`)。
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

/// Python の識別子の先頭に置ける文字か(ASCII の外は英字・数字の類を識別子の文字とみなす)。
fn is_identifier_start(c: char) -> bool {
    c == '_' || c.is_alphabetic()
}

/// Python の識別子の 2 文字目以降に置ける文字か。
fn is_identifier_continue(c: char) -> bool {
    c == '_' || c.is_alphanumeric()
}

/// 文字列が Python の識別子か。
fn is_identifier(text: &str) -> bool {
    let mut chars = text.chars();
    match chars.next() {
        Some(first) => is_identifier_start(first) && chars.all(is_identifier_continue),
        None => false,
    }
}

/// mangle の `XnameX` の name(Unicode の文字の名を小文字にし、`-` を `H`・空白を `_` にした物)。
/// ASCII の記号は表で引き、表に無い文字は `U` と 16 進の code point にする。
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

/// repo の根からの path(`a/b/c.hy`)を module の綴り(`a.b.c`)にする。最後の `.` から後(拡張子)を外す。
pub fn module_of(rel: &str) -> String {
    let stem = match rel.rfind('.') {
        Some(at) => &rel[..at],
        None => rel,
    };
    stem.replace('/', ".")
}

/// 相対の module の綴り(先頭の点の数 = 上る段)を、base の module から絶対の綴りへ解く。
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

/// 名を `-`・`_`・`.` で切った語のうち、words に在る物(小文字・並べた・重なりなし)。
pub fn environment_words_of(name: &str, words: &std::collections::BTreeSet<String>) -> Vec<String> {
    let lowered = name.to_lowercase().replace(['-', '.'], "_");
    let found: std::collections::BTreeSet<String> = lowered.split('_').filter(|w| words.contains(*w)).map(str::to_string).collect();
    found.into_iter().collect()
}

/// 名が大文字だけか(Python の `str.isupper` — 大文字小文字のある文字が 1 つ以上で、全部が大文字)。定数の名を外すため。
pub fn is_upper_name(name: &str) -> bool {
    let cased: Vec<char> = name.chars().filter(|c| c.is_lowercase() || c.is_uppercase()).collect();
    !cased.is_empty() && cased.iter().all(|c| c.is_uppercase())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mangle_matches_hy() {
        // 期待値は Hy 1.3.0 の hy.mangle の実出力。
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
