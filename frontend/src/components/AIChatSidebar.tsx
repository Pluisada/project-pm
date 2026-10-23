"use client";

import { useState, useEffect, useRef, type Ref } from "react";
import { getErrorMessage, sendAIMessage, type AIChatResponse, type AIChatTurn } from "@/lib/api";

interface Message {
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
  /** An error notice shown to the person; never sent back to the model. */
  failed?: boolean;
}

interface AIChatSidebarProps {
  boardId: number;
  onBoardChange?: () => void;
  onClose?: () => void;
  closeRef?: Ref<HTMLButtonElement>;
  /** Sends a message somewhere other than the legacy board endpoint (the central company board). When given, actions
   *  the server refused are listed under the answer, so a conflict is never mistaken for a success. */
  send?: (message: string, history: AIChatTurn[]) => Promise<AIChatResponse>;
  /** Identifies the conversation (the company). When it changes, the visible conversation is dropped at once and any
   *  answer still in flight for the previous one is discarded. */
  scopeKey?: string;
  /** Central pages: surfaces and text follow the theme (the legacy panel keeps its white surfaces). */
  themed?: boolean;
}

const UNMOUNTED = Symbol("unmounted");

const TYPING_DOT_DELAYS = ["0s", "0.2s", "0.4s"];

const EXAMPLE_PROMPTS = [
  "Which next actions are due this week?",
  "Which leads are overdue?",
  "Move the Acme lead to Proposal",
];

