# Stack — NestJS + Fastify + Drizzle (PostgreSQL) + Redis (BullMQ) + pino + JWT

A general review prompt for NestJS monorepos using Fastify, Drizzle ORM over PostgreSQL, Redis (BullMQ) for queues, pino for logging, and JWT for auth. Reviewers should apply these conventions in addition to the base review areas. Any project-specific deviations (custom guard names, internal paths, threshold tunings) should be supplied via the consumer's `extra_prompt` or `extra_prompt_path` input and will appear under the "Repo-specific notes" section.

## Architecture & Patterns

- **Controllers are thin.** They map HTTP shape only and delegate to services. No business logic in the controller layer.
- **Services are constructor-injectable and unit-testable.** Avoid `new` inside services; let the DI container compose dependencies.
- **DTOs use `class-validator` + `class-transformer`** decorators for every request body, query string, and route param shape. Never read `req.body` directly past the controller signature.
- **Guards for auth, Interceptors for transforms, Pipes for validation** — keep the responsibilities separated; don't smuggle business logic into a Pipe.
- **Don't re-import `@Global()` modules.** Globals are already available everywhere via the root module.
- **Feature modules** follow a consistent layout: `<feature>.module.ts`, `<feature>.controller.ts`, `<feature>.service.ts`, optional `<feature>.repository.ts`, a `dto/` subdirectory, and a `<feature>.constants.ts` for magic values.
- **Throw typed exceptions** from your application's common-exceptions library (e.g. project-defined `NotFoundException`, `ConflictException`, `BadRequestException`). Avoid raw `HttpException` — the project's exception class encodes the response envelope, error code, and logging behaviour consistently.
- **Don't manually construct the response envelope.** A global `ResponseInterceptor` (or equivalent) wraps every response — duplicating that shape in controllers diverges over time.
- **Read the authenticated user via a `@CurrentUser()` decorator** (or equivalent) — never `request.user` directly. The decorator centralises null-handling and type-narrowing.
- **Pagination returns `{ items, _meta: { total, page, limit } }`** from services so the response interceptor can merge `_meta` consistently across endpoints.
- **External integrations** (payments, SMS, email, KYC, etc.) live behind an orchestrator/provider interface — application code injects the orchestrator, never the provider adapter directly. This keeps swap-outs and multi-provider routing isolated to one layer.

## Security

- **Auth guards on every non-public endpoint.** Any `@Public()`-style escape hatch must be justified inline.
- **Input validation via DTOs + `class-validator` only** — never `req.body` directly.
- **Drizzle ORM for all queries** — no raw SQL string concatenation. If raw SQL is unavoidable for a query the ORM can't express, parameterise via the driver's `sql` template tag.
- **`ThrottlerGuard` (or equivalent rate limiter) is configured globally;** flag endpoints that need a custom (usually tighter) limit, e.g. login, OTP send/verify, password reset, payment webhooks.
- **Log redaction must cover** `Authorization` headers, `Cookie` headers, and any request-body fields named `password`, `passwordConfirmation`, `token`, `refreshToken`, `secret`, `apiKey`. Flag any sensitive field added to a payload that isn't on the redaction list.
- **CORS configured per environment.** Never `origin: '*'` in production. Reviewer should check that the production allow-list matches the deployed frontend origins.
- **Helmet CSP must be configured and not regress.** Any `unsafe-inline` / `unsafe-eval` addition needs an inline justification.
- **Webhooks verify HMAC against the raw body** (not the parsed JSON). The body parser must preserve `rawBody` (or equivalent) for webhook routes.

## Database

