/** Append one service warning without exposing control bytes, duplicates or unbounded prose. */
export function appendPublicWarning(warnings: string[], value: string) {
  const normalized = value
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 500);
  // Index sync emits one warning per request; identical lines add nothing.
  if (normalized && warnings.length < 32 && !warnings.includes(normalized)) warnings.push(normalized);
}
