"use client";

import { useState, useEffect, useCallback } from "react";
import { Play, Square, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { api, type SimStatus } from "@/lib/api";

const SPEEDS = [
  { label: "1x", value: 1 },
  { label: "10x", value: 10 },
  { label: "60x", value: 60 },
  { label: "Max", value: 600 },
];

export function SimLauncher() {
  const [sim, setSim] = useState<SimStatus | null>(null);
  const [date, setDate] = useState("2026-06-17");
  const [speed, setSpeed] = useState(60);
  const [err, setErr] = useState<string | null>(null);

  const poll = useCallback(async () => {
    try {
      setSim(await api.simStatus());
    } catch {
      setSim(null);
    }
  }, []);

  useEffect(() => {
    poll();
    const t = setInterval(poll, 1000);
    return () => clearInterval(t);
  }, [poll]);

  const state = sim?.state ?? "idle";

  async function start() {
    setErr(null);
    try {
      setSim(await api.simStart(date, speed));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to start");
    }
  }
  async function stop() {
    setErr(null);
    try {
      setSim(await api.simStop());
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to stop");
    }
  }

  return (
    <div className={cn("hidden md:flex items-center gap-1.5")}>
      {state === "idle" || state === "error" ? (
        <>
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="px-2 py-1 rounded-md bg-zinc-800/60 border border-zinc-700/40 text-xs text-zinc-200"
          />
          <select
            value={speed}
            onChange={(e) => setSpeed(Number(e.target.value))}
            className="px-2 py-1 rounded-md bg-zinc-800/60 border border-zinc-700/40 text-xs text-zinc-200"
          >
            {SPEEDS.map((s) => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>
          <button
            onClick={start}
            className="flex items-center gap-1 px-2.5 py-1 rounded-md bg-emerald-500/15 border border-emerald-500/30 text-xs text-emerald-300 hover:bg-emerald-500/25"
          >
            <Play className="w-3.5 h-3.5" /> Start Sim
          </button>
          {(err || (state === "error" && sim?.error)) && (
            <span className="text-[11px] text-rose-400 max-w-[180px] truncate">
              {err || sim?.error}
            </span>
          )}
        </>
      ) : state === "loading" ? (
        <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-indigo-500/10 border border-indigo-500/25 text-xs text-indigo-300">
          <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading market data…
        </span>
      ) : (
        <button
          onClick={stop}
          className="flex items-center gap-1 px-2.5 py-1 rounded-md bg-rose-500/15 border border-rose-500/30 text-xs text-rose-300 hover:bg-rose-500/25"
        >
          <Square className="w-3.5 h-3.5" /> Stop Sim
        </button>
      )}
    </div>
  );
}
