export const CURRENT_COMPILER_MODEL = "gpt-6-luna" as const;

// Historical artifacts retain their original model provenance.
export const HISTORICAL_COMPILER_MODEL = "gpt-5.6-luna" as const;
export const FALLBACK_COMPILER_MODEL = "deepseek-v4-flash" as const;

export type CompilerModel = typeof CURRENT_COMPILER_MODEL | typeof HISTORICAL_COMPILER_MODEL | typeof FALLBACK_COMPILER_MODEL;

export function isCompilerModel(value: unknown): value is CompilerModel {
  return value === CURRENT_COMPILER_MODEL || value === HISTORICAL_COMPILER_MODEL || value === FALLBACK_COMPILER_MODEL;
}
