/** Browser input checks preserve the exact text that is bound by the service. */
export type SearchHistoryTurn = { role: "user" | "assistant"; content: string };

/** Match the service's Unicode whitespace definition (Python str.strip). */
export function contextTextIsBlank(content: string): boolean {
  return !/[^\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]/u.test(content);
}

export function contextTextError(content: string): string | null {
  if (contextTextIsBlank(content)) return "请输入非空白文本。";
  if (/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(content)) {
    return "文本含有无效 Unicode 字符，请检查原文后重新输入。";
  }
  if (content.normalize("NFC") !== content) return "文本不是 NFC 格式，请在来源中确认规范化文本后重新粘贴；系统不会自动改写原文。";
  if (Array.from(content).length > 8000) return "每条文本最多 8,000 个 Unicode 字符，请手动精简；系统不会截断文本。";
  return null;
}

export function contextHistoryError(turns: SearchHistoryTurn[]): string | null {
  if (turns.length > 16) return "对话历史最多 16 条，请手动移除多余条目。";
  for (let index = 0; index < turns.length; index += 1) {
    const error = contextTextError(turns[index].content);
    if (error) return `第 ${index + 1} 条历史：${error}`;
  }
  if (turns.reduce((total, turn) => total + new TextEncoder().encode(turn.content).length, 0) > 32768) {
    return "对话历史合计超过 32,768 UTF-8 字节，请手动精简；系统不会删减历史。";
  }
  return null;
}

export type SearchContinuation =
  | { clarification_selection: { clarification_token: string; option_id: string } }
  | { context_submission: { context_token: string; content: string } };

/** Each new signed state already carries prior supplements and any selected task. */
export function continueContextRequest(request: Record<string, unknown>, continuation: SearchContinuation) {
  const base = { ...request };
  delete base.clarification_selection;
  delete base.context_submission;
  return { ...base, ...continuation };
}
