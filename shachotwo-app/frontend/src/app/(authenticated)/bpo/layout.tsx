"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Button } from "@/components/ui/button";

const bpoMenuItems = [
  { href: "/bpo", label: "一覧" },
  { href: "/bpo/estimation", label: "積算（建設）" },
  { href: "/bpo/sites", label: "現場管理" },
  { href: "/bpo/workers", label: "作業員管理" },
  { href: "/bpo/contracts", label: "出来高・請求" },
  { href: "/bpo/costs", label: "原価管理" },
  { href: "/bpo/manufacturing", label: "見積（製造業）" },
];

export default function BPOLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="space-y-4">
      {/* BPOサブナビ */}
      <div className="flex flex-wrap gap-2 border-b pb-3">
        {bpoMenuItems.map((item) => {
          const isActive =
            item.href === "/bpo"
              ? pathname === "/bpo"
              : pathname.startsWith(item.href);
          return (
            <Link key={item.href} href={item.href}>
              <Button
                variant={isActive ? "default" : "outline"}
                size="sm"
              >
                {item.label}
              </Button>
            </Link>
          );
        })}
      </div>

      {children}
    </div>
  );
}
