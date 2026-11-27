import type { Metadata } from "next";
import { Inter } from "next/font/google";
import Sidebar, { TopBar } from "@/components/Nav";
import Providers from "@/components/Providers";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });

export const metadata: Metadata = {
  title: "ConfluenceAI — Crypto Market Intelligence",
  description: "Explainable, continuously-operating crypto market research and decision support.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={inter.variable}>
      <body className="flex h-screen overflow-hidden">
        <Providers>
          <Sidebar />
          <div className="flex min-w-0 flex-1 flex-col">
            <TopBar />
            <main className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden">{children}</main>
          </div>
        </Providers>
      </body>
    </html>
  );
}
