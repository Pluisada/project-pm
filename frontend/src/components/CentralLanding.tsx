"use client";

/**
 * Full-screen Sales Tracker artwork shown before the central sign-in form. Wide screens fill the viewport;
 * narrow ones show the whole image over a blurred copy. Without `open` it offers the LOGIN button; with it,
 * the children (the sign-in form) sit over the artwork.
 */
export const CentralLanding = ({ open, onLogin, children }: { open: boolean; onLogin: () => void; children: React.ReactNode }) => (
  <div className="fixed inset-0 overflow-hidden bg-[#0b1f3a]">
    {/* eslint-disable-next-line @next/next/no-img-element -- static export, local asset */}
    <img src="/sales-tracker-login.jpg" alt="" aria-hidden="true" className="absolute inset-0 h-full w-full scale-110 object-cover blur-2xl" />
    {/* eslint-disable-next-line @next/next/no-img-element -- static export, local asset */}
    <img
      src="/sales-tracker-login.jpg"
      alt="Sales Tracker: three racehorses wearing the Lity, Salcomp and Valor Log colors"
      className="absolute inset-0 h-full w-full object-cover object-[50%_20%] [@media(max-aspect-ratio:3/2)]:object-contain [@media(max-aspect-ratio:3/2)]:object-center"
    />
    {open ? (
      <div className="absolute inset-0 overflow-y-auto bg-black/50">{children}</div>
    ) : (
      <button
        type="button"
        onClick={onLogin}
        className="absolute bottom-[max(2rem,env(safe-area-inset-bottom))] left-1/2 -translate-x-1/2 rounded-lg border-2 border-white bg-[var(--navy-dark)] px-12 py-3 text-base font-bold uppercase tracking-[0.25em] text-white shadow-[var(--shadow)] transition hover:opacity-90 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-white"
      >
        LOGIN
      </button>
    )}
  </div>
);
