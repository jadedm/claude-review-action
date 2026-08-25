# Stack — Python (pytest, pandas/numpy, requests, uv/pip)

A general review prompt for Python codebases: libraries, CLIs, data pipelines and services alike. Reviewers should apply these conventions in addition to the base review areas. Project-specific deviations (framework choices, house layout, threshold tunings) should be supplied via the consumer's `extra_prompt` or `extra_prompt_path` input and will appear under the "Repo-specific notes" section.

Several base-prompt bullets are TypeScript-shaped and do not apply here: `any` / `unknown` escape hatches, floating promises, and blocking the event loop. Their Python equivalents are covered below.

## Language traps

- **Mutable default arguments.** `def f(x, items=[])` or `={}` creates one object shared by every call. Flag any default that is a list, dict, set, or a call such as `datetime.now()`. Use `None` and build inside the body.
- **Late-binding closures.** A lambda or comprehension capturing a loop variable sees its final value, so `[lambda: i for i in range(3)]` returns three functions all yielding `2`. Bind explicitly with a default argument.
- **Truthiness of containers and of zero.** `if not df`, `if not arr` and `if not count` conflate empty with missing and with `0`. Prefer `is None`, `len(x) == 0`, or an explicit comparison. On a DataFrame or ndarray, plain truthiness raises.
- **Identity against equality.** `is` on `int`, `str` or tuple literals works by interning accident and breaks on larger values. Reserve `is` for `None`, `True`, `False` and sentinels.
- **Shadowed builtins and modules.** A local named `id`, `type`, `list`, or a file named `types.py` next to code importing `types`, both fail far from the cause.
- **Equality and hashing.** A class defining `__eq__` without `__hash__` becomes unhashable. A dataclass with `eq=True` (the default) does the same unless `frozen=True`.

## Errors and exception boundaries

- **The exception tuple must cover the whole family.** Catching `(ConnectionError, Timeout, HTTPError)` from `requests` lets `TooManyRedirects` and `ContentDecodingError` escape as raw transport errors to a caller the function promises a typed error to. `requests.RequestException` is the parent. Check every hand-written tuple against the library's hierarchy.
- **Decoding failures are not transport failures.** `json.JSONDecodeError` subclasses `ValueError`, not the transport error type, so a 200 carrying a consent page or a truncated body escapes a transport-only boundary.
- **`except Exception` does not catch `KeyboardInterrupt` or `SystemExit`.** If a `try` block guards a restore or cleanup path that must run when a user presses Ctrl-C, it needs `BaseException` or a `finally`. Flag any rollback reachable only from a narrow `except`.
- **Bare `except:` and `except Exception: pass`.** Both hide the failure. If swallowing is intended, log it and say why in a comment.
- **`raise ... from exc`** on re-raise. Losing `__cause__` costs the original traceback and is the single most common reason a production error is undiagnosable.
- **Errors carry codes, not just prose.** A caller cannot branch on a message string.

## Data, serialisation and numerics

- **An empty DataFrame has no columns.** `df[df["col"] == x]` raises `KeyError` when `df` is empty, so any filter reachable with an empty frame needs a guard. This is a common source of a crash that only appears when upstream returns nothing.
- **Types at the boundary are part of the contract.** A field arriving as `"2"` instead of `2` makes an equality filter match nothing and produces a valid-looking empty result rather than an error. Coerce and validate at the edge; flag comparisons between a parsed value and a literal of a different type.
- **`json.dumps` defaults to `allow_nan=True`** and emits bare `NaN` and `Infinity`, which are not valid JSON and break strict readers. Any dict built from pandas or numpy should be serialised with `allow_nan=False`, with `NaN` converted to `None` first.
- **NumPy scalars are not JSON-serialisable.** `np.int64` and `np.float64` raise in `json.dumps`. Convert with `.item()` or `float()`.
- **Chained assignment and `inplace`.** `df[mask]["col"] = value` writes to a copy and is silently lost. `inplace=True` is not a performance win and is being removed. Prefer `.loc` assignment and reassignment.
- **Silent dtype coercion.** A column of ints gains a `NaN` and becomes `float64`; a merge on mismatched dtypes produces no rows. Flag merges and comparisons that do not establish dtypes first.
- **Floating-point equality.** `==` on floats, including in test assertions, should be `math.isclose`, `pytest.approx`, or `np.allclose` with a stated tolerance.
- **Correlation and aggregation on degenerate input.** A group with fewer than two rows, or zero variance, yields `NaN` rather than an error, and that `NaN` then travels. Decide and encode what those cases should return.

