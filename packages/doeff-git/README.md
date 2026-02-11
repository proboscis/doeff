# doeff-git

Provider-agnostic git effects for doeff.

- Local git operations (`GitCommit`, `GitPush`, `GitPull`, `GitDiff`)
- Hosting operations (`CreatePR`, `MergePR`)
- Pluggable handlers (`GitLocalHandler`, `GitHubHandler`, `mock_handlers`)

Use `production_handlers()` for git CLI + GitHub (`gh`) execution, or `mock_handlers()` for tests.
