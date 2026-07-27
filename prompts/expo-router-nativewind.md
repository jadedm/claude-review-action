# Stack — Expo (SDK 57+) + Expo Router + React Native + NativeWind + TypeScript

A general review prompt for React Native apps built on Expo with the managed workflow: Expo Router file-based navigation, NativeWind (Tailwind for RN) for styling, Reanimated for animation, and EAS for builds. Reviewers should apply these conventions in addition to the base review areas. Any project-specific deviations — the exact route group layout, the chosen state/data library, which tabs or stacks exist, allowed `any` deviations, native config plugins in use — belong in the consumer's `extra_prompt_path` and will appear under the "Repo-specific notes" section. **When such a note exists, prefer it over the generic guidance below.**

**Check the installed Expo SDK version in `package.json` before flagging API usage.** Expo's API surface moves quickly and the versioned docs at `https://docs.expo.dev/versions/v<major>.0.0/` are authoritative. Do not flag an API as wrong because it differs from an older SDK.

## Architecture & Routing

- **The routes directory is routes-only.** Files under the Expo Router root (`app/` or `src/app/`) map to screens. Screen components stay thin — business logic, data fetching, and transformers live outside the routes tree (`features/`, `hooks/`, `lib/`, `services/`). A route file that holds an API client or a large reducer is misplaced.
- **Layouts own navigation configuration.** `_layout.tsx` declares the `Stack` / `Tabs` / `Drawer` and its `screenOptions`. Per-screen options belong on the corresponding `Screen` entry or via `<Stack.Screen options={...} />`, not scattered as side effects inside the screen body.
- **Route groups `(name)` do not appear in the URL.** Flag route-group renames that silently change deep links or `router.push` targets.
- **Navigate with the typed `router` / `Link` API from `expo-router`.** Flag raw string paths built by concatenation when `typedRoutes` is enabled in the app config — they defeat the generated route types.
- **Default exports are required for route files.** This is the one place default exports are correct; do not flag them there. Elsewhere prefer named exports.
- **Screen constants shared across layout, labels, and icon maps must have one source of truth.** When a route name is repeated as a string literal in a layout, a label map, and an icon map, flag the duplication — a single `SCREENS`-style constant keyed consistently is the maintainable form. Adding a screen should not require editing four files that can silently drift.

## Styling (NativeWind + Tailwind)

- **Tailwind `content` globs must cover every directory that uses `className`.** This is the single most common NativeWind bug: a `className` in a file outside the configured globs produces no styles at all, silently, with no error. Flag any new directory containing `className` that is not matched by `tailwind.config.js`'s `content` array.
- **Prefer `className` for static styling; use the `style` prop for computed values.** Inset-aware offsets, responsive scaling, animated values, and focus-dependent colours are legitimate `style` cases. Purely static layout and colour should be `className`. Flag inline style objects that only restate static Tailwind utilities.
- **No magic colour values scattered in components.** Colours belong in the Tailwind theme or a single exported constants module. Flag literal hex codes repeated across components.
- **React Native has no CSS cascade.** Flag web-only CSS assumptions: percentage heights that depend on a parent chain, `position: fixed`, `z-index` stacking assumptions, hover-only affordances, and `gap` on unsupported RN versions.
- **`darkMode: 'class'` in a native app needs an explicit theme mechanism.** If the config declares class-based dark mode, there must be code that actually applies the class or drives `colorScheme`; otherwise dark variants never activate. Flag the gap.

## React Native Correctness

- **Never block the JS thread.** Flag synchronous heavy work in render, in `useEffect` without deferral, or in gesture/scroll callbacks.
- **Lists must be virtualized.** Use `FlashList` or `FlatList` for any list that can grow. Flag `.map()` over an unbounded array inside a `ScrollView`. Every list needs a stable `keyExtractor` — index keys are a bug when items reorder or get removed.
- **Images need explicit dimensions or a sizing strategy.** Remote images without width/height cause layout jumps.
- **Safe-area handling is explicit.** Screens that render to the device edge must use `useSafeAreaInsets` or `SafeAreaView` from `react-native-safe-area-context`. Flag hardcoded status-bar or home-indicator padding constants.
- **Clean up subscriptions, timers, listeners, and animations on unmount.** `AppState`, `Dimensions`, `Keyboard`, and navigation listeners all return removers that must run in the effect cleanup.
- **Platform differences are handled deliberately.** Use `Platform.select` / `Platform.OS` (or `process.env.EXPO_OS` where the project uses it) rather than assuming iOS behaviour. Haptics, blur, shadows, and elevation all differ. Flag iOS-only APIs called unconditionally.
- **Dimensions captured at module scope do not react to rotation, split-screen, or font-scaling changes.** If a project derives sizes from a one-time `Dimensions.get(...)`, that is acceptable only for a locked-orientation app; flag it if the app supports rotation, and flag any new use in a resizable context. Prefer `useWindowDimensions` when reactivity matters.