export const AIChatSidebar = ({ boardId, onBoardChange, onClose, closeRef, send, themed, scopeKey }: AIChatSidebarProps) => {
  const white = themed ? "bg-[var(--surface)] text-[var(--navy-dark)]" : "bg-white";
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scopeRef = useRef<string | undefined | symbol>(scopeKey);
  const [shownScope, setShownScope] = useState(scopeKey);

  // Render-time reset: the first paint after a company switch already shows an empty conversation.
  if (shownScope !== scopeKey) {
    setShownScope(scopeKey);
    setMessages([]);
    setInput("");
    setError(null);
    setIsLoading(false);
  }
  useEffect(() => {
    scopeRef.current = scopeKey;
    return () => {
      scopeRef.current = UNMOUNTED; // an answer arriving after the panel is gone belongs to nobody
    };
  }, [scopeKey]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const addMessage = (role: Message["role"], content: string, failed = false) => {
    setMessages((prev) => [...prev, { role, content, timestamp: new Date(), failed }]);
  };

  const sendMessage = async () => {
    if (!input.trim() || isLoading) return;

    const message = input;
    const scope = scopeKey;
    const history: AIChatTurn[] = messages
      .filter((m) => !m.failed)
      .slice(-10)
      .map(({ role, content }) => ({ role, content }));
    addMessage("user", message);
    setInput("");
    setError(null);
    setIsLoading(true);

    try {
      const result = send ? await send(message, history) : await sendAIMessage(boardId, message, history);
      if (scopeRef.current !== scope) return;
      const refused = send ? (result.actions_applied?.failed ?? []) : [];
      const notApplied = refused.length
        ? `\n\nNot applied:\n${refused.map((f) => `- ${f.code === "card_version_conflict" ? `${f.reason ?? "The card changed"} (reload the board and ask again)` : (f.reason ?? "This action could not be applied")}`).join("\n")}`
        : "";
      addMessage("assistant", `${result.response}${notApplied}`);

      // The board is reloaded by the parent, so only signal that it changed
      if (result.actions_applied?.successful?.length) {
        onBoardChange?.();
      }
    } catch (err) {
      if (scopeRef.current !== scope) return;
      const detail = getErrorMessage(err, "Failed to get AI response");
      setError(detail);
      addMessage("assistant", `Error: ${detail}. Please try again.`, true);
    } finally {
      if (scopeRef.current === scope) setIsLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    sendMessage();
  };

  const handleClearHistory = () => {
    setMessages([]);
    setError(null);
  };

  return (
    <div className={`flex h-full flex-col border-l border-[var(--stroke)] ${themed ? "" : "bg-white/50"} backdrop-blur`}>
      {/* Header */}
      <div className="border-b border-t-[3px] border-b-[var(--stroke)] border-t-[var(--secondary-purple)] px-6 py-4">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-lg font-semibold text-[var(--navy-dark)]">AI Assistant</h2>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close AI Assistant"
            className={`rounded-lg border border-[var(--stroke)] ${white} px-3 py-1 text-xs font-semibold text-[var(--gray-text)] transition hover:bg-[var(--surface)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--primary-blue)]`}
          >
            Close
          </button>
        </div>
        <p className="mt-1 text-xs text-[var(--gray-text)]">
          Ask me about your leads or to move them between stages
        </p>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {messages.length === 0 ? (
          <div className="flex h-full items-center justify-center">
            <div className="text-center">
              <p className="text-sm text-[var(--gray-text)]">
                No messages yet. Ask me to help organize your board!
              </p>
              <div className="mt-4 space-y-2 text-xs text-[var(--gray-text)]">
                <p className="font-semibold text-[var(--navy-dark)]">Try asking:</p>
                <ul className="space-y-1">
                  {EXAMPLE_PROMPTS.map((prompt) => (
                    <li key={prompt}>{`• "${prompt}"`}</li>
                  ))}
                </ul>
              </div>
            </div>
          </div>
        ) : (
          <>
            {messages.map((msg, idx) => (
              <div
                key={idx}
                className={`flex ${
                  msg.role === "user" ? "justify-end" : "justify-start"
                }`}
              >
                <div
                  className={`max-w-xs rounded-xl px-4 py-2 text-sm ${
                    msg.role === "user"
                      ? "bg-[var(--secondary-purple)] text-white"
                      : "bg-[var(--surface)] text-[var(--navy-dark)]"
                  }`}
                >
                  <p className="whitespace-pre-wrap break-words">{msg.content}</p>
                  <span className="mt-1 block text-xs opacity-70">
                    {msg.timestamp.toLocaleTimeString([], {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </span>
                </div>
              </div>
            ))}
            {isLoading && (
              <div className="flex justify-start">
                <div className="rounded-xl bg-[var(--surface)] px-4 py-3">
                  <div className="flex gap-1">
                    {TYPING_DOT_DELAYS.map((delay) => (
                      <div
                        key={delay}
                        className="h-2 w-2 rounded-full bg-[var(--primary-blue)] animate-bounce"
                        style={{ animationDelay: delay }}
                      />
                    ))}
                  </div>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </>
        )}
      </div>

      {/* Error */}
      {error && (
        <div className="border-t border-[var(--stroke)] bg-red-50 px-6 py-3">
          <p className="text-xs text-red-700">{error}</p>
        </div>
      )}

      {/* Input */}
      <div className="border-t border-[var(--stroke)] px-6 py-4 space-y-3">
        <form onSubmit={handleSubmit} className="flex gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask me to organize your board... (Shift+Enter for newline)"
            disabled={isLoading}
            className={`flex-1 resize-none rounded-lg border border-[var(--stroke)] ${white} px-3 py-2 text-sm outline-none placeholder:text-[var(--gray-text)] disabled:opacity-50 focus:border-[var(--primary-blue)] focus:ring-2 focus:ring-[var(--primary-blue)]/20`}
            rows={3}
          />
          <button
            type="submit"
            disabled={isLoading || !input.trim()}
            className="self-end rounded-lg bg-[var(--secondary-purple)] px-4 py-2 text-sm font-semibold text-white transition hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Send
          </button>
        </form>

        <button
          onClick={handleClearHistory}
          disabled={isLoading || messages.length === 0}
          className="w-full rounded-lg border border-[var(--stroke)] px-3 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)] transition hover:bg-[var(--surface)] disabled:opacity-50"
        >
          Clear History
        </button>
      </div>
    </div>
  );
};
