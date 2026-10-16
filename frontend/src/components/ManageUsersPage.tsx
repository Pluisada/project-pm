"use client";

import { useEffect, useState, type FormEvent } from "react";
import { createUser, getErrorMessage, listUsers, type UserResponse } from "@/lib/api";

const initialFormState = { email: "" };

export const ManageUsersPage = () => {
  const [users, setUsers] = useState<UserResponse[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [formState, setFormState] = useState(initialFormState);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    listUsers()
      .then(setUsers)
      .catch((err: unknown) => console.error("Failed to load users", err))
      .finally(() => setIsLoading(false));
  }, []);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setSubmitting(true);

    try {
      const newUser = await createUser(formState.email);
      setUsers((prev) => [...prev, newUser]);
      setFormState(initialFormState);
    } catch (err) {
      setError(getErrorMessage(err, "Failed to create user"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mx-auto max-w-2xl px-6 py-6">
      <div className="mb-6">
        <div>
          <h1 className="font-display text-2xl font-semibold text-[var(--navy-dark)]">
            Manage Users
          </h1>
          <p className="mt-1 text-sm text-[var(--gray-text)]">
            Invite team members by email. They get the member profile once they follow the emailed link and choose a password.
          </p>
        </div>
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
              htmlFor="new-user-email"
              className="block text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)]"
            >
              Email
            </label>
            <input
              id="new-user-email"
              type="email"
              value={formState.email}
              onChange={(e) => setFormState({ email: e.target.value })}
              placeholder="name@company.com"
              disabled={submitting}
              required
              className="mt-2 w-full rounded-lg border border-[var(--stroke)] bg-white px-4 py-3 text-sm outline-none transition focus:border-[var(--primary-blue)] focus:ring-2 focus:ring-[var(--primary-blue)]/20 disabled:opacity-50"
            />
          </div>
          <button
            type="submit"
            disabled={submitting}
            className="rounded-full bg-[var(--secondary-purple)] px-6 py-3 text-xs font-semibold uppercase tracking-wide text-white transition hover:brightness-110 disabled:opacity-50"
          >
            {submitting ? "Inviting..." : "Invite user"}
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
