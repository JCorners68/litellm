import { renderWithProviders, screen } from "../../../tests/test-utils";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import ExportTypeSelector from "./ExportTypeSelector";

describe("ExportTypeSelector", () => {
  it("should render", () => {
    renderWithProviders(<ExportTypeSelector value="daily" onChange={vi.fn()} entityType="team" />);
    expect(screen.getByText("Export type")).toBeInTheDocument();
  });

  it("offers the three export types for an entity", () => {
    renderWithProviders(<ExportTypeSelector value="daily" onChange={vi.fn()} entityType="team" />);
    expect(screen.getByRole("radio", { name: /Day-by-day breakdown by team Daily metrics/i })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Day-by-day breakdown by team and key/i })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Totals by team/i })).toBeInTheDocument();
  });

  it("should display the correct entity type for different entities", () => {
    renderWithProviders(<ExportTypeSelector value="daily" onChange={vi.fn()} entityType="organization" />);
    expect(
      screen.getByRole("radio", { name: /Day-by-day breakdown by organization Daily metrics/i }),
    ).toBeInTheDocument();
  });

  it("should call onChange when a radio option is selected", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(<ExportTypeSelector value="daily" onChange={onChange} entityType="team" />);
    await user.click(screen.getByRole("radio", { name: /Day-by-day breakdown by team and key/i }));
    expect(onChange).toHaveBeenCalledWith("daily_with_keys");
  });

  it("should call onChange with entities for the totals option", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(<ExportTypeSelector value="daily" onChange={onChange} entityType="tag" />);
    await user.click(screen.getByRole("radio", { name: /Totals by tag/i }));
    expect(onChange).toHaveBeenCalledWith("entities");
  });

  it("should have the correct radio checked", () => {
    renderWithProviders(<ExportTypeSelector value="entities" onChange={vi.fn()} entityType="team" />);
    expect(screen.getByRole("radio", { name: /Totals by team/i })).toBeChecked();
  });
});
