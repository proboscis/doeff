use std::fs;
use std::path::PathBuf;

use anyhow::Result;
use clap::{Parser, Subcommand, ValueEnum};
use doeff_indexer::{
    build_index, entry_matches_with_markers, find_interceptors, find_interpreters, find_kleisli,
    find_kleisli_with_type, find_transforms, ProgramTypeKind,
};

#[derive(Parser, Debug)]
#[command(author, version, about = "Index doeff programs and Kleisli programs", long_about = None)]
struct Cli {
    /// Root directory to scan
    #[arg(long, default_value = ".", global = true)]
    root: PathBuf,

    /// Output file for the index (prints to stdout if not provided)
    #[arg(long, global = true)]
    output: Option<PathBuf>,

    /// Pretty-print JSON output
    #[arg(long, default_value_t = false, global = true)]
    pretty: bool,

    #[command(subcommand)]
    command: Option<Commands>,
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Build full index (default behavior)
    Index {
        /// Filter by type usage kind: program, kleisli, or any
        #[arg(long, value_enum, default_value_t = QueryKind::Any)]
        kind: QueryKind,

        /// Filter by type argument (for example "User"); omit or use Any to match all
        #[arg(long)]
        type_arg: Option<String>,

        /// Filter by doeff marker (e.g. "interpreter", "transform")
        #[arg(long)]
        marker: Option<String>,

        /// Filter by file path (relative or absolute)
        #[arg(long)]
        file: Option<String>,
    },

    /// Find interpreter functions (with # doeff: interpreter marker or Program -> T signature)
    FindInterpreters,

    /// Find transform functions (with # doeff: transform marker or Program -> Program signature)
    FindTransforms,

    /// Find Kleisli functions (with # doeff: kleisli marker or @do decorator)
    FindKleisli {
        /// Filter by type argument (for example "User")
        #[arg(long)]
        type_arg: Option<String>,
    },

    /// Find interceptor functions (with # doeff: interceptor marker)
    FindInterceptors {
        /// Filter by effect type (for example "LogEffect")
        #[arg(long)]
        type_arg: Option<String>,
    },
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

/// Normalize a file path to handle both relative and absolute paths
fn normalize_file_path(root: &PathBuf, file_path: &str) -> String {
    use std::path::Path;

    let path = Path::new(file_path);

    // If path is absolute, use it as-is
    if path.is_absolute() {
        return path.to_string_lossy().to_string();
    }

    // Otherwise, resolve relative to root
    root.join(path).to_string_lossy().to_string()
}

/// Check if an entry's file path matches the target file path
fn file_path_matches(entry_path: &str, target_path: &str) -> bool {
    use std::path::Path;

    // Normalize both paths for comparison
    let entry_normalized = Path::new(entry_path)
        .canonicalize()
        .unwrap_or_else(|_| Path::new(entry_path).to_path_buf());

    let target_normalized = Path::new(target_path)
        .canonicalize()
        .unwrap_or_else(|_| Path::new(target_path).to_path_buf());

    entry_normalized == target_normalized
}

fn main() -> Result<()> {
    env_logger::Builder::from_env(env_logger::Env::default())
        .format_timestamp(None)
        .try_init()
        .ok();

    let cli = Cli::parse();
    let mut index = build_index(&cli.root)?;

    // Process based on command
    match cli.command {
        None | Some(Commands::Index { .. }) => {
            // Default behavior or explicit index command
            if let Some(Commands::Index {
                kind,
                type_arg,
                marker,
                file,
            }) = cli.command
            {
                let kind_filter = kind.to_program_kind();
                let type_arg_ref = type_arg.as_deref();
                let marker_ref = marker.as_deref();

                // Filter by file if specified
                if let Some(file_path) = file {
                    let normalized_file = normalize_file_path(&cli.root, &file_path);
                    log::debug!("Filtering by file: {} (normalized: {})", file_path, normalized_file);
                    log::debug!("Total entries before filter: {}", index.entries.len());
                    index.entries.retain(|entry| {
                        let matches_criteria = entry_matches_with_markers(entry, kind_filter, type_arg_ref, marker_ref);
                        let matches_file = file_path_matches(&entry.file_path, &normalized_file);
                        log::debug!("Entry {}: criteria={}, file={} (entry_path={})",
                            entry.qualified_name, matches_criteria, matches_file, entry.file_path);
                        matches_criteria && matches_file
                    });
                    log::debug!("Total entries after filter: {}", index.entries.len());
                } else {
                    index.entries.retain(|entry| {
                        entry_matches_with_markers(entry, kind_filter, type_arg_ref, marker_ref)
                    });
                }
            }
        }

        Some(Commands::FindInterpreters) => {
            // Filter to only interpreters using marker-aware logic
            let interpreters = find_interpreters(&index.entries);
            index.entries = interpreters.into_iter().cloned().collect();
        }

        Some(Commands::FindTransforms) => {
            // Filter to only transforms using marker-aware logic
            let transforms = find_transforms(&index.entries);
            index.entries = transforms.into_iter().cloned().collect();
        }

        Some(Commands::FindKleisli { type_arg }) => {
            // Filter to only kleisli functions using marker-aware logic
            if let Some(type_arg_ref) = type_arg.as_deref() {
                // Use special type filtering for Kleisli (matches first parameter)
                let kleisli = find_kleisli_with_type(&index.entries, type_arg_ref);
                index.entries = kleisli.into_iter().cloned().collect();
            } else {
                let kleisli = find_kleisli(&index.entries);
                index.entries = kleisli.into_iter().cloned().collect();
            }
        }

        Some(Commands::FindInterceptors { type_arg }) => {
            // Filter to only interceptor functions using marker-aware logic
            let interceptors = find_interceptors(&index.entries);
            index.entries = interceptors.into_iter().cloned().collect();

            // Further filter by type if specified
            if let Some(type_arg_ref) = type_arg.as_deref() {
                // For interceptors, filter by Effect type in first parameter
                index.entries.retain(|entry| {
                    if let Some(first_param) = entry.all_parameters.iter().find(|p| p.is_required) {
                        if let Some(annotation) = &first_param.annotation {
                            annotation.contains(type_arg_ref) || annotation == "Effect"
                        } else {
                            false
                        }
                    } else {
                        false
                    }
                });
            }
        }
    }

    // Output the result
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