- **All Drizzle calls go through your application's database wrapper** — typically a `DatabaseService` with `db.query(...)` and `db.tx(...)` helpers — rather than the raw drizzle instance. Wrappers centralise error mapping, query-context logging, and transaction propagation.
- **Tables use `snake_case` columns; TypeScript uses `camelCase`.** Drizzle's column-name mapping handles the translation. Flag any mismatch.
- **Every table includes** `id` (uuid), `createdAt`, `updatedAt`, `deletedAt` (soft delete). Every read query must include `isNull(table.deletedAt)` in its `where` — soft-deleted rows must not leak.
- **Single-row reads use `.limit(1)` followed by destructuring**: `const [row] = await ...`. Avoid `.then(r => r[0])` style — it hides intent.
- **Foreign-key conventions:**
  - `restrict` on parent FKs of financial / audit tables (orders, payments, refunds, payouts, ledger entries — anything that must be preserved for accounting).
  - `cascade` on dependent child rows that lose meaning without the parent (e.g. order line items → order).
  - `set null` on circular FKs (use `AnyPgColumn` to break the circular import in the type system).
- **Index coverage for query patterns** — flag missing indexes on columns used in `WHERE`, `JOIN`, or `ORDER BY` of new queries. Composite-index column order should match the most selective filter first.
- **Transaction boundaries** (`db.tx(...)` or equivalent) cover multi-step writes that must commit atomically. Reviewer should flag any sequence of writes that should have been wrapped but wasn't.
- **N+1 query prevention** — eager-load related rows in a single query or batch-load via `inArray()`. Flag any `for ... of` loop containing an awaited DB call.
- **Migrations are timestamp-prefixed and named.** Always run the generator with an explicit `--name=<descriptive>` flag; never commit a `0000_random_codename.sql` file.
- **Never use `drizzle-kit push` against a migrated database** — migrations only. `push` corrupts migration state and is reserved for local scratch work.

## Code Quality

- **No `any` types** — prefer `unknown` plus narrowing, or a proper interface / type alias. An `any` escape hatch needs an inline justification comment.
- **No `console.log`** — always use the pino-backed logger. Console output bypasses redaction, log levels, and structured-log ingestion.
- **Unused variables prefixed with `_`** to satisfy ESLint without disabling the rule.
- **`async`/`await` throughout** — no raw `.then()` chains, no unhandled rejections, no `void`-promise floats.
- **Proper HTTP status codes:** 201 for create, 204 for delete (no body), 404 for missing resources (never `200 OK` with `null` payload), 409 for conflicts, 422 for semantic validation failures.
- **No circular dependencies between modules.** If module A imports module B which imports module A, refactor — usually by extracting the shared piece into a third module.
- **Config accessed via a typed config service** (typically extending a `BaseConfigService` or equivalent) — never `process.env.*` outside the config layer.
- **BullMQ processors extend `WorkerHost`** and implement `process(job)`. Concurrency, retries, and backoff are set via the `@Processor()` decorator options or the worker config — not hand-coded inside `process`.
- **Webhook controllers** opt out of the response envelope interceptor (e.g. via a `@SkipEnvelope()` decorator) and verify HMAC against the raw request body before any parsing.

## Infrastructure (when reviewing `infra/` changes)

- **IAM policies scoped to the minimum required permissions.** Flag any role binding that grants broader access than the resource needs (e.g. `roles/editor` where a narrow `roles/<service>.user` would suffice).
- **Prefer OIDC / Workload Identity Federation for cloud auth from CI** — flag any long-lived static credential (`AWS_ACCESS_KEY_ID` env var, JSON service-account key in a secret) being introduced.
- **Environment-specific configuration properly separated** (dev / staging / prod). No prod-leaning defaults bleeding into dev configs.
- **Terraform lock files** (`.terraform.lock.hcl`) consistent across environments. Mismatched provider versions across env folders cause non-deterministic plans.
- **Destructive changes** (column drops, table renames, type changes, removed env vars consumed by live services) require a documented backfill / migration plan.
- **Secrets in Secret Manager / equivalent**, never inline in `.tfvars` or shell scripts. Any new secret should be CMEK-encrypted (or KMS-encrypted on the equivalent cloud) and accessed by the consuming service via a narrowly-scoped IAM binding.
