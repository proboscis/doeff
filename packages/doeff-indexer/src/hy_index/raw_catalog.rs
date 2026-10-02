//! 生の副作用の目録 — `data/raw_side_effects.json` を build 時に取り込み、型のある表にする。
//! 利用者の追加(`--raw-catalog-extra <json>`)も同じ表へ足す。判定(`raw.rs`)はこの表だけを見る。

use serde::Deserialize;
use std::collections::BTreeMap;

/// 同梱の目録(宣言の唯一の場所)。
const BUNDLED_CATALOG: &str = include_str!("../../data/raw_side_effects.json");

/// 生の副作用の分類(閉じた集合)。
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Deserialize, serde::Serialize)]
#[serde(rename_all = "lowercase")]
pub enum RawCategory {
    Http,
    Async,
    Time,
    Random,
    File,
    Process,
    Env,
    Network,
    Db,
    Thread,
}

impl RawCategory {
    /// 目録の順(表示の順)。
    pub const ALL: [RawCategory; 10] = [
        RawCategory::Http,
        RawCategory::Async,
        RawCategory::Time,
        RawCategory::Random,
        RawCategory::File,
        RawCategory::Process,
        RawCategory::Env,
        RawCategory::Network,
        RawCategory::Db,
        RawCategory::Thread,
    ];

    /// 契約の綴り。
    pub fn as_str(self) -> &'static str {
        match self {
            RawCategory::Http => "http",
            RawCategory::Async => "async",
            RawCategory::Time => "time",
            RawCategory::Random => "random",
            RawCategory::File => "file",
            RawCategory::Process => "process",
            RawCategory::Env => "env",
            RawCategory::Network => "network",
            RawCategory::Db => "db",
            RawCategory::Thread => "thread",
        }
    }

    /// 綴りから分類を引く(知らなければ None)。
    pub fn parse(text: &str) -> Option<RawCategory> {
        RawCategory::ALL.into_iter().find(|category| category.as_str() == text)
    }
}

/// method 名だけで拾う証拠(弱い)と、それを数える条件の module。
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct RawMethod {
    pub name: String,
    /// この module が file の import か定義の中の参照に見える時だけ数える。空なら常に数える。
    pub context: Vec<String>,
    /// 呼びの受け手を除く引数がこの数より多い呼びは数えない(書かなければ上限なし)。同じ名の別の型の method と見分けるため —
    /// `Path.replace(target)` は 1 つ・文字列の `(.replace s "/" "_")` は 2 つ(agora-redesign #3014)。呼びの頭でない参照
    /// (`(. p replace)` を値として渡す等)は引数が分からないので、今までどおり数える。
    #[serde(default)]
    pub max_args: Option<usize>,
}

/// 分類 1 つの目録。
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct RawCatalogEntry {
    pub category: RawCategory,
    pub patterns: Vec<String>,
    pub builtins: Vec<String>,
    pub methods: Vec<RawMethod>,
}

/// 目録の全体。
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct RawCatalog {
    pub categories: Vec<RawCatalogEntry>,
    /// pattern に合っても副作用でない名前(純粋な module・小文字の例外の型)。
    pub ignored: Vec<String>,
    /// この語で終わる名前は例外の型として数えない。
    pub exception_suffixes: Vec<String>,
}

/// 同梱の file の外側の形(説明の欄を読み飛ばすため)。
#[derive(Deserialize)]
struct BundledFile {
    categories: Vec<RawCatalogEntry>,
    ignored: Vec<String>,
    exception_suffixes: Vec<String>,
}

impl RawCatalog {
    /// 同梱の目録を読む。同梱の file は build と一緒に検めるので、読めなければ build の誤り(テストが赤にする)。
    pub fn bundled() -> Result<RawCatalog, String> {
        let file: BundledFile =
            serde_json::from_str(BUNDLED_CATALOG).map_err(|error| format!("同梱の目録を読めない: {}", error))?;
        Ok(RawCatalog {
            categories: file.categories,
            ignored: file.ignored,
            exception_suffixes: file.exception_suffixes,
        })
    }

    /// 利用者の追加(分類 → 名前の配列の JSON)を足す。名前の書き方: `.name` は method 名(弱い・条件なし)、
    /// `builtin:name` は組み込み、それ以外は dotted の名前。知らない分類・配列でない値は理由を返して飛ばす。
    pub fn with_extra(&self, extra: &serde_json::Value) -> (RawCatalog, Vec<String>) {
        let mut catalog = self.clone();
        let mut problems = Vec::new();
        let Some(object) = extra.as_object() else {
            problems.push("--raw-catalog-extra は「分類 → 名前の配列」の object であること".to_string());
            return (catalog, problems);
        };
        let mut added: BTreeMap<RawCategory, Vec<String>> = BTreeMap::new();
        for (key, value) in object {
            let Some(category) = RawCategory::parse(key) else {
                let known: Vec<&str> = RawCategory::ALL.iter().map(|c| c.as_str()).collect();
                problems.push(format!("rawSideEffects の分類 \"{}\" は知らない(使える分類: {})", key, known.join(", ")));
                continue;
            };
            let Some(items) = value.as_array() else {
                problems.push(format!("rawSideEffects.{} が配列でない", key));
                continue;
            };
            for item in items {
                match item.as_str().filter(|text| !text.trim().is_empty()) {
                    Some(text) => added.entry(category).or_default().push(text.to_string()),
                    None => problems.push(format!("rawSideEffects.{} に文字列でない値 {}", key, item)),
                }
            }
        }
        for entry in &mut catalog.categories {
            for item in added.remove(&entry.category).unwrap_or_default() {
                if let Some(method) = item.strip_prefix('.') {
                    entry.methods.push(RawMethod { name: method.to_string(), context: Vec::new(), max_args: None });
                } else if let Some(builtin) = item.strip_prefix("builtin:") {
                    entry.builtins.push(builtin.to_string());
                } else {
                    entry.patterns.push(item);
                }
            }
        }
        (catalog, problems)
    }
}
