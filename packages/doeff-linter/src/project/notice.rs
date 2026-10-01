//! 設定の知らない鍵と規則の ID の知らせ(DOEFF100・agora-redesign #848)。
//!
//! 設定(pyproject の `[tool.doeff-linter]` の全部の段・architecture.hy)は linter より先に進むことがある — agora-controllers が
//! 新しい鍵を書いた後、置き場の binary が組み直されるまでの間。その間に lint 全体を設定の誤り(終了コード 2)で止めると、
//! エディタの違反の欄が空になり hook も黙る(2026-09-28 に 4 回)。だから知らない鍵と、この binary に無い規則の ID
//! (`DOEFF` と 3 桁の形)は、その鍵だけを読まずに残りの規則を走らせ、黙って捨てずに設定の file のその行へ warning の違反
//! DOEFF100 として出す。書き違いも同じ形で見える(形の崩れた ID・型の違う値・存在しない層の名は今までどおり誤り)。

use std::path::{Path, PathBuf};

use super::explain::Explanation;
use super::rule::ProjectRule;
use super::paths::relative_path;
use super::{Finding, FindingOrigin};
use crate::models::Severity;
use crate::position::{line_range, Range};

/// 知らなかった物の種類。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum NoticeKind {
    /// 設定の鍵(`semantic.proxy_url`・architecture.hy の `:dependency-layers` など)。
    Key,
    /// 設定が参照する規則の ID(この binary に無い `DOEFF` と 3 桁の ID)。
    RuleId(String),
}

/// 知らせ 1 件 — どの file のどの鍵を、どこで読まなかったか。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConfigNotice {
    pub file: PathBuf,
    /// 鍵の path(`semantic.proxy_url`・`laws.<名>.rules`・`defarchitecture :layers[0] :dependency-layers`)。
    pub key: String,
    pub kind: NoticeKind,
    pub range: Range,
}

/// 設定が参照したが、この binary に無い規則の ID(file と位置は設定の file を読む所で決める)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UnknownRuleRef {
    /// 参照していた鍵の path(`laws.<名>.rules`・`registry.reconciling`・`rules.<ID>`)。
    pub key: String,
    pub id: String,
}

/// 規則の ID の形(`DOEFF` と 3 桁・大文字小文字は問わない)か — この形で知らない ID は「この binary より新しい規則」として
/// 知らせに回し、形の崩れた ID(`DOEFF1O1` など)は書き違いとして今までどおり誤りにする。
pub fn is_rule_id_shape(id: &str) -> bool {
    let upper = id.to_uppercase();
    upper.len() == 8 && upper.starts_with("DOEFF") && upper[5..].chars().all(|c| c.is_ascii_digit())
}

/// TOML の本文の中で鍵の path(`.` 区切り・配列の添字は数字の段)が書かれた行の頭の byte の位置。
/// 節の見出し(`[tool.doeff-linter.<段>]`・`[[…]]`・節の中身だけの file の `[<段>]`)を長い順に探し、見出しの下の最初の鍵の行を
/// 探す。見つからなければ `[tool.doeff-linter]` の見出しの行、それも無ければ 0。
pub fn locate_toml_key(text: &str, key: &str) -> usize {
    let segments: Vec<&str> = key.split('.').filter(|s| !s.is_empty() && !s.chars().all(|c| c.is_ascii_digit())).collect();
    let lines: Vec<(usize, &str)> = line_starts(text);
    for n in (1..=segments.len()).rev() {
        let prefix = segments[..n].join(".");
        let headers = [
            format!("[tool.doeff-linter.{}]", prefix),
            format!("[[tool.doeff-linter.{}]]", prefix),
            format!("[{}]", prefix),
            format!("[[{}]]", prefix),
        ];
        let Some(index) = lines.iter().position(|(_, line)| headers.iter().any(|h| line.trim_start().starts_with(h.as_str()))) else {
            continue;
        };
        if n == segments.len() {
            return lines[index].0;
        }
        return key_line_in_section(&lines[index + 1..], segments[n]).unwrap_or(lines[index].0);
    }
    let top = lines.iter().position(|(_, line)| line.trim_start().starts_with("[tool.doeff-linter]"));
    let body = top.map(|i| &lines[i + 1..]).unwrap_or(&lines[..]);
    match segments.first().and_then(|first| key_line_in_section(body, first)) {
        Some(at) => at,
        None => top.map(|i| lines[i].0).unwrap_or(0),
    }
}

/// 節の本文(次の見出しの手前まで)で `<鍵> =` の行を探す(鍵は裸か引用符つき)。
fn key_line_in_section(lines: &[(usize, &str)], key: &str) -> Option<usize> {
    for (start, line) in lines {
        let trimmed = line.trim_start();
        if trimmed.starts_with('[') {
            return None;
        }
        let rest = trimmed
            .strip_prefix(key)
            .or_else(|| trimmed.strip_prefix(&format!("\"{}\"", key)))
            .or_else(|| trimmed.strip_prefix(&format!("'{}'", key)));
        if rest.is_some_and(|r| r.trim_start().starts_with('=') || r.starts_with('.')) {
            return Some(*start);
        }
    }
    None
}

