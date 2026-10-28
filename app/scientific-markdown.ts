const LATEX_COMMAND_NAMES = [
  "alpha", "beta", "gamma", "delta", "epsilon", "theta", "lambda", "mu", "pi", "phi", "sigma", "omega",
  "Gamma", "Delta", "Theta", "Lambda", "Phi", "Sigma", "Omega",
  "frac", "cfrac", "sqrt", "left", "right", "mathrm", "mathbf", "mathit", "operatorname", "text",
  "sin", "cos", "tan", "arcsin", "arccos", "arctan", "ln", "log", "exp",
  "times", "cdot", "pm", "mp", "leq", "geq", "approx", "perp", "parallel",
  "sum", "prod", "int", "partial", "nabla", "circ", "infty", "begin", "end", "tag",
] as const;

const SIMPLE_SYMBOL_NAMES = new Set([
  "alpha", "beta", "gamma", "delta", "epsilon", "theta", "lambda", "mu", "pi", "phi", "sigma", "omega",
  "Gamma", "Delta", "Theta", "Lambda", "Phi", "Sigma", "Omega", "partial", "nabla", "infty",
]);
const LATEX_COMMAND_SET = new Set<string>(LATEX_COMMAND_NAMES);
const LATEX_COMMAND = new RegExp(`\\\\(?:${LATEX_COMMAND_NAMES.join("|")})\\b`, "u");
const FORMULA_SHAPE = /(?:=|\\frac\b|\\cfrac\b|\\sqrt\b|\\left\b|\\right\b|\\begin\b|\\(?:times|cdot|pm|mp|leq|geq|approx|perp|parallel)\b|[_^]\s*(?:\{|[A-Za-z0-9]))/u;
const PROSE_SENTENCE = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}。！？]|[.!?]\s*$|\b(?:and|because|command|equation|formula|from|holds?|indicates?|is|or|shows?|suggests?|the|therefore|to|use|using|was|were|when|where|which|with)\b/iu;
const HARD_PROSE_BOUNDARY = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}、。！，：；？]/gu;
const WINDOWS_PATH = /(?:^|\s)[A-Za-z]:\\/u;
const MATH_PROSE = /\b(?:and|because|between|commonly|during|from|in|into|seawater|shales|the|their|these|was|were|with)\b/iu;

