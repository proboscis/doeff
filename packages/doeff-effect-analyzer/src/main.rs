use std::path::{Path, PathBuf};

use clap::{Parser, Subcommand};
use doeff_effect_analyzer::{analyze_dotted_path, dot_output, html_output, hy_analyzer, Report};

#[derive(Parser, Debug)]
#[command(author, version, about = "Static Effect Dependency Analyzer", long_about = None)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Analyze a dotted module path and emit JSON report (Python)
    Analyze {
        /// Fully qualified target (e.g., package.module.program)
        target: String,
    },

    /// Analyze Hy source files and emit effect DAG
    Hy {
        /// Hy source files to analyze
        #[arg(required = true)]
        files: Vec<PathBuf>,

        /// Output format: json, dot, tree, or html
        #[arg(short, long, default_value = "tree")]
        format: String,

        /// Output file path (for html format)
        #[arg(short, long)]
        output: Option<PathBuf>,

        /// Filter by effect type (e.g., "ask", "Traverse", "Compute")
        #[arg(short = 'e', long)]
        effect: Option<String>,

        /// Root function to analyze (if omitted, shows all defk functions)
        #[arg(short, long)]
        root: Option<String>,
    },
}

fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();

    match cli.command {
        Commands::Analyze { target } => {
            let report: Report = analyze_dotted_path(&target)?;
            let json = serde_json::to_string_pretty(&report)?;
            println!("{}", json);
        }
        Commands::Hy {
            files,
            format,
            effect,
            root,
            output,
        } => {
            run_hy_analysis(&files, &format, effect.as_deref(), root.as_deref(), output.as_deref())?;
        }
    }

    Ok(())
}

fn run_hy_analysis(
    files: &[PathBuf],
    format: &str,
    effect_filter: Option<&str>,
    root: Option<&str>,
    output: Option<&Path>,
) -> anyhow::Result<()> {
    // Parse all files
    let mut all_functions: Vec<(String, Vec<String>, Vec<String>)> = Vec::new();
    let mut all_infos = Vec::new();

    for file_path in files {
        let source = std::fs::read_to_string(file_path)
            .map_err(|e| anyhow::anyhow!("failed to read '{}': {}", file_path.display(), e))?;

        let info = hy_analyzer::analyze_hy_source(&source, file_path)
            .map_err(|e| anyhow::anyhow!("failed to parse '{}': {}", file_path.display(), e))?;

        all_infos.push((file_path.clone(), info));
    }

    // Build function list with effects and call targets
    for (_path, info) in &all_infos {
        for (name, func_info) in &info.function_defs {
            let effects: Vec<String> = func_info
                .summary
                .local_effects
                .iter()
                .map(|e| e.key.clone())
                .collect();

            let calls: Vec<String> = func_info
                .summary
                .calls
                .iter()
                .filter_map(|c| c.callee.clone())
                .collect();

            all_functions.push((name.clone(), effects, calls));
        }
    }

    // If root specified, filter to reachable functions
    if let Some(root_name) = root {
        let root_python = root_name.replace('-', "_");
        let reachable: std::collections::BTreeSet<String> =
            collect_reachable(&root_python, &all_functions)
                .into_iter()
                .map(|s| s.to_string())
                .collect();
        all_functions.retain(|(name, _, _)| reachable.contains(name.as_str()));
    }

    match format {
        "json" => {
            let json = serde_json::to_string_pretty(&build_json(&all_functions, &all_infos))?;
            println!("{}", json);
        }
        "dot" => {
            let dot = dot_output::dag_to_dot(&all_functions, effect_filter);
            println!("{}", dot);
        }
        "tree" => {
            print_tree(&all_functions, effect_filter, root);
        }
        "html" => {
            let root_name = root
                .map(|r| r.replace('-', "_"))
                .ok_or_else(|| anyhow::anyhow!("--root is required for html format"))?;

            let tree = html_output::build_trace_tree(&root_name, &all_infos, effect_filter);
            let html = html_output::render_html(&tree, &root_name);

            let out_path = output
                .map(|p| p.to_path_buf())
                .unwrap_or_else(|| PathBuf::from("/tmp/seda.html"));
            std::fs::write(&out_path, &html)?;
            eprintln!("Written to: {}", out_path.display());

            // Try to open in browser
            #[cfg(target_os = "macos")]
            {
                let _ = std::process::Command::new("open")
                    .arg(&out_path)
                    .spawn();
            }
        }
        _ => {
            anyhow::bail!("unknown format '{}', expected: json, dot, tree, html", format);
        }
    }

    Ok(())
}

