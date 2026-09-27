//! Configuration loading for doeff-linter
//!
//! Loads configuration from pyproject.toml [tool.doeff-linter] section

use crate::project::settings::{
    EnvironmentNamesSection, LawEntry, LayersSection, ProjectSections, ProjectSettings, RawSideEffectsSection, RegistrySection,
    RolesSection, TagsSection,
};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::{Path, PathBuf};

/// Main configuration structure
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct Config {
    /// Rules to enable (empty means all rules, or use ["ALL"])
    #[serde(default)]
    pub enable: Vec<String>,

    /// Rules to disable
    #[serde(default)]
    pub disable: Vec<String>,

    /// Paths to exclude from linting
    #[serde(default)]
    pub exclude: Vec<String>,

    /// Rule-specific configuration
    #[serde(default)]
    pub rules: HashMap<String, RuleConfig>,

    /// Git integration settings
    #[serde(default)]
    pub git: GitConfig,

    /// Path to log file for recording lint results (JSON Lines format)
    /// Defaults to ".doeff-lint.jsonl"
    #[serde(default = "default_log_file")]
    pub log_file: Option<String>,

    /// 層の規則(DOEFF101〜105)の層の順・置き場・import の決まり — `[tool.doeff-linter.layers]`
    #[serde(default)]
    pub layers: Option<LayersSection>,

    /// タグの読み方 — `[tool.doeff-linter.tags]`(既定のままでよい)
    #[serde(default)]
    pub tags: Option<TagsSection>,

    /// role の閉じた一覧と層ごとに許す role — `[tool.doeff-linter.roles]`
    #[serde(default)]
    pub roles: Option<RolesSection>,

    /// 業務の名に付けてはいけない環境の語(DOEFF108)— `[tool.doeff-linter.environment_names]`
    #[serde(default)]
    pub environment_names: Option<EnvironmentNamesSection>,

    /// 生の副作用に直に触ってよい層(DOEFF106・107)— `[tool.doeff-linter.raw_side_effects]`
    #[serde(default)]
    pub raw_side_effects: Option<RawSideEffectsSection>,

    /// 規則 ID と ADR の law の対応 — `[[tool.doeff-linter.laws]]`
    #[serde(default)]
    pub laws: Vec<LawEntry>,

    /// 既知の破れの登録簿と照合中の規則 — `[tool.doeff-linter.registry]`
    #[serde(default)]
    pub registry: Option<RegistrySection>,
}

impl Config {
    /// 層の規則の節を検めて、規則が読む形にする(名前の食い違いは理由の文の列)。
    pub fn project_settings(&self) -> Result<ProjectSettings, Vec<String>> {
        ProjectSettings::validate(&ProjectSections {
            layers: self.layers.as_ref(),
            tags: self.tags.as_ref(),
            roles: self.roles.as_ref(),
            environment_names: self.environment_names.as_ref(),
            raw_side_effects: self.raw_side_effects.as_ref(),
            laws: &self.laws,
            registry: self.registry.as_ref(),
        })
    }
}

fn default_log_file() -> Option<String> {
    Some(".doeff-lint.jsonl".to_string())
}

/// Git integration configuration
#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct GitConfig {
    /// Include untracked files when using --modified
    #[serde(default = "default_include_untracked")]
    pub include_untracked: bool,
}

impl Default for GitConfig {
    fn default() -> Self {
        Self {
            include_untracked: true,
        }
    }
}

fn default_include_untracked() -> bool {
    true
}

/// Rule-specific configuration
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct RuleConfig {
    /// DOEFF003: Maximum number of mutable attributes
    pub max_mutable_attributes: Option<usize>,

    /// DOEFF009: Skip private functions (starting with _)
    pub skip_private_functions: Option<bool>,

    /// DOEFF009: Skip test functions (starting with test_)
    pub skip_test_functions: Option<bool>,
}

