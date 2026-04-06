"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { useRouter, usePathname, useSearchParams } from "next/navigation";
import Link from "next/link";
import {
  ApprovalIcon,
  BarChartIcon,
  BPOIcon,
  BrainIcon,
  BuildingIcon,
  ChevronDownIcon,
  ConnectorIcon,
  CRMCustomerIcon,
  CRMRequestIcon,
  CRMRevenueIcon,
  HomeIcon,
  LearningIcon,
  LightbulbIcon,
  ListIcon,
  LogOutIcon,
  MarketingABTestIcon,
  MarketingOutreachIcon,
  MenuIcon,
  MessageCircleQuestionIcon,
  PenLineIcon,
  SalesContractIcon,
  SalesForecastIcon,
  SalesLeadIcon,
  SalesPipelineIcon,
  SalesProposalIcon,
  SettingsIcon,
  SupportTicketIcon,
  TwinIcon,
  UpsellIcon,
  UsersIcon,
  XIcon,
} from "@/components/authenticated-layout-icons";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/hooks/use-auth";
import { OnboardingIndustryProvider, useOnboardingIndustry } from "@/hooks/use-onboarding-industry";
import { getBpoIndustryDisplayLabel } from "@/lib/bpo-industry-labels";
import {
  PendingApprovalsProvider,
  usePendingApprovals,
} from "@/hooks/use-pending-approvals";

const NPSModal = dynamic(
  () => import("@/components/nps-modal").then((m) => ({ default: m.NPSModal })),
  { ssr: false }
);

// --- ナビゲーション定義 ---

type NavItem = {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  tooltip?: string;
  /** true のとき遷移しない（業種未設定時の業種向けリンクなど） */
  disabled?: boolean;
};

type NavGroup = {
  key: string;
  label: string;
  items: NavItem[];
};

type NavCategory = {
  key: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  /** Brain / BPO は items のみ。OS は groups を使い items は空 */
  items: NavItem[];
  groups?: NavGroup[];
};

function categoryAllNavItems(cat: NavCategory): NavItem[] {
  if (cat.groups?.length) {
    return cat.groups.flatMap((g) => g.items);
  }
  return cat.items;
}

/** OS サブメニュー: テーマごとの枠・左アクセント・見出しドット（ライト/ダーク両対応） */
type OsGroupChrome = { block: string; dot: string };

const OS_GROUP_CHROME: Record<string, OsGroupChrome> = {
  marketing: {
    block:
      "space-y-0.5 rounded-md border border-border/60 bg-muted/25 py-1.5 pl-2 border-l-[3px] border-l-violet-500/55 dark:border-l-violet-400/50",
    dot: "bg-violet-500/70 dark:bg-violet-400/65",
  },
  sales: {
    block:
      "space-y-0.5 rounded-md border border-border/60 bg-muted/25 py-1.5 pl-2 border-l-[3px] border-l-blue-500/55 dark:border-l-blue-400/50",
    dot: "bg-blue-500/70 dark:bg-blue-400/65",
  },
  cs: {
    block:
      "space-y-0.5 rounded-md border border-border/60 bg-muted/25 py-1.5 pl-2 border-l-[3px] border-l-emerald-500/50 dark:border-l-emerald-400/45",
    dot: "bg-emerald-500/65 dark:bg-emerald-400/60",
  },
  support: {
    block:
      "space-y-0.5 rounded-md border border-border/60 bg-muted/25 py-1.5 pl-2 border-l-[3px] border-l-amber-500/55 dark:border-l-amber-400/50",
    dot: "bg-amber-500/70 dark:bg-amber-400/65",
  },
  backoffice: {
    block:
      "space-y-0.5 rounded-md border border-border/60 bg-muted/25 py-1.5 pl-2 border-l-[3px] border-l-slate-500/50 dark:border-l-slate-400/45",
    dot: "bg-slate-500/65 dark:bg-slate-400/55",
  },
};

function getOsGroupChrome(groupKey: string): OsGroupChrome {
  return (
    OS_GROUP_CHROME[groupKey] ?? {
      block: "space-y-0.5 rounded-md border border-border/50 bg-muted/15 py-1.5 pl-2",
      dot: "bg-muted-foreground/55",
    }
  );
}

