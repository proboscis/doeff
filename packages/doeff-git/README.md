# doeff-git

Provider-agnostic git effects for doeff.

- Local git operations (`GitCommit`, `GitPush`, `GitPull`, `GitDiff`)
- Hosting operations (`CreatePR`, `MergePR`)
- Handlers: `git_local_handler` (git CLI), `github_handler` (GitHub CLI `gh`), `mock_handlers`

Use `production_handlers()` for git CLI + GitHub (`gh`) execution, or `mock_handlers()` for tests:

```python
from doeff import do, run
from doeff_git import GitCommit, production_handlers

@do
def save(work_dir):
    return (yield GitCommit(work_dir=work_dir, message="save"))

sha = run(production_handlers()(save(repo_path)))
```

`git_local_handler` and `github_handler` can be installed on their own; to answer some
effects another way, install your handler inside them (nearer the program).