/// 行の頭の byte の位置と行の本文の列。
fn line_starts(text: &str) -> Vec<(usize, &str)> {
    let mut out = Vec::new();
    let mut start = 0usize;
    for line in text.split_inclusive('\n') {
        out.push((start, line.trim_end_matches(['\n', '\r'])));
        start += line.len();
    }
    out
}

/// TOML の設定の file の中の知らない鍵の知らせ(位置は本文から探す)。
pub fn key_notice(file: &Path, text: &str, key: &str) -> ConfigNotice {
    ConfigNotice { file: file.to_path_buf(), key: key.to_string(), kind: NoticeKind::Key, range: line_range(text, locate_toml_key(text, key)) }
}

/// 設定の file の中の知らない規則の ID の知らせ(位置は ID の綴りが最初に出る行・無ければ鍵の行)。
pub fn rule_notice(file: &Path, text: &str, unknown: &UnknownRuleRef) -> ConfigNotice {
    let at = text.find(unknown.id.as_str()).unwrap_or_else(|| locate_toml_key(text, &unknown.key));
    ConfigNotice { file: file.to_path_buf(), key: unknown.key.clone(), kind: NoticeKind::RuleId(unknown.id.clone()), range: line_range(text, at) }
}

/// 知らせを DOEFF100 の違反にする(warning・登録簿の外・決定的な規則)。
pub fn findings(notices: &[ConfigNotice], root: &Path) -> Vec<Finding> {
    let rule = ProjectRule::UnknownConfigKey;
    notices
        .iter()
        .map(|notice| {
            let rel = relative_path(root, &notice.file).unwrap_or_else(|| notice.file.to_string_lossy().into_owned());
            let (message, subject, what) = match &notice.kind {
                NoticeKind::Key => (
                    format!("設定の鍵 {} をこの linter は知らない — 読まずに残りの規則を走らせた", notice.key),
                    format!("これは {} の鍵 {}", rel, notice.key),
                    "この鍵",
                ),
                NoticeKind::RuleId(id) => (
                    format!("設定の {} が参照する規則 {} をこの linter は知らない — その参照だけを読まずに残りの規則を走らせた", notice.key, id),
                    format!("これは {} の {} が参照する規則 {}", rel, notice.key, id),
                    "この規則",
                ),
            };
            let detail = match &notice.kind {
                NoticeKind::Key => notice.key.clone(),
                NoticeKind::RuleId(id) => format!("{}::{}", notice.key, id),
            };
            Finding {
                rule,
                law: None,
                adr: None,
                severity: Severity::Warning,
                path: notice.file.clone(),
                key: format!("{}::{}::{}", rel, rule.id(), detail),
                rel,
                range: notice.range,
                message,
                hint: rule.hint().to_string(),
                registered: false,
                base_severity: Severity::Warning,
                standing: super::Standing::New,
                explanation: Explanation {
                    subject,
                    reason: format!(
                        "この linter(doeff {} から組んだ)は{}を知らない。linter が設定より古い(開発版の置き場の binary は doeff の本線から自動で組み直される — それを待つ)か、書き違い",
                        crate::build_info::BUILD_COMMIT,
                        what
                    ),
                    law_statement: None,
                },
                origin: FindingOrigin::Linter,
                probability: None,
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    const PYPROJECT: &str = "[project]\nname = \"x\"\n\n[tool.doeff-linter]\nenable = [\"ALL\"]\nnew_top = 1\n\n[tool.doeff-linter.semantic]\nmodel = \"m\"\nproxy_url = \"http://x\"\n\n[tool.doeff-linter.smells]\nshape_check_layers = []\n";

    /// 行の番号(0 始まり)を返す。
    fn line_of(text: &str, offset: usize) -> usize {
        text[..offset].matches('\n').count()
    }

    #[test]
    fn locates_nested_top_level_and_section_keys() {
        assert_eq!(line_of(PYPROJECT, locate_toml_key(PYPROJECT, "semantic.proxy_url")), 9);
        assert_eq!(line_of(PYPROJECT, locate_toml_key(PYPROJECT, "smells")), 11);
        assert_eq!(line_of(PYPROJECT, locate_toml_key(PYPROJECT, "new_top")), 5);
        assert_eq!(line_of(PYPROJECT, locate_toml_key(PYPROJECT, "nowhere")), 3, "見つからない鍵は [tool.doeff-linter] の行");
    }

    #[test]
    fn rule_id_shape_separates_newer_rules_from_typos() {
        assert!(is_rule_id_shape("DOEFF999"));
        assert!(is_rule_id_shape("doeff127"));
        assert!(!is_rule_id_shape("DOEFF1O1"));
        assert!(!is_rule_id_shape("DOEFF12"));
    }
}
