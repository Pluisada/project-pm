# Company branding and theme (central mode)

Presentation only (frontend). It never decides access or which database is used; the backend does that on every
request.

## How it works

- Colors are semantic tokens in `frontend/src/app/globals.css`. The neutral set under `:root` is the original palette
  and is what the sign-in, landing and company-picker screens use. Each company overrides the tokens under
  `:root[data-company="<slug>"]` (light), and `:root[data-company][data-theme="dark"]` adds the dark variants.
- `frontend/src/lib/branding.ts` holds the logos and `useCompanyBranding(slug)`, which `CentralApp` calls with the
  active company. It sets `data-company` on `<html>`. A missing or unknown slug sets nothing, so the neutral palette
  applies; no company ever receives another company's identity.
- The identity follows the active company: it is applied when a company is entered, replaced on a switch, and removed
  on "Switch company", sign-out and session expiry.
- Light / Dark / System is chosen with `ThemeToggle`, shown in `CentralHeader` (so only inside a company).
  `frontend/src/lib/theme.ts` saves the choice in `localStorage` under `pm_theme` (default System) and writes the
  resolved value to `<html data-theme>`. The choice is per browser, not per company, and survives switching company.
  The neutral screens have no dark variant.
- Heading faces differ per company (`.font-display` under each `data-company` in `globals.css`); the faces are loaded
  by `next/font` in `frontend/src/app/layout.tsx`. Body text stays in the base font.

## Assets

`frontend/public/brand/`: `salcomp-logo.png`, `lity-logo.webp`, `valorlog-wordmark.png`. The header places the logo
on a `--logo-bg` chip: white for Lity (whose header is red) and for Salcomp in dark mode, transparent otherwise. The
company picker shows the logos decoratively on white. Whether each logo is legible on its header has not been
checked by eye.

## Colors and their sources

Values marked "observed" in `globals.css` were recorded from each company's public site by whoever wrote the
stylesheet; every other value is a derived step chosen for contrast. The observations and the origin of the logo
files were not re-checked when this file was written, so confirm the logos and colors with each company before a
public release.

## Tests

`frontend/src/lib/branding.test.tsx` asserts WCAG contrast (text, secondary text, brand, buttons, badges and
navigation) of every company palette in light and dark, that the three identities differ, and that unknown slugs
get none. `theme.test.tsx` covers the preference, `CentralApp.test.tsx` the identity across selection, switching,
sign-out and expiry. `components/themeSurfaces.test.ts` keeps hard-coded white surfaces out of the central pages.

Rendered review (2026-10-20, development preview with disposable data, a Super Admin account): the company picker,
the three company headers and the Members panel in light and dark, and a narrow (375 px) viewport, were looked at
in a browser. The account-changed and no-access notices, the board and card views (the preview companies had no
board) and the neutral screens in a dark OS setting were not reviewed.