function navItemPath(href: string): string {
  const i = href.indexOf("?");
  return i >= 0 ? href.slice(0, i) : href;
}

function itemMatchesPathForCategory(pathname: string, href: string): boolean {
  const path = navItemPath(href);
  return pathname === path || pathname.startsWith(`${path}/`);
}

function getActiveCategoryKey(categories: NavCategory[], pathname: string): string {
  for (const cat of categories) {
    for (const item of categoryAllNavItems(cat)) {
      if (itemMatchesPathForCategory(pathname, item.href)) {
        return cat.key;
      }
    }
  }
  for (const item of SETTINGS_ITEMS) {
    if (pathname === item.href || pathname.startsWith(`${item.href}/`)) {
      return "settings";
    }
  }
  return categories[0]?.key ?? "brain";
}

function isNavItemActive(
  href: string,
  pathname: string,
  searchParams: URLSearchParams
): boolean {
  const path = navItemPath(href);
  const qi = href.indexOf("?");
  const wantTab = qi >= 0 ? new URLSearchParams(href.slice(qi + 1)).get("tab") : null;

  if (path === "/bpo") {
    if (pathname !== "/bpo") return false;
    const cur = searchParams.get("tab");
    if (wantTab === null) {
      return cur !== "industry" && cur !== "common";
    }
    return cur === wantTab;
  }

  if (pathname === path) return true;
  if (pathname.startsWith(`${path}/`)) return true;
  return false;
}

function buildBpoNavItems(industryKey: string | null, industryLoading: boolean): NavItem[] {
  const label = getBpoIndustryDisplayLabel(industryKey);
  const industryDisabled = !industryLoading && !industryKey;

  return [
    { href: "/dashboard", label: "ホーム", icon: HomeIcon },
    {
      href: "/bpo",
      label: "自動化・一覧",
      icon: BPOIcon,
      tooltip: "業種向けと共通の自動化を一覧表示",
    },
    {
      href: "/bpo?tab=industry",
      label: label ? `自動化・${label}向け` : "自動化・業種向け",
      icon: BPOIcon,
      tooltip: industryDisabled
        ? "初期セットアップで業種を設定すると利用できます"
        : `${label ?? "自社業種"}向けのおすすめのみ表示`,
      disabled: industryDisabled,
    },
    {
      href: "/bpo?tab=common",
      label: "自動化・共通",
      icon: BPOIcon,
      tooltip: "全業種共通の自動化のみ表示",
    },
    {
      href: "/bpo/approvals",
      label: "承認フロー",
      icon: ApprovalIcon,
      tooltip: "AIが自動実行した業務の承認・却下",
    },
    {
      href: "/learning",
      label: "学習・改善",
      icon: LearningIcon,
      tooltip: "受注/失注分析・CS品質フィードバック",
    },
  ];
}

const BRAIN_CATEGORY: NavCategory = {
  key: "brain",
  label: "Brain",
  icon: BrainIcon,
  items: [
    { href: "/knowledge/input", label: "ナレッジ入力", icon: PenLineIcon },
    { href: "/knowledge", label: "ナレッジ一覧", icon: ListIcon },
    { href: "/knowledge/qa", label: "Q&A", icon: MessageCircleQuestionIcon, tooltip: "AIに質問する" },
    { href: "/twin", label: "会社の状態", icon: TwinIcon, tooltip: "ヒト・プロセス・コストの現状を可視化" },
    { href: "/proposals", label: "AI提案", icon: LightbulbIcon, tooltip: "AIからの改善提案・リスクアラート" },
  ],
};

