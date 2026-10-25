# Security Policy

## Supported versions

Security fixes are made on `main` and shipped in the next release. Only the latest release on
[PyPI](https://pypi.org/project/doeff/) receives them; there are no backports to older minor versions.

| Version | Supported |
| --- | --- |
| latest release (0.7.x) | yes |
| older releases | no — upgrade to the latest release |

The same applies to the packages published from `packages/` (`doeff-vm`, `doeff-core-effects`,
`doeff-hy` and the other integrations).

## Reporting a vulnerability

Do not report a vulnerability in a public issue, pull request or discussion.

Report it privately through GitHub:
[Security → Report a vulnerability](https://github.com/proboscis/doeff/security/advisories/new).
Only the maintainer can read the report. Please include:

- the doeff version (and the package, if it is not `doeff` itself), the Python version and the platform
- what an attacker can do, and under which conditions
- a minimal program or steps that show the problem

## What to expect

doeff is maintained by one person, [@proboscis](https://github.com/proboscis), so these are targets
rather than guarantees:

- **Acknowledgement** within 7 days of the report.
- **First assessment** within 14 days: whether it is accepted as a vulnerability, how severe it is,
  and the plan for a fix.
- **Fix and release** as soon as practical, usually within 90 days of the report. You are kept up to
  date in the private advisory while the fix is prepared.
- **Disclosure** when the fixed release is on PyPI: the advisory is published, with credit to you
  unless you ask to stay anonymous. Please keep the details private until then.

If you have not had an acknowledgement after 7 days, open a public issue that says only that you sent
a private report and are waiting for an answer — without any details of the vulnerability.

## Out of scope

doeff runs the Python code it is given; it is not a sandbox. These are expected behaviour, not
vulnerabilities:

- Running an untrusted program, effect or handler. Handlers decide what effects do, including file,
  network and process access, so a program is as trusted as the handlers it is run with.
- Loading untrusted data with `pickle` or `cloudpickle`, including pickled effects. Unpickling can run
  arbitrary code by design.
- The `doeff` command importing and running the module path it is given.

If you are not sure whether something is in scope, report it privately anyway.
