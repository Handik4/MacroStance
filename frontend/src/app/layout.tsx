import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "MacroStance",
  description:
    "Autonomous macroeconomic policy oracle and DeFi risk controller on GenLayer: three independent feeds, one deterministic stance, a 24-hour timelock.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-paper text-slate-800">{children}</body>
    </html>
  );
}
