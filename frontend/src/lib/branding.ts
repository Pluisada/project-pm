/**
 * Company visual identity. Presentation only: it never decides access or which database is used.
 *
 * The colors live in globals.css as semantic tokens under :root[data-company="<slug>"]; this module holds
 * the logos and applies the attribute. An unknown or missing company gets no attribute, i.e. the neutral
 * palette, never another company's.
 */
import { useEffect } from "react";

export interface CompanyBrand {
  slug: string;
  name: string;
  logo: { src: string; width: number; height: number };
}

const BRANDS: Record<string, CompanyBrand> = {
  salcomp: { slug: "salcomp", name: "Salcomp", logo: { src: "/brand/salcomp-logo.png", width: 576, height: 88 } },
  "valor-log": { slug: "valor-log", name: "Valor-Log", logo: { src: "/brand/valorlog-wordmark.png", width: 480, height: 52 } },
  lity: { slug: "lity", name: "Lity", logo: { src: "/brand/lity-logo.webp", width: 153, height: 40 } },
};

export const BRAND_ATTRIBUTE = "data-company";

export function brandFor(slug: string | null | undefined): CompanyBrand | null {
  return slug && Object.prototype.hasOwnProperty.call(BRANDS, slug) ? BRANDS[slug] : null;
}

/** Sets the active company's identity on <html>, or clears it (neutral) for none or an unknown slug. */
export function useCompanyBranding(slug: string | null | undefined): void {
  const brand = brandFor(slug);
  useEffect(() => {
    const root = document.documentElement;
    if (brand) root.setAttribute(BRAND_ATTRIBUTE, brand.slug);
    else root.removeAttribute(BRAND_ATTRIBUTE);
    return () => root.removeAttribute(BRAND_ATTRIBUTE);
  }, [brand]);
}
