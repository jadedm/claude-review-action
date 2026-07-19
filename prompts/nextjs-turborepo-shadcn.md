# Stack — Next.js (App Router) + Turborepo + shadcn/ui + Tailwind + TypeScript

A general review prompt for Next.js 14/15 monorepos using the App Router, pnpm workspaces + Turborepo, shadcn/ui on Tailwind v4, React Query / Zustand / react-hook-form + Zod for state and forms, and a BFF route handler for backend access. Reviewers should apply these conventions in addition to the base review areas. Any project-specific deviations — the exact BFF path, the name of the token-management module, the chosen logger / animation library / sanitiser, allowed `any` deviations, app-specific feature directories — belong in the consumer's `extra_prompt_path` and will appear under the "Repo-specific notes" section. **When such a note exists, prefer it over the generic guidance below.**

## Architecture & Patterns

- **`app/` is routes-only.** Page components stay thin — they compose UI from `features/` (or `shared/`) and never hold business logic, transformers, or data-fetching primitives directly.
- **Feature-based organisation.** Cross-cutting concerns for a feature live together: `features/<feature>/{components,hooks,api,transformers,types,lib}/`. No cross-feature imports — a feature should only import from the workspace's shared package, the app's own `shared/` folder, or its own directory.
- **Data flow is `API → Transformer → Hook → Component`.** Components consume only transformed, UI-shaped data. Never call `fetch` / `axios` directly from a component, and never consume raw API DTO shapes — always go through a transformer (even if today the shapes look identical; the transformer is the seam).
- **React Query is the single source of truth for server state.** Server data is never duplicated into `useState`, Zustand, Context, or any in-memory cache as a fallback. Use query-key factories (object literals) for consistent invalidation.
- **Mutation hooks return `mutateAsync` + `isPending`** (not `mutate`) so callers can await. `onSuccess` invalidates every related query (list, detail, counts).
- **Workspace packages must be added to `next.config.{mjs,ts}` under `transpilePackages`.** Flag any new workspace import that isn't transpiled.

## Security

- **All backend calls go through a BFF route handler** under `app/api/` (the exact path / catch-all shape is per-repo). Never call the backend origin directly from the browser, and never expose backend URLs in `NEXT_PUBLIC_*` env vars unless they are public.
- **Auth tokens live in `httpOnly` cookies set server-side by the BFF.** Repos typically wrap the read/clear/refresh surface in a module (commonly `tokenManager` or similar). NEVER store access or refresh tokens in `localStorage`, `sessionStorage`, or any client-readable state. Flag any code that writes a token to `document.cookie` from the client.
- **No server data in `localStorage` / `sessionStorage` / Context / Zustand-persisted state.** Every page must fetch its data via hooks on mount; if a refresh causes data to disappear, the implementation is wrong. The only acceptable client-storage uses are UI-only preferences (theme, sidebar collapsed, last-selected tab) and explicit auth-state flags (commonly an `*_authenticated` non-httpOnly companion cookie).
- **No hardcoded secrets, API keys, account IDs, vendor secrets, or production URLs** in source. `NEXT_PUBLIC_*` env vars are shipped to the browser — never put a secret behind that prefix.
- **Input validation with Zod at every system boundary** — form submit, query-string parse, BFF route-handler input. `react-hook-form`'s `zodResolver` is the standard form path.
- **`dangerouslySetInnerHTML` requires a sanitiser** (DOMPurify or whatever the repo uses) and an inline justification. Same for `eval` / `Function(...)`-style dynamic code.
- **Webhooks proxied through the BFF must verify the upstream signature against the raw body**, not the parsed JSON. The body parser must preserve the raw bytes.

## TypeScript & Code Quality

These are common defaults; some repos relax them deliberately. **Defer to `Repo-specific notes` when present**, and read `tsconfig.json` / `eslint.config.*` before flagging style violations.

- **Avoid `any`.** Prefer proper types or `unknown` + narrowing. Some monorepos disable `no-explicit-any` intentionally — don't flag `any` if the consumer's extras say it's allowed.
- **Inline type imports** if `consistent-type-imports` with `fixStyle: "inline-type-imports"` is enforced: `import { type Foo, bar } from "module"` rather than `import type { Foo } from "module"`. Check `eslint.config.*` before insisting.
- **`noUncheckedIndexedAccess`** is OFF by default in TypeScript; if the consumer's `tsconfig.json` sets it to `true` (recommended for new repos), flag unguarded indexed access — `array[0]` is `T | undefined` and must be checked before use. If the flag is off, don't flag it.
- **Avoid `console.log` in committed code.** Use `console.warn` / `console.error` for unexpected paths, or a structured logger if the project has one.
- **No nested ternaries.** Use early returns, lookup objects, or extracted helpers.
- **Named exports only.** Default exports are reserved for Next.js pages / layouts / route handlers where the framework requires them.

