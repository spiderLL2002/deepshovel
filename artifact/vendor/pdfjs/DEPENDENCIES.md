# Local PDF renderer dependency

Mozilla PDF.js, npm package `pdfjs-dist@6.4.299`, Apache-2.0.
Official registry tarball: https://registry.npmjs.org/pdfjs-dist/-/pdfjs-dist-6.4.299.tgz
Upstream: https://github.com/mozilla/pdf.js
Fetched using explicit official npm registry in isolated `revision/pdfjs-check-env`; no application/global dependency installed.

Tarball SHA-256: `86269b40170eb41740ea05ad2102d71d33de4f122b34eb2f478be0ea7779c415`.
Registry SHA-1: `4a46f5146ac6505564c1a5d472900be4df8bf628`.
Registry SRI: `sha512-AVl138zALtfaAPvADulE0PZThbYzCBS79nL4pOSL/6Sm/4AH5A21BD9VHt97OlCuzJuCpmeZtAtkinisF4Vb1g==`.

Vendored legacy ESM renderer (official build with compatibility polyfills, located under vendor/pdfjs/build) + worker and complete cmaps, standard_fonts, wasm directories, retaining their license files; root LICENSE and package.json retained. Per-file SHA-256: vendor-sha256.json. No CDN or remote renderer. Existing standard-library HTTP server suffices; Node is not an application runtime dependency.
