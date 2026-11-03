import type { Metadata } from "next";
import Nav from "@/components/Nav";
import Providers from "@/components/Providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "ConfluenceAI — Crypto Market Intelligence",
  description: "Explainable, continuously-operating crypto market research and decision support.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="flex h-screen flex-col overflow-hidden">
        <Providers>
          <Nav />
          <main className="min-h-0 flex-1 overflow-hidden">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
