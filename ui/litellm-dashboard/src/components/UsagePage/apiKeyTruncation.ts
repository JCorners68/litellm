export interface ApiKeyTruncation {
  limit: number;
  total: number;
}

export const getApiKeyTruncation = (
  apiKeyLimit: number | null | undefined,
  totalApiKeys: number | null | undefined,
): ApiKeyTruncation | undefined => {
  if (typeof apiKeyLimit !== "number" || typeof totalApiKeys !== "number") return undefined;
  return totalApiKeys > apiKeyLimit ? { limit: apiKeyLimit, total: totalApiKeys } : undefined;
};
