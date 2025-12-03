//! HTML Report generation module
//!
//! Generates standalone HTML reports with interactive charts using Chart.js

use crate::stats::LogStats;
use std::fs::File;
use std::io::Write;
use std::path::Path;

/// Generate an HTML report from log statistics
pub fn generate_html_report(stats: &LogStats, output_path: &Path) -> std::io::Result<()> {
    let mut file = File::create(output_path)?;

    let rules_data = stats.rules_sorted();
    let rule_labels: Vec<_> = rules_data.iter().map(|(r, _)| format!("\"{}\"", r)).collect();
    let rule_values: Vec<_> = rules_data.iter().map(|(_, c)| c.to_string()).collect();

    let top_files = stats.top_files(10);
    let file_labels: Vec<_> = top_files
        .iter()
        .map(|(f, _)| {
            let display = if f.len() > 40 {
                format!("...{}", &f[f.len() - 37..])
            } else {
                f.to_string()
            };
            format!("\"{}\"", display)
        })
        .collect();
    let file_values: Vec<_> = top_files.iter().map(|(_, c)| c.to_string()).collect();

    // Get daily trend data
    let mut dates: Vec<_> = stats.runs_by_date.iter().collect();
    dates.sort_by(|a, b| a.0.cmp(b.0));
    let trend_labels: Vec<_> = dates.iter().map(|(d, _)| format!("\"{}\"", d)).collect();
    let trend_values: Vec<_> = dates.iter().map(|(_, c)| c.to_string()).collect();

    let html = format!(
        r##"<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>doeff-linter Report</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {{
            --bg-primary: #0d1117;
            --bg-secondary: #161b22;
            --bg-tertiary: #21262d;
            --text-primary: #c9d1d9;
            --text-secondary: #8b949e;
            --accent-blue: #58a6ff;
            --accent-green: #3fb950;
            --accent-yellow: #d29922;
            --accent-red: #f85149;
            --accent-purple: #a371f7;
            --border-color: #30363d;
        }}
        
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            line-height: 1.6;
            padding: 2rem;
        }}
        
        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}
        
        header {{
            text-align: center;
            margin-bottom: 3rem;
            padding-bottom: 2rem;
            border-bottom: 1px solid var(--border-color);
        }}
        
        h1 {{
            font-size: 2.5rem;
            font-weight: 600;
            margin-bottom: 0.5rem;
            background: linear-gradient(135deg, var(--accent-blue), var(--accent-purple));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}
        
        .subtitle {{
            color: var(--text-secondary);
            font-size: 1.1rem;
        }}
        
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1.5rem;
            margin-bottom: 3rem;
        }}
        
        .stat-card {{
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.5rem;
            text-align: center;
            transition: transform 0.2s, box-shadow 0.2s;
        }}
        
        .stat-card:hover {{
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
        }}
        
        .stat-value {{
            font-size: 2.5rem;
            font-weight: 700;
            margin-bottom: 0.5rem;
        }}
        
        .stat-label {{
            color: var(--text-secondary);
            font-size: 0.9rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        
        .stat-card.errors .stat-value {{ color: var(--accent-red); }}
        .stat-card.warnings .stat-value {{ color: var(--accent-yellow); }}
        .stat-card.info .stat-value {{ color: var(--accent-blue); }}
        .stat-card.total .stat-value {{ color: var(--accent-purple); }}
        .stat-card.runs .stat-value {{ color: var(--accent-green); }}
        
        .charts-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(500px, 1fr));
            gap: 2rem;
            margin-bottom: 3rem;
        }}
        
        .chart-card {{
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.5rem;
        }}
        
        .chart-card h2 {{
            font-size: 1.2rem;
            margin-bottom: 1rem;
            color: var(--text-primary);
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }}
        
        .chart-container {{
            position: relative;
            height: 300px;
        }}
        
        .full-width {{
            grid-column: 1 / -1;
        }}
        
        .time-info {{
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.5rem;
            text-align: center;
            margin-top: 2rem;
        }}
        
        .time-info span {{
            color: var(--text-secondary);
            margin: 0 1rem;
        }}
        
        .time-info strong {{
            color: var(--accent-blue);
        }}
        
        footer {{
            text-align: center;
            margin-top: 3rem;
            padding-top: 2rem;
            border-top: 1px solid var(--border-color);
            color: var(--text-secondary);
        }}
        
        @media (max-width: 768px) {{
            .charts-grid {{
                grid-template-columns: 1fr;
            }}
            
            .stats-grid {{
                grid-template-columns: repeat(2, 1fr);
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🔍 doeff-linter Report</h1>
            <p class="subtitle">Code Quality Analysis Dashboard</p>
        </header>
        
        <div class="stats-grid">
            <div class="stat-card runs">
                <div class="stat-value">{total_runs}</div>
                <div class="stat-label">Lint Runs</div>
            </div>
            <div class="stat-card total">
                <div class="stat-value">{total_violations}</div>
                <div class="stat-label">Total Violations</div>
            </div>
            <div class="stat-card errors">
                <div class="stat-value">{total_errors}</div>
                <div class="stat-label">Errors</div>
            </div>
            <div class="stat-card warnings">
                <div class="stat-value">{total_warnings}</div>
                <div class="stat-label">Warnings</div>
            </div>
            <div class="stat-card info">
                <div class="stat-value">{total_info}</div>
                <div class="stat-label">Info</div>
            </div>
        </div>
        
        <div class="charts-grid">
            <div class="chart-card">
                <h2>📋 Violations by Rule</h2>
                <div class="chart-container">
                    <canvas id="ruleChart"></canvas>
                </div>
            </div>
            
            <div class="chart-card">
                <h2>🎯 Severity Distribution</h2>
                <div class="chart-container">
                    <canvas id="severityChart"></canvas>
                </div>
            </div>
            
            <div class="chart-card full-width">
                <h2>📁 Top Files by Violations</h2>
                <div class="chart-container">
                    <canvas id="fileChart"></canvas>
                </div>
            </div>
            
            <div class="chart-card full-width">
                <h2>📈 Daily Activity</h2>
                <div class="chart-container">
                    <canvas id="trendChart"></canvas>
                </div>
            </div>
        </div>
        
        <div class="time-info">
            <span>First run: <strong>{first_run}</strong></span>
            <span>Last run: <strong>{last_run}</strong></span>
            <span>Days tracked: <strong>{days_tracked}</strong></span>
        </div>
        
        <footer>
            Generated by doeff-linter • {generated_at}
        </footer>
    </div>
    
    <script>
        Chart.defaults.color = '#8b949e';
        Chart.defaults.borderColor = '#30363d';
        
        const colors = {{
            blue: '#58a6ff',
            green: '#3fb950',
            yellow: '#d29922',
            red: '#f85149',
            purple: '#a371f7',
            cyan: '#39c5cf',
            orange: '#db6d28',
            pink: '#db61a2'
        }};
        
        const colorPalette = [
            colors.blue, colors.green, colors.yellow, colors.red,
            colors.purple, colors.cyan, colors.orange, colors.pink
        ];
        
        // Rule Chart
        new Chart(document.getElementById('ruleChart'), {{
            type: 'bar',
            data: {{
                labels: [{rule_labels}],
                datasets: [{{
                    label: 'Violations',
                    data: [{rule_values}],
                    backgroundColor: colorPalette,
                    borderRadius: 4
                }}]
            }},
            options: {{
                indexAxis: 'y',
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{ display: false }}
                }},
                scales: {{
                    x: {{ grid: {{ color: '#21262d' }} }},
                    y: {{ grid: {{ display: false }} }}
                }}
            }}
        }});
        
        // Severity Chart
        new Chart(document.getElementById('severityChart'), {{
            type: 'doughnut',
            data: {{
                labels: ['Errors', 'Warnings', 'Info'],
                datasets: [{{
                    data: [{total_errors}, {total_warnings}, {total_info}],
                    backgroundColor: [colors.red, colors.yellow, colors.blue],
                    borderWidth: 0
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{
                        position: 'bottom'
                    }}
                }}
            }}
        }});
        
        // File Chart
        new Chart(document.getElementById('fileChart'), {{
            type: 'bar',
            data: {{
                labels: [{file_labels}],
                datasets: [{{
                    label: 'Violations',
                    data: [{file_values}],
                    backgroundColor: colors.purple,
                    borderRadius: 4
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{ display: false }}
                }},
                scales: {{
                    x: {{ grid: {{ display: false }} }},
                    y: {{ grid: {{ color: '#21262d' }} }}
                }}
            }}
        }});
        
        // Trend Chart
        new Chart(document.getElementById('trendChart'), {{
            type: 'line',
            data: {{
                labels: [{trend_labels}],
                datasets: [{{
                    label: 'Lint Runs',
                    data: [{trend_values}],
                    borderColor: colors.green,
                    backgroundColor: 'rgba(63, 185, 80, 0.1)',
                    fill: true,
                    tension: 0.4,
                    pointRadius: 4,
                    pointBackgroundColor: colors.green
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{ display: false }}
                }},
                scales: {{
                    x: {{ grid: {{ color: '#21262d' }} }},
                    y: {{ 
                        grid: {{ color: '#21262d' }},
                        beginAtZero: true
                    }}
                }}
            }}
        }});
    </script>
</body>
</html>
"##,
        total_runs = stats.total_runs,
        total_violations = stats.total_violations,
        total_errors = stats.total_errors,
        total_warnings = stats.total_warnings,
        total_info = stats.total_info,
        rule_labels = rule_labels.join(", "),
        rule_values = rule_values.join(", "),
        file_labels = file_labels.join(", "),
        file_values = file_values.join(", "),
        trend_labels = trend_labels.join(", "),
        trend_values = trend_values.join(", "),
        first_run = stats.first_run.as_deref().unwrap_or("N/A"),
        last_run = stats.last_run.as_deref().unwrap_or("N/A"),
        days_tracked = stats.runs_by_date.len(),
        generated_at = chrono::Utc::now().format("%Y-%m-%d %H:%M:%S UTC"),
    );

    file.write_all(html.as_bytes())?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn test_generate_empty_report() {
        let dir = TempDir::new().unwrap();
        let output = dir.path().join("report.html");
        let stats = LogStats::default();

        generate_html_report(&stats, &output).unwrap();

        let content = std::fs::read_to_string(&output).unwrap();
        assert!(content.contains("doeff-linter Report"));
        assert!(content.contains("Chart.js"));
    }
}

