//! 名の綴りを file の字面で探す道具 — 1 file の実行が、repo の Hy の全部を解析せずに、名を綴る file だけを選ぶ(card ki-58bfad2c13a1 の残り)。
//!
//! 1 file の検査が引く repo の事実(呼び手が引数に渡した Program・失敗の型の宣言)は、引く名が決まっている。その事実を持つ file は、名の最後の段の綴りを
//! 字面に必ず含む: 記号は `Scope::qualify` で module まで解かれ(解き方は `smells::Scope::qualify` の 1 か所)、解いた名の最後の段は記号の最後の段の
//! mangle で、import の束縛も別名も module の段も最後の段には効かない。mangle は `-` を `_` に直す(先頭の `_` の並びと、その次の 1 字は直さない)だけなので、
//! 名の `_` は `-` とも綴れる。名に識別子でない文字が入る時(mangle が `hyx_` へ逃がした名)は綴りを決められないので、字面で絞らない(どの file も合う)。

use regex::Regex;

use super::names::is_identifier;

/// 名 1 つの綴りの探り方。
pub struct Spelling {
    /// 名の綴りに、後ろの区切りを足した正規表現。None = 綴りを決められない名 — どの file も合う物として扱う。
    pattern: Option<Regex>,
    /// 綴りを決められる名の、`-` を `_` に畳んだ綴り(= 名そのもの — mangle 済みの名に `-` は無い)。覚えの引き(`blob_spell`)が使う。
    folded: Option<String>,
}

impl Spelling {
    /// mangle 済みの名(module の段を除いた最後の段)の綴り。
    pub fn of(name: &str) -> Spelling {
        let pattern = spelling_pattern(name).and_then(|spelling| Regex::new(&format!("{spelling}{SYMBOL_END}")).ok());
        let folded = pattern.as_ref().map(|_| name.to_string());
        Spelling { pattern, folded }
    }

    /// 綴りを決められる名の綴り(決められない名は None — どの file も合う)。
    pub fn folded_name(&self) -> Option<&str> {
        self.folded.as_deref()
    }

    /// source に、記号の最後の段として名を綴った所が在るか。綴りで始まる正規表現は頭の字が定まって速く探せるので(前の区切りまで
    /// 正規表現に入れると頭が定まらない)、当たりを探してから前の区切りを当たりごとに見る。当たりは後ろの区切りの字までを含むが、綴りに
    /// 区切りの字は無いので、前の区切りが合わずに捨てた当たりが、次の当たりの頭を食う事は無い。
    pub fn in_source(&self, source: &str) -> bool {
        let Some(pattern) = &self.pattern else { return true };
        pattern.find_iter(source).any(|found| starts_a_segment(source, found.start()))
    }
}

/// 記号の最後の段の後ろ。記号は空白と `( ) [ ] { } " ;` で終わる(読み取り器の `is_delimiter`)。
/// 長い記号の途中(`rows-by-text-all` の `rows-by-text`)や module の path の段(`controllers.entry.system` の `entry`)は、名の綴りでも最後の段ではない。
/// 空白は ASCII の物(読み取り器の `is_ascii_whitespace` と垂直タブ)— 数え損なう事はなく、余分に合っても解析する file が増えるだけで答えは変わらない。
const SYMBOL_END: &str = r#"(?:[ \t\n\x0B\x0C\r()\[\]{}";]|$)"#;

/// `at`(名の綴りの頭)が記号の頭か、`.` の次か — 最後の段の前。
fn starts_a_segment(source: &str, at: usize) -> bool {
    match at.checked_sub(1).map(|before| source.as_bytes()[before]) {
        None => true,
        Some(byte) => byte.is_ascii_whitespace() || matches!(byte, 0x0B | b'.' | b'(' | b')' | b'[' | b']' | b'{' | b'}' | b'"' | b';'),
    }
}

/// mangle 済みの名 `name` に mangle される綴り全部に合う正規表現。綴りを決められない名は None。
fn spelling_pattern(name: &str) -> Option<String> {
    let stripped = name.trim_start_matches('_');
    // 識別子でない文字は mangle が `hyx_` へ逃がす(逃がす前の綴りは名から戻せない)。
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

    /// 名の綴りの正規表現は、その名に mangle される綴り(`-` と `_` の取り合わせ)を全部拾う。
    #[test]
    fn the_spelling_pattern_matches_every_spelling_that_mangles_to_the_name() {
        for name in ["rows_by_text", "a", "a_b", "a__b", "_private_fn", "__dunder__", "x_1_y", "名_前", "ab_"] {
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
                    assert!(pattern.is_match(&spelling), "{name} に mangle される {spelling} を拾わない");
                }
            }
        }
        assert!(spelling_pattern("hyx_checkXquestion_markX").is_none());
        assert!(spelling_pattern("_hyx_a").is_none());
        assert!(spelling_pattern("").is_none());
    }

    /// 記号の最後の段として綴った所だけを拾う — 記号の頭・`.` の次・各種の括弧と引用符の隣は拾い、長い記号の途中・module の path の段は拾わない。
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
        // 綴りを決められない名はどの file も合う。
        assert!(Spelling::of("hyx_checkXquestion_markX").in_source("nothing here"));
    }
}