## React & Component Patterns

- **shadcn/ui is the UI primitive layer.** Always compose from the workspace's shared shadcn components. Never hand-roll a custom button/input/dialog/card/etc. when a shadcn equivalent exists. New shadcn components are added from the shared package (commonly `packages/shared/`), and any new file's `src/lib/utils` import must be rewritten to the project's `@/lib/utils` alias.
- **Use `cn()` for `className` composition.** Don't string-concatenate Tailwind classes or conditionally branch with ternaries inline; pass a structured arg to `cn()`.
- **`class-variance-authority` (cva) for component variants.** Don't fork a component to vary a prop — define variants.
- **Forms: `react-hook-form` + Zod schema** in `features/<feature>/lib/`. Map API field errors back to `setError` via the project's error helpers.
- **Animations:** prefer a JS animation library (commonly `framer-motion` or `motion`) for any stateful / interactive case (mount, exit, layout, gestures). CSS is fine for purely static fades. Don't flag CSS animations as wrong — just flag stateful animations *hand-rolled* with CSS variables + `useEffect`.
- **`'use client'` only where required.** Server components by default. Flag any page-level component that's marked client-only without a justification (a hook, an event handler, a browser API).

## Next.js & Routing

- **App Router only.** No `pages/` patterns, no `getServerSideProps`, no `_app.tsx`. Data loading goes through server components, route handlers, or React Query on the client.
- **`output: "standalone"`** in `next.config.{mjs,ts}` for any app that ships in a container. Flag a missing standalone output on a deployed app.
- **Middleware is the route-protection layer.** Public auth routes bypass; protected routes redirect to the project's sign-in URL with a `callbackUrl=…`. The check reads the httpOnly access-token cookie — never a `localStorage`-backed flag.
- **Path aliases:** `@/*` → app root, the workspace's shared-package alias → shared package, app's `@/shared/*` → app's own shared folder. Flag relative imports (`../../../...`) that should use an alias.

## Tooling & Workflow

- **pnpm exclusively.** No `npm` or `yarn` commands in scripts, READMEs, or CI. Lockfile is `pnpm-lock.yaml`.
- **Turborepo for task orchestration.** Tasks declared in `turbo.json` must list their inputs/outputs so the cache is correct. Don't add a task that ignores caching without justification.
- **Imports auto-sorted** by `simple-import-sort` (or `eslint-plugin-import` order) if the consumer's eslint config has it. Don't hand-order imports against the configured sorter.
- **Lint must pass with `--max-warnings 0`** if CI enforces it. `onlyWarn` (if used) demotes errors to warnings; `--max-warnings 0` still makes them blocking. Don't disable a rule inline without a `// eslint-disable-next-line <rule> — <why>` comment.
- **No `*.test.ts` if no test runner is wired up.** Check the project for Jest/Vitest/Playwright before suggesting test files; if only Playwright e2e is configured, unit-test files won't run and shouldn't be added.

## Styling

- **Tailwind v4 patterns only.** Design tokens via `@theme` directive in `globals.css`; no `tailwind.config.{js,ts}` v3-style config file unless the project explicitly opts back in.
- **Shared global styles** live in the workspace shared package. App-specific extensions go in the app's own globals.
- **No magic colour / spacing values** in JSX — use the design tokens. Flag literal hex codes / `px` values that should be Tailwind utilities or `@theme` tokens.

## Observability

- **Correlation IDs propagate end-to-end.** Generated per session (or per request) and injected as `x-correlation-id` on every BFF→backend call and into every client log. The BFF preserves the inbound header.
- **A structured logger over `console`.** Server-side: a logger that supports redaction of `authorization`, `cookie`, `*.password`, `*.refreshToken`, `*.accessToken` and similar sensitive fields. Client-side: a batching log sink (typically POSTing to an `/api/logs`-style endpoint) — not raw `fetch('/log', …)` inline in components. The specific library (pino, winston, custom) is per-repo and should be named in `Repo-specific notes`.
- **A health endpoint** (commonly `/api/health`) returning `{ status, app, version, startedAt, timestamp }` for every deployed app.