const OS_CATEGORY: NavCategory = {
  key: "os",
  label: "OS",
  icon: BuildingIcon,
  items: [],
  groups: [
      {
        key: "marketing",
        label: "マーケ",
        items: [
          { href: "/marketing/outreach", label: "アウトリーチ", icon: MarketingOutreachIcon, tooltip: "企業リサーチ・自動メール送信" },
          { href: "/marketing/ab-tests", label: "A/Bテスト", icon: MarketingABTestIcon, tooltip: "メール文面のA/Bテスト管理" },
        ],
      },
      {
        key: "sales",
        label: "セールス",
        items: [
          { href: "/sales/leads", label: "リード管理", icon: SalesLeadIcon, tooltip: "見込み客のスコアと進捗を管理" },
          { href: "/sales/pipeline", label: "商談", icon: SalesPipelineIcon, tooltip: "商談パイプラインと金額・確度を管理" },
          { href: "/sales/proposals", label: "提案書", icon: SalesProposalIcon, tooltip: "AI生成の提案書の送付・開封状況" },
          { href: "/sales/contracts", label: "見積・契約", icon: SalesContractIcon, tooltip: "見積書と契約書の電子署名管理" },
          { href: "/sales/forecast", label: "売上予測", icon: SalesForecastIcon, tooltip: "パイプライン加重予測と月別グラフ" },
        ],
      },
      {
        key: "cs",
        label: "カスタマーサクセス",
        items: [
          { href: "/crm/customers", label: "顧客管理", icon: CRMCustomerIcon, tooltip: "顧客一覧・ヘルススコア・ライフサイクル" },
          { href: "/crm/requests", label: "要望管理", icon: CRMRequestIcon, tooltip: "顧客要望の優先順位付けとステータス管理" },
          { href: "/upsell", label: "アップセル", icon: UpsellIcon, tooltip: "拡張提案の機会検出とブリーフィング" },
        ],
      },
      {
        key: "support",
        label: "カスタマーサポート",
        items: [
          { href: "/support/tickets", label: "サポート", icon: SupportTicketIcon, tooltip: "チケット一覧・AI自動回答・SLA監視" },
        ],
      },
      {
        key: "backoffice",
        label: "バックオフィス",
        items: [
          { href: "/crm/revenue", label: "売上管理", icon: CRMRevenueIcon, tooltip: "MRR/ARR/チャーン率の月次推移" },
        ],
      },
    ],
};

const SETTINGS_ITEMS: NavItem[] = [
  { href: "/settings/members", label: "メンバー管理", icon: UsersIcon },
  { href: "/settings/connectors", label: "外部ツール連携", icon: ConnectorIcon, tooltip: "kintone・freee・Slack等の外部ツール接続管理" },
];

