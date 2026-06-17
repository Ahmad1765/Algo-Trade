"use client";

import { useState } from "react";
import * as Tabs from "@radix-ui/react-tabs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { cn, formatCurrency } from "@/lib/utils";
import type { ToastData } from "@/components/ui/toast";
import { api } from "@/lib/api";

interface OrderPanelProps {
  onToast: (t: Omit<ToastData, "id">) => void;
}

type Side = "buy" | "sell";
type OrderType = "market" | "limit";

interface FormState {
  symbol: string;
  qty: string;
  price: string;
  orderType: OrderType;
}

const INITIAL: FormState = { symbol: "AAPL", qty: "", price: "", orderType: "market" };

function validate(form: FormState, side: Side): string | null {
  if (!form.symbol.trim()) return "Symbol is required";
  if (!form.qty || isNaN(Number(form.qty)) || Number(form.qty) <= 0)
    return "Enter a valid quantity";
  if (form.orderType === "limit") {
    if (!form.price || isNaN(Number(form.price)) || Number(form.price) <= 0)
      return "Enter a valid limit price";
  }
  return null;
}

export function OrderPanel({ onToast }: OrderPanelProps) {
  const [side, setSide] = useState<Side>("buy");
  const [form, setForm] = useState<FormState>(INITIAL);
  const [error, setError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [loading, setLoading] = useState(false);

  const estimatedCost = form.qty && form.price
    ? Number(form.qty) * Number(form.price)
    : null;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const err = validate(form, side);
    if (err) { setError(err); return; }
    setError(null);
    setConfirmOpen(true);
  }

  async function handleConfirm() {
    setLoading(true);
    try {
      const res = await api.placeOrder({
        symbol: form.symbol,
        side,
        qty: Number(form.qty),
        ...(form.orderType === "limit" ? { price: Number(form.price) } : {}),
        orderType: form.orderType,
      });
      setConfirmOpen(false);
      const submittedQty = form.qty;
      const submittedSymbol = form.symbol;
      setForm(INITIAL);
      if (res.ok) {
        onToast({
          type: "success",
          message: `${side === "buy" ? "Buy" : "Sell"} order for ${submittedQty} ${submittedSymbol} submitted`,
        });
      } else {
        onToast({ type: "error", message: res.error ?? "Order failed." });
      }
    } catch {
      setConfirmOpen(false);
      onToast({ type: "error", message: "Cannot reach backend." });
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Place Order</CardTitle>
        </CardHeader>
        <CardContent className="pt-3">
          {/* Buy / Sell tabs */}
          <div className="flex rounded-lg border border-zinc-800 p-0.5 mb-4">
            {(["buy", "sell"] as Side[]).map((s) => (
              <button
                key={s}
                onClick={() => setSide(s)}
                className={cn(
                  "flex-1 py-1.5 text-sm font-medium rounded-md transition-colors duration-150 capitalize",
                  side === s
                    ? s === "buy"
                      ? "bg-emerald-500/15 text-emerald-400"
                      : "bg-red-500/15 text-red-400"
                    : "text-zinc-500 hover:text-zinc-300"
                )}
              >
                {s}
              </button>
            ))}
          </div>

          <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-3">
            <Input
              label="Symbol"
              value={form.symbol}
              onChange={(e) => setForm((f) => ({ ...f, symbol: e.target.value.toUpperCase() }))}
              placeholder="e.g. AAPL"
              autoComplete="off"
            />

            {/* Order type */}
            <div className="flex flex-col gap-1.5">
              <span className="text-xs font-medium text-zinc-400">Order Type</span>
              <div className="flex gap-2">
                {(["market", "limit"] as OrderType[]).map((t) => (
                  <button
                    key={t}
                    type="button"
                    onClick={() => setForm((f) => ({ ...f, orderType: t }))}
                    className={cn(
                      "flex-1 py-1.5 text-xs rounded-lg border transition-colors duration-150 capitalize",
                      form.orderType === t
                        ? "border-zinc-600 bg-zinc-800 text-zinc-100 font-medium"
                        : "border-zinc-800 text-zinc-500 hover:text-zinc-300 hover:border-zinc-700"
                    )}
                  >
                    {t}
                  </button>
                ))}
              </div>
            </div>

            <Input
              label="Quantity"
              type="number"
              min="1"
              step="1"
              placeholder="0"
              suffix="shares"
              value={form.qty}
              onChange={(e) => setForm((f) => ({ ...f, qty: e.target.value }))}
            />

            {form.orderType === "limit" && (
              <Input
                label="Limit Price"
                type="number"
                min="0.01"
                step="0.01"
                placeholder="0.00"
                prefix="$"
                value={form.price}
                onChange={(e) => setForm((f) => ({ ...f, price: e.target.value }))}
              />
            )}

            {estimatedCost !== null && (
              <div className="flex items-center justify-between text-xs text-zinc-500 px-1">
                <span>Estimated cost</span>
                <span className="text-zinc-300 tabular-nums font-medium">
                  {formatCurrency(estimatedCost)}
                </span>
              </div>
            )}

            {error && (
              <p role="alert" className="text-xs text-red-400 flex items-center gap-1.5">
                <span className="w-1 h-1 rounded-full bg-red-400 shrink-0" aria-hidden />
                {error}
              </p>
            )}

            <Button
              type="submit"
              variant={side === "buy" ? "primary" : "danger"}
              size="lg"
              className={cn(
                "mt-1 w-full",
                side === "sell" &&
                  "bg-red-500 hover:bg-red-400 text-white border-none"
              )}
            >
              {side === "buy" ? "Review Buy Order" : "Review Sell Order"}
            </Button>
          </form>
        </CardContent>
      </Card>

      {/* Confirmation modal */}
      <Modal
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={`Confirm ${side === "buy" ? "Buy" : "Sell"} Order`}
        description="Please review your order before submitting."
      >
        <div className="space-y-3 mb-5">
          <Row label="Symbol" value={form.symbol} />
          <Row label="Side" value={<span className={cn("font-medium capitalize", side === "buy" ? "text-emerald-400" : "text-red-400")}>{side}</span>} />
          <Row label="Type" value={<span className="capitalize">{form.orderType}</span>} />
          <Row label="Quantity" value={`${form.qty} shares`} />
          {form.orderType === "limit" && <Row label="Limit Price" value={`$${Number(form.price).toFixed(2)}`} />}
          {estimatedCost !== null && <Row label="Est. Total" value={formatCurrency(estimatedCost)} highlight />}
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" className="flex-1" onClick={() => setConfirmOpen(false)}>
            Cancel
          </Button>
          <Button
            variant={side === "buy" ? "primary" : "danger"}
            className={cn("flex-1", side === "sell" && "bg-red-500 hover:bg-red-400 text-white border-none")}
            loading={loading}
            onClick={handleConfirm}
          >
            Confirm {side === "buy" ? "Buy" : "Sell"}
          </Button>
        </div>
      </Modal>
    </>
  );
}

function Row({
  label,
  value,
  highlight = false,
}: {
  label: string;
  value: React.ReactNode;
  highlight?: boolean;
}) {
  return (
    <div className="flex items-center justify-between text-sm border-b border-zinc-800 pb-2 last:border-0">
      <span className="text-zinc-400">{label}</span>
      <span className={cn("text-zinc-100 tabular-nums", highlight && "font-semibold")}>{value}</span>
    </div>
  );
}
