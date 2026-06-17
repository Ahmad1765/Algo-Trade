"use client";

import { useEffect, useState, useCallback } from "react";
import {
  PieChart,
  Pie,
  Cell,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { TrendingUp, TrendingDown } from "lucide-react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { cn, formatCurrency, formatPercent } from "@/lib/utils";
import { api, type Position } from "@/lib/api";

const PALETTE = [
  "#10b981", "#3b82f6", "#f59e0b", "#8b5cf6",
  "#ef4444", "#06b6d4", "#f97316", "#ec4899",
];

interface EnrichedRow {
  symbol: string;
  option_symbol: string;
  direction: "CALL" | "PUT";
  cost_basis: number;
  unrealized_pnl: number | null;
  color: string;
}

interface PortfolioSummaryProps {
  masked?: boolean;
}

export function PortfolioSummary({ masked = false }: PortfolioSummaryProps) {
  const [rows, setRows]               = useState<EnrichedRow[]>([]);
  const [totalCost, setTotalCost]     = useState(0);
  const [offline, setOffline]         = useState(false);
  const [loading, setLoading]         = useState(true);

  const loadPositions = useCallback(async () => {
    try {
      const res = await api.positions();
      const entries = Object.values(res.open_positions).map((p: Position, i) => ({
        symbol:         p.symbol,
        option_symbol:  p.option_symbol,
        direction:      p.direction,
        cost_basis:     p.cost_basis ?? p.entry_price * p.quantity * 100,
        unrealized_pnl: p.unrealized_pnl,
        color:          PALETTE[i % PALETTE.length],
      }));
      setRows(entries);
      setTotalCost(res.total_cost_basis);
      setOffline(false);
    } catch {
      setOffline(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadPositions();
    const id = setInterval(loadPositions, 30_000);
    return () => clearInterval(id);
  }, [loadPositions]);

  const totalPnl = rows.reduce((s, r) => s + (r.unrealized_pnl ?? 0), 0);
  const totalPct = totalCost > 0 ? (totalPnl / totalCost) * 100 : 0;
  const pieData  = rows.map((r) => ({ name: r.symbol, value: r.cost_basis, color: r.color }));

  if (loading) {
    return (
      <Card>
        <CardHeader><CardTitle>Portfolio</CardTitle></CardHeader>
        <CardContent className="pt-3 space-y-2">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="h-8 rounded-lg bg-zinc-800/60 animate-pulse" />
          ))}
        </CardContent>
      </Card>
    );
  }

  if (offline || rows.length === 0) {
    return (
      <Card>
        <CardHeader><CardTitle>Portfolio</CardTitle></CardHeader>
        <CardContent className="pt-3">
          <div className="flex flex-col items-center justify-center py-8 gap-2 text-center">
            {offline ? (
              <p className="text-sm text-amber-400">
                Backend offline — start the trading engine to see positions.
              </p>
            ) : (
              <>
                <p className="text-sm font-medium text-zinc-400">No open positions</p>
                <p className="text-xs text-zinc-600 max-w-xs">
                  Positions appear here once the screener generates signals
                  during market hours (9:30 AM – 4:00 PM ET).
                </p>
              </>
            )}
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Portfolio</CardTitle>
        <span className={cn(
          "text-xs font-medium tabular-nums",
          totalPnl > 0 ? "text-emerald-400" : totalPnl < 0 ? "text-red-400" : "text-zinc-500",
        )}>
          {masked ? "••••" : totalPnl === 0 ? "P&L pending" : `${totalPnl >= 0 ? "+" : ""}${formatPercent(totalPct)}`}
        </span>
      </CardHeader>
      <CardContent className="pt-3">
        {/* Summary row */}
        <div className="grid grid-cols-3 gap-3 mb-4">
          <SummaryItem label="Cost Basis"   value={formatCurrency(totalCost, { masked })} />
          <SummaryItem label="Positions"    value={String(rows.length)} />
          <SummaryItem
            label="Unrealized P&L"
            value={totalPnl === 0 ? "—" : formatCurrency(Math.abs(totalPnl), { masked })}
            className={totalPnl === 0 ? "text-zinc-500" : totalPnl > 0 ? "text-emerald-400" : "text-red-400"}
            prefix={masked || totalPnl === 0 ? "" : totalPnl > 0 ? "+" : "-"}
          />
        </div>

        {/* Allocation chart + rows */}
        <div className="flex gap-4 items-start">
          <div className="w-[90px] h-[90px] shrink-0">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={pieData}
                  cx="50%"
                  cy="50%"
                  innerRadius={26}
                  outerRadius={42}
                  paddingAngle={2}
                  dataKey="value"
                  strokeWidth={0}
                >
                  {pieData.map((entry, i) => (
                    <Cell key={i} fill={entry.color} opacity={0.9} />
                  ))}
                </Pie>
                <Tooltip
                  content={({ active, payload }) =>
                    active && payload?.length ? (
                      <div className="rounded-lg border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 shadow-xl text-xs text-zinc-200">
                        {payload[0].name}: {formatCurrency(payload[0].value as number, { masked })}
                      </div>
                    ) : null
                  }
                />
              </PieChart>
            </ResponsiveContainer>
          </div>

          <div className="flex-1 min-w-0 space-y-2">
            {rows.map((r) => (
              <div key={r.option_symbol} className="flex items-center gap-2">
                <span
                  className="w-1.5 h-1.5 rounded-full shrink-0"
                  style={{ backgroundColor: r.color }}
                  aria-hidden
                />
                <span className="flex items-center gap-1 text-xs font-medium text-zinc-300 w-14 shrink-0">
                  {r.symbol}
                  {r.direction === "CALL"
                    ? <TrendingUp  className="w-3 h-3 text-emerald-500" aria-label="CALL" />
                    : <TrendingDown className="w-3 h-3 text-red-500"    aria-label="PUT"  />
                  }
                </span>
                <div className="flex-1 min-w-0">
                  <div
                    className="h-1 rounded-full bg-zinc-800"
                    role="progressbar"
                    aria-valuenow={totalCost > 0 ? Math.round((r.cost_basis / totalCost) * 100) : 0}
                    aria-valuemin={0}
                    aria-valuemax={100}
                  >
                    <div
                      className="h-1 rounded-full transition-all"
                      style={{
                        width: totalCost > 0 ? `${(r.cost_basis / totalCost) * 100}%` : "0%",
                        backgroundColor: r.color,
                        opacity: 0.8,
                      }}
                    />
                  </div>
                </div>
                <span className={cn(
                  "text-xs tabular-nums font-medium w-14 text-right shrink-0",
                  r.unrealized_pnl === null
                    ? "text-zinc-500"
                    : r.unrealized_pnl >= 0 ? "text-emerald-400" : "text-red-400",
                )}>
                  {masked
                    ? "••••"
                    : r.unrealized_pnl === null
                      ? "—"
                      : formatPercent(r.cost_basis > 0 ? (r.unrealized_pnl / r.cost_basis) * 100 : 0)}
                </span>
              </div>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function SummaryItem({
  label,
  value,
  className,
  prefix,
}: {
  label: string;
  value: string;
  className?: string;
  prefix?: string;
}) {
  return (
    <div className="rounded-lg bg-zinc-800/40 px-3 py-2">
      <p className="text-xs text-zinc-500 mb-0.5 truncate">{label}</p>
      <p className={cn("text-sm font-semibold text-zinc-100 tabular-nums truncate", className)}>
        {prefix}{value}
      </p>
    </div>
  );
}
