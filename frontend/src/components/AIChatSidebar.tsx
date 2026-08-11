"use client";

import { useState, useEffect, useRef } from "react";
import { sendAIMessage, type ApiError } from "@/lib/api";

interface Message {
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
}

interface AIChatSidebarProps {
  boardId: number;
  onBoardChange?: () => void;
}

const TYPING_DOT_DELAYS = ["0s", "0.2s", "0.4s"];

const EXAMPLE_PROMPTS = [
  "Create a bug fix task",
  "Move urgent items to In Progress",
  "What's in the backlog?",
];

export const AIChatSidebar = ({ boardId, onBoardChange }: AIChatSidebarProps) => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const addMessage = (role: Message["role"], content: string) => {
    setMessages((prev) => [...prev, { role, content, timestamp: new Date() }]);
  };

  const sendMessage = async () => {
    if (!input.trim() || isLoading) return;

    const message = input;
    addMessage("user", message);
    setInput("");
    setError(null);
    setIsLoading(true);

    try {
      const result = await sendAIMessage(boardId, message);
      addMessage("assistant", result.response);

      // The board is reloaded by the parent, so only signal that it changed
      if (result.actions_applied?.successful?.length) {
        onBoardChange?.();
      }
    } catch (err) {
      const detail = (err as ApiError).detail || "Failed to get AI response";
      setError(detail);
      addMessage("assistant", `Error: ${detail}. Please try again.`);
    } finally {
      setIsLoading(false);
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
    <div className="flex h-full flex-col border-l border-[var(--stroke)] bg-white/50 backdrop-blur">
      {/* Header */}
      <div className="border-b border-[var(--stroke)] px-6 py-4">
        <h2 className="text-lg font-semibold text-[var(--navy-dark)]">AI Assistant</h2>
        <p className="mt-1 text-xs text-[var(--gray-text)]">
          Ask me to organize your board
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
                  className={`max-w-xs rounded-2xl px-4 py-2 text-sm ${
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
                <div className="rounded-2xl bg-[var(--surface)] px-4 py-3">
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
            className="flex-1 resize-none rounded-lg border border-[var(--stroke)] bg-white px-3 py-2 text-sm outline-none placeholder:text-[var(--gray-text)] disabled:opacity-50 focus:border-[var(--primary-blue)] focus:ring-2 focus:ring-[var(--primary-blue)]/20"
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
