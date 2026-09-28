//! 読めない Hy の file の知らせ(DOEFF128)。
//!
//! 読み取り器が括弧か文字列の閉じない所を見つけた file は、規則の判定が空になる(読めた所までしか事実が無い)。前は理由の文を
//! 全体の `errors` に積むだけで、エディタのその file の違反の欄は空、agent の hook は何も言わなかった(2026-09-28 — agora の
//! controllers/durable/protocol/contract.hy を読み取り器の誤りで読めず、違反が黙って空になった)。だから読めない file は、
//! 有効な規則の一覧に関わらず、その file の最初の読めない所に error の違反 DOEFF128 として出す(エディタの欄・hook・text の全部に出る)。

use std::path::Path;

use doeff_indexer::hy_index::reader::{ReadIssue, Reader};

use super::explain::Explanation;
use super::rule::ProjectRule;
use super::{Finding, FindingOrigin};
use crate::models::Severity;
use crate::position::LineIndex;

/// 読み取りの壊れた箇所の byte の位置と短い説明。
fn describe(issue: &ReadIssue) -> (usize, &'static str) {
    match issue {
        ReadIssue::Unclosed { open, .. } => (*open, "開き括弧が file の終わりまで閉じない"),
        ReadIssue::Mismatched { at, .. } => (*at, "閉じ括弧が開き括弧と対応しない"),
        ReadIssue::StrayCloser { at } => (*at, "余った閉じ括弧"),
        ReadIssue::UnterminatedString { start } => (*start, "文字列が file の終わりまで閉じない"),
    }
}

/// 1 つの Hy の file を読み、読めない所があれば DOEFF128 の違反を 1 つ返す(位置は最初の読めない所)。
pub fn finding(rel: &str, path: &Path, source: &str) -> Option<Finding> {
    let mut reader = Reader::new(source, 0, source.len());
    reader.read_all();
    let mut issues: Vec<(usize, &'static str)> = reader.issues.iter().map(describe).collect();
    issues.sort_by_key(|(at, _)| *at);
    let (first, what) = *issues.first()?;
    let lines = LineIndex::new(source);
    let range = lines.range(first, (first + 1).min(source.len()));
    let rule = ProjectRule::UnreadableFile;
    let line = range.start.line + 1;
    Some(Finding {
        rule,
        law: None,
        adr: None,
        severity: Severity::Error,
        path: path.to_path_buf(),
        key: format!("{}::{}", rel, rule.id()),
        rel: rel.to_string(),
        range,
        message: format!(
            "{} を読めない — 読めない所が {} か所(最初は {} 行目: {})。この file の規則の判定は読めた所までで、違反が欠けている",
            rel,
            issues.len(),
            line,
            what
        ),
        hint: rule.hint().to_string(),
        registered: false,
        explanation: Explanation {
            subject: format!("{} の {} 行目 — {}", rel, line, what),
            reason: "doeff-linter の Hy の読み取り器がこの file を最後まで読めなかった。読めた所までしか規則を判じないので、この file の違反は欠けている(空に見えても合格ではない)。Hy 本体が読めるなら読み取り器の誤りなので doeff-linter に知らせ、Hy も読めないなら括弧か文字列を直す。".to_string(),
            law_statement: None,
        },
        origin: FindingOrigin::Linter,
        probability: None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_unreadable_file_is_an_error_at_its_first_broken_place() {
        let found = finding("app/x.hy", Path::new("/r/app/x.hy"), "(defk ok [] 1)\n(defk broken [x]\n  (print \"no end)\n").expect("読めない");
        assert_eq!(found.rule, ProjectRule::UnreadableFile);
        assert_eq!(found.severity, Severity::Error);
        assert_eq!(found.key, "app/x.hy::DOEFF128");
        assert_eq!(found.range.start.line, 1);
        assert!(found.message.contains("読めない所が"), "{}", found.message);
        // 読める file は何も出さない(f 文字列の欄の中の文字列も読める)。
        assert!(finding("app/y.hy", Path::new("/r/app/y.hy"), "(print f\"{(.join \"; \" xs)}\")\n").is_none());
    }
}
