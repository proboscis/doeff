use anyhow::Result;
use clap::{Parser, Subcommand};
use doeff_indexer::{
    build_index, find_interceptors, find_interceptors_with_type, find_interpreters,
    find_kleisli, find_kleisli_with_type, find_transforms,
};
use std::path::PathBuf;

#[derive(Parser)]
#[command(name = "doeff-indexer")]
#[command(about = "Index and query doeff functions in Python code")]
#[command(version = "0.1.0")]
struct Cli {
    /// Root directory to search
    #[arg(short, long, default_value = ".")]
    root: PathBuf,

    /// Subcommand to execute
    #[command(subcommand)]
    command: Option<Commands>,
}

#[derive(Subcommand)]
enum Commands {
    /// Find functions marked as interpreters
    FindInterpreters {
        /// Root directory to search
        #[arg(short, long, default_value = ".")]
        root: PathBuf,
    },
    /// Find functions marked as transforms
    FindTransforms {
        /// Root directory to search
        #[arg(short, long, default_value = ".")]
        root: PathBuf,
    },
    /// Find Kleisli functions (marked or @do decorated)
    FindKleisli {
        /// Root directory to search
        #[arg(short, long, default_value = ".")]
        root: PathBuf,
        /// Filter by first parameter type
        #[arg(short, long)]
        type_arg: Option<String>,
    },
    /// Find functions marked as interceptors
    FindInterceptors {
        /// Root directory to search
        #[arg(short, long, default_value = ".")]
        root: PathBuf,
        /// Filter by Effect type
        #[arg(short, long)]
        type_arg: Option<String>,
    },
}

fn main() -> Result<()> {
    env_logger::init();
    
    let cli = Cli::parse();
    
    match cli.command {
        Some(Commands::FindInterpreters { root }) => {
            let index = build_index(&root)?;
            let interpreters = find_interpreters(&index.entries);
            
            let output = doeff_indexer::IndexOutput {
                entries: interpreters.into_iter().cloned().collect(),
                total_files: index.total_files,
                total_functions: index.entries.len(),
            };
            
            println!("{}", serde_json::to_string_pretty(&output)?);
        }
        Some(Commands::FindTransforms { root }) => {
            let index = build_index(&root)?;
            let transforms = find_transforms(&index.entries);
            
            let output = doeff_indexer::IndexOutput {
                entries: transforms.into_iter().cloned().collect(),
                total_files: index.total_files,
                total_functions: index.entries.len(),
            };
            
            println!("{}", serde_json::to_string_pretty(&output)?);
        }
        Some(Commands::FindKleisli { root, type_arg }) => {
            let index = build_index(&root)?;
            
            let kleisli = if let Some(type_arg) = type_arg {
                find_kleisli_with_type(&index.entries, &type_arg)
            } else {
                find_kleisli(&index.entries)
            };
            
            let output = doeff_indexer::IndexOutput {
                entries: kleisli.into_iter().cloned().collect(),
                total_files: index.total_files,
                total_functions: index.entries.len(),
            };
            
            println!("{}", serde_json::to_string_pretty(&output)?);
        }
        Some(Commands::FindInterceptors { root, type_arg }) => {
            let index = build_index(&root)?;
            
            let interceptors = if let Some(type_arg) = type_arg {
                find_interceptors_with_type(&index.entries, &type_arg)
            } else {
                find_interceptors(&index.entries)
            };
            
            let output = doeff_indexer::IndexOutput {
                entries: interceptors.into_iter().cloned().collect(),
                total_files: index.total_files,
                total_functions: index.entries.len(),
            };
            
            println!("{}", serde_json::to_string_pretty(&output)?);
        }
        None => {
            // Default: build and output full index
            let index = build_index(&cli.root)?;
            println!("{}", serde_json::to_string_pretty(&index)?);
        }
    }
    
    Ok(())
}