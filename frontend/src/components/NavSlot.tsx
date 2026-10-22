"use client";

import type { ReactNode } from "react";
import { createPortal } from "react-dom";

/** A named place in the top navigation that a screen's own controls can be rendered into (the controls keep living
 *  in the component that owns their state; only where they appear moves). */
export const NavSlot = ({ id }: { id: string }) => <span id={id} className="inline-flex items-center gap-2 empty:hidden" />;

/**
 * Renders `children` into the NavSlot called `id`. Without such a slot on the page (a component used on its own, as in
 * its unit tests) the children simply render where they are, so nothing is lost.
 */
export const InNavSlot = ({ id, children }: { id: string; children: ReactNode }) => {
  // Looked up while rendering: these components only render in the browser, after the page (and its header) exist.
  const slot = typeof document === "undefined" ? null : document.getElementById(id);
  return slot ? createPortal(children, slot) : <>{children}</>;
};