/// Find pyproject.toml file starting from a path and walking up
pub fn find_pyproject_toml(start_path: &Path) -> Option<PathBuf> {
    let mut current = if start_path.is_file() {
        start_path.parent()?
    } else {
        start_path
    };

    loop {
        let pyproject = current.join("pyproject.toml");
        if pyproject.exists() {
            return Some(pyproject);
        }

        current = current.parent()?;
    }
}

/// Find pyproject.toml with [tool.doeff-linter] section
pub fn find_config_pyproject_toml(start_path: &Path) -> Option<PathBuf> {
    let mut current = if start_path.is_file() {
        start_path.parent()?
    } else {
        start_path
    };

    loop {
        let pyproject = current.join("pyproject.toml");
        if pyproject.exists() {
            if let Ok(content) = std::fs::read_to_string(&pyproject) {
                if let Ok(value) = toml::from_str::<toml::Value>(&content) {
                    if let Some(tool) = value.get("tool") {
                        if tool.get("doeff-linter").is_some() {
                            return Some(pyproject);
                        }
                    }
                }
            }
        }

        current = current.parent()?;
    }
}

/// Load configuration from pyproject.toml
pub fn load_config(path: Option<&Path>) -> Option<Config> {
    let config_path = if let Some(p) = path {
        if p.exists() {
            p.to_path_buf()
        } else {
            return None;
        }
    } else {
        find_config_pyproject_toml(&std::env::current_dir().ok()?)?
    };

    let content = std::fs::read_to_string(&config_path).ok()?;
    let value: toml::Value = toml::from_str(&content).ok()?;

    let tool = value.get("tool")?;
    let doeff_linter = tool.get("doeff-linter")?;

    let config: Config = doeff_linter.clone().try_into().ok()?;

    Some(config)
}

/// 見つけた設定 file と、その中の `[tool.doeff-linter]` の節。
#[derive(Debug, Clone)]
pub struct LoadedConfig {
    pub config: Config,
    /// 設定を読んだ file。
    pub path: PathBuf,
}

/// 設定 file を読む。`[tool.doeff-linter]` の節を持つ file(pyproject.toml の形)でも、節の中身だけを書いた file でもよい。
/// 読めない・TOML でない・欄の型が違う時は理由の文を返す(黙って既定値にしない)。
pub fn load_config_file(path: &Path) -> Result<Config, String> {
    let content = std::fs::read_to_string(path).map_err(|e| format!("{} を読めない: {}", path.display(), e))?;
    let value: toml::Value = toml::from_str(&content).map_err(|e| format!("{} は TOML として読めない: {}", path.display(), e))?;
    let section = match value.get("tool").and_then(|tool| tool.get("doeff-linter")) {
        Some(section) => section.clone(),
        None => value,
    };
    section.try_into().map_err(|e: toml::de::Error| format!("{} の [tool.doeff-linter] を読めない: {}", path.display(), e))
}

/// 設定を探して読む: explicit(`--config`)があればそれを、無ければ start から上へ `[tool.doeff-linter]` を持つ pyproject.toml を探す。
/// 見つからなければ Ok(None)。見つけた file が読めなければ Err。
pub fn load_config_checked(explicit: Option<&Path>, start: &Path) -> Result<Option<LoadedConfig>, String> {
    let path = match explicit {
        Some(path) => path.to_path_buf(),
        None => match find_config_pyproject_toml(start) {
            Some(path) => path,
            None => return Ok(None),
        },
    };
    load_config_file(&path).map(|config| Some(LoadedConfig { config, path }))
}

// Re-export get_all_rule_ids from rules module
pub use crate::rules::get_all_rule_ids;

