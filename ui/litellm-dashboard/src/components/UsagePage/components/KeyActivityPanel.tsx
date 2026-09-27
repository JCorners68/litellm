import { Search, X } from "lucide-react";
import React, { useEffect, useMemo, useRef, useState } from "react";

import { ActivityMetrics } from "@/components/activity_metrics";
import type { Team } from "@/components/key_team_helpers/key_list";
import type { ApiKeyTruncation } from "@/components/UsagePage/apiKeyTruncation";
import type { DailyActivityKeySearchResponse } from "@/components/UsagePage/dailyActivityApi";
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from "@/components/ui/input-group";

import { filterKeyActivity } from "../keyActivityFilter";
import type { ModelActivityData } from "../types";
import { keyActivityRowsToMetrics } from "./keySearch";

const SEARCH_DEBOUNCE_MS = 300;
const MIN_SEARCH_LENGTH = 2;

interface KeyActivityPanelProps {
  keyMetrics: Record<string, ModelActivityData>;
  hidePromptCachingMetrics?: boolean;
  apiKeyTruncation?: ApiKeyTruncation;
  teams?: Team[];
  searchKeys?: (search: string) => Promise<DailyActivityKeySearchResponse>;
}

const KeyActivityPanel: React.FC<KeyActivityPanelProps> = ({
  keyMetrics,
  hidePromptCachingMetrics = false,
  apiKeyTruncation,
  teams = [],
  searchKeys,
}) => {
  const [query, setQuery] = useState("");
  const [searchResult, setSearchResult] = useState<{ term: string; metrics: Record<string, ModelActivityData> } | null>(
    null,
  );
  const searchIdRef = useRef(0);

  const localFiltered = useMemo(() => filterKeyActivity(keyMetrics, query), [keyMetrics, query]);
  const remoteSearchEnabled = apiKeyTruncation !== undefined && searchKeys !== undefined;
  const trimmedQuery = query.trim();
  const remoteSearchActive = remoteSearchEnabled && trimmedQuery.length >= MIN_SEARCH_LENGTH;
  const searchTerm = remoteSearchActive ? trimmedQuery : null;

  useEffect(() => {
    if (!searchTerm || !searchKeys) return;
    const searchId = ++searchIdRef.current;
    const timer = setTimeout(() => {
      searchKeys(searchTerm)
        .then((response) => {
          if (searchIdRef.current !== searchId) return;
          setSearchResult({ term: searchTerm, metrics: keyActivityRowsToMetrics(response.api_keys, teams) });
        })
        .catch((error) => {
          if (searchIdRef.current !== searchId) return;
          console.error("Key activity search failed:", error);
          setSearchResult({ term: searchTerm, metrics: {} });
        });
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
    // teams is a stable list per render of the parent view
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchTerm, searchKeys]);

  const searching = searchTerm !== null && searchResult?.term !== searchTerm;
  const remoteMetrics = searchResult?.term === searchTerm ? searchResult.metrics : {};

  // Server rows win over the locally filtered map: they cover keys beyond the
  // truncated client-side breakdown.
  const filtered = useMemo(
    () => ({ ...localFiltered, ...remoteMetrics }),
    [localFiltered, remoteMetrics],
  );

  const totalKeys = Object.keys(keyMetrics).length;
  const shownKeys = Object.keys(filtered).length;
  const isFiltering = query.trim() !== "";

  return (
    <div className="space-y-4">
      <div className="mt-2 flex items-center gap-3">
        <InputGroup className="max-w-md">
          <InputGroupAddon>
            <Search className="size-4 text-muted-foreground" />
          </InputGroupAddon>
          <InputGroupInput
            aria-label="Search keys"
            placeholder="Search by key alias, key hash, user ID, or email"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {isFiltering && (
            <InputGroupAddon align="inline-end">
              <InputGroupButton size="icon-xs" aria-label="Clear key search" onClick={() => setQuery("")}>
                <X />
              </InputGroupButton>
            </InputGroupAddon>
          )}
        </InputGroup>
        <span className="text-sm text-muted-foreground">
          {searching
            ? "Searching..."
            : `Showing ${shownKeys.toLocaleString()} of ${totalKeys.toLocaleString()} keys`}
        </span>
        {apiKeyTruncation !== undefined && (
          <span className="text-sm text-muted-foreground" role="note">
            Only the {apiKeyTruncation.limit.toLocaleString()} highest-spend keys of{" "}
            {apiKeyTruncation.total.toLocaleString()} are loaded
          </span>
        )}
      </div>
      {isFiltering && totalKeys > 0 && shownKeys === 0 ? (
        <p className="rounded-lg border p-6 text-center text-sm text-muted-foreground">
          No keys match &quot;{query.trim()}&quot; in this date range
        </p>
      ) : (
        <ActivityMetrics modelMetrics={filtered} hidePromptCachingMetrics={hidePromptCachingMetrics} />
      )}
    </div>
  );
};

export default KeyActivityPanel;
