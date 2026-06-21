import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { AppShell } from "@/components/providers";
import "./globals.css";

const sans = Geist({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

const mono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  metadataBase: new URL("https://algotrade.app"),
  title: {
    default: "AlgoTrade — Options trading dashboard",
    template: "%s · AlgoTrade",
  },
  description:
    "Live algorithmic options trading: signals, positions, risk limits and P&L in one console.",
  applicationName: "AlgoTrade",
  openGraph: {
    title: "AlgoTrade — Options trading dashboard",
    description:
      "Live algorithmic options trading: signals, positions, risk limits and P&L in one console.",
    type: "website",
  },
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: "#09090b",
  colorScheme: "dark",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`dark ${sans.variable} ${mono.variable}`}>
      <body className="antialiased bg-zinc-950 text-zinc-100">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
