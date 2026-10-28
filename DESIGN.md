# DeepShovel Design System

## Product

DeepShovel is a scientific evidence workbench. Researchers move from a Claim through observations and reasoning to its original document, then compare related findings across the corpus.

The interface stays precise and calm during long reading sessions. Real geological landscapes remain as a quiet texture beneath a cool, lightly gridded research canvas; translucent work surfaces keep the research itself clear.

## Color

- Primary ink: `#17243F`.
- Body text: `#2F3C55`; secondary text: `#52627A`.
- Interactive accent: `#2F64E8`; pressed accent: `#234FC4`.
- Selected surface: `#E8EFFF`.
- Structural rules: `#D6DFED`; stronger rules: `#B9C6DA`.
- Work surfaces use cool white layers between 78% and 96% opacity.
- Navigation uses the same translucent blue-white material as the workbench.
- Teal is reserved for supporting scientific status and environmental glow, rather than primary interaction.
- Contradiction, destructive actions, success, warning, and processing each retain a distinct labeled semantic state.

## Typography

- Latin letters and numbers: the platform UI sans (`-apple-system`, `BlinkMacSystemFont`, or `Segoe UI`).
- Simplified Chinese: the platform UI sans with `Noto Sans SC Variable` as the bundled fallback.
- Identifiers and exact coordinates: `Geist Mono Variable`.
- Shared UI stack: `-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC Variable", sans-serif`.
- Font weights: 400 for reading, 500 for controls, and 600 for headings.
- Type scale: `0.75rem`, `0.8125rem`, `0.875rem`, `1rem`, `1.125rem`, `1.5rem`.
- Scientific prose uses the shared `0.875rem` body size, with a comfortable line length of approximately 72 characters.
- Headings have zero tracking for Chinese; numerical records use tabular figures.

## Structure

- Shared compact spacing is `4, 6, 8, 12, 16, 24, 32, 40px`; panel insets default to 16px. Desktop controls are 36px tall; touch controls remain 44px. Typography is independent of density.
- Desktop navigation is a fixed 12rem translucent rail with visible labels; content reserves a 14rem left inset in normal, hover, and keyboard-focus states. It never expands over page content.
- Touch-first tablets use the same reserved rail geometry.
- Phones use a compact top brand bar and eight-item bottom navigation with safe-area spacing.
- Search moves directly from the research question to scoped evidence and its sources.
- Growing document, review, task, and Claim ledgers use server-backed pagination with 20 items by default, 10/20/50 size choices, search, and direct page jumps.
- Growing figure/table and source-selection ledgers follow the same pagination contract; figure/table pages default to 5 items and are sliced by the server before reaching the browser. Their search/filter toolbar remains sticky while results scroll independently.
- The library uses a continuous document ledger.
- Paper details keep Claims, observations, citations, and source anchors adjacent.
- The evidence graph presents classification and verifiable Claim relationships.
- Evidence-graph taxonomy is fetched once and cached; intermediate hierarchy navigation is local, while Topic selection fetches only that Topic's Claim page.
- Human review focuses on the selected dossier and its decision.
- Workbenches adapt to their available content width with container queries. Narrow library rows wrap their metadata and actions; review columns stack, and the review form stays in document flow so it cannot cover the evidence.

## Interaction

- Controls have visible focus, readable labels, and a minimum touch target of `2.75rem`.
- Transitions last approximately 160–220 ms and respect reduced-motion preferences.
- Errors state the failed operation and the available next action.
- Scientific judgments retain their exact scope, supporting observations, and original source location.
- Background-image attribution remains available from the navigation.

## Verification

Check primary workflows at desktop, tablet, and mobile widths; preserve keyboard access, text contrast, source-document fidelity, and the production build.

- Primary workspaces share a 72rem maximum width with symmetric desktop gutters of 24–64px beyond the navigation rail. Content-based breakpoints apply within each workspace.
