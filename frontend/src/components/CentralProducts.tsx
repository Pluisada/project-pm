"use client";

import { useCallback, useEffect, useState } from "react";
import {
  createCompanyProduct,
  deleteCompanyProduct,
  getCompanyProducts,
  getErrorMessage,
  isAbortError,
  renameCompanyProduct,
  type ApiError,
  type CompanyProduct,
  type ProductCatalog,
} from "@/lib/api";

const NAME_MAX_LENGTH = 100; // the backend's limit (product_catalog.NAME_MAX_LENGTH)

const fieldClass =
  "mt-1 w-full rounded-lg border border-[var(--stroke)] bg-[var(--surface-strong)] text-[var(--navy-dark)] px-3 py-2 text-sm outline-none focus:border-[var(--primary-blue)] disabled:opacity-50";
const labelClass = "block text-xs font-semibold uppercase tracking-[0.2em] text-[var(--gray-text)]";
const primaryButton =
  "rounded-lg bg-[var(--secondary-purple)] px-4 py-2 text-sm font-semibold text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";
const secondaryButton =
  "rounded-lg border border-[var(--stroke)] px-3 py-1.5 text-xs font-semibold text-[var(--primary-blue)] transition hover:border-[var(--primary-blue)] disabled:cursor-not-allowed disabled:opacity-50";

type Notice = { kind: "ok" | "error"; text: string };

function failure(err: unknown, what: string): string {
  const code = (err as Partial<ApiError>).status_code;
  if (code === 0) return `Network error - ${what} may not have been saved. Reload the list and check.`;
  if (code === 401) return "Your session expired.";
  if (code === 403) return `You are not allowed to ${what}.`;
  if (code === 404) return "That product was not found - the list was reloaded.";
  if (code === 409 || code === 422) return getErrorMessage(err, `Could not ${what}.`);
  if (code === 429) return "Too many requests. Wait a moment and try again.";
  return `Could not ${what}.`;
}

/**
 * The product catalog of one company, for the Super Admin (the backend re-checks every request and answers 403 to
 * anyone else): add, rename and delete products. A product that an opportunity still uses can be neither renamed
 * nor deleted (the server says so); a catalog that was never initialized is shown read-only with its defaults.
 */
