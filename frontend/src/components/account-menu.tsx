"use client";

import { Cable, ChevronRight, LogOut, Settings } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import type { Account } from "@/lib/api";

export function accountInitials(displayName: string): string {
  const parts = displayName.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  const letters = parts.length === 1 ? [parts[0][0]] : [parts[0][0], parts.at(-1)?.[0]];
  return letters.join("").toUpperCase();
}

export function AccountMenu({ account, onSignOut }: { account: Account; onSignOut: () => void }) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    function closeOutside(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setOpen(false);
      triggerRef.current?.focus();
    }
    document.addEventListener("mousedown", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  return (
    <div className="account-menu" ref={containerRef}>
      {open ? (
        <div className="account-menu-list" role="menu">
          <div className="account-identity">
            <strong>{account.display_name}</strong>
            <span>{account.email}</span>
          </div>
          <div className="account-menu-divider" />
          <Link className="account-menu-item" href="/settings" onClick={() => setOpen(false)} role="menuitem">
            <Settings aria-hidden="true" /> Settings
          </Link>
          <Link className="account-menu-item" href="/integrations" onClick={() => setOpen(false)} role="menuitem">
            <Cable aria-hidden="true" /> Integrations
          </Link>
          <div className="account-menu-divider" />
          <button className="account-menu-item" onClick={onSignOut} role="menuitem" type="button">
            <LogOut aria-hidden="true" /> Log out
          </button>
        </div>
      ) : null}
      <button
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label={`Account: ${account.display_name}`}
        className="account-trigger"
        onClick={() => setOpen((current) => !current)}
        ref={triggerRef}
        type="button"
      >
        <span aria-hidden="true" className="account-avatar">{accountInitials(account.display_name)}</span>
        <span className="account-name">{account.display_name}</span>
        <ChevronRight aria-hidden="true" className="account-chevron" />
      </button>
    </div>
  );
}
