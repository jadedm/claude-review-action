# Stack — NestJS 11 + Fastify + Drizzle (PostgreSQL) + Redis (BullMQ) + pino + JWT

This repository is a NestJS 11 monorepo on Fastify, using Drizzle ORM over PostgreSQL, Redis (BullMQ) for queues, pino for logging, and JWT for auth. Apply the following conventions in addition to the base review areas.

## Architecture & Patterns

- Controllers are thin: they map HTTP shape only and delegate to services. No business logic.
- Services are constructor-injectable and unit-testable.
- DTOs use `class-validator` + `class-transformer` decorators for every request body / query / param shape.
- Use Guards for auth, Interceptors for transforms, Pipes for validation.
- Don't re-import `@Global()` modules (DatabaseModule, AppLoggerModule, AppCacheModule, MonitoringModule, EventModule, JwtModule).
- Feature module layout: `<feature>.module.ts`, `<feature>.controller.ts`, `<feature>.service.ts`, optional `<feature>.repository.ts`, `dto/`, `<feature>.constants.ts`.
- Throw typed `AppException` subclasses from `@app/common` (`NotFoundException`, `ConflictException`, `BadRequestException`, …) — never raw `HttpException`.
- Don't manually construct the response envelope — use the global `ResponseInterceptor`.
- Read the authenticated user via `@CurrentUser()` / `@OptionalCurrentUser()` — never `request.user` directly.
- Pagination returns `{ items, _meta: { total, page, limit } }` from services so the interceptor can merge `_meta`.
- Integrations follow the orchestrator/provider/strategy pattern in `libs/common/src/integrations/<domain>/` — app code injects orchestrators, never provider adapters directly.

## Security

- Auth guards on all non-public endpoints (`JwtAuthGuard`, `ProducerVerifiedGuard`, `AdminRoleGuard`).
- Input validation via DTOs + `class-validator` only — never trust `req.body` directly.
- Drizzle ORM for all queries — no raw SQL string concatenation.
- `ThrottlerGuard` is global (100 req / 60s); flag endpoints that need a custom limit.
- Redaction config in `AppLoggerModule` covers `req.headers.authorization`, `req.headers.cookie`, `req.body.password`, `req.body.passwordConfirmation`, `req.body.token`, `req.body.refreshToken` — flag anything sensitive added to a request body that isn't redacted.
- CORS configured per environment; never `*` in production.
- Helmet CSP must be configured and not regress.

## Database

- All Drizzle calls go through the `DatabaseService` wrappers: `db.query(promise, 'context-string')` and `db.tx(fn, 'context-string')` — never the raw drizzle instance.
- Tables use snake_case columns, TypeScript uses camelCase.
- Every table includes `id` (uuid), `createdAt`, `updatedAt`, `deletedAt` (soft delete). Every WHERE in a read query includes `isNull(table.deletedAt)`.
- Single-row reads use `.limit(1)` followed by `const [row] = await this.db.query(...)`.
- Foreign-key conventions:
  - `restrict` on parent FKs of financial / audit tables (orders, payments, refunds, payouts, discount_usage)
  - `cascade` on dependent data (child rows of a parent record like order_items → orders)
  - `set null` on circular FKs (use `AnyPgColumn` to break the circular import)
- Index coverage for query patterns — flag missing indexes on columns used in WHERE / JOIN / ORDER BY of new queries.
- Transaction boundaries (`db.tx`) cover multi-step writes that must commit atomically.
- Migrations are timestamp-prefixed, always generated with `--name=<descriptive>` flag.
- Never use `db:push` against a migrated database — migrations only.

## Code Quality

- No `any` types — prefer `unknown` plus narrowing, or a proper interface.
- No `console.log` — always use the pino-backed logger.
- Unused variables prefixed with `_` to satisfy ESLint.
- `async`/`await` throughout — no raw `.then()` chains, no unhandled rejections.
- Proper HTTP status codes: 201 for create, 204 for delete, 404 (not 200 + null) for missing resources.
- No circular dependencies between modules.
- Config accessed exclusively via the typed service extending `BaseConfigService` — never `process.env.*` outside the config layer.
- BullMQ processors extend `WorkerHost` and implement `process(job)`.
- Webhook controllers use `@SkipEnvelope()` and verify HMAC against `req.rawBody` (not the parsed JSON).

## Infrastructure (for `infra/` changes)

- IAM policies scoped to the minimum required permissions.
- Prefer OIDC for AWS auth from CI — flag any static `AWS_ACCESS_KEY_ID` introduced.
- Environment-specific configuration properly separated (dev / staging / prod).
- Terraform lock files (`.terraform.lock.hcl`) consistent across environments.
- Destructive changes (drop, rename, change of type) require a documented backfill plan.
