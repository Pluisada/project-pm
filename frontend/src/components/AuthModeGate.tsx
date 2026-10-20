"use client";

import { useCallback, useEffect, useState } from "react";
import { getAuthMode, type AuthMode } from "@/lib/auth";
import { CentralApp } from "@/components/CentralApp";
import { LoadingScreen } from "@/components/LoadingScreen";
import { ProtectedRoute } from "@/components/ProtectedRoute";

/** Picks the sign-in flow the backend runs; the legacy application is untouched. */
export const AuthModeGate = () => {
  const [mode, setMode] = useState<AuthMode | null>(null);
  const [failed, setFailed] = useState(false);

  const detect = useCallback(() => {
    getAuthMode().then(setMode, () => setFailed(true));
  }, []);

  useEffect(detect, [detect]);

  const retry = () => {
    setFailed(false);
    detect();
  };

  if (failed) {
    return (
      <main className="flex min-h-screen items-center justify-center px-6">
        <div className="text-center">
          <p role="alert" className="text-sm font-medium text-red-800">
            Could not reach the server. Check your connection and try again.
          </p>
          <button type="button" onClick={retry}
            className="mt-4 rounded-lg bg-[var(--secondary-purple)] px-6 py-3 font-semibold text-white transition hover:opacity-90">
            Try again
          </button>
        </div>
      </main>
    );
  }
  if (!mode) return <LoadingScreen message="Loading..." />;
  return mode === "central" ? <CentralApp /> : <ProtectedRoute />;
};
