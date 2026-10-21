import type { Metadata } from "next";
import { Lato, Manrope, Poppins, Raleway, Space_Grotesk } from "next/font/google";
import "./globals.css";

const displayFont = Space_Grotesk({
  variable: "--font-display",
  subsets: ["latin"],
});

const bodyFont = Manrope({
  variable: "--font-body",
  subsets: ["latin"],
});

// Company heading faces (observed on each company's site), used only while that company is active.
// Raleway is a variable font: giving it a weight list makes `next dev` (Turbopack) fail with "queries have exactly one
// entry" and answer 500 on every page, so it is declared without one. Lato and Poppins are static and need theirs.
const salcompFont = Raleway({ variable: "--font-raleway", subsets: ["latin"], preload: false });
const lityFont = Lato({ variable: "--font-lato", subsets: ["latin"], weight: ["700", "900"], preload: false });
const valorLogFont = Poppins({ variable: "--font-poppins", subsets: ["latin"], weight: ["600", "700"], preload: false });

export const metadata: Metadata = {
  title: "Kanban Studio",
  description: "A focused, single-board kanban workspace.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${displayFont.variable} ${bodyFont.variable} ${salcompFont.variable} ${lityFont.variable} ${valorLogFont.variable}`}>
        {children}
      </body>
    </html>
  );
}
