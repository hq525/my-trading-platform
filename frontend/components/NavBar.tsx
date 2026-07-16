"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { api } from "@/lib/api";
import { AccountSwitcher } from "@/components/AccountSwitcher";

const paperLinks = [
  { href: "/", label: "Dashboard" },
  { href: "/trade", label: "Trade" },
  { href: "/orders", label: "Orders" },
  { href: "/journal", label: "Journal" },
  { href: "/strategies", label: "Strategies" },
];

const liveLinks = [
  { href: "/live", label: "Dashboard" },
  { href: "/live/trade", label: "Trade" },
  { href: "/live/orders", label: "Orders" },
];

const replayLinks = [{ href: "/replay", label: "Sessions" }];

const modeTab = (active: boolean) =>
  `px-3 py-1 ${active ? "bg-gray-800 text-white" : "text-gray-400 hover:text-gray-200"}`;

export function NavBar() {
  const pathname = usePathname();
  const live = pathname === "/live" || pathname.startsWith("/live/");
  const replay = pathname === "/replay" || pathname.startsWith("/replay/");
  const links = live ? liveLinks : replay ? replayLinks : paperLinks;
  return (
    <header className="border-b border-gray-800 bg-gray-900">
      <nav className="mx-auto flex max-w-7xl items-center gap-1 px-4 py-2">
        <span className="mr-2 font-semibold text-gray-100">Trading</span>
        {live && (
          <span className="mr-2 rounded bg-amber-600 px-1.5 py-0.5 text-[10px] font-bold text-black">
            LIVE
          </span>
        )}
        <div className="mr-4 flex overflow-hidden rounded border border-gray-700 text-sm">
          <Link href="/" className={modeTab(!live && !replay)}>
            Paper
          </Link>
          <Link href="/live" className={modeTab(live)}>
            Live
          </Link>
          <Link href="/replay" className={modeTab(replay)}>
            Replay
          </Link>
        </div>
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
          {!live && !replay && <AccountSwitcher />}
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
