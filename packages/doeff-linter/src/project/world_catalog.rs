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
        for name in ["doeff_agents._handlers.claude_process_layer_handler", "doeff_agents.claude_process_layer_handler"] {
            assert!(catalog.handlers.contains_key(name), "{} が目録に無い", name);
        }
        // doeff_core_effects の子を _ へ移した後も、移し替えの module が残る間は古い綴りで :wraps に書ける(card ki-c09b3b527fad)。
        for name in [
            "doeff_core_effects._handlers.await_handler",
            "doeff_core_effects.await_handler",
            "doeff_core_effects.handlers.await_handler",
            "doeff_core_effects.handlers.env_var_ask",
        ] {
            assert!(catalog.handlers.contains_key(name), "{} が目録に無い", name);
        }
        // 計算・VM・測りの子も同じく、_ の綴り・入口の綴り・古い綴りの 3 つで在る(card ki-32623e1651c6)。
        for (child, handler) in [
            ("thread_pool_compute", "thread_pool_compute_handler"),
            ("gc_freeze", "gc_freeze_handler"),
            ("faulthandler_stack_dump", "faulthandler_stack_dump_handler"),
            // file・memo・cache の子も同じ(card ki-375e0a35b7a0)。
            ("os_file", "os_file_handler"),
            ("cache_handlers", "sqlite_cache_handler"),
            ("memo_handlers", "sqlite_memo_handler"),
            // process の子も同じ(card ki-8d33df2c82df)。
            ("os_process", "subprocess_handler"),
            ("stop_signal_handlers", "os_signal_stop_handler"),
            ("process_meter", "process_meter_handler"),
            ("process_latest", "process_latest_handler"),
            // http・ws の子も同じ(card ki-d4ba562a1367)。
            ("http_handlers", "http_production_handler"),
            ("http_handlers", "http_fixture_handler"),
            ("aiohttp_ws_client", "aiohttp_ws_link_handler"),
        ] {
            for name in [
                format!("doeff_core_effects._{child}.{handler}"),
                format!("doeff_core_effects.{handler}"),
                format!("doeff_core_effects.{child}.{handler}"),
            ] {
                assert!(catalog.handlers.contains_key(&name), "{} が目録に無い", name);
            }
        }
    }

    /// 子を _ へ移した handler の行は、移し替えの module(1 行目 `# moved-submodule-shim: <古い path> -> <新しい path>`)が残る間、
    /// 古い綴りの行も目録に持つ。外の repo の :wraps は古い綴りのまま書いてあり、目録から消すと設定の誤りで全部の commit が止まる
    /// (42f897000 が aiohttp-http-server の古い綴りを _ の綴りへ置き換え、agora-controllers の commit-msg の hook を止めた・card ki-cd6a513ae4b5)。
    #[test]
    fn catalog_keeps_the_old_spelling_of_every_moved_handler() {
        let packages = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..");
        let mut moved_from = BTreeMap::new();
        for entry in walkdir::WalkDir::new(&packages)
            .into_iter()
            .filter_entry(|e| !matches!(e.file_name().to_str(), Some("target" | "node_modules" | ".venv")))
            .filter_map(Result::ok)
            .filter(|e| e.path().extension().is_some_and(|x| x == "py"))
        {
            let Ok(text) = std::fs::read_to_string(entry.path()) else { continue };
            let Some(mark) = text.lines().next().and_then(|line| line.strip_prefix("# moved-submodule-shim: ")) else { continue };
            if let Some((old, new)) = mark.split_once(" -> ") {
                moved_from.insert(new.trim().to_string(), old.trim().to_string());
            }
        }
        // aiohttp_http_server の殻は入口の名と同じ名なので消した(ADR-DOE-API-001 R34・card ki-db44e4d3efe4)— 残る殻で探しを確かめる。
        assert!(moved_from.contains_key("doeff_core_effects._thread_pool_compute"), "移し替えの module を見つけられない(探す根 {})", packages.display());
        let catalog = WorldCatalog::bundled();
        let missing: Vec<String> = catalog
            .handlers
            .values()
            .filter_map(|handler| {
                let spelling = handler.definition.spelling();
                let (module, name) = spelling.split_once(':')?;
                let old = moved_from.get(module)?;
                let old_spelling = format!("{}:{}", old, name);
                let known = DefinitionRef::parse(&old_spelling).is_some_and(|d| catalog.handlers.contains_key(&d.target()));
                (!known).then(|| format!("{} の古い綴り {}", spelling, old_spelling))
            })
            .collect();
        assert!(missing.is_empty(), "移し替えの module が残るのに古い綴りの行が目録に無い:\n{}", missing.join("\n"));
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
