"use client";

// Prevent static prerendering — avoids a workUnitAsyncStorage invariant in Next.js 16.1.6
export const dynamic = "force-dynamic";

export default function GlobalError({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en" className="dark">
      <body
        style={{
          display: "flex",
          minHeight: "100dvh",
          alignItems: "center",
          justifyContent: "center",
          background: "#09090b",
          color: "#f4f4f5",
          fontFamily: "var(--font-sans), ui-sans-serif, system-ui, sans-serif",
          margin: 0,
          padding: "24px",
        }}
      >
        <div style={{ textAlign: "center", maxWidth: "20rem" }}>
          <p style={{ margin: "0 0 6px", fontSize: "15px", fontWeight: 600, color: "#f4f4f5" }}>
            The dashboard hit an error
          </p>
          <p style={{ margin: "0 0 16px", fontSize: "13px", lineHeight: 1.5, color: "#a1a1aa" }}>
            This is on our side, not your data. Reloading usually clears it.
          </p>
          <button
            onClick={reset}
            style={{
              padding: "8px 16px",
              borderRadius: "8px",
              background: "#27272a",
              color: "#f4f4f5",
              border: "1px solid #3f3f46",
              cursor: "pointer",
              fontSize: "13px",
            }}
          >
            Try again
          </button>
        </div>
      </body>
    </html>
  );
}
