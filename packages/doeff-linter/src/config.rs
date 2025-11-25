//! Configuration loading for doeff-linter
//!
//! Loads configuration from pyproject.toml [tool.doeff-linter] section

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

/// Get all available rule IDs
pub fn get_all_rule_ids() -> Vec<String> {
    vec![
        "DOEFF001".to_string(),
        "DOEFF002".to_string(),
        "DOEFF003".to_string(),
        "DOEFF004".to_string(),
        "DOEFF005".to_string(),
        "DOEFF006".to_string(),
        "DOEFF007".to_string(),
        "DOEFF008".to_string(),
        "DOEFF009".to_string(),
        "DOEFF010".to_string(),
    ]
}

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



