use std::fs;
use std::path::PathBuf;

use anyhow::Result;
use clap::{Parser, ValueEnum};
use doeff_indexer::{build_index, entry_matches_with_markers, ProgramTypeKind};

#[derive(Parser, Debug)]
#[command(author, version, about = "Index doeff programs and Kleisli programs", long_about = None)]
struct Cli {
    /// Root directory to scan
    #[arg(long, default_value = ".")]
    root: PathBuf,

    /// Output file for the index (prints to stdout if not provided)
    #[arg(long)]
    output: Option<PathBuf>,

    /// Pretty-print JSON output
    #[arg(long, default_value_t = false)]
    pretty: bool,

    /// Filter by type usage kind: program, kleisli, or any
    #[arg(long, value_enum, default_value_t = QueryKind::Any)]
    kind: QueryKind,

    /// Filter by type argument (for example "User"); omit or use Any to match all
    #[arg(long)]
    type_arg: Option<String>,
    
    /// Filter by doeff marker (e.g. "interpreter", "transform")
    #[arg(long)]
    marker: Option<String>,
}

#[derive(Copy, Clone, Debug, Eq, PartialEq, ValueEnum)]
enum QueryKind {
    Program,
    Kleisli,
    Any,
}

impl QueryKind {
    fn to_program_kind(self) -> Option<ProgramTypeKind> {
        match self {
            QueryKind::Program => Some(ProgramTypeKind::Program),
            QueryKind::Kleisli => Some(ProgramTypeKind::KleisliProgram),
            QueryKind::Any => None,
        }
    }
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    let mut index = build_index(&cli.root)?;

    let kind_filter = cli.kind.to_program_kind();
    let type_arg = cli.type_arg.as_deref();
    let marker = cli.marker.as_deref();
    index
        .entries
        .retain(|entry| entry_matches_with_markers(entry, kind_filter, type_arg, marker));

    let json = if cli.pretty {
        serde_json::to_string_pretty(&index)?
    } else {
        serde_json::to_string(&index)?
    };

    if let Some(output_path) = cli.output {
        if let Some(parent) = output_path.parent() {
            if !parent.as_os_str().is_empty() {
                fs::create_dir_all(parent)?;
            }
        }
        fs::write(&output_path, json)?;
    } else {
        println!("{}", json);
    }

    Ok(())
}
