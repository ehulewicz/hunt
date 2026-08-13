import { render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { ListingsWorkspace } from "@/components/jobs/listings-workspace";

vi.mock("@/components/workspace", () => ({ useAccessToken: () => "approved" }));
vi.mock("@/lib/jobs-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/jobs-api")>();
  return {
    ...original,
    getListings: vi.fn().mockResolvedValue({
      items: [],
      total: 0,
      limit: 200,
      offset: 0,
      location_options: ["New York City, NY", "Remote"],
    }),
  };
});

test("listings expose only source category and multi-location filters", async () => {
  render(<ListingsWorkspace />);

  await waitFor(() => expect(screen.getByText("No internship or co-op listings match.")));
  expect(screen.getByRole("radio", { name: "All" })).toBeChecked();
  expect(screen.getByRole("radio", { name: "FAANG+" })).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "Quant" })).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "New York City, NY" })).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "Remote" })).toBeInTheDocument();
  expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
  expect(screen.queryByText("Career stage")).not.toBeInTheDocument();
});