fn collect_reachable<'a>(
    root: &str,
    functions: &'a [(String, Vec<String>, Vec<String>)],
) -> std::collections::BTreeSet<&'a str> {
    let mut reachable = std::collections::BTreeSet::new();
    let mut stack = vec![root.to_string()];

    while let Some(name) = stack.pop() {
        if reachable.contains(name.as_str()) {
            // Already visited — but we need to check against the actual strings in functions
            continue;
        }
        // Find in functions
        for (fname, _, calls) in functions {
            if fname == &name {
                reachable.insert(fname.as_str());
                for target in calls {
                    if !reachable.contains(target.as_str()) {
                        stack.push(target.clone());
                    }
                }
                break;
            }
        }
    }
    reachable
}

fn print_tree(
    functions: &[(String, Vec<String>, Vec<String>)],
    effect_filter: Option<&str>,
    root: Option<&str>,
) {
    let functions_to_show: Vec<&(String, Vec<String>, Vec<String>)> = if let Some(root_name) = root
    {
        let root_python = root_name.replace('-', "_");
        // Print from root, depth-first
        print_tree_from(&root_python, functions, effect_filter, "", &mut std::collections::BTreeSet::new());
        return;
    } else {
        functions.iter().collect()
    };

    for (name, effects, calls) in &functions_to_show {
        let filtered_effects: Vec<&String> = if let Some(filter) = effect_filter {
            effects.iter().filter(|e| e.contains(filter)).collect()
        } else {
            effects.iter().collect()
        };

        if effect_filter.is_some() && filtered_effects.is_empty() && calls.is_empty() {
            continue;
        }

        println!("{}:", name);
        for effect in &filtered_effects {
            println!("  ◆ {}", effect);
        }
        for target in calls {
            println!("  → {}", target);
        }
        println!();
    }
}

fn print_tree_from(
    name: &str,
    functions: &[(String, Vec<String>, Vec<String>)],
    effect_filter: Option<&str>,
    indent: &str,
    visited: &mut std::collections::BTreeSet<String>,
) {
    if !visited.insert(name.to_string()) {
        println!("{}{}  (recursive)", indent, name);
        return;
    }

    let func = functions.iter().find(|(n, _, _)| n == name);

    match func {
        Some((_, effects, calls)) => {
            let filtered_effects: Vec<&String> = if let Some(filter) = effect_filter {
                effects.iter().filter(|e| e.contains(filter)).collect()
            } else {
                effects.iter().collect()
            };

            println!("{}{}", indent, name);
            let child_indent = format!("{}  ", indent);
            for effect in &filtered_effects {
                println!("{}◆ {}", child_indent, effect);
            }
            for target in calls {
                print_tree_from(target, functions, effect_filter, &child_indent, visited);
            }
        }
        None => {
            println!("{}{}  (external)", indent, name);
        }
    }

    visited.remove(name);
}

fn build_json(
    functions: &[(String, Vec<String>, Vec<String>)],
    infos: &[(PathBuf, hy_analyzer::HyModuleInfo)],
) -> serde_json::Value {
    let mut funcs = Vec::new();
    for (name, effects, calls) in functions {
        // Find file
        let file = infos
            .iter()
            .find(|(_, info)| info.function_defs.contains_key(name))
            .map(|(p, _)| p.to_string_lossy().to_string())
            .unwrap_or_default();

        funcs.push(serde_json::json!({
            "name": name,
            "file": file,
            "effects": effects,
            "calls": calls,
        }));
    }

    serde_json::json!({
        "functions": funcs,
        "imports": infos.iter().flat_map(|(_, info)| {
            info.imports.iter().map(|(local, imp)| {
                serde_json::json!({
                    "local": local,
                    "module": imp.module,
                    "symbol": imp.symbol,
                })
            })
        }).collect::<Vec<_>>(),
    })
}
