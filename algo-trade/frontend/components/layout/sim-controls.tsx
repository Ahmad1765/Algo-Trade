"use client";

import { useState, useEffect, useCallback } from "react";
import { Pause, Play } from "lucide-react";
import { cn } from "@/lib/utils";
import { api, type SimStatus } from "@/lib/api";

const SPEEDS: { label: string; value: number }[] = [
  { label: "1x", value: 1 },
  { label: "10x", value: 10 },
  { label: "60x", value: 60 },
  { label: "Max", value: 600 },
];

export function SimControls() {
  const [sim, setSim] = useState<SimStatus | null>(null);

  const poll = useCallback(async () => {
    try {
      const s = await api.simStatus();
      setSim(s.active ? s : null);
    } catch {
      setSim(null);
    }
  }, []);

  useEffect(() => {
    poll();
    const t = setInterval(poll, 1000);
    return () => clearInterval(t);
  }, [poll]);

  if (!sim || !sim.active) return null;

  async function control(action: "pause" | "resume" | "set_speed", speed?: number) {
    try {
      const s = await api.simControl(action, speed);
      setSim(s);
    } catch {
      /* backend unreachable — next poll recovers */
    }
  }

  return (
    <div className="hidden md:flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-indigo-500/10 border border-indigo-500/25">
      <span className="text-[11px] font-medium text-indigo-300 tabular-nums">
        SIM {sim.sim_time ?? ""}
      </span>
      <button
        onClick={() => control(sim.paused ? "resume" : "pause")}
        className="flex items-center justify-center w-6 h-6 rounded-md text-indigo-200 hover:bg-indigo-500/20 transition-colors"
        aria-label={sim.paused ? "Resume" : "Pause"}
      >
        {sim.paused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
      </button>
      {SPEEDS.map((s) => (
        <button
          key={s.value}
          onClick={() => control("set_speed", s.value)}
          className={cn(
            "px-1.5 py-0.5 rounded text-[11px] font-medium transition-colors",
            sim.speed === s.value
              ? "bg-indigo-500/30 text-indigo-100"
              : "text-indigo-300/70 hover:bg-indigo-500/20"
          )}
        >
          {s.label}
        </button>
      ))}
    </div>
  );
}
