"use client";

import {
  Briefcase,
  Database,
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
        <Link aria-label="Personal OS home" className="sidebar-brand" href="/hunt">
          <span className="brand-mark brand-mark-small"><Database aria-hidden="true" /></span>
          <div className="rail-label"><strong>Personal OS</strong></div>
        </Link>
        <nav aria-label="Primary navigation">
          <Link aria-current={pathname === "/hunt" ? "page" : undefined} aria-label="Hunt" className={pathname === "/hunt" ? "nav-link active" : "nav-link"} href="/hunt" title="Hunt">
            <Briefcase aria-hidden="true" /><span className="rail-label">Hunt</span>
          </Link>
        </nav>
        <div className="sidebar-spacer" />
        <div className="account-block"><AccountMenu account={account} onSignOut={onSignOut} /></div>
      </aside>
      <main className={pathname === "/hunt" ? "main-content main-content-hunt" : "main-content"}>{children}</main>
    </div>
  );
}
