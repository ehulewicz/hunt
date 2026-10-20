"use client";

import {
  Briefcase,
  Database,
  LayoutDashboard,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { AccountMenu } from "@/components/account-menu";
import type { Account } from "@/lib/api";

export function AppShell({
  account,
  onSignOut,
  children,
}: {
  account: Account;
  onSignOut: () => void;
  children: ReactNode;
}) {
  const pathname = usePathname();
  return (
    <div className="app-layout">
      <aside className="sidebar">
        <Link aria-label="Personal OS dashboard" className="sidebar-brand" href="/dashboard">
          <span className="brand-mark brand-mark-small"><Database aria-hidden="true" /></span>
          <div><strong>Personal OS</strong><span>Hosted workspace</span></div>
        </Link>
        <nav aria-label="Primary navigation">
          <Link aria-current={pathname === "/dashboard" ? "page" : undefined} className={pathname === "/dashboard" ? "nav-link active" : "nav-link"} href="/dashboard">
            <LayoutDashboard aria-hidden="true" /><span>Dashboard</span>
          </Link>
          <Link aria-current={pathname === "/hunt" ? "page" : undefined} className={pathname === "/hunt" ? "nav-link active" : "nav-link"} href="/hunt">
            <Briefcase aria-hidden="true" /><span>Hunt</span>
          </Link>
        </nav>
        <div className="sidebar-spacer" />
        <div className="account-block"><AccountMenu account={account} onSignOut={onSignOut} /></div>
      </aside>
      <main className="main-content">{children}</main>
    </div>
  );
}
