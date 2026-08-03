"use client";

import {
  AppWindow,
  BookOpenText,
  Building2,
  Database,
  FileQuestion,
  FileText,
  LayoutDashboard,
  ScrollText,
  UserRound,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { AccountMenu } from "@/components/account-menu";
import type { Account } from "@/lib/api";

const jobsLinks = [
  { href: "/jobs/listings", label: "Listings", icon: Building2 },
  { href: "/jobs/applications", label: "Applications", icon: ScrollText },
  { href: "/jobs/resumes", label: "Resumes", icon: FileText },
  { href: "/jobs/cover-letters", label: "Cover Letters", icon: BookOpenText },
  { href: "/jobs/answers", label: "Answers", icon: FileQuestion },
  { href: "/jobs/profile", label: "Job Profile", icon: UserRound },
];

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
  const jobsActive = pathname.startsWith("/jobs");
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
          <div className="nav-group">
            <Link aria-current={jobsActive ? "page" : undefined} className={jobsActive ? "nav-link active" : "nav-link"} href="/jobs/listings">
              <AppWindow aria-hidden="true" /><span>Jobs</span>
            </Link>
            {jobsActive ? (
              <div className="nav-subgroup">
                {jobsLinks.map(({ href, label, icon: Icon }) => (
                  <Link aria-current={pathname === href ? "page" : undefined} className={pathname === href ? "nav-sublink active" : "nav-sublink"} href={href} key={href}>
                    <Icon aria-hidden="true" /><span>{label}</span>
                  </Link>
                ))}
              </div>
            ) : null}
          </div>
        </nav>
        <div className="sidebar-spacer" />
        <div className="account-block"><AccountMenu account={account} onSignOut={onSignOut} /></div>
      </aside>
      <main className="main-content">{children}</main>
    </div>
  );
}
