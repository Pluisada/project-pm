import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CentralProducts } from "@/components/CentralProducts";

const json = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });
type Product = { id: number | null; name: string; card_count: number };
type Call = { method: string; url: string; body: unknown };

function backend(initial: { initialized?: boolean; products?: Product[] } = {}, write?: (c: Call) => Response) {
  let state = { initialized: true, products: [{ id: 1, name: "Chargers", card_count: 2 }, { id: 2, name: "Adapters", card_count: 0 }], ...initial };
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const call = { method: init?.method ?? "GET", url: String(url), body: init?.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    if (call.method === "GET") return json(200, state);
    const answer = write?.(call);
    if (answer && answer.status >= 400) return answer;
    if (call.method === "POST") {
      const p = { id: 9, name: (call.body as { name: string }).name, card_count: 0 };
      state = { ...state, products: [...state.products, p] };
      return json(201, p);
    }
    if (call.method === "PUT") {
      const id = Number(call.url.split("/").pop());
      state = { ...state, products: state.products.map((p) => (p.id === id ? { ...p, name: (call.body as { name: string }).name } : p)) };
      return json(200, state.products.find((p) => p.id === id));
    }
    const id = Number(call.url.split("/").pop());
    state = { ...state, products: state.products.filter((p) => p.id !== id) };
    return new Response(null, { status: 204 });
  }));
  return calls;
}

const writes = (calls: Call[]) => calls.filter((c) => c.method !== "GET");

describe("CentralProducts", () => {
  afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

  it("lists the catalog of the company with how many opportunities use each product", async () => {
    const calls = backend();
    render(<CentralProducts companySlug="lity" companyName="Lity" />);
    expect(await screen.findByRole("heading", { name: "Products of Lity" })).toBeInTheDocument();
    const rows = await screen.findAllByTestId("product-row");
    expect(rows.map((r) => r.textContent)).toEqual([expect.stringContaining("Chargers"), expect.stringContaining("Adapters")]);
    expect(rows[0]).toHaveTextContent("2 opportunities");
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toMatch(/\/api\/companies\/lity\/products$/);
  });

  it("adds a product with a trimmed name and shows it", async () => {
    const calls = backend();
    render(<CentralProducts companySlug="lity" companyName="Lity" />);
    await userEvent.type(await screen.findByLabelText("New product"), "  Cables ");
    await userEvent.click(screen.getByRole("button", { name: "Add product" }));
    expect(await screen.findByText('Added "Cables".')).toBeInTheDocument();
    expect(writes(calls)).toEqual([expect.objectContaining({ method: "POST", body: { name: "Cables" } })]);
    expect(await screen.findByText("Cables")).toBeInTheDocument();
    expect(screen.getByLabelText("New product")).toHaveValue("");
  });

  it("refuses an empty name without calling the server", async () => {
    const calls = backend();
    render(<CentralProducts companySlug="lity" companyName="Lity" />);
    await screen.findByText("Chargers");
    expect(screen.getByRole("button", { name: "Add product" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("New product"), "   ");
    expect(screen.getByRole("button", { name: "Add product" })).toBeDisabled();
    expect(writes(calls)).toEqual([]);
  });

  it("shows the server's reason when a name is taken and keeps what was typed", async () => {
    backend({}, () => json(409, { detail: "A product with this name already exists" }));
    render(<CentralProducts companySlug="lity" companyName="Lity" />);
    await userEvent.type(await screen.findByLabelText("New product"), "Chargers");
    await userEvent.click(screen.getByRole("button", { name: "Add product" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("A product with this name already exists");
    expect(screen.getByLabelText("New product")).toHaveValue("Chargers");
  });

  it("renames an unused product and does not offer rename or delete for a used one", async () => {
    const calls = backend();
    render(<CentralProducts companySlug="lity" companyName="Lity" />);
    await screen.findByText("Adapters");
    expect(screen.getByRole("button", { name: "Rename Chargers" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Delete Chargers" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Rename Adapters" }));
    const input = screen.getByLabelText("New name for Adapters");
    await userEvent.clear(input);
    await userEvent.type(input, "Power adapters");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText('Renamed to "Power adapters".')).toBeInTheDocument();
    expect(writes(calls)).toEqual([expect.objectContaining({ method: "PUT", body: { name: "Power adapters" }, url: expect.stringMatching(/\/products\/2$/) })]);
  });

  it("deletes only after a confirmation, and cancelling deletes nothing", async () => {
    const calls = backend();
    render(<CentralProducts companySlug="lity" companyName="Lity" />);
    await userEvent.click(await screen.findByRole("button", { name: "Delete Adapters" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(writes(calls)).toEqual([]);
    await userEvent.click(screen.getByRole("button", { name: "Delete Adapters" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm delete Adapters" }));
    expect(await screen.findByText('Deleted "Adapters".')).toBeInTheDocument();
    expect(writes(calls)).toEqual([expect.objectContaining({ method: "DELETE", url: expect.stringMatching(/\/products\/2$/) })]);
    await waitFor(() => expect(screen.queryByText("Adapters")).not.toBeInTheDocument());
  });

  it("shows a 403 as a refusal", async () => {
    backend({}, () => json(403, { detail: "no" }));
    render(<CentralProducts companySlug="lity" companyName="Lity" />);
    await userEvent.type(await screen.findByLabelText("New product"), "Cables");
    await userEvent.click(screen.getByRole("button", { name: "Add product" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("You are not allowed to add the product.");
  });

  it("shows the defaults read-only while the catalog is not initialized", async () => {
    backend({ initialized: false, products: [{ id: null, name: "Chargers", card_count: 0 }] });
    render(<CentralProducts companySlug="lity" companyName="Lity" />);
    expect(await screen.findByText(/has not been set up yet/)).toBeInTheDocument();
    expect(screen.getByText("Chargers")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add product" })).not.toBeInTheDocument();
    expect(within(screen.getByTestId("product-row")).queryByRole("button")).toBeNull();
  });

  it("offers a retry when the catalog cannot be read", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(500, { detail: "x" })));
    render(<CentralProducts companySlug="lity" companyName="Lity" />);
    expect(await screen.findByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
