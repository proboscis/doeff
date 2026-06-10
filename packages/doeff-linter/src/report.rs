//! HTML Report generation module
//!
//! Generates standalone HTML reports with interactive charts using Chart.js

use crate::stats::LogStats;
use std::fs::File;
use std::io::Write;
use std::path::Path;

/// Rule information for tooltips and documentation
struct RuleInfo {
    id: &'static str,
    name: &'static str,
    description: &'static str,
    fix: &'static str,
    severity: &'static str,
}

fn get_all_rule_info() -> Vec<RuleInfo> {
    vec![
        RuleInfo {
            id: "DOEFF001",
            name: "Builtin Shadowing",
            description: "A function parameter or variable shadows a Python builtin (e.g., list, dict, id).",
            fix: "Rename the variable to avoid shadowing: items instead of list, mapping instead of dict.",
            severity: "error",
        },
        RuleInfo {
            id: "DOEFF002",
            name: "Mutable Attribute Naming",
            description: "A mutable class attribute (list, dict, set) doesn't follow the _mut_ naming convention.",
            fix: "Prefix mutable attributes with _mut_: self._mut_items = [] instead of self.items = [].",
            severity: "warning",
        },
        RuleInfo {
            id: "DOEFF003",
            name: "Max Mutable Attributes",
            description: "A class has too many mutable attributes, indicating potential design issues.",
            fix: "Refactor the class to reduce mutable state, or split into smaller classes.",
            severity: "warning",
        },
        RuleInfo {
            id: "DOEFF004",
            name: "No os.environ Access",
            description: "Direct access to os.environ breaks dependency injection principles.",
            fix: "Inject configuration as function parameters or use a config dataclass instead.",
            severity: "warning",
        },
        RuleInfo {
            id: "DOEFF005",
            name: "No Setter Methods",
            description: "Setter methods (set_*, @property.setter) violate immutability principles.",
            fix: "Use immutable patterns: return new instances with modified values instead of mutating.",
            severity: "warning",
        },
        RuleInfo {
            id: "DOEFF006",
            name: "No Tuple Returns",
            description: "Returning raw tuples reduces code readability and type safety.",
            fix: "Use a dataclass or NamedTuple: @dataclass class Result: value: int; error: str.",
            severity: "error",
        },
        RuleInfo {
            id: "DOEFF007",
            name: "No Mutable Argument Mutations",
            description: "Mutating function arguments (list.append, dict.update) causes side effects.",
            fix: "Create a copy first: items = items.copy(); items.append(x) or return new collections.",
            severity: "warning",
        },
        RuleInfo {
            id: "DOEFF008",
            name: "No Dataclass Attribute Mutation",
            description: "Mutating dataclass attributes after creation breaks immutability.",
            fix: "Use frozen=True dataclasses and dataclasses.replace() to create modified copies.",
            severity: "warning",
        },
        RuleInfo {
            id: "DOEFF009",
            name: "Missing Return Type Annotation",
            description: "Functions without return type annotations reduce code clarity and type safety.",
            fix: "Add return type: def foo() -> int: or def bar() -> None: for no return value.",
            severity: "warning",
        },
        RuleInfo {
            id: "DOEFF010",
            name: "Test File Placement",
            description: "Test files should be in a tests/ directory, not mixed with source code.",
            fix: "Move test files to a dedicated tests/ directory at the project root.",
            severity: "warning",
        },
        RuleInfo {
            id: "DOEFF011",
            name: "No Flag/Mode Arguments",
            description: "Functions use flag/mode arguments instead of callbacks or protocol objects.",
            fix: "Accept a callback or protocol object instead of boolean flags.",
            severity: "warning",
        },
        RuleInfo {
            id: "DOEFF012",
            name: "No Append Loop Pattern",
            description: "Empty list initialization followed by for-loop append obscures the data transformation.",
            fix: "Use list comprehension: data = [process(x) for x in items].",
            severity: "warning",
        },
        RuleInfo {
            id: "DOEFF013",
            name: "Prefer Maybe Monad",
            description: "Optional[X] or X | None should use doeff's Maybe monad for explicit null handling.",
            fix: "Use Maybe[X] instead of Optional[X]. Import from doeff import Maybe, Some, NOTHING.",
            severity: "warning",
        },
        RuleInfo {
            id: "DOEFF014",
            name: "No Try-Except Blocks",
            description: "Using try-except blocks hides error handling flow.",
            fix: "Use Safe(program) to get a Result, program.recover(fallback) for fallbacks.",
            severity: "warning",
        },
    ]
}

fn get_rule_info_map() -> std::collections::HashMap<&'static str, RuleInfo> {
    get_all_rule_info()
        .into_iter()
        .map(|r| (r.id, r))
        .collect()
}

