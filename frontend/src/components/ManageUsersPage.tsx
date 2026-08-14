"use client";

import { useEffect, useState, type FormEvent } from "react";
import { createUser, listUsers, type UserResponse } from "@/lib/api";
import type { ApiError } from "@/lib/api";

interface ManageUsersPageProps {
  onBack: () => void;
}

const initialFormState = { username: "", password: "" };

export const ManageUsersPage = ({ onBack }: ManageUsersPageProps) => {
  const [users, setUsers] = useState<UserResponse[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [formState, setFormState] = useState(initialFormState);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const loadUsers = async () => {
    setIsLoading(true);
    try {
      setUsers(await listUsers());
    } catch (err) {
      console.error("Failed to load users", err);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadUsers();
  }, []);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setSubmitting(true);

    try {
      const newUser = await createUser(formState.username, formState.password);
      setUsers((prev) => [...prev, newUser]);
      setFormState(initialFormState);
    } catch (err) {
      setError((err as ApiError).detail || "Failed to create user");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mx-auto max-w-2xl px-6 py-10">
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="font-display text-2xl font-semibold text-[var(--navy-dark)]">
            Manage Users
          </h1>
          <p className="mt-1 text-sm text-[var(--gray-text)]">
            Create accounts for team members. New users get the member profile.
          </p>
        </div>
        <button
          onClick={onBack}
          className="rounded-lg border border-[var(--stroke)] px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)] transition hover:text-[var(--navy-dark)]"
        >
          Back to board
        </button>
      </div>

      <form
        onSubmit={handleSubmit}
        className="mb-8 rounded-[24px] border border-[var(--stroke)] bg-white/80 p-6 shadow-[var(--shadow)]"
      >
        {error && (
          <div className="mb-4 rounded-lg border border-red-300 bg-red-50 p-3">
            <p className="text-sm font-medium text-red-800">{error}</p>
          </div>
        )}

        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <div className="flex-1">
            <label
              htmlFor="new-user-username"
              className="block text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)]"
            >
              Username
            </label>
            <input
              id="new-user-username"
              type="text"
              value={formState.username}
              onChange={(e) =>
                setFormState((prev) => ({ ...prev, username: e.target.value }))
              }
              placeholder="New username"
              disabled={submitting}
              minLength={3}
              maxLength={50}
              required
              className="mt-2 w-full rounded-lg border border-[var(--stroke)] bg-white px-4 py-3 text-sm outline-none transition focus:border-[var(--primary-blue)] focus:ring-2 focus:ring-[var(--primary-blue)]/20 disabled:opacity-50"
            />
          </div>
          <div className="flex-1">
            <label
              htmlFor="new-user-password"
              className="block text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)]"
            >
              Password
            </label>
            <input
              id="new-user-password"
              type="password"
              value={formState.password}
              onChange={(e) =>
                setFormState((prev) => ({ ...prev, password: e.target.value }))
              }
              placeholder="At least 8 characters"
              disabled={submitting}
              minLength={8}
              maxLength={128}
              required
              className="mt-2 w-full rounded-lg border border-[var(--stroke)] bg-white px-4 py-3 text-sm outline-none transition focus:border-[var(--primary-blue)] focus:ring-2 focus:ring-[var(--primary-blue)]/20 disabled:opacity-50"
            />
          </div>
          <button
            type="submit"
            disabled={submitting}
            className="rounded-full bg-[var(--secondary-purple)] px-6 py-3 text-xs font-semibold uppercase tracking-wide text-white transition hover:brightness-110 disabled:opacity-50"
          >
            {submitting ? "Creating..." : "Add user"}
          </button>
        </div>
      </form>

      <div className="rounded-[24px] border border-[var(--stroke)] bg-white/80 shadow-[var(--shadow)]">
        {isLoading ? (
          <p className="p-6 text-sm text-[var(--gray-text)]">Loading users...</p>
        ) : (
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-[var(--stroke)] text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)]">
                <th className="px-6 py-3">Username</th>
                <th className="px-6 py-3">Role</th>
                <th className="px-6 py-3">Created</th>
              </tr>
            </thead>
            <tbody>
              {users.map((user) => (
                <tr key={user.id} className="border-b border-[var(--stroke)] last:border-0">
                  <td className="px-6 py-3 font-medium text-[var(--navy-dark)]">
                    {user.username}
                  </td>
                  <td className="px-6 py-3 capitalize text-[var(--gray-text)]">
                    {user.role}
                  </td>
                  <td className="px-6 py-3 text-[var(--gray-text)]">
                    {new Date(user.created_at).toLocaleDateString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
};
