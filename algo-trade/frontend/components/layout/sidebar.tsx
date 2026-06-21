"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  LayoutDashboard,
  Zap,
  Briefcase,
  BarChart2,
  FlaskConical,
  Settings,
  ScrollText,
  MonitorDot,
} from "lucide-react";
import { cn } from "@/lib/utils";

const navItems = [
  { href: "/dashboard",  icon: LayoutDashboard, label: "Dashboard",   sub: "Overview & portfolio" },
  { href: "/signals",    icon: Zap,             label: "Signals",     sub: "Buy / sell signals" },
  { href: "/positions",  icon: Briefcase,        label: "Positions",   sub: "Open & closed trades" },
  { href: "/status",     icon: MonitorDot,       label: "Status",      sub: "P&L & activity log" },
  { href: "/strategies", icon: BarChart2,        label: "Strategies",  sub: "Strategy performance" },
  { href: "/backtest",   icon: FlaskConical,     label: "Backtest",    sub: "Test a strategy" },
  { href: "/settings",   icon: Settings,         label: "Settings",    sub: "Broker, SMTP & keys" },
];

export function Sidebar() {
  const pathname = usePathname() ?? "";

  return (
    <aside className="w-52 shrink-0 bg-zinc-950 border-r border-zinc-800/60 flex flex-col py-4 min-h-screen">
      {/* Brand */}
      <Link href="/dashboard" className="flex items-center gap-3 px-4 mb-6">
        <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-emerald-500/10 shrink-0">
          <Activity className="w-5 h-5 text-emerald-400" />
        </div>
        <div className="min-w-0">
          <p className="text-sm font-semibold text-zinc-100 leading-none">AlgoTrade</p>
          <p className="text-[11px] text-zinc-500 leading-none mt-0.5">Options System</p>
        </div>
      </Link>

      {/* Main nav */}
      <nav className="flex flex-col gap-0.5 flex-1 px-2" aria-label="Main navigation">
        <p className="px-3 mb-1 text-[10px] font-semibold uppercase tracking-widest text-zinc-600">
          Menu
        </p>
        {navItems.map(({ href, icon: Icon, label, sub }) => {
          const active = pathname === href || pathname.startsWith(href + "/");
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "group relative flex items-center gap-3 px-3 py-2.5 rounded-xl transition-colors duration-150",
                active
                  ? "bg-emerald-500/10 text-emerald-400 before:absolute before:left-0 before:top-1/2 before:-translate-y-1/2 before:h-5 before:w-0.5 before:rounded-full before:bg-emerald-400"
                  : "text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800/80"
              )}
            >
              <Icon className="w-[17px] h-[17px] shrink-0" aria-hidden />
              <div className="min-w-0">
                <p className={cn("text-sm font-medium leading-none", active ? "text-emerald-400" : "")}>
                  {label}
                </p>
                <p className={cn("text-[11px] leading-none mt-0.5 truncate", active ? "text-emerald-600" : "text-zinc-600")}>
                  {sub}
                </p>
              </div>
            </Link>
          );
        })}
      </nav>

    </aside>
  );
}
