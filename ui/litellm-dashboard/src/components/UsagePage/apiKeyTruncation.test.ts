import { describe, expect, it } from "vitest";

import { getApiKeyTruncation } from "./apiKeyTruncation";

describe("getApiKeyTruncation", () => {
  it("reports truncation once the proxy saw more keys than it returned", () => {
    expect(getApiKeyTruncation(100, 101)).toEqual({ limit: 100, total: 101 });
  });

  it("stays quiet when exactly the cap exists, since every key is on screen", () => {
    expect(getApiKeyTruncation(100, 100)).toBeUndefined();
    expect(getApiKeyTruncation(100, 7)).toBeUndefined();
  });

  it("stays quiet when the response carries no cap", () => {
    expect(getApiKeyTruncation(undefined, undefined)).toBeUndefined();
    expect(getApiKeyTruncation(100, null)).toBeUndefined();
    expect(getApiKeyTruncation(null, 300)).toBeUndefined();
  });
});