function AuthenticatedAppChrome({ children }: { children: React.ReactNode }) {
  const { user, signOut } = useAuth();
  const { count: pendingApprovalCount } = usePendingApprovals();
  const { industry, isLoading: industryLoading } = useOnboardingIndustry();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const categories = useMemo<NavCategory[]>(
    () => [
      BRAIN_CATEGORY,
      {
        key: "report",
        label: "BPO",
        icon: BarChartIcon,
        items: buildBpoNavItems(industry, industryLoading),
      },
      OS_CATEGORY,
    ],
    [industry, industryLoading]
  );
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [openCategory, setOpenCategory] = useState<string>(() =>
    getActiveCategoryKey(categories, pathname)
  );

  // ルート変更時にモバイルサイドバーを閉じ、アクティブカテゴリを自動展開
  useEffect(() => {
    setSidebarOpen(false);
    setOpenCategory(getActiveCategoryKey(categories, pathname));
  }, [pathname, categories]);

  // Cmd+\ でサイドバー折りたたみ
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "\\" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setSidebarCollapsed((prev) => !prev);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const companyName = user?.user_metadata?.company_name || "マイカンパニー";

  function toggleCategory(key: string) {
    setOpenCategory((prev) => (prev === key ? "" : key));
  }

  const itemActive = (href: string) => isNavItemActive(href, pathname, searchParams);

  return (
    <div className="flex h-screen overflow-hidden">
      {/* モバイルオーバーレイ */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/50 md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* サイドバー */}
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex flex-col border-r bg-card transition-all duration-200 md:static md:translate-x-0 ${
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        } ${sidebarCollapsed ? "md:w-14" : "md:w-52"}`}
      >
        {/* サイドバーヘッダー */}
        <div className="flex h-14 items-center justify-between border-b px-3">
          <Link href="/dashboard" className="flex items-center gap-2 min-w-0">
            <span className="text-lg font-bold text-primary shrink-0">
              {sidebarCollapsed ? "S" : "シャチョツー"}
            </span>
          </Link>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              className="hidden md:inline-flex h-7 w-7"
              onClick={() => setSidebarCollapsed((prev) => !prev)}
              title="サイドバー折りたたみ (Cmd+\)"
            >
              <span className="text-xs text-muted-foreground">
                {sidebarCollapsed ? "▸" : "◂"}
              </span>
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="md:hidden"
              onClick={() => setSidebarOpen(false)}
            >
              <XIcon className="h-5 w-5" />
            </Button>
          </div>
        </div>

        {/* ナビゲーション */}
        <nav className="flex-1 overflow-y-auto p-2 space-y-0.5">
          {categories.map((cat) => {
            const isOpen = openCategory === cat.key && !sidebarCollapsed;
            const allItems = categoryAllNavItems(cat);
            const isCatActive = allItems.some((item) => itemActive(item.href));

            return (
              <div key={cat.key}>
                {/* カテゴリヘッダー */}
                <button
                  onClick={() => !sidebarCollapsed && toggleCategory(cat.key)}
                  title={sidebarCollapsed ? cat.label : undefined}
                  className={`w-full flex items-center gap-2 rounded-md px-2 py-2 text-sm font-medium transition-colors hover:bg-accent hover:text-accent-foreground ${
                    isCatActive
                      ? "text-primary"
                      : "text-muted-foreground"
                  } ${sidebarCollapsed ? "justify-center" : "justify-between"}`}
                >
                  <span className={`flex items-center gap-2 ${sidebarCollapsed ? "" : "min-w-0"}`}>
                    <cat.icon className="h-4 w-4 shrink-0" />
                    {!sidebarCollapsed && (
                      <span className="truncate">{cat.label}</span>
                    )}
                  </span>
                  {!sidebarCollapsed && (
                    <ChevronDownIcon
                      className={`h-4 w-4 shrink-0 transition-transform duration-200 ${
                        isOpen ? "rotate-180" : ""
                      }`}
                    />
                  )}
                </button>

                {/* サブメニュー（折りたたみなし時のみ） */}
                {isOpen && (
                  <div
                    className={`mt-0.5 mb-1 ml-2 border-l border-border pl-2 ${
                      cat.groups?.length ? "space-y-2" : "space-y-0.5"
                    }`}
                  >
                    {cat.groups?.length ? (
                      cat.groups.map((group) => {
                        const chrome = getOsGroupChrome(group.key);
                        return (
                          <div key={group.key} className={chrome.block}>
                            <div className="flex items-center gap-1.5 px-1 pb-1 pt-0.5">
                              <span
                                className={`h-1.5 w-1.5 shrink-0 rounded-full ${chrome.dot}`}
                                aria-hidden
                              />
                              <span className="text-[11px] font-semibold tracking-wide text-foreground/80">
                                {group.label}
                              </span>
                            </div>
                            {group.items.map((item) => {
                              const active = itemActive(item.href);
                              const isApproval = navItemPath(item.href) === "/bpo/approvals";
                              const showBadge = isApproval && pendingApprovalCount > 0;
                              return (
                                <Link
                                  key={item.href}
                                  href={item.href}
                                  title={item.tooltip}
                                >
                                  <Button
                                    variant={active ? "secondary" : "ghost"}
                                    className="w-full justify-start gap-2 h-8 px-2 text-sm"
                                  >
                                    <item.icon className="h-3.5 w-3.5 shrink-0" />
                                    <span className="flex flex-1 items-center justify-between min-w-0">
                                      <span className="truncate">{item.label}</span>
                                      {showBadge && (
                                        <span className="ml-1 shrink-0 rounded-full bg-destructive px-1.5 py-0.5 text-[11px] font-bold text-destructive-foreground leading-none">
                                          {pendingApprovalCount}
                                        </span>
                                      )}
                                    </span>
                                  </Button>
                                </Link>
                              );
                            })}
                          </div>
                        );
                      })
                    ) : (
                      cat.items.map((item) => {
                        const active = itemActive(item.href);
                        const isApproval = navItemPath(item.href) === "/bpo/approvals";
                        const showBadge = isApproval && pendingApprovalCount > 0;
                        const button = (
                          <Button
                            variant={active ? "secondary" : "ghost"}
                            className="w-full justify-start gap-2 h-8 px-2 text-sm"
                            disabled={item.disabled}
                          >
                            <item.icon className="h-3.5 w-3.5 shrink-0" />
                            <span className="flex flex-1 items-center justify-between min-w-0">
                              <span className="truncate">{item.label}</span>
                              {showBadge && (
                                <span className="ml-1 shrink-0 rounded-full bg-destructive px-1.5 py-0.5 text-[11px] font-bold text-destructive-foreground leading-none">
                                  {pendingApprovalCount}
                                </span>
                              )}
                            </span>
                          </Button>
                        );
                        if (item.disabled) {
                          return (
                            <div key={item.href} title={item.tooltip} className="w-full">
                              {button}
                            </div>
                          );
                        }
                        return (
                          <Link key={item.href} href={item.href} title={item.tooltip}>
                            {button}
                          </Link>
                        );
                      })
                    )}
                  </div>
                )}

                {/* 折りたたみ時: カテゴリアイコンのみ（サブメニューなし） */}
                {sidebarCollapsed && (
                  <div className="space-y-0.5">
                    {allItems.map((item) => {
                      const active = itemActive(item.href);
                      const isApproval = navItemPath(item.href) === "/bpo/approvals";
                      const showBadge = isApproval && pendingApprovalCount > 0;
                      const button = (
                        <Button
                          variant={active ? "secondary" : "ghost"}
                          className="relative w-full justify-center px-0 h-8"
                          disabled={item.disabled}
                        >
                          <item.icon className="h-3.5 w-3.5 shrink-0" />
                          {showBadge && (
                            <span className="absolute top-0.5 right-0.5 h-2 w-2 rounded-full bg-destructive" />
                          )}
                        </Button>
                      );
                      if (item.disabled) {
                        return (
                          <div key={item.href} title={item.tooltip ?? item.label} className="relative w-full">
                            {button}
                          </div>
                        );
                      }
                      return (
                        <Link
                          key={item.href}
                          href={item.href}
                          title={item.tooltip ?? item.label}
                          className="relative block w-full"
                        >
                          {button}
                        </Link>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </nav>

        {/* フッター: 設定 + ログアウト */}
        <div className="border-t p-2 space-y-0.5">
          {/* 設定メニュー */}
          {!sidebarCollapsed && (
            <button
              onClick={() => toggleCategory("settings")}
              className={`w-full flex items-center justify-between gap-2 rounded-md px-2 py-2 text-sm font-medium transition-colors hover:bg-accent hover:text-accent-foreground ${
                SETTINGS_ITEMS.some((item) => itemActive(item.href))
                  ? "text-primary"
                  : "text-muted-foreground"
              }`}
            >
              <span className="flex items-center gap-2 min-w-0">
                <SettingsIcon className="h-4 w-4 shrink-0" />
                <span className="truncate">設定</span>
              </span>
              <ChevronDownIcon
                className={`h-4 w-4 shrink-0 transition-transform duration-200 ${
                  openCategory === "settings" ? "rotate-180" : ""
                }`}
              />
            </button>
          )}

          {/* 設定サブメニュー */}
          {openCategory === "settings" && !sidebarCollapsed && (
            <div className="mb-1 ml-2 space-y-0.5 border-l border-border pl-2">
              {SETTINGS_ITEMS.map((item) => {
                const active = itemActive(item.href);
                return (
                  <Link key={item.href} href={item.href} title={item.tooltip}>
                    <Button
                      variant={active ? "secondary" : "ghost"}
                      className="w-full justify-start gap-2 h-8 px-2 text-sm"
                    >
                      <item.icon className="h-3.5 w-3.5 shrink-0" />
                      <span className="truncate">{item.label}</span>
                    </Button>
                  </Link>
                );
              })}
            </div>
          )}

          {/* 折りたたみ時の設定アイコン */}
          {sidebarCollapsed && (
            <div className="space-y-0.5">
              {SETTINGS_ITEMS.map((item) => {
                const active = itemActive(item.href);
                return (
                  <Link key={item.href} href={item.href} title={item.label}>
                    <Button
                      variant={active ? "secondary" : "ghost"}
                      className="w-full justify-center px-0 h-8"
                    >
                      <item.icon className="h-3.5 w-3.5 shrink-0" />
                    </Button>
                  </Link>
                );
              })}
            </div>
          )}

          {/* ログアウト */}
          <Button
            variant="ghost"
            className={`w-full gap-2 text-muted-foreground ${
              sidebarCollapsed ? "justify-center px-0" : "justify-start"
            }`}
            onClick={signOut}
            title={sidebarCollapsed ? "ログアウト" : undefined}
          >
            <LogOutIcon className="h-4 w-4 shrink-0" />
            {!sidebarCollapsed && <span>ログアウト</span>}
          </Button>
        </div>
      </aside>

      {/* メインエリア */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* トップバー */}
        <header className="flex h-14 items-center justify-between border-b bg-card px-4">
          <div className="flex items-center gap-3">
            <Button
              variant="ghost"
              size="icon"
              className="md:hidden"
              onClick={() => setSidebarOpen(true)}
            >
              <MenuIcon className="h-5 w-5" />
            </Button>
            <span className="text-sm font-medium text-muted-foreground">
              {companyName}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="hidden text-sm text-muted-foreground sm:inline">
              {user?.email}
            </span>
            <Button variant="ghost" size="sm" onClick={signOut}>
              ログアウト
            </Button>
          </div>
        </header>

        {/* ページコンテンツ */}
        <main className="flex-1 overflow-y-auto p-4 md:p-6">
          {children}
        </main>
      </div>
      <NPSModal />
    </div>
  );
}

export default function AuthenticatedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { loading, session } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !session) {
      router.push("/login");
    }
  }, [loading, session, router]);

  if (loading) {
    return (
      <div className="flex h-screen overflow-hidden bg-background">
        <aside className="hidden w-52 shrink-0 border-r border-border bg-card md:block">
          <div className="h-14 animate-pulse border-b border-border bg-muted/40" />
          <div className="space-y-2 p-2">
            <div className="h-9 animate-pulse rounded-md bg-muted" />
            <div className="h-9 animate-pulse rounded-md bg-muted" />
            <div className="h-9 animate-pulse rounded-md bg-muted" />
            <div className="h-9 animate-pulse rounded-md bg-muted" />
          </div>
        </aside>
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <header className="h-14 shrink-0 animate-pulse border-b border-border bg-card" />
          <main className="flex-1 overflow-y-auto p-4 md:p-6">
            <div className="mx-auto max-w-5xl space-y-4">
              <div className="h-8 w-48 animate-pulse rounded-md bg-muted" />
              <div className="h-36 animate-pulse rounded-lg border border-border bg-muted/30" />
              <div className="h-36 animate-pulse rounded-lg border border-border bg-muted/30" />
              <p className="text-center text-xs text-muted-foreground pt-2">
                セッションを確認しています…
              </p>
            </div>
          </main>
        </div>
      </div>
    );
  }

  if (!session) {
    return null;
  }

  return (
    <PendingApprovalsProvider>
      <OnboardingIndustryProvider>
        <Suspense
          fallback={
            <div className="flex h-screen overflow-hidden bg-background">
              <aside className="hidden w-52 shrink-0 flex-col border-r border-border bg-card md:flex">
                <div className="h-14 border-b border-border" />
                <div className="flex-1 space-y-2 p-2">
                  <div className="h-8 animate-pulse rounded-md bg-muted" />
                  <div className="h-8 animate-pulse rounded-md bg-muted" />
                  <div className="h-8 animate-pulse rounded-md bg-muted" />
                </div>
              </aside>
              <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
                <header className="h-14 shrink-0 border-b border-border bg-card" />
                <main className="flex-1 overflow-y-auto p-4 md:p-6">{children}</main>
              </div>
            </div>
          }
        >
          <AuthenticatedAppChrome>{children}</AuthenticatedAppChrome>
        </Suspense>
      </OnboardingIndustryProvider>
    </PendingApprovalsProvider>
  );
}