## Animation & Gestures

- **Reanimated worklets must not close over non-shared mutable JS state.** Values crossing the boundary must be shared values or passed through `runOnJS` / `runOnUI` appropriately.
- **Prefer `react-native-gesture-handler` over the legacy `PanResponder`** and RN's built-in touchables where the project already depends on it.
- **Layout animations need stable keys** to animate rather than remount.
- **Reanimated v4 requires the worklets plugin and a compatible Babel/Metro setup.** Flag animation code added without the corresponding config when the project is on v4+.

## Expo Config & Native Layer

- **Native configuration belongs in the app config, not in edited native projects.** When `ios/` and `android/` are gitignored and generated by prebuild or EAS, any change requiring native modification must go through `app.json` / `app.config.*` `plugins`. Flag instructions or code that assume hand-edited native files will persist.
- **Adding a library with a native component requires a config plugin entry and a new development build.** Flag a new native dependency added without the plugin registration, and note that Expo Go will not pick it up.
- **Bundle identifier, package name, scheme, and EAS project ID must stay consistent.** Flag changes that would break deep links, OAuth redirects, or the EAS project link.
- **Secrets never live in the app config or `EXPO_PUBLIC_*` env vars.** Everything in the client bundle is readable by anyone with the IPA/APK. `EXPO_PUBLIC_*` is compile-time inlined and public by definition. Flag API keys, signing material, and private endpoints placed there.
- **`app.json` version fields interact with EAS `autoIncrement`.** Flag manual version bumps that fight the configured `appVersionSource` / `autoIncrement` strategy.

## Security (mobile-specific)

- **Tokens and credentials belong in `expo-secure-store` (Keychain / Keystore), never `AsyncStorage`.** `AsyncStorage` is unencrypted plaintext on device and readable on a rooted or jailbroken phone, and it can end up in device backups. Flag any token, refresh token, PIN, or PII written to it.
- **Deep links are untrusted input.** Any parameter arriving via a `scheme://` URL or universal link must be validated before it drives navigation, authentication, or a network call. Flag deep-link handlers that pass parameters straight into a request or an auth flow.
- **`WebView` needs its surface locked down** where the project uses one: no arbitrary origin loading, no unvalidated `injectedJavaScript`, and `postMessage` payloads must be validated on receipt.
- **No sensitive data in logs shipped to production builds.** RN logs are readable via device tooling. Flag token and PII logging that is not stripped from release builds.

## TypeScript & Code Quality

These are common defaults; some repos relax them deliberately. **Defer to `Repo-specific notes` when present**, and read `tsconfig.json` / `eslint.config.*` before flagging style violations.

- **Avoid `any`.** Prefer proper types or `unknown` with narrowing.
- **Use configured path aliases** (commonly `@/*`) rather than deep relative chains.
- **`strict` is expected on new Expo projects.** If `tsconfig.json` sets it, flag unguarded optional access; if the project extends `expo/tsconfig.base` without strict, do not.
- **Avoid `console.log` in committed code.** Use `console.warn` / `console.error` or the project's logger.
- **When React Compiler is enabled** in the app config's `experiments`, do not flag missing `useMemo` / `useCallback` — the compiler handles memoization, and manual memoization is noise. Check the config before recommending it.

## Testing & Tooling

- **Do not suggest test files when no test runner is configured.** Check `package.json` for `jest-expo`, Vitest, Maestro, or Detox before recommending tests. If nothing is wired up, raise the absence once as a maintainability note rather than per-file.
- **Lint and format must pass.** Respect the project's Prettier config rather than reformatting against it, and honour any Tailwind class-sorting plugin.
- **Do not recommend `npm`/`yarn`/`pnpm` commands that contradict the project's lockfile.**
