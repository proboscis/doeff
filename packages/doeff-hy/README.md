# doeff-hy

Standard Hy macros for doeff effect composition.

## Usage

```hy
(require doeff-hy.macros [do! defk deff <- defprogram do-list do-list-try do-try-list do-dict-try do-try])
(import doeff [do :as _doeff_do])  ; required by defk/defprogram
(import doeff_core_effects [Ask Try slog])

(defk fetch-data [url]
  (<- response (http-get url))
  (return response))

(defprogram my-pipeline
  (<- data (fetch-data "https://example.com"))
  (process data))
```

## Macros

| Macro | Purpose |
|-------|---------|
| `do!` | Monadic do block — inline effect sequencing with optional `:pre/:post` |
| `<-` | Perform effect, bind result |
| `defk` | Define kleisli function (`@do` + contracts + bang) |
| `deff` | Define function with `:pre/:post` contracts |
| `!` | Inline effect bind in argument position (inside `defk`/`defprogram`) |
| `defprogram` | Define Program constant with implicit do-block |
| `do-list` | List comprehension with effects → `list[T]` |
| `do-list-try` | Per-element Try → `list[Result[T]]` |
| `do-try-list` | All-or-nothing Try → `Result[list[T]]` |
| `do-dict-try` | Dict building, skip errors → `dict[K,V]` |
| `do-try` | Single Result wrap → `Result[T]` |