## Tests

- **A test that passes with its feature deleted is not a test.** This is the highest-value thing to look for. Ask of each new test: if the function body were replaced with `return None` or a constant, would this still pass? If yes, say so and name what to assert instead.
- **For a feature whose job is a side effect, assert the side effect.** Asserting that a collaborator was called, or that a function returned without raising, proves the wiring and not the work. At least one case must observe the file written, the row inserted, or the state changed.
- **A test must not supply the value it claims to check.** Passing an explicit `root=tmp_path` cannot demonstrate that the default resolves correctly. The default path needs a case that exercises the default.
- **`assert` with no message on a compound condition** gives an unreadable failure. Split it or add the message.
- **`skip` and `skipif` hide regressions.** A test skipped because an artefact is missing reports green forever on every machine lacking that artefact. Flag any new skip whose condition is true in CI, and ask what makes the build fail if the skipped test would have failed.
- **Fixtures must not reach outside the repo.** A test reading a gitignored data directory, an absolute home path, or the network fails on a clean checkout. Use `tmp_path`, vendor a minimal fixture, or mark it as requiring the resource.
- **Mutable state shared between tests.** A module-level dict, a `lru_cache`, or a monkeypatched global that is not restored makes the suite order-dependent. `monkeypatch` and fixtures with teardown, not manual assignment.
- **Time and randomness.** `datetime.now()` and unseeded `random` make a test that fails once a year. Inject a clock; seed explicitly.

## Files, IO and concurrency

- **Writes that must not be seen half-finished go to a temporary path and are renamed.** `rename` is atomic within a filesystem; writing in place leaves a truncated file if the process dies. When replacing a directory, make sure the failure path restores the original rather than leaving it under a temporary name.
- **Check-then-act is not atomic.** `if not path.exists(): write(path)` races. So does reading a timestamp and then overwriting based on it. Flag any guard whose decision can be invalidated between the check and the write.
- **Every acquired resource uses a context manager.** Files, locks, connections, and `subprocess` pipes. A bare `open()` whose handle is closed on a later line does not survive an exception.
- **`pathlib` over string concatenation**, and never `os.path.join` on a value that could be absolute.
- **`subprocess` without `shell=True`,** passing a list. With `shell=True` any interpolated value is an injection. Always pass `check=True` or inspect `returncode`; it is silently ignored otherwise.
- **A pipeline's exit status is the last command's.** `cmd | tail; echo $?` reports `tail`. In CI steps and helper scripts, use `set -o pipefail` or capture the status directly.
- **Threads share memory; processes do not.** Flag a `ThreadPoolExecutor` mutating a shared dict or list without a lock, and a `ProcessPoolExecutor` expecting a mutation to be visible in the parent.

## Packaging, environments and configuration

- **The lockfile must be asserted, not merely used.** `uv sync --frozen` and `pip install` from a lockfile both accept a lockfile that has drifted from `pyproject.toml`. `uv sync --locked` asserts the lock would not change and fails on drift. Flag the weaker flag in CI wherever versions matter, which is anywhere a pickle, a model artefact, or a serialised format is loaded.
- **Pinned versions where an artefact demands them.** Anything unpickling a model or reading a version-sensitive binary format needs exact pins for the libraries that wrote it, not lower bounds.
- **Configuration read once, at an edge.** `os.environ[...]` scattered through modules makes behaviour untestable and failures late. Read at startup, validate, and pass the result.
- **Import-time side effects.** Network calls, file reads, and mutable global construction at module scope run on import, including during test collection. Move them into functions.
- **`sys.path` manipulation** in library or test code hides a packaging problem rather than solving it.

## Code quality

- **Type hints on public functions**, and no `# type: ignore` without a reason on the same line. If the project runs no type checker, do not report missing annotations as findings.
- **f-strings for formatting, except in logging calls.** `logger.info("x %s", value)` defers interpolation and keeps the message template groupable; `logger.info(f"x {value}")` does neither.
- **No `print` in library code.** Use `logging`. A CLI printing to stdout is fine and should say so.
- **Comments explain why.** A docstring restating the signature is noise; a comment naming the failure mode a guard exists for is the most valuable line in the file.
