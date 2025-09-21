use std::fs;
use std::path::PathBuf;

use anyhow::Result;
use clap::Parser;
use doeff_indexer::analyze_dependencies;

#[derive(Parser, Debug)]
#[command(author, version, about = "Inspect @do dependency usage for Dep effects", long_about = None)]
struct Cli {
    /// Root directory to scan
    #[arg(long, default_value = ".")]
    root: PathBuf,

    /// Output file for the dependency report (prints to stdout if omitted)
    #[arg(long)]
    output: Option<PathBuf>,

    /// Pretty-print JSON output
    #[arg(long, default_value_t = false)]
    pretty: bool,

    /// Filter results to functions whose qualified name contains the provided substring
    #[arg(long)]
    function: Vec<String>,
}

fn main() -> Result<()> {
    let cli = Cli::parse();

    let mut entries = analyze_dependencies(&cli.root)?;

    if !cli.function.is_empty() {
        entries.retain(|entry| {
            cli.function
                .iter()
                .any(|needle| entry.qualified_name.contains(needle))
        });
    }

    let json = if cli.pretty {
        serde_json::to_string_pretty(&entries)?
    } else {
        serde_json::to_string(&entries)?
    };

    if let Some(path) = cli.output {
        if let Some(parent) = path.parent() {
            if !parent.as_os_str().is_empty() {
                fs::create_dir_all(parent)?;
            }
        }
        fs::write(&path, json)?;
    } else {
        println!("{}", json);
    }

    Ok(())
}