function mathOutsideTextGroups(value: string) {
  // Words inside explicit TeX text groups are legitimate formula labels.
  let output = "";
  for (let cursor = 0; cursor < value.length;) {
    const group = /^\\(?:text\w*|mathrm|mathbf|mathit|operatorname|mbox|hbox)\s*\{/u.exec(value.slice(cursor));
    if (group) {
      let depth = 1;
      cursor += group[0].length;
      while (cursor < value.length && depth) {
        if (!escaped(value, cursor)) {
          if (value[cursor] === "{") depth += 1;
          if (value[cursor] === "}") depth -= 1;
        }
        cursor += 1;
      }
      output += " x ";
    } else if (/^\\(?:rm|bf|it|sf|tt|cal)\b/u.test(value.slice(cursor))) {
      // Legacy font declarations apply through their enclosing TeX group.
      while (cursor < value.length && (value[cursor] !== "}" || escaped(value, cursor))) cursor += 1;
      output += " x ";
    } else {
      output += value[cursor++];
    }
  }
  return output.replace(/\\[A-Za-z]+/gu, "x");
}

function tildeFenceEnd(value: string, cursor: number) {
  if (value[cursor] !== "~"
    || !/^ {0,3}$/u.test(value.slice(value.lastIndexOf("\n", cursor - 1) + 1, cursor))) return -1;
  const marker = /^~{3,}/u.exec(value.slice(cursor));
  if (!marker) return -1;
  const lineEnd = value.indexOf("\n", cursor);
  if (lineEnd < 0) return value.length;
  const closing = new RegExp(`^ {0,3}~{${marker[0].length},}[\\t ]*$`, "gmu");
  closing.lastIndex = lineEnd + 1;
  const match = closing.exec(value);
  return match ? match.index + match[0].length : value.length;
}

function balancedMathGroups(value: string) {
  let depth = 0;
  for (let cursor = 0; cursor < value.length; cursor += 1) {
    if (escaped(value, cursor)) continue;
    if (value[cursor] === "{") depth += 1;
    if (value[cursor] === "}" && --depth < 0) return false;
  }
  return depth === 0;
}

function mathFragmentPrefix(value: string) {
  return !!value.trim() && !MATH_PROSE.test(mathOutsideTextGroups(value))
    && !/[\p{Script=Han}]/u.test(mathOutsideTextGroups(value))
    && (/\\[A-Za-z]+|[_^{}=<>]/u.test(value)
      || (/\d/u.test(value) && /^[\s\d.+−-]+$/u.test(value)));
}

function literalMathFragment(value: string) {
  const width = Math.max(0, ...[...value.matchAll(/`+/gu)].map((match) => match[0].length)) + 1;
  const marker = "`".repeat(width);
  return `${marker} ${value} ${marker}`;
}

/** Quote boundaries may bisect math. Keep those fragments literal, never guess a repair. */
function protectIncompleteMath(value: string) {
  let output = "";
  let start = 0;
  let cursor = 0;
  let incomplete = false;
  while (cursor < value.length) {
    const fenceEnd = tildeFenceEnd(value, cursor);
    if (fenceEnd >= 0) {
      output += value.slice(start, fenceEnd);
      start = cursor = fenceEnd;
      continue;
    }
    if (value[cursor] === "`") {
      const marker = /^`+/u.exec(value.slice(cursor))![0];
      const end = closingMarker(value, cursor, marker);
      if (end >= 0) {
        output += value.slice(start, end);
        start = cursor = end;
        continue;
      }
    }
    if (value[cursor] !== "$" || escaped(value, cursor)) { cursor += 1; continue; }
    const marker = value[cursor + 1] === "$" ? "$$" : "$";
    const end = closingMarker(value, cursor, marker);
    const body = end < 0 ? value.slice(cursor + marker.length)
      : value.slice(cursor + marker.length, end - marker.length);
    const prefix = value.slice(start, cursor);
    const outsideText = mathOutsideTextGroups(body);
    const prose = MATH_PROSE.test(outsideText)
      || (mathFragmentPrefix(prefix) && /[A-Za-z]{3,}/u.test(outsideText)
        && !/[=+*/^_<>\\]/u.test(body));
    if (end >= 0 && !prose && balancedMathGroups(body)) {
      output += value.slice(start, end);
      start = cursor = end;
      continue;
    }
    if (prose || end < 0) {
      if (mathFragmentPrefix(prefix)) {
        output += literalMathFragment(prefix + marker);
        incomplete = true;
      } else if (end < 0 && !prose && (/\\[A-Za-z]+|[_^{}=<>]/u.test(body)
        || (LATEX_COMMAND.test(value) && /^[\s\d.+−-]+$/u.test(body)))) {
        output += prefix + literalMathFragment(marker + body);
        incomplete = true;
        start = cursor = value.length;
        continue;
      } else {
        // A rejected closer must not pair with the next complete formula.
        output += prefix + marker.replace(/\$/gu, "\\$");
      }
      start = cursor = cursor + marker.length;
    } else {
      output += prefix + literalMathFragment(value.slice(cursor, end));
      incomplete = true;
      start = cursor = end;
    }
  }
  return { value: output + value.slice(start), incomplete };
}

function escaped(value: string, index: number) {
  let slashes = 0;
  for (let cursor = index - 1; cursor >= 0 && value[cursor] === "\\"; cursor -= 1) slashes += 1;
  return slashes % 2 === 1;
}

function closingMarker(value: string, start: number, marker: string) {
  let cursor = start + marker.length;
  while (cursor < value.length) {
    const found = value.indexOf(marker, cursor);
    if (found < 0) return -1;
    const adjacentDollar = marker === "$" && (value[found - 1] === "$" || value[found + 1] === "$");
    if (!escaped(value, found) && !adjacentDollar) return found + marker.length;
    cursor = found + 1;
  }
  return -1;
}

/** Apply a transform outside existing Markdown code and dollar-delimited math. */
function mapPlainMarkdown(value: string, transform: (plain: string) => string) {
  let output = "";
  let plainStart = 0;
  let cursor = 0;
  while (cursor < value.length) {
    const fenceEnd = tildeFenceEnd(value, cursor);
    if (fenceEnd >= 0) {
      output += transform(value.slice(plainStart, cursor)) + value.slice(cursor, fenceEnd);
      plainStart = cursor = fenceEnd;
      continue;
    }
    let marker = "";
    if (value[cursor] === "`") {
      let end = cursor + 1;
      while (value[end] === "`") end += 1;
      marker = value.slice(cursor, end);
    } else if (value[cursor] === "$" && !escaped(value, cursor)) {
      marker = value[cursor + 1] === "$" ? "$$" : "$";
    }
    if (!marker) {
      cursor += 1;
      continue;
    }
    const end = closingMarker(value, cursor, marker);
    if (end < 0) {
      cursor += marker.length;
      continue;
    }
    output += transform(value.slice(plainStart, cursor));
    output += value.slice(cursor, end);
    plainStart = end;
    cursor = end;
  }
  return output + transform(value.slice(plainStart));
}

function aloneOnLine(source: string, start: number, end: number) {
  const lineStart = source.lastIndexOf("\n", start - 1) + 1;
  const nextBreak = source.indexOf("\n", end);
  const lineEnd = nextBreak < 0 ? source.length : nextBreak;
  return !source.slice(lineStart, start).trim() && !source.slice(end, lineEnd).trim();
}

function normalizeMathDelimiters(value: string, inline: boolean) {
  return value
    .replace(/\\\[([\s\S]*?)\\\]/gu, (match, body: string, offset: number, source: string) => {
      const math = body.trim();
      return !inline && aloneOnLine(source, offset, offset + match.length)
        ? `$$\n${math}\n$$`
        : `$${math}$`;
    })
    .replace(/\\\(([\s\S]*?)\\\)/gu, (_match, body: string) => `$${body.trim()}$`);
}

function isBareFormula(value: string) {
  return !WINDOWS_PATH.test(value)
    && LATEX_COMMAND.test(value)
    && FORMULA_SHAPE.test(value)
    && !PROSE_SENTENCE.test(value);
}

function symbolEnd(value: string, start: number) {
  const command = /^\\([A-Za-z]+)/u.exec(value.slice(start));
  if (!command || !LATEX_COMMAND_SET.has(command[1]) || !SIMPLE_SYMBOL_NAMES.has(command[1])) return start;
  let cursor = start + command[0].length;
  while (cursor < value.length) {
    const suffixStart = cursor;
    while (value[cursor] === " ") cursor += 1;
    if (value[cursor] !== "_" && value[cursor] !== "^") return suffixStart;
    cursor += 1;
    while (value[cursor] === " ") cursor += 1;
    if (value[cursor] === "{") {
      const close = value.indexOf("}", cursor + 1);
      if (close < 0 || value.slice(cursor + 1, close).includes("{")) return suffixStart;
      cursor = close + 1;
    } else if (/[A-Za-z0-9]/u.test(value[cursor] ?? "")) {
      cursor += 1;
    } else {
      return suffixStart;
    }
  }
  return cursor;
}

function wrapSimpleSymbols(value: string) {
  let output = "";
  let plainStart = 0;
  for (let cursor = 0; cursor < value.length; cursor += 1) {
    if (value[cursor] !== "\\") continue;
    const previous = value[cursor - 1] ?? "";
    if (previous && /[A-Za-z0-9\\/:]/u.test(previous)) continue;
    const end = symbolEnd(value, cursor);
    if (end === cursor) continue;
    output += `${value.slice(plainStart, cursor)}$${value.slice(cursor, end)}$`;
    plainStart = end;
    cursor = end - 1;
  }
  return output + value.slice(plainStart);
}

function normalizeBareRun(value: string) {
  const leading = /^\s*/u.exec(value)?.[0] ?? "";
  const trailing = /\s*$/u.exec(value)?.[0] ?? "";
  const core = value.slice(leading.length, value.length - trailing.length);
  if (core && isBareFormula(core)) return `${leading}$${core}$${trailing}`;
  return wrapSimpleSymbols(value);
}

function normalizeBareTexInProse(value: string) {
  let output = "";
  let cursor = 0;
  for (const boundary of value.matchAll(HARD_PROSE_BOUNDARY)) {
    const index = boundary.index;
    output += normalizeBareRun(value.slice(cursor, index));
    output += boundary[0];
    cursor = index + boundary[0].length;
  }
  return output + normalizeBareRun(value.slice(cursor));
}

/** Normalize common model/parser math forms without treating ordinary prose as TeX. */
export function prepareScientificMarkdown(value: string, inline = false) {
  const protectedMath = protectIncompleteMath(value);
  const delimited = mapPlainMarkdown(protectedMath.value, (plain) => normalizeMathDelimiters(plain, inline));
  const trimmed = delimited.trim();
  if (!trimmed) return { markdown: delimited, incompleteMath: protectedMath.incomplete };
  if (!delimited.includes("$") && isBareFormula(trimmed)) {
    return { markdown: inline ? `$${trimmed}$` : `$$\n${trimmed}\n$$`, incompleteMath: protectedMath.incomplete };
  }
  return { markdown: mapPlainMarkdown(delimited, normalizeBareTexInProse), incompleteMath: protectedMath.incomplete };
}

export function normalizeScientificMarkdown(value: string, inline = false) {
  return prepareScientificMarkdown(value, inline).markdown;
}

/** Expand only bisected math boundaries using an exact, unique source quote. */
export function expandQuoteMathContext(markdown: string, quote: string): string | null {
  if (!quote) return null;
  const quoteStart = markdown.indexOf(quote);
  if (quoteStart < 0 || markdown.indexOf(quote, quoteStart + 1) >= 0) return null;
  const quoteEnd = quoteStart + quote.length;
  let expandedStart = quoteStart;
  let expandedEnd = quoteEnd;
  for (let cursor = 0; cursor < markdown.length;) {
    const fenceEnd = tildeFenceEnd(markdown, cursor);
    if (fenceEnd >= 0) { cursor = fenceEnd; continue; }
    if (markdown[cursor] === "`" && !escaped(markdown, cursor)) {
      const marker = /^`+/u.exec(markdown.slice(cursor))![0];
      const linePrefix = markdown.slice(markdown.lastIndexOf("\n", cursor - 1) + 1, cursor);
      if (marker.length >= 3 && /^ {0,3}$/u.test(linePrefix)) {
        const lineEnd = markdown.indexOf("\n", cursor);
        if (lineEnd < 0) break;
        const closing = new RegExp(`^ {0,3}\u0060{${marker.length},}[\\t ]*$`, "gmu");
        closing.lastIndex = lineEnd + 1;
        const match = closing.exec(markdown);
        if (!match) break;
        cursor = match.index + match[0].length;
        continue;
      }
      // A code span needs a closing run of exactly the same length.
      let end = closingMarker(markdown, cursor, marker);
      while (end >= 0 && (markdown[end] === "`" || markdown[end - marker.length - 1] === "`")) {
        const next = markdown.indexOf(marker, end);
        end = next < 0 ? -1 : next + marker.length;
      }
      cursor = end < 0 ? cursor + marker.length : end;
      continue;
    }
    if (markdown[cursor] !== "$" || escaped(markdown, cursor)) { cursor += 1; continue; }
    const marker = /^\$+/u.exec(markdown.slice(cursor))![0];
    if (marker.length > 2) { cursor += marker.length; continue; }
    const end = closingMarker(markdown, cursor, marker);
    if (end < 0) {
      if (quoteStart > cursor || quoteEnd > cursor) return null;
      break;
    }
    const body = markdown.slice(cursor + marker.length, end - marker.length);
    const outsideText = mathOutsideTextGroups(body);
    // Reject dollar-separated prose/currency and incomplete TeX groups. Text
    // explicitly enclosed in TeX labels is allowed; source characters stay intact.
    const math = !!body.trim() && balancedMathGroups(body)
      && !MATH_PROSE.test(outsideText)
      && !/[A-Za-z]{3,}|[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]|`|\n\s*\n/u.test(outsideText)
      && !body.includes("$")
      && (marker.length === 2 || !body.includes("\n"))
      && markdown[end] !== "$";
    if (math) {
      if (quoteStart > cursor && quoteStart < end) expandedStart = cursor;
      if (quoteEnd > cursor && quoteEnd < end) expandedEnd = end;
    } else if ((quoteStart > cursor && quoteStart < end) || (quoteEnd > cursor && quoteEnd < end)) {
      return null;
    }
    cursor = end;
  }
  if (expandedStart === quoteStart && expandedEnd === quoteEnd) return null;
  return markdown.slice(expandedStart, expandedEnd);
}