/// Merge command line arguments with config file settings
/// CLI arguments take precedence
pub fn merge_config(
    config: Option<&Config>,
    cli_enable: &[String],
    cli_disable: &[String],
    cli_exclude: &[String],
) -> (Option<Vec<String>>, Vec<String>) {
    let mut enable = None;
    let mut exclude = vec![];

    // Start with config file settings
    if let Some(cfg) = config {
        if !cfg.enable.is_empty() && cli_enable.is_empty() && cli_disable.is_empty() {
            if cfg.enable.contains(&"ALL".to_string()) {
                let all_rules = get_all_rule_ids();
                let enabled: Vec<String> = if !cfg.disable.is_empty() {
                    all_rules
                        .into_iter()
                        .filter(|r| !cfg.disable.contains(r))
                        .collect()
                } else {
                    all_rules
                };
                enable = Some(enabled);
            } else {
                enable = Some(cfg.enable.clone());
            }
        } else if !cfg.disable.is_empty() && cli_enable.is_empty() && cli_disable.is_empty() {
            let all_rules = get_all_rule_ids();
            let enabled: Vec<String> = all_rules
                .into_iter()
                .filter(|r| !cfg.disable.contains(r))
                .collect();
            enable = Some(enabled);
        }

        exclude.extend(cfg.exclude.iter().cloned());
    }

    // Apply CLI overrides
    if !cli_enable.is_empty() {
        if cli_enable.contains(&"ALL".to_string()) {
            let all_rules = get_all_rule_ids();
            let enabled: Vec<String> = if !cli_disable.is_empty() {
                all_rules
                    .into_iter()
                    .filter(|r| !cli_disable.contains(r))
                    .collect()
            } else {
                all_rules
            };
            enable = Some(enabled);
        } else {
            enable = Some(cli_enable.to_vec());
        }
    } else if !cli_disable.is_empty() {
        let all_rules = get_all_rule_ids();
        let enabled: Vec<String> = all_rules
            .into_iter()
            .filter(|r| !cli_disable.contains(r))
            .collect();
        enable = Some(enabled);
    }

    // Add CLI exclude patterns
    exclude.extend(cli_exclude.iter().cloned());

    // Add default excludes
    let defaults = vec![
        ".venv",
        "venv",
        "__pycache__",
        ".git",
        ".tox",
        "build",
        "dist",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
        ".mypy_cache",
    ];
    for default in defaults {
        if !exclude.contains(&default.to_string()) {
            exclude.push(default.to_string());
        }
    }

    (enable, exclude)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::TempDir;

    #[test]
    fn test_find_pyproject_toml() {
        let dir = TempDir::new().unwrap();
        let pyproject_path = dir.path().join("pyproject.toml");
        fs::write(
            &pyproject_path,
            "[tool.doeff-linter]\nexclude = [\"test\"]",
        )
        .unwrap();

        assert_eq!(
            find_pyproject_toml(dir.path()),
            Some(pyproject_path.clone())
        );

        let subdir = dir.path().join("subdir");
        fs::create_dir(&subdir).unwrap();
        assert_eq!(find_pyproject_toml(&subdir), Some(pyproject_path));
    }

    #[test]
    fn test_load_config() {
        let dir = TempDir::new().unwrap();
        let pyproject_path = dir.path().join("pyproject.toml");

        let content = r#"
[tool.doeff-linter]
enable = ["DOEFF001", "DOEFF002"]
exclude = ["venv", "build"]

[tool.doeff-linter.rules.DOEFF003]
max_mutable_attributes = 5
"#;
        fs::write(&pyproject_path, content).unwrap();

        let config = load_config(Some(&pyproject_path)).unwrap();
        assert_eq!(config.enable, vec!["DOEFF001", "DOEFF002"]);
        assert_eq!(config.exclude, vec!["venv", "build"]);
        assert_eq!(config.rules["DOEFF003"].max_mutable_attributes, Some(5));
    }

    #[test]
    fn test_merge_config() {
        let config = Config {
            enable: vec!["DOEFF001".to_string()],
            disable: vec![],
            exclude: vec!["custom_dir".to_string()],
            ..Default::default()
        };

        let (enable, exclude) = merge_config(
            Some(&config),
            &["DOEFF002".to_string()],
            &[],
            &["skip_me".to_string()],
        );

        assert_eq!(enable, Some(vec!["DOEFF002".to_string()]));
        assert!(exclude.contains(&"custom_dir".to_string()));
        assert!(exclude.contains(&"skip_me".to_string()));
        assert!(exclude.contains(&".venv".to_string()));
    }
}