/// Generate an HTML report from log statistics
pub fn generate_html_report(stats: &LogStats, output_path: &Path) -> std::io::Result<()> {
    let mut file = File::create(output_path)?;

    let rule_info_map = get_rule_info_map();
    let rules_data = stats.rules_sorted();
    
    // Generate rule data with info for tooltips
    let rule_labels: Vec<_> = rules_data.iter().map(|(r, _)| format!("\"{}\"", r)).collect();
    let rule_values: Vec<_> = rules_data.iter().map(|(_, c)| c.to_string()).collect();
    
    // Generate rule info JSON for JavaScript
    let rule_info_json: Vec<String> = get_all_rule_info()
        .iter()
        .map(|r| {
            format!(
                "\"{}\": {{\"name\": \"{}\", \"description\": \"{}\", \"fix\": \"{}\", \"severity\": \"{}\"}}",
                r.id,
                r.name.replace('"', "\\\""),
                r.description.replace('"', "\\\""),
                r.fix.replace('"', "\\\""),
                r.severity
            )
        })
        .collect();

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

    // Generate rule details HTML
    let mut rule_details_html = String::new();
    for (rule_id, count) in &rules_data {
        if let Some(info) = rule_info_map.get(rule_id.as_str()) {
            let severity_class = info.severity;
            let severity_badge = match info.severity {
                "error" => r#"<span class="badge badge-error">Error</span>"#,
                "warning" => r#"<span class="badge badge-warning">Warning</span>"#,
                _ => r#"<span class="badge badge-info">Info</span>"#,
            };
            rule_details_html.push_str(&format!(
                r#"
                <div class="rule-item" data-rule="{rule_id}">
                    <div class="rule-header">
                        <span class="rule-id {severity_class}">{rule_id}</span>
                        <span class="rule-name">{name}</span>
                        {severity_badge}
                        <span class="rule-count">{count} violations</span>
                    </div>
                    <div class="rule-body">
                        <p class="rule-description"><strong>Problem:</strong> {description}</p>
                        <p class="rule-fix"><strong>Fix:</strong> {fix}</p>
                    </div>
                </div>
                "#,
                rule_id = rule_id,
                name = info.name,
                description = info.description,
                fix = info.fix,
                count = count,
                severity_class = severity_class,
                severity_badge = severity_badge,
            ));
        }
    }

    // Generate all rules reference HTML
    let mut all_rules_html = String::new();
    for info in get_all_rule_info() {
        let count = stats.violations_by_rule.get(info.id).unwrap_or(&0);
        let severity_badge = match info.severity {
            "error" => r#"<span class="badge badge-error">Error</span>"#,
            "warning" => r#"<span class="badge badge-warning">Warning</span>"#,
            _ => r#"<span class="badge badge-info">Info</span>"#,
        };
        let active_class = if *count > 0 { "active" } else { "inactive" };
        all_rules_html.push_str(&format!(
            r#"
            <div class="rule-ref-item {active_class}">
                <div class="rule-ref-header">
                    <span class="rule-id {severity}">{id}</span>
                    <span class="rule-name">{name}</span>
                    {severity_badge}
                    <span class="rule-count">{count}</span>
                </div>
                <div class="rule-ref-body">
                    <p><strong>Problem:</strong> {description}</p>
                    <p><strong>Fix:</strong> {fix}</p>
                </div>
            </div>
            "#,
            id = info.id,
            name = info.name,
            description = info.description,
            fix = info.fix,
            count = count,
            severity = info.severity,
            severity_badge = severity_badge,
            active_class = active_class,
        ));
    }

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
            --accent-cyan: #39c5cf;
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
        
        /* Collapsible Sections */
        details {{
            margin-bottom: 1.5rem;
        }}
        
        summary {{
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1rem 1.5rem;
            cursor: pointer;
            font-size: 1.2rem;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 0.75rem;
            transition: background 0.2s;
            list-style: none;
        }}
        
        summary::-webkit-details-marker {{
            display: none;
        }}
        
        summary::before {{
            content: '▶';
            font-size: 0.8rem;
            transition: transform 0.2s;
            color: var(--accent-blue);
        }}
        
        details[open] summary::before {{
            transform: rotate(90deg);
        }}
        
        summary:hover {{
            background: var(--bg-tertiary);
        }}
        
        .section-content {{
            padding: 1.5rem;
            margin-top: 0.5rem;
        }}
        
        /* Stats Grid */
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 1rem;
            margin-bottom: 1rem;
        }}
        
        .stat-card {{
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.25rem;
            text-align: center;
            transition: transform 0.2s, box-shadow 0.2s;
        }}
        
        .stat-card:hover {{
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
        }}
        
        .stat-value {{
            font-size: 2.25rem;
            font-weight: 700;
            margin-bottom: 0.25rem;
        }}
        
        .stat-label {{
            color: var(--text-secondary);
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        
        .stat-card.errors .stat-value {{ color: var(--accent-red); }}
        .stat-card.warnings .stat-value {{ color: var(--accent-yellow); }}
        .stat-card.info .stat-value {{ color: var(--accent-blue); }}
        .stat-card.total .stat-value {{ color: var(--accent-purple); }}
        .stat-card.runs .stat-value {{ color: var(--accent-green); }}
        
        /* Charts Grid */
        .charts-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(450px, 1fr));
            gap: 1.5rem;
        }}
        
        .chart-card {{
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.25rem;
        }}
        
        .chart-card h3 {{
            font-size: 1rem;
            margin-bottom: 1rem;
            color: var(--text-primary);
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }}
        
        .chart-container {{
            position: relative;
            height: 280px;
        }}
        
        .full-width {{
            grid-column: 1 / -1;
        }}
        
        /* Rule Items */
        .rule-item {{
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            margin-bottom: 0.75rem;
            overflow: hidden;
            transition: border-color 0.2s;
        }}
        
        .rule-item:hover {{
            border-color: var(--accent-blue);
        }}
        
        .rule-header {{
            padding: 1rem 1.25rem;
            display: flex;
            align-items: center;
            gap: 0.75rem;
            flex-wrap: wrap;
        }}
        
        .rule-body {{
            padding: 0 1.25rem 1rem;
            border-top: 1px solid var(--border-color);
            margin-top: 0;
            padding-top: 1rem;
        }}
        
        .rule-id {{
            font-family: 'SF Mono', Consolas, monospace;
            font-weight: 600;
            padding: 0.25rem 0.5rem;
            border-radius: 4px;
            font-size: 0.85rem;
        }}
        
        .rule-id.error {{
            background: rgba(248, 81, 73, 0.2);
            color: var(--accent-red);
        }}
        
        .rule-id.warning {{
            background: rgba(210, 153, 34, 0.2);
            color: var(--accent-yellow);
        }}
        
        .rule-name {{
            font-weight: 600;
            color: var(--text-primary);
        }}
        
        .rule-count {{
            margin-left: auto;
            color: var(--text-secondary);
            font-size: 0.9rem;
        }}
        
        .rule-description, .rule-fix {{
            color: var(--text-secondary);
            font-size: 0.9rem;
            margin-bottom: 0.5rem;
        }}
        
        .rule-fix {{
            margin-bottom: 0;
        }}
        
        .rule-fix strong {{
            color: var(--accent-green);
        }}
        
        /* Badge */
        .badge {{
            font-size: 0.7rem;
            padding: 0.2rem 0.5rem;
            border-radius: 10px;
            text-transform: uppercase;
            font-weight: 600;
        }}
        
        .badge-error {{
            background: rgba(248, 81, 73, 0.2);
            color: var(--accent-red);
        }}
        
        .badge-warning {{
            background: rgba(210, 153, 34, 0.2);
            color: var(--accent-yellow);
        }}
        
        .badge-info {{
            background: rgba(88, 166, 255, 0.2);
            color: var(--accent-blue);
        }}
        
        /* Rule Reference Section */
        .rule-ref-item {{
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            margin-bottom: 0.5rem;
            overflow: hidden;
        }}
        
        .rule-ref-item.inactive {{
            opacity: 0.5;
        }}
        
        .rule-ref-item.active {{
            border-left: 3px solid var(--accent-purple);
        }}
        
        .rule-ref-header {{
            padding: 0.75rem 1rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
            flex-wrap: wrap;
            cursor: pointer;
        }}
        
        .rule-ref-body {{
            display: none;
            padding: 0.75rem 1rem;
            border-top: 1px solid var(--border-color);
            font-size: 0.85rem;
        }}
        
        .rule-ref-item:hover .rule-ref-body {{
            display: block;
        }}
        
        .rule-ref-item .rule-count {{
            font-family: 'SF Mono', Consolas, monospace;
            background: var(--bg-tertiary);
            padding: 0.2rem 0.5rem;
            border-radius: 4px;
            font-size: 0.8rem;
        }}
        
        /* Time Info */
        .time-info {{
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1rem 1.5rem;
            display: flex;
            justify-content: center;
            gap: 2rem;
            flex-wrap: wrap;
            margin-top: 1rem;
        }}
        
        .time-info span {{
            color: var(--text-secondary);
        }}
        
        .time-info strong {{
            color: var(--accent-cyan);
        }}
        
        footer {{
            text-align: center;
            margin-top: 2rem;
            padding-top: 1.5rem;
            border-top: 1px solid var(--border-color);
            color: var(--text-secondary);
            font-size: 0.9rem;
        }}
        
        /* Tooltip */
        .tooltip {{
            position: relative;
        }}
        
        .tooltip .tooltip-text {{
            visibility: hidden;
            background: var(--bg-tertiary);
            color: var(--text-primary);
            padding: 0.75rem 1rem;
            border-radius: 8px;
            border: 1px solid var(--border-color);
            position: absolute;
            z-index: 100;
            bottom: 125%;
            left: 50%;
            transform: translateX(-50%);
            width: 300px;
            font-size: 0.85rem;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
            opacity: 0;
            transition: opacity 0.2s;
        }}
        
        .tooltip:hover .tooltip-text {{
            visibility: visible;
            opacity: 1;
        }}
        
        @media (max-width: 768px) {{
            .charts-grid {{
                grid-template-columns: 1fr;
            }}
            
            .stats-grid {{
                grid-template-columns: repeat(2, 1fr);
            }}
            
            .time-info {{
                flex-direction: column;
                gap: 0.5rem;
                text-align: center;
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
        
        <!-- Summary Section (Open by default) -->
        <details open>
            <summary>📊 Summary</summary>
            <div class="section-content">
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
                <div class="time-info">
                    <span>First run: <strong>{first_run}</strong></span>
                    <span>Last run: <strong>{last_run}</strong></span>
                    <span>Days tracked: <strong>{days_tracked}</strong></span>
                </div>
            </div>
        </details>
        
        <!-- Detected Issues Section -->
        <details open>
            <summary>⚠️ Detected Issues ({total_violations} violations)</summary>
            <div class="section-content">
                {rule_details_html}
            </div>
        </details>
        
        <!-- Charts Section -->
        <details>
            <summary>📈 Charts & Visualizations</summary>
            <div class="section-content">
                <div class="charts-grid">
                    <div class="chart-card">
                        <h3>📋 Violations by Rule</h3>
                        <div class="chart-container">
                            <canvas id="ruleChart"></canvas>
                        </div>
                    </div>
                    
                    <div class="chart-card">
                        <h3>🎯 Severity Distribution</h3>
                        <div class="chart-container">
                            <canvas id="severityChart"></canvas>
                        </div>
                    </div>
                    
                    <div class="chart-card full-width">
                        <h3>📁 Top Files by Violations</h3>
                        <div class="chart-container">
                            <canvas id="fileChart"></canvas>
                        </div>
                    </div>
                    
                    <div class="chart-card full-width">
                        <h3>📅 Daily Activity</h3>
                        <div class="chart-container">
                            <canvas id="trendChart"></canvas>
                        </div>
                    </div>
                </div>
            </div>
        </details>
        
        <!-- Rules Reference Section -->
        <details>
            <summary>📚 Rules Reference (hover for details)</summary>
            <div class="section-content">
                <p style="color: var(--text-secondary); margin-bottom: 1rem; font-size: 0.9rem;">
                    Hover over any rule to see its description and fix suggestion. 
                    Rules with violations are highlighted with a purple border.
                </p>
                {all_rules_html}
            </div>
        </details>
        
        <footer>
            Generated by doeff-linter • {generated_at}
        </footer>
    </div>
    
    <script>
        // Rule information for tooltips
        const ruleInfo = {{{rule_info_json}}};
        
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
        
        // Custom tooltip for rule chart
        const ruleTooltip = {{
            callbacks: {{
                title: function(context) {{
                    const ruleId = context[0].label;
                    const info = ruleInfo[ruleId];
                    return info ? `${{ruleId}} - ${{info.name}}` : ruleId;
                }},
                afterTitle: function(context) {{
                    const ruleId = context[0].label;
                    const info = ruleInfo[ruleId];
                    return info ? `\n${{info.description}}` : '';
                }},
                afterBody: function(context) {{
                    const ruleId = context[0].label;
                    const info = ruleInfo[ruleId];
                    return info ? [`\n💡 Fix: ${{info.fix}}`] : [];
                }}
            }},
            bodyFont: {{ size: 12 }},
            titleFont: {{ size: 13, weight: 'bold' }},
            padding: 12,
            boxPadding: 6,
            backgroundColor: '#21262d',
            borderColor: '#30363d',
            borderWidth: 1,
            displayColors: false
        }};
        
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
                    legend: {{ display: false }},
                    tooltip: ruleTooltip
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
        rule_info_json = rule_info_json.join(", "),
        rule_details_html = rule_details_html,
        all_rules_html = all_rules_html,
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
        assert!(content.contains("chart.js"));
        assert!(content.contains("Rules Reference"));
    }
    
    #[test]
    fn test_rule_info_complete() {
        let rules = get_all_rule_info();
        assert!(rules.len() >= 14);
        for rule in &rules {
            assert!(!rule.id.is_empty());
            assert!(!rule.name.is_empty());
            assert!(!rule.description.is_empty());
            assert!(!rule.fix.is_empty());
        }
    }
}
