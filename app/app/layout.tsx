import "../styles/globals.css";
import type { Metadata } from "next";
import type { ReactNode } from "react";
import AuthToolbar from "./components/AuthToolbar";

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
      <body className="min-h-screen text-slate-900 m-0 p-0">
        <AuthToolbar />
        <main className="w-full h-full">{children}</main>
      </body>
    </html>
  );
}
