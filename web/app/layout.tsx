import type { Metadata } from "next";
import { AppShell } from "@/components/app-shell";
import "./globals.css";
import { Inter } from "next/font/google";
import { cn } from "@/lib/utils";

const inter = Inter({ subsets: ["latin", "cyrillic"], variable: "--font-sans" });

export const metadata: Metadata = {
  title: "track-seeds",
  description: "Семена в дробилках: камеры, тревоги, уведомления",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru" className={cn("font-sans", inter.variable)}>
      <body className="antialiased">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
