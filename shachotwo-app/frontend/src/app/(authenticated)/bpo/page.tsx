"use client";

import { Suspense } from "react";
import { BPODashboardPageInner } from "./bpo-dashboard-content";

function BPODashboardFallback() {
  return (
    <div className="space-y-8">
      <div className="h-8 w-48 animate-pulse rounded-md bg-muted" />
      <div className="h-10 w-full max-w-md animate-pulse rounded-md bg-muted" />
      <div className="h-9 w-full animate-pulse rounded-md bg-muted" />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="h-48 animate-pulse rounded-lg bg-muted" />
        ))}
      </div>
    </div>
  );
}

/** route segment の default export はこのファイルだけに固定（HMR 安定化） */
export default function BpoDashboardPage() {
  return (
    <Suspense fallback={<BPODashboardFallback />}>
      <BPODashboardPageInner />
    </Suspense>
  );
}
