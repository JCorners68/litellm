import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ModelActivityData } from "../types";
import KeyActivityPanel from "./KeyActivityPanel";

vi.mock("@/components/activity_metrics", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/components/activity_metrics")>()),
  ActivityMetrics: ({ modelMetrics }: { modelMetrics: Record<string, ModelActivityData> }) => (
    <ul data-testid="rendered-keys">
      {Object.keys(modelMetrics).map((hash) => (
        <li key={hash}>{hash}</li>
      ))}
    </ul>
  ),
}));

function activity(label: string, user_email: string | null, user_id: string | null): ModelActivityData {
  return {
    label,
    key_metadata: { key_alias: label, team_id: "team-1", user_id, user_email },
    total_requests: 1,
    total_successful_requests: 1,
    total_failed_requests: 0,
    total_cache_read_input_tokens: 0,
    total_cache_creation_input_tokens: 0,
    total_tokens: 10,
    prompt_tokens: 5,
    completion_tokens: 5,
    total_spend: 0.01,
    top_api_keys: [],
    top_models: [],
    daily_data: [],
  };
}

const keyMetrics: Record<string, ModelActivityData> = {
  "hash-alice": activity("alice-key", "alice@example.com", "user-alice"),
  "hash-bob": activity("bob-key", "bob@example.com", "user-bob"),
};

describe("KeyActivityPanel", () => {
  it("renders every key and the full count before searching", () => {
    render(<KeyActivityPanel keyMetrics={keyMetrics} />);
    expect(screen.getByTestId("rendered-keys")).toHaveTextContent("hash-alicehash-bob");
    expect(screen.getByText("Showing 2 of 2 keys")).toBeInTheDocument();
  });

  it("narrows the rendered keys to those matching the user email", () => {
    render(<KeyActivityPanel keyMetrics={keyMetrics} />);
    fireEvent.change(screen.getByLabelText("Search keys"), { target: { value: "bob@example.com" } });
    expect(screen.getByTestId("rendered-keys")).toHaveTextContent("hash-bob");
    expect(screen.getByTestId("rendered-keys")).not.toHaveTextContent("hash-alice");
    expect(screen.getByText("Showing 1 of 2 keys")).toBeInTheDocument();
  });

  it("shows an empty state instead of zeroed metrics when nothing matches", () => {
    render(<KeyActivityPanel keyMetrics={keyMetrics} />);
    fireEvent.change(screen.getByLabelText("Search keys"), { target: { value: "carol" } });
    expect(screen.queryByTestId("rendered-keys")).not.toBeInTheDocument();
    expect(screen.getByText('No keys match "carol" in this date range')).toBeInTheDocument();
  });

  it("clears the search and restores every key", () => {
    render(<KeyActivityPanel keyMetrics={keyMetrics} />);
    fireEvent.change(screen.getByLabelText("Search keys"), { target: { value: "user-alice" } });
    expect(screen.getByTestId("rendered-keys")).toHaveTextContent("hash-alice");
    fireEvent.click(screen.getByLabelText("Clear key search"));
    expect(screen.getByLabelText("Search keys")).toHaveValue("");
    expect(screen.getByTestId("rendered-keys")).toHaveTextContent("hash-alicehash-bob");
  });

  it("says how many keys the proxy left out when only the top spenders were loaded", () => {
    render(<KeyActivityPanel keyMetrics={keyMetrics} apiKeyTruncation={{ limit: 2, total: 3000 }} />);
    expect(screen.getByRole("note")).toHaveTextContent("Only the 2 highest-spend keys of 3,000 are loaded");
  });

  it("shows no truncation note when every key is loaded", () => {
    render(<KeyActivityPanel keyMetrics={keyMetrics} />);
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  describe("remote search", () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    const remoteSearchProps = (searchKeys = vi.fn().mockResolvedValue({ api_keys: [] })) => ({
      apiKeyTruncation: { limit: 2, total: 3000 },
      searchKeys,
    });

    it("calls searchKeys after the debounce when the list is truncated and the query has 2+ characters", async () => {
      const searchKeys = vi.fn().mockResolvedValue({ api_keys: [] });
      render(<KeyActivityPanel keyMetrics={keyMetrics} {...remoteSearchProps(searchKeys)} />);

      fireEvent.change(screen.getByLabelText("Search keys"), { target: { value: "al" } });
      expect(searchKeys).not.toHaveBeenCalled();

      await act(async () => {
        vi.advanceTimersByTime(300);
      });

      expect(searchKeys).toHaveBeenCalledWith("al");
    });

    it("does not call searchKeys for a one-character query or when every key is already loaded", async () => {
      const searchKeys = vi.fn().mockResolvedValue({ api_keys: [] });
      render(<KeyActivityPanel keyMetrics={keyMetrics} {...remoteSearchProps(searchKeys)} />);

      fireEvent.change(screen.getByLabelText("Search keys"), { target: { value: "a" } });
      await act(async () => {
        vi.advanceTimersByTime(1000);
      });
      expect(searchKeys).not.toHaveBeenCalled();

      render(<KeyActivityPanel keyMetrics={keyMetrics} searchKeys={searchKeys} />);
      fireEvent.change(screen.getAllByLabelText("Search keys")[1], { target: { value: "alice" } });
      await act(async () => {
        vi.advanceTimersByTime(1000);
      });
      expect(searchKeys).not.toHaveBeenCalled();
    });

    it("renders keys the server returns that the truncated local list does not contain", async () => {
      const searchKeys = vi.fn().mockResolvedValue({
        api_keys: [
          {
            api_key: "hash-carol",
            metrics: {
              spend: 0.5,
              prompt_tokens: 0,
              completion_tokens: 0,
              total_tokens: 0,
              api_requests: 0,
              successful_requests: 0,
              failed_requests: 0,
              cache_read_input_tokens: 0,
              cache_creation_input_tokens: 0,
            },
            metadata: { key_alias: "carol-key", team_id: null },
          },
        ],
      });
      render(<KeyActivityPanel keyMetrics={keyMetrics} {...remoteSearchProps(searchKeys)} />);

      fireEvent.change(screen.getByLabelText("Search keys"), { target: { value: "carol" } });
      expect(screen.getByText("Searching...")).toBeInTheDocument();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(300);
        await Promise.resolve();
      });

      expect(searchKeys).toHaveBeenCalledWith("carol");
      expect(screen.getByTestId("rendered-keys")).toHaveTextContent("hash-carol");
    });
  });
});
