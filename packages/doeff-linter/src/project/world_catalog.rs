//! doeff の実 I/O の handler の目録(`data/world_handlers.json` — agora-redesign #1209)。
//!
//! 何が外の世界に触れる handler かは doeff の事実なので、利用者の repo の architecture.hy ごとに書かせず、doeff の側に 1 つ置く
//! (生の I/O の目録 doeff-indexer `data/raw_side_effects.json` と同じ考え)。linter は:
//!   * 目録の handler をどれも DOEFF131(名簿の外で名指す)と DOEFF133(テストの種類)の相手にする。
//!   * `:world-handlers` の `:wraps` は目録に在る handler だけ(無ければ設定の誤り)。
//!   * 目録の触れる先(`touches`)を、DOEFF133 が `:edge-touches` と突き合わせる。
//!
//! 形: `{"handlers": [{"handler": "doeff_core_effects.os_file:os-file-handler", "touches": ["file"], "why": "…"}]}`。

use std::collections::BTreeMap;
use std::sync::OnceLock;

use serde::Deserialize;

use super::architecture::{Architecture, DefinitionRef, WorldTouch};
use doeff_indexer::hy_index::RawCategory;

#[derive(Deserialize)]
struct CatalogFile {
    handlers: Vec<CatalogEntry>,
}

#[derive(Deserialize)]
struct CatalogEntry {
    handler: String,
    touches: Vec<String>,
    /// false = 名簿の :wraps には書けるが、実 I/O として数えない(触れる先を別の行へ移した後、repo の :wraps が移るまでの行)。
    #[serde(default = "counted_by_default")]
    counted: bool,
    /// 目録に載せた根拠(読むのは人だけ)。
    #[allow(dead_code)]
    why: String,
}

/// 目録の handler 1 つ。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CatalogHandler {
    pub definition: DefinitionRef,
    pub touches: Vec<WorldTouch>,
    /// false = :wraps に書けるが数えない(移行の間の行 — agora-redesign #1318)。
    pub counted: bool,
}

fn counted_by_default() -> bool {
    true
}

impl CatalogHandler {
    /// この行を名指す所を実 I/O として数えるか(数えない行は :wraps に書けるだけ)。
    pub fn counts(&self) -> bool {
        self.counted
    }
}

/// 目録(完全修飾名 → handler)。
#[derive(Debug, Clone, Default)]
pub struct WorldCatalog {
    pub handlers: BTreeMap<String, CatalogHandler>,
}

impl WorldCatalog {
    /// 目録の doeff の実 I/O の handler の完全修飾名 → (綴り, それを宣言の :wraps に挙げた名簿の定義の綴りの列, 触れる先)。
    /// 宣言(architecture)と目録を突き合わせるのは目録の側 — 宣言は目録を読まない(agora-redesign #2124)。
    pub fn world_targets(&self, architecture: &Architecture) -> BTreeMap<String, (String, Vec<String>, Vec<WorldTouch>)> {
        let wrapped = architecture.wrapped_targets();
        self.handlers
            .iter()
            .map(|(target, handler)| {
                let by = wrapped.get(target).map(|(_, by)| by.clone()).unwrap_or_default();
                (target.clone(), (handler.definition.spelling(), by, handler.touches.clone()))
            })
            .collect()
    }

    /// JSON の中身を目録にする(読めない要素は理由の列)。
    pub fn parse(text: &str) -> Result<WorldCatalog, Vec<String>> {
        let file: CatalogFile = serde_json::from_str(text).map_err(|e| vec![format!("world_handlers.json を読めない: {}", e)])?;
        let mut problems = Vec::new();
        let mut handlers = BTreeMap::new();
        for entry in file.handlers {
            let Some(definition) = DefinitionRef::parse(&entry.handler) else {
                problems.push(format!("world_handlers.json の {} は \"module.path:名\" の綴りでない", entry.handler));
                continue;
            };
            let mut touches = Vec::new();
            for word in &entry.touches {
                match WorldTouch::parse(word) {
                    Some(touch) if !touches.contains(&touch) => touches.push(touch),
                    Some(_) => problems.push(format!("world_handlers.json の {} の touches の {} が 2 度", entry.handler, word)),
                    None => problems.push(format!("world_handlers.json の {} の touches の {} は語の外", entry.handler, word)),
                }
            }
            if touches.is_empty() {
                problems.push(format!("world_handlers.json の {} に touches が無い", entry.handler));
            }
            if handlers.insert(definition.target(), CatalogHandler { definition, touches, counted: entry.counted }).is_some() {
                problems.push(format!("world_handlers.json の {} が 2 度載っている", entry.handler));
            }
        }
        if problems.is_empty() {
            Ok(WorldCatalog { handlers })
        } else {
            Err(problems)
        }
    }

    /// この binary に同梱した目録(読みは 1 度だけ)。
    pub fn bundled() -> &'static WorldCatalog {
        static BUNDLED: OnceLock<WorldCatalog> = OnceLock::new();
        BUNDLED.get_or_init(|| {
            WorldCatalog::parse(include_str!("../../data/world_handlers.json")).expect("同梱の world_handlers.json が読めない(検 bundled_catalog_is_well_formed が赤のはず)")
        })
    }
}

/// 生の I/O の証拠の分類を、触れる先の語へ写す(async の待ち・thread は thread、時刻と乱数は clock — どちらも決定的でない源)。
pub fn touch_of_raw(category: RawCategory) -> WorldTouch {
    match category {
        RawCategory::Http => WorldTouch::Http,
        RawCategory::Async | RawCategory::Thread => WorldTouch::Thread,
        RawCategory::Time | RawCategory::Random => WorldTouch::Clock,
        RawCategory::File => WorldTouch::File,
        RawCategory::Process => WorldTouch::Process,
        RawCategory::Env => WorldTouch::Env,
        RawCategory::Network => WorldTouch::Network,
        RawCategory::Db => WorldTouch::Db,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bundled_catalog_is_well_formed() {
        let parsed = WorldCatalog::parse(include_str!("../../data/world_handlers.json"));
        let catalog = parsed.unwrap_or_else(|problems| panic!("同梱の目録が読めない:\n{}", problems.join("\n")));
        assert!(catalog.handlers.contains_key("doeff_core_effects.os_file.os_file_handler"), "os-file-handler が目録に無い");
        // 層 2 だけの入口(子 process の claude を起こす — agora-redesign #3507)も実 I/O の handler として目録に在る(綴りは 2 つ)。
        for name in ["doeff_agents.handlers.claude_process_layer_handler", "doeff_agents.claude_process_layer_handler"] {
            assert!(catalog.handlers.contains_key(name), "{} が目録に無い", name);
        }
    }

    #[test]
    fn misreadings_are_reasons() {
        let bad = r#"{"handlers": [
            {"handler": "a.b:x", "touches": ["file", "smoke"], "why": "w"},
            {"handler": "nocolon", "touches": ["file"], "why": "w"},
            {"handler": "a.b:y", "touches": [], "why": "w"},
            {"handler": "a.b:x", "touches": ["file"], "why": "w"}]}"#;
        let problems = WorldCatalog::parse(bad).unwrap_err().join("\n");
        for needle in ["smoke は語の外", "nocolon は", "a.b:y に touches が無い", "a.b:x が 2 度載っている"] {
            assert!(problems.contains(needle), "{} が無い:\n{}", needle, problems);
        }
    }
}
