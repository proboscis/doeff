#!/usr/bin/env bash
# The runs behind every value the CLI Auto-Discovery video shows. Run from anywhere; writes discover.log and cli.log
# next to this file. doeff-indexer 0.1.3 is what `pip install doeff-indexer` installs; `python -m doeff run` is the
# same command as `doeff run`, run by the python that has doeff-indexer next to doeff.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../../../../../.." && pwd)"
cd "$here/project"
doeff_run() {
  echo "\$ doeff run $*"
  PYTHONPATH=. uv run --quiet --project "$repo" --with doeff-indexer==0.1.3 python -m doeff run "$@" 2>&1 | grep -v '^\s*$'
  echo
}
PYTHONPATH=. uv run --quiet --project "$repo" --with doeff-indexer==0.1.3 python "$here/discover.py" > "$here/discover.log" 2>&1
{
  echo "doeff $(git -C "$repo" rev-parse --short HEAD), doeff-indexer 0.1.3"
  echo
  doeff_run --program myapp.features.auth.login.login_program
  doeff_run --program myapp.features.auth.login.login_program --format json
  doeff_run --program myapp.features.auth.login.login_program --interpreter myapp.features.auth.auth_interpreter --env myapp.base_env --env myapp.features.features_env --env myapp.features.auth.auth_env --format json
  doeff_run --program myapp.features.auth.login.login_program --env tests.fakes.test_env --format json
  doeff_run --program myapp.features.auth.login.login_program --interpreter tests.fakes.test_interpreter --format json
  doeff_run --program myapp.features.auth.login.login_program --interpreter tests.fakes.test_interpreter --env tests.fakes.test_env --format json
  doeff_run --format json --hy '(import myapp.features.auth.login [login-program]) (import doeff-core-effects [reader]) ((reader :env {"db_host" "fake-db" "timeout" 1}) login-program)'
  doeff_run --program other.job.job_program --format json
  doeff_run --program other.job.ping_program --format json
} > "$here/cli.log"
