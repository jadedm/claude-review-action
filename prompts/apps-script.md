# Stack — Google Apps Script

A general review prompt for Google Apps Script projects (`.gs` / `.js` in the
Apps Script V8 runtime), including add-ons, Sheets/Docs automation, and small
API-to-Spreadsheet tools. Reviewers should apply these conventions in addition
to the base review areas. Any project-specific deviations (metric schemas, API
quirks, sheet layouts) should be supplied via the consumer's `extra_prompt` or
`extra_prompt_path` input and will appear under the "Repo-specific notes" section.

## Runtime & Environment

- **This is the Apps Script V8 runtime, NOT Node.js.** There is no npm, no
  `require`/`import`, no `package.json` dependency graph, and no Node or browser
  globals (`fetch`, `process`, `Buffer`, `window`, `document`, `setTimeout`,
  `setInterval`). Flag any use of these — they will throw at runtime.
- **Only Apps Script services are available**, e.g. `UrlFetchApp`,
  `SpreadsheetApp`, `DocumentApp`, `DriveApp`, `GmailApp`, `Logger`,
  `console`, `Utilities`, `Session`, `PropertiesService`, `CacheService`,
  `LockService`, `ScriptApp`. HTTP is `UrlFetchApp.fetch(url, options)` — not
  `fetch`. Sleeping is `Utilities.sleep(ms)` — not `setTimeout`.
- **Files share one global scope.** All top-level symbols across every file in
  the project are visible to each other (the project is effectively concatenated).
  Ordering between files is not guaranteed, so avoid top-level execution that
  depends on another file's top-level side effects.

## Library vs copy-paste (symbol exposure)

- If the project is meant to be usable **as an Apps Script library**, only
  top-level **function declarations** and `var` globals are exposed to
  consumers. Top-level `class`, `const`, and `let` are **not** exposed — a
  consumer calling `Lib.SomeClass` gets `undefined`. Public entry points should
  therefore be `function` declarations, typically thin facades over an internal
  class.
- **Private-by-convention:** a trailing underscore (`helper_`, `MyClass_`) hides
  a symbol from library consumers. Internal-only classes/helpers should use it.
- **Library code runs under the consumer's authorization**, so the consuming
  project must authorize the scopes the library touches (`UrlFetchApp`,
  `SpreadsheetApp`, etc.). New scope requirements should be documented.
- Flag changes that would break either copy-paste use or library use when the
  project's README/CLAUDE.md states it must support both.

## External requests (`UrlFetchApp`)

- Use `muteHttpExceptions: true` and check `response.getResponseCode()` before
  parsing — otherwise non-2xx responses throw and abort the run.
- Wrap `JSON.parse(response.getContentText())` in try/catch; APIs return HTML
  error pages that are not valid JSON.
- **`fetchAll()` is rate-limited by Apps Script** ("Service invoked too many
  times") and removes per-request timeout control. Sequential `fetch()` with a
  small `Utilities.sleep()` between calls is a legitimate, often preferable
  pattern — do not reflexively suggest parallelizing it.

## Spreadsheet & Drive access

- **Batch reads/writes.** Prefer a single `getValues()` / `setValues(2dArray)`
  over per-cell `getValue()`/`setValue()` in a loop — per-cell calls are the
  most common Apps Script performance bug. Flag loops that touch the sheet each
  iteration.
- Compute the target range explicitly (`getRange(row, col, numRows, numCols)`);
  don't rely on the active range/selection in an automated (trigger) context.
- Create sheets/headers idempotently (check `getSheetByName` before insert).

## Quotas, limits & triggers

- Respect the **6-minute execution limit** (30 min for some Workspace tiers).
  Long jobs must checkpoint progress (e.g. in `PropertiesService`) and continue
  on the next trigger, not assume unlimited runtime.
- Daily quotas apply to `UrlFetchApp` calls, email sends, and triggers — flag
  unbounded loops over large inputs.
- Time-based triggers can fire more than once; operations that must not double
  should guard with `LockService` or a `PropertiesService` flag.

## Auth & secrets

- **No hardcoded API keys, tokens, or spreadsheet IDs of production data.** Use
  `PropertiesService.getScriptProperties()` for secrets and configuration.
- Note that Script Properties are readable by anyone who can edit the script —
  they are obfuscation, not encryption.
- OAuth scopes in `appsscript.json` should be the minimum required; flag
  broad scopes (e.g. full Drive) where a narrower one would do.

## Code quality

- **Prefer `const`/`let`; V8 is enabled.** But remember the library-exposure
  caveat above when choosing `const`/`class` vs `function`/`var` for the public
  surface.
- Use `Logger.log` / `console.log` for diagnostics; there is no structured
  logger. Don't leave noisy per-iteration logging in hot loops.
- Defensive null-handling for missing API fields / empty cells, with an explicit
  placeholder rather than silent `undefined` in output rows.
- Keep functions that are meant to be run from the editor dropdown at top level
  with clear names; keep helpers private (`_` suffix).

## Testing

- Apps Script has no local test runner; tests are typically plain functions
  (e.g. `runUnitTests()`) executed from the editor, with hand-rolled mocks for
  `UrlFetchApp`/`SpreadsheetApp`/etc. Because the runtime is concatenated global
  scope, test files reference production symbols directly. When reviewing test
  changes, check that renamed public symbols are updated in the mocks/tests too.
- `clasp` may be used to sync source to the project; there is usually no build
  step. Don't expect CI to run the Apps Script tests — they run in Google's
  environment.
