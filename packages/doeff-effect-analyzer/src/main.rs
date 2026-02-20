use clap::{Parser, Subcommand};
use doeff_effect_analyzer::{analyze_dotted_path, Report};

#[derive(Parser, Debug)]
#[command(author, version, about = "Static Effect Dependency Analyzer", long_about = None)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Analyze a dotted module path and emit JSON report
    Analyze {
        /// Fully qualified target (e.g., package.module.program)
        target: String,
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
    }

    Ok(())
}