export const CentralProducts = ({ companySlug, companyName }: { companySlug: string; companyName: string }) => {
  const [catalog, setCatalog] = useState<ProductCatalog | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [editing, setEditing] = useState<{ id: number; name: string } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<CompanyProduct | null>(null);

  const load = useCallback(
    (signal?: AbortSignal): Promise<void> =>
      getCompanyProducts(companySlug, signal).then(
        (loaded) => {
          setCatalog(loaded);
          setLoadFailed(false);
        },
        (err) => {
          if (isAbortError(err) || (err as Partial<ApiError>).status_code === 401) return;
          setLoadFailed(true);
          setNotice({ kind: "error", text: failure(err, "load the products") });
        }
      ),
    [companySlug]
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const run = async (what: string, call: () => Promise<void>, okText: string): Promise<boolean> => {
    setBusy(true);
    setNotice(null);
    try {
      await call();
      setNotice({ kind: "ok", text: okText });
      return true;
    } catch (err) {
      setNotice({ kind: "error", text: failure(err, what) });
      return false;
    } finally {
      await load();
      setBusy(false);
    }
  };

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    const clean = name.trim();
    if (!clean) return setNotice({ kind: "error", text: "Enter a product name." });
    if (clean.length > NAME_MAX_LENGTH) return setNotice({ kind: "error", text: `A product name can have at most ${NAME_MAX_LENGTH} characters.` });
    if (await run("add the product", async () => { await createCompanyProduct(companySlug, clean); }, `Added "${clean}".`)) setName("");
  };

  const rename = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editing) return;
    const clean = editing.name.trim();
    if (!clean) return setNotice({ kind: "error", text: "Enter a product name." });
    if (clean.length > NAME_MAX_LENGTH) return setNotice({ kind: "error", text: `A product name can have at most ${NAME_MAX_LENGTH} characters.` });
    if (await run("rename the product", async () => { await renameCompanyProduct(companySlug, editing.id, clean); }, `Renamed to "${clean}".`)) setEditing(null);
  };

  const remove = async (product: CompanyProduct) => {
    if (product.id === null) return;
    const id = product.id;
    await run("delete the product", () => deleteCompanyProduct(companySlug, id), `Deleted "${product.name}".`);
    setConfirmDelete(null);
  };

  const manageable = catalog?.initialized === true;

  return (
    <div data-testid="central-products-screen">
      <h2 className="font-display text-xl font-semibold text-[var(--navy-dark)]">Products of {companyName}</h2>
      <p className="mt-2 text-sm text-[var(--gray-text)]">
        The products an opportunity can name as its product line. A product that an opportunity still uses cannot be renamed or deleted.
      </p>
      {notice && (
        <p role={notice.kind === "ok" ? "status" : "alert"} data-testid="products-notice"
          className={`mt-3 text-sm font-medium ${notice.kind === "ok" ? "text-green-800" : "text-red-800"}`}>
          {notice.text}
        </p>
      )}
      {loadFailed && (
        <button type="button" onClick={() => void load()} className={`${primaryButton} mt-3`}>Try again</button>
      )}
      {!catalog && !loadFailed && <p role="status" className="mt-4 text-sm text-[var(--gray-text)]">Loading products...</p>}
      {catalog && (
        <>
          {!catalog.initialized && (
            <p role="status" className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
              The product catalog of this company has not been set up yet, so the built-in defaults are shown and products cannot be changed here.
            </p>
          )}
          {manageable && (
            <form onSubmit={add} className="mt-4 flex flex-wrap items-end gap-3" noValidate>
              <label className={`${labelClass} min-w-[16rem] flex-1`}>
                New product
                <input value={name} onChange={(e) => setName(e.target.value)} maxLength={NAME_MAX_LENGTH} disabled={busy} className={fieldClass} />
              </label>
              <button type="submit" disabled={busy || !name.trim()} className={primaryButton}>Add product</button>
            </form>
          )}
          {catalog.products.length === 0 ? (
            <p className="mt-4 text-sm text-[var(--gray-text)]">No products yet.</p>
          ) : (
            <ul className="mt-4 divide-y divide-[var(--stroke)] rounded-2xl border border-[var(--stroke)]">
              {catalog.products.map((product) => (
                <li key={product.id ?? product.name} data-testid="product-row" className="flex flex-wrap items-center gap-3 px-4 py-3">
                  {editing && product.id === editing.id ? (
                    <form onSubmit={rename} className="flex flex-1 flex-wrap items-center gap-2" noValidate>
                      <input aria-label={`New name for ${product.name}`} value={editing.name} autoFocus maxLength={NAME_MAX_LENGTH} disabled={busy}
                        onChange={(e) => setEditing({ id: editing.id, name: e.target.value })} className={`${fieldClass} mt-0 max-w-sm`} />
                      <button type="submit" disabled={busy} className={primaryButton}>Save</button>
                      <button type="button" disabled={busy} onClick={() => setEditing(null)} className={secondaryButton}>Cancel</button>
                    </form>
                  ) : (
                    <>
                      <span className="min-w-0 flex-1 truncate text-sm font-semibold text-[var(--navy-dark)]">{product.name}</span>
                      <span className="text-xs text-[var(--gray-text)]">{product.card_count} {product.card_count === 1 ? "opportunity" : "opportunities"}</span>
                      {manageable && product.id !== null && (
                        <>
                          <button type="button" disabled={busy || product.card_count > 0} onClick={() => setEditing({ id: product.id as number, name: product.name })}
                            aria-label={`Rename ${product.name}`} title={product.card_count > 0 ? "Used by opportunities" : undefined} className={secondaryButton}>
                            Rename
                          </button>
                          {confirmDelete?.id === product.id ? (
                            <>
                              <button type="button" disabled={busy} onClick={() => void remove(product)} aria-label={`Confirm delete ${product.name}`}
                                className="rounded-lg bg-[var(--danger-solid)] px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-60">Confirm delete</button>
                              <button type="button" disabled={busy} onClick={() => setConfirmDelete(null)} className={secondaryButton}>Cancel</button>
                            </>
                          ) : (
                            <button type="button" disabled={busy || product.card_count > 0} onClick={() => setConfirmDelete(product)}
                              aria-label={`Delete ${product.name}`} title={product.card_count > 0 ? "Used by opportunities" : undefined}
                              className="rounded-lg border border-red-300 px-3 py-1.5 text-xs font-semibold text-red-800 transition hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50">
                              Delete
                            </button>
                          )}
                        </>
                      )}
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
};
