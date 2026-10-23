import { beforeAll, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AIChatSidebar } from "@/components/AIChatSidebar";
import type { AIChatResponse, AIChatTurn } from "@/lib/api";

const answer = (response: string, successful = 0): AIChatResponse =>
  ({ response, actions: [], confidence: 1, tokens_used: 0, actions_applied: { successful: Array(successful).fill({ type: "x" }) } }) as unknown as AIChatResponse;

type Send = (message: string, history: AIChatTurn[]) => Promise<AIChatResponse>;

const panel = (slug: string, send: Send, extra: { onBoardChange?: () => void; keyed?: boolean } = {}) => (
  <AIChatSidebar key={extra.keyed === false ? "same" : slug} scopeKey={slug} boardId={1} send={send} onBoardChange={extra.onBoardChange} />
);

const ask = async (text: string) => {
  const user = userEvent.setup();
  await user.type(screen.getByRole("textbox"), text);
  await user.click(screen.getByRole("button", { name: "Send" }));
};

describe("AIChatSidebar conversation isolation by company", () => {
  beforeAll(() => {
    Element.prototype.scrollIntoView = vi.fn(); // not implemented by jsdom
  });

  it("does not show or send the previous company's conversation after a switch", async () => {
    const salcomp = vi.fn<Send>().mockResolvedValue(answer("SALCOMP-ANSWER"));
    const valor = vi.fn<Send>().mockResolvedValue(answer("valor reply"));
    const { rerender } = render(panel("salcomp", salcomp));
    await ask("salcomp question");
    expect(await screen.findByText("SALCOMP-ANSWER")).toBeInTheDocument();
    expect(salcomp).toHaveBeenCalledWith("salcomp question", []);

    rerender(panel("valor-log", valor));
    expect(screen.queryByText("SALCOMP-ANSWER")).not.toBeInTheDocument();
    expect(screen.queryByText("salcomp question")).not.toBeInTheDocument();

    await ask("Hi");
    expect(await screen.findByText("valor reply")).toBeInTheDocument();
    expect(valor).toHaveBeenCalledWith("Hi", []);

    // Back in the first company the conversation starts empty (conversations are not persisted by design).
    rerender(panel("salcomp", salcomp));
    expect(screen.queryByText("valor reply")).not.toBeInTheDocument();
    expect(screen.queryByText("SALCOMP-ANSWER")).not.toBeInTheDocument();
  });

  it.each([true, false])("discards an answer that arrives after the switch (remounted: %s)", async (keyed) => {
    let finish!: (r: AIChatResponse) => void;
    const slow = vi.fn<Send>(() => new Promise<AIChatResponse>((resolve) => { finish = resolve; }));
    const valor = vi.fn<Send>().mockResolvedValue(answer("valor reply"));
    const changed = vi.fn();
    const { rerender } = render(panel("salcomp", slow, { onBoardChange: changed, keyed }));
    await ask("slow question");

    rerender(panel("valor-log", valor, { onBoardChange: changed, keyed }));
    await act(async () => { finish(answer("LATE-SALCOMP-ANSWER", 1)); });

    expect(screen.queryByText("LATE-SALCOMP-ANSWER")).not.toBeInTheDocument();
    expect(changed).not.toHaveBeenCalled();
    // The new company's panel is usable (not stuck on "loading") and stays clean.
    await ask("Hi");
    expect(await screen.findByText("valor reply")).toBeInTheDocument();
    expect(valor).toHaveBeenCalledWith("Hi", []);
    expect(screen.queryByText("LATE-SALCOMP-ANSWER")).not.toBeInTheDocument();
  });

  it("within one company sends the visible conversation as history, without error notices", async () => {
    const send = vi.fn<Send>()
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce(answer("first answer"))
      .mockResolvedValueOnce(answer("second answer"));
    render(panel("salcomp", send));
    await ask("one");
    await screen.findByText(/Please try again/);
    await ask("two");
    await screen.findByText("first answer");
    await ask("three");
    await screen.findByText("second answer");

    expect(send.mock.calls[0][1]).toEqual([]);
    expect(send.mock.calls[1][1]).toEqual([{ role: "user", content: "one" }]);
    expect(send.mock.calls[2][1]).toEqual([
      { role: "user", content: "one" }, { role: "user", content: "two" }, { role: "assistant", content: "first answer" },
    ]);
  });

  it("Clear History also clears what the model is given", async () => {
    const send = vi.fn<Send>().mockResolvedValue(answer("a"));
    render(panel("salcomp", send));
    await ask("one");
    await screen.findByText("a");
    await userEvent.setup().click(screen.getByRole("button", { name: "Clear History" }));
    await ask("two");
    await screen.findAllByText("a");
    expect(send.mock.calls[1][1]).toEqual([]);
  });
});
