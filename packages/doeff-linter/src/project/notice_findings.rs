//! 設定の知らせ(DOEFF100)を違反の形(Finding)へ直す — 説明(Explanation)と違反の型を読む側を、知らせの型と鍵の位置の読み
//! (`notice`)から分けた。`notice` は宣言を読む `architecture`・設定を組む `settings` が下から読むので、違反と説明を読まない(agora-redesign #2124)。

use std::path::Path;

use super::explain::Explanation;
use super::notice::{ConfigNotice, NoticeKind};
use super::paths::relative_path;
use super::report::{Finding, FindingOrigin};
use super::rule::ProjectRule;
use crate::models::Severity;

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
                standing: super::report::Standing::New,
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
