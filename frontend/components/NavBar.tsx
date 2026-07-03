"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { api } from "@/lib/api";
import { AccountSwitcher } from "@/components/AccountSwitcher";

const links = [
  { href: "/", label: "Dashboard" },
  { href: "/trade", label: "Trade" },
  { href: "/orders", label: "Orders" },
  { href: "/journal", label: "Journal" },
  { href: "/strategies", label: "Strategies" },
];

export function NavBar() {
  const pathname = usePathname();
  return (
    <header className="border-b border-gray-800 bg-gray-900">
      <nav className="mx-auto flex max-w-7xl items-center gap-1 px-4 py-2">
        <span className="mr-4 font-semibold text-gray-100">Paper Trading</span>
        {links.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className={`rounded px-3 py-1.5 text-sm ${
              pathname === l.href
                ? "bg-gray-800 text-white"
                : "text-gray-400 hover:text-gray-200"
            }`}
          >
            {l.label}
          </Link>
        ))}
        <div className="ml-auto flex items-center gap-2">
          <AccountSwitcher />
          <button
            onClick={() => {
              void api.logout().then(() => {
                window.location.href = "/login";
              });
            }}
            className="rounded px-3 py-1.5 text-sm text-gray-400 hover:text-gray-200"
          >
            Log out
          </button>
        </div>
      </nav>
    </header>
  );
}
