export type TranslationParams = Record<string, string | number>;

/** A missing locale entry must never take down the Agent workspace. */
export function formatTranslation(
  message: string | undefined,
  fallback: string,
  params?: TranslationParams,
): string {
  const template = message ?? fallback;
  if (!params) return template;
  return Object.entries(params).reduce(
    (result, [key, value]) => result.replaceAll(`{${key}}`, String(value)),
    template,
  );
}
