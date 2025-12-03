use std::fs;
use std::path::{Path, PathBuf};

use anyhow::Result;
use clap::{Parser, Subcommand, ValueEnum};
use doeff_indexer::{
    build_index, entry_matches_with_markers, find_interceptors, find_interpreters, find_kleisli,
    find_kleisli_with_type, find_transforms, IndexEntry, ProgramTypeKind,
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
    FindInterpreters {
        /// Sort by proximity to this file path (closest first)
        #[arg(long)]
        proximity_file: Option<String>,

        /// Sort by proximity to this line number (used with --proximity-file)
        #[arg(long, default_value_t = 0)]
        proximity_line: usize,
    },

    /// Find transform functions (with # doeff: transform marker or Program -> Program signature)
    FindTransforms {
        /// Sort by proximity to this file path (closest first)
        #[arg(long)]
        proximity_file: Option<String>,

        /// Sort by proximity to this line number (used with --proximity-file)
        #[arg(long, default_value_t = 0)]
        proximity_line: usize,
    },

    /// Find Kleisli functions (with # doeff: kleisli marker or @do decorator)
    FindKleisli {
        /// Filter by type argument (for example "User")
        #[arg(long)]
        type_arg: Option<String>,

        /// Sort by proximity to this file path (closest first)
        #[arg(long)]
        proximity_file: Option<String>,

        /// Sort by proximity to this line number (used with --proximity-file)
        #[arg(long, default_value_t = 0)]
        proximity_line: usize,
    },

    /// Find interceptor functions (with # doeff: interceptor marker)
    FindInterceptors {
        /// Filter by effect type (for example "LogEffect")
        #[arg(long)]
        type_arg: Option<String>,

        /// Sort by proximity to this file path (closest first)
        #[arg(long)]
        proximity_file: Option<String>,

        /// Sort by proximity to this line number (used with --proximity-file)
        #[arg(long, default_value_t = 0)]
        proximity_line: usize,
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
    // Normalize both paths for comparison
    let entry_normalized = Path::new(entry_path)
        .canonicalize()
        .unwrap_or_else(|_| Path::new(entry_path).to_path_buf());

    let target_normalized = Path::new(target_path)
        .canonicalize()
        .unwrap_or_else(|_| Path::new(target_path).to_path_buf());

    entry_normalized == target_normalized
}

/// Sort entries by proximity to a target file and line.
/// Priority:
/// 1. Same file - sorted by line number proximity (closest first)
/// 2. Same directory
/// 3. Same package (longest common path prefix)
/// 4. Everything else - alphabetically by qualified name
fn sort_by_proximity(entries: &mut [IndexEntry], target_file: &str, target_line: usize) {
    let target_path = Path::new(target_file);
    let target_canonical = target_path
        .canonicalize()
        .unwrap_or_else(|_| target_path.to_path_buf());
    let target_dir = target_canonical.parent().map(|p| p.to_path_buf());
    let target_components: Vec<_> = target_canonical.components().collect();

    entries.sort_by(|a, b| {
        let a_path = Path::new(&a.file_path);
        let b_path = Path::new(&b.file_path);
        
        let a_canonical = a_path.canonicalize().unwrap_or_else(|_| a_path.to_path_buf());
        let b_canonical = b_path.canonicalize().unwrap_or_else(|_| b_path.to_path_buf());

        // Priority 1: Same file (by line proximity)
        let a_same_file = a_canonical == target_canonical;
        let b_same_file = b_canonical == target_canonical;

        match (a_same_file, b_same_file) {
            (true, true) => {
                // Both in same file - sort by line proximity
                let a_dist = (a.line as isize - target_line as isize).unsigned_abs();
                let b_dist = (b.line as isize - target_line as isize).unsigned_abs();
                return a_dist.cmp(&b_dist);
            }
            (true, false) => return std::cmp::Ordering::Less,
            (false, true) => return std::cmp::Ordering::Greater,
            (false, false) => {}
        }

        // Priority 2: Same directory
        let a_dir = a_canonical.parent();
        let b_dir = b_canonical.parent();
        let a_same_dir = a_dir == target_dir.as_deref();
        let b_same_dir = b_dir == target_dir.as_deref();

        match (a_same_dir, b_same_dir) {
            (true, false) => return std::cmp::Ordering::Less,
            (false, true) => return std::cmp::Ordering::Greater,
            _ => {}
        }

        // Priority 3: Common path prefix length (more = better)
        let a_components: Vec<_> = a_canonical.components().collect();
        let b_components: Vec<_> = b_canonical.components().collect();
        let a_common = common_prefix_len(&target_components, &a_components);
        let b_common = common_prefix_len(&target_components, &b_components);

        match b_common.cmp(&a_common) {
            std::cmp::Ordering::Equal => {}
            other => return other,
        }

        // Priority 4: Alphabetically by qualified name
        a.qualified_name.cmp(&b.qualified_name)
    });
}

fn common_prefix_len<T: PartialEq>(a: &[T], b: &[T]) -> usize {
    a.iter().zip(b.iter()).take_while(|(x, y)| x == y).count()
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

        Some(Commands::FindInterpreters { proximity_file, proximity_line }) => {
            // Filter to only interpreters using marker-aware logic
            let interpreters = find_interpreters(&index.entries);
            index.entries = interpreters.into_iter().cloned().collect();

            // Sort by proximity if file is specified
            if let Some(ref file) = proximity_file {
                let normalized = normalize_file_path(&cli.root, file);
                sort_by_proximity(&mut index.entries, &normalized, proximity_line);
            }
        }

        Some(Commands::FindTransforms { proximity_file, proximity_line }) => {
            // Filter to only transforms using marker-aware logic
            let transforms = find_transforms(&index.entries);
            index.entries = transforms.into_iter().cloned().collect();

            // Sort by proximity if file is specified
            if let Some(ref file) = proximity_file {
                let normalized = normalize_file_path(&cli.root, file);
                sort_by_proximity(&mut index.entries, &normalized, proximity_line);
            }
        }

        Some(Commands::FindKleisli { type_arg, proximity_file, proximity_line }) => {
            // Filter to only kleisli functions using marker-aware logic
            if let Some(type_arg_ref) = type_arg.as_deref() {
                // Use special type filtering for Kleisli (matches first parameter)
                let kleisli = find_kleisli_with_type(&index.entries, type_arg_ref);
                index.entries = kleisli.into_iter().cloned().collect();
            } else {
                let kleisli = find_kleisli(&index.entries);
                index.entries = kleisli.into_iter().cloned().collect();
            }

            // Sort by proximity if file is specified
            if let Some(ref file) = proximity_file {
                let normalized = normalize_file_path(&cli.root, file);
                sort_by_proximity(&mut index.entries, &normalized, proximity_line);
            }
        }

        Some(Commands::FindInterceptors { type_arg, proximity_file, proximity_line }) => {
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

            // Sort by proximity if file is specified
            if let Some(ref file) = proximity_file {
                let normalized = normalize_file_path(&cli.root, file);
                sort_by_proximity(&mut index.entries, &normalized, proximity_line);
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
