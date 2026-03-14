import "../styles/globals.css";
import type { Metadata } from "next";
import type { ReactNode } from "react";
import AppLayout from "./components/AppLayout";

export const metadata: Metadata = {
  title: "GovBudgetChecker",
  description: "Automated validation for government budget disclosures",
};

export default function RootLayout({
  children,
}: {
  children: ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen bg-surface-50 text-slate-900 antialiased">
        <AppLayout>{children}</AppLayout>
      </body>
    </html>
  );
}
