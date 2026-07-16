# doeff-domain

`doeff-domain` provides opt-in vocabulary declarations for doeff effect families.
It records which domain introduces each effect class, derives handler participation
from explicit annotations or `defhandler` structure, and exposes coverage and orphan
checks that adopting projects can wire into their own tests.

```bash
uv add doeff-domain
```

```hy
(require doeff-domain.macros [defdomain])

(defdomain account-effects
  :title "Account effects"
  :effects [LoadAccount SaveAccount]
  :handlers [account-handler])
```

Importing a declaration module registers its domains in the current process. The
package does not install a global test or lint gate; conformance remains explicitly
opt-in.
