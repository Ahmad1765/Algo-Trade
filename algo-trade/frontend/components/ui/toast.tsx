"use client";

import { useEffect } from "react";
import { CheckCircle, XCircle, X } from "lucide-react";
import { cn } from "@/lib/utils";

export interface ToastData {
  id: string;
  type: "success" | "error";
  message: string;
}

interface ToastProps {
  toast: ToastData;
  onDismiss: (id: string) => void;
}

export function Toast({ toast, onDismiss }: ToastProps) {
  useEffect(() => {
    const timer = setTimeout(() => onDismiss(toast.id), 4000);
    return () => clearTimeout(timer);
  }, [toast.id, onDismiss]);

  return (
    <div
      className={cn(
        "flex items-start gap-3 rounded-xl border px-4 py-3 shadow-lg",
        "animate-in slide-in-from-bottom-2 fade-in duration-200",
        toast.type === "success"
          ? "border-emerald-500/20 bg-zinc-900"
          : "border-red-500/20 bg-zinc-900"
      )}
      role={toast.type === "error" ? "alert" : "status"}
    >
      {toast.type === "success" ? (
        <CheckCircle className="w-4 h-4 mt-0.5 shrink-0 text-emerald-400" />
      ) : (
        <XCircle className="w-4 h-4 mt-0.5 shrink-0 text-red-400" />
      )}
      <p className="text-sm text-zinc-200 flex-1">{toast.message}</p>
      <button
        onClick={() => onDismiss(toast.id)}
        className="ml-1 text-zinc-500 hover:text-zinc-300 transition-colors"
        aria-label="Dismiss"
      >
        <X className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}

interface ToastContainerProps {
  toasts: ToastData[];
  onDismiss: (id: string) => void;
}

export function ToastContainer({ toasts, onDismiss }: ToastContainerProps) {
  if (toasts.length === 0) return null;
  return (
    <div className="fixed bottom-5 right-5 z-[100] flex flex-col gap-2 w-80" aria-live="polite">
      {toasts.map((t) => (
        <Toast key={t.id} toast={t} onDismiss={onDismiss} />
      ))}
    </div>
  );
}
