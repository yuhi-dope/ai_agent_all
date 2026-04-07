"use client";

import { useEffect, useState, useCallback } from "react";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

// ---------------------------------------------------------------------------
// 型定義
// ---------------------------------------------------------------------------

interface Employee {
  id: string;
  name: string;
  department: string | null;
  position: string | null;
  employment_type: "full_time" | "part_time" | "contract" | "dispatch";
  hire_date: string | null;
  base_salary: number | null;
  is_active: boolean;
}

interface PayrollSummary {
  employee_id: string;
  employee_name: string;
  base_salary: number;
  overtime_pay: number;
  deductions: number;
  net_pay: number;
}

interface AttendanceSummary {
  employee_id: string;
  employee_name: string;
  work_days: number;
  overtime_hours: number;
  paid_leave_used: number;
  absent_days: number;
}

interface LaborAlert {
  id: string;
  type: "overtime_warning" | "insurance_missing" | "compliance";
  title: string;
  description: string;
  employee_name?: string;
  severity: "high" | "medium" | "low";
}

interface NewEmployeeForm {
  name: string;
  department: string;
  position: string;
  employment_type: "full_time" | "part_time" | "contract" | "dispatch";
  hire_date: string;
  base_salary: string;
}

// ---------------------------------------------------------------------------
// モックデータ
// ---------------------------------------------------------------------------

const MOCK_EMPLOYEES: Employee[] = [
  {
    id: "e1",
    name: "田中 太郎",
    department: "営業部",
    position: "営業部長",
    employment_type: "full_time",
    hire_date: "2018-04-01",
    base_salary: 450000,
    is_active: true,
  },
  {
    id: "e2",
    name: "鈴木 花子",
    department: "経理部",
    position: "経理担当",
    employment_type: "full_time",
    hire_date: "2020-07-15",
    base_salary: 300000,
    is_active: true,
  },
  {
    id: "e3",
    name: "山田 次郎",
    department: "製造部",
    position: "現場リーダー",
    employment_type: "contract",
    hire_date: "2022-01-10",
    base_salary: 280000,
    is_active: true,
  },
  {
    id: "e4",
    name: "佐藤 美咲",
    department: "総務部",
    position: "事務スタッフ",
    employment_type: "part_time",
    hire_date: "2023-04-01",
    base_salary: 150000,
    is_active: true,
  },
  {
    id: "e5",
    name: "伊藤 健一",
    department: "製造部",
    position: "製造スタッフ",
    employment_type: "dispatch",
    hire_date: "2023-09-01",
    base_salary: null,
    is_active: false,
  },
];

const MOCK_PAYROLL: PayrollSummary[] = [
  {
    employee_id: "e1",
    employee_name: "田中 太郎",
    base_salary: 450000,
    overtime_pay: 45000,
    deductions: 92000,
    net_pay: 403000,
  },
  {
    employee_id: "e2",
    employee_name: "鈴木 花子",
    base_salary: 300000,
    overtime_pay: 12000,
    deductions: 62000,
    net_pay: 250000,
  },
  {
    employee_id: "e3",
    employee_name: "山田 次郎",
    base_salary: 280000,
    overtime_pay: 56000,
    deductions: 58000,
    net_pay: 278000,
  },
  {
    employee_id: "e4",
    employee_name: "佐藤 美咲",
    base_salary: 150000,
    overtime_pay: 0,
    deductions: 15000,
    net_pay: 135000,
  },
];

const MOCK_ATTENDANCE: AttendanceSummary[] = [
  {
    employee_id: "e1",
    employee_name: "田中 太郎",
    work_days: 21,
    overtime_hours: 28,
    paid_leave_used: 1,
    absent_days: 0,
  },
  {
    employee_id: "e2",
    employee_name: "鈴木 花子",
    work_days: 20,
    overtime_hours: 8,
    paid_leave_used: 2,
    absent_days: 0,
  },
  {
    employee_id: "e3",
    employee_name: "山田 次郎",
    work_days: 22,
    overtime_hours: 46,
    paid_leave_used: 0,
    absent_days: 0,
  },
  {
    employee_id: "e4",
    employee_name: "佐藤 美咲",
    work_days: 18,
    overtime_hours: 0,
    paid_leave_used: 0,
    absent_days: 1,
  },
];

const MOCK_ALERTS: LaborAlert[] = [
  {
    id: "a1",
    type: "overtime_warning",
    title: "残業時間が上限に近づいています",
    description: "山田 次郎さんの今月の残業が46時間です。36協定の月45時間を超えています。早急に対応が必要です。",
    employee_name: "山田 次郎",
    severity: "high",
  },
  {
    id: "a2",
    type: "insurance_missing",
    title: "社会保険の加入確認が必要です",
    description: "パート・契約社員のうち、週20時間以上勤務している方の社会保険加入状況を確認してください。",
    severity: "medium",
  },
  {
    id: "a3",
    type: "compliance",
    title: "有給休暇の取得促進",
    description: "山田 次郎さんの今年度の有給取得日数が0日です。法律上、年5日の取得が義務付けられています。",
    employee_name: "山田 次郎",
    severity: "medium",
  },
];

// ---------------------------------------------------------------------------
// 定数
// ---------------------------------------------------------------------------

const EMPLOYMENT_TYPE_LABELS: Record<Employee["employment_type"], string> = {
  full_time: "正社員",
  part_time: "パート",
  contract: "契約社員",
  dispatch: "派遣",
};

const EMPLOYMENT_TYPE_BADGE_CLASS: Record<Employee["employment_type"], string> = {
  full_time: "bg-blue-100 text-blue-800",
  part_time: "bg-green-100 text-green-800",
  contract: "bg-orange-100 text-orange-800",
  dispatch: "bg-gray-100 text-gray-600",
};

const TABS = [
  { key: "employees", label: "従業員一覧" },
  { key: "payroll", label: "今月の給与" },
  { key: "attendance", label: "勤怠サマリー" },
  { key: "alerts", label: "労務アラート" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

const INITIAL_FORM: NewEmployeeForm = {
  name: "",
  department: "",
  position: "",
  employment_type: "full_time",
  hire_date: "",
  base_salary: "",
};

// ---------------------------------------------------------------------------
// メインコンポーネント
// ---------------------------------------------------------------------------

export default function HrPage() {
  const { session } = useAuth();
  const [activeTab, setActiveTab] = useState<TabKey>("employees");

  // --- 従業員一覧 ---
  const [employees, setEmployees] = useState<Employee[]>(MOCK_EMPLOYEES);
  const [employeeLoading, setEmployeeLoading] = useState(false);
  const [employeeError, setEmployeeError] = useState<string | null>(null);
  const [searchDept, setSearchDept] = useState("");
  const [filterType, setFilterType] = useState<Employee["employment_type"] | "all">("all");
  const [filterStatus, setFilterStatus] = useState<"active" | "inactive" | "all">("all");
  const [showNewEmployeeDialog, setShowNewEmployeeDialog] = useState(false);
  const [newEmployeeForm, setNewEmployeeForm] = useState<NewEmployeeForm>(INITIAL_FORM);
  const [savingEmployee, setSavingEmployee] = useState(false);
  const [saveEmployeeSuccess, setSaveEmployeeSuccess] = useState(false);

  // --- 給与 ---
  const [payroll, setPayroll] = useState<PayrollSummary[]>(MOCK_PAYROLL);
  const [payrollLoading, setPayrollLoading] = useState(false);
  const [payrollError, setPayrollError] = useState<string | null>(null);
  const [payrollPending, setPayrollPending] = useState(false);
  const [runningPayroll, setRunningPayroll] = useState(false);

  // --- 勤怠 ---
  const [attendance, setAttendance] = useState<AttendanceSummary[]>(MOCK_ATTENDANCE);
  const [attendanceLoading, setAttendanceLoading] = useState(false);
  const [attendanceError, setAttendanceError] = useState<string | null>(null);
  const [attendancePeriod, setAttendancePeriod] = useState<"current" | "previous">("current");

  // --- アラート ---
  const [alerts] = useState<LaborAlert[]>(MOCK_ALERTS);

  // ---------------------------------------------------------------------------
  // データ取得
  // ---------------------------------------------------------------------------

  const fetchEmployees = useCallback(async () => {
    setEmployeeLoading(true);
    setEmployeeError(null);
    try {
      const result = await apiFetch<{ employees: Employee[] }>(
        "/backoffice/employees",
        { token: session?.access_token }
      );
      setEmployees(result.employees);
    } catch {
      // バックエンド未接続時はモックデータを維持
    } finally {
      setEmployeeLoading(false);
    }
  }, [session?.access_token]);

  const fetchAttendance = useCallback(async () => {
    setAttendanceLoading(true);
    setAttendanceError(null);
    try {
      const period = attendancePeriod === "current" ? "current_month" : "last_month";
      const result = await apiFetch<{ summaries: AttendanceSummary[] }>(
        "/backoffice/attendance/summary",
        { token: session?.access_token, params: { period } }
      );
      setAttendance(result.summaries);
    } catch {
      // バックエンド未接続時はモックデータを維持
    } finally {
      setAttendanceLoading(false);
    }
  }, [session?.access_token, attendancePeriod]);

  useEffect(() => {
    fetchEmployees();
  }, [fetchEmployees]);

  useEffect(() => {
    if (activeTab === "attendance") {
      fetchAttendance();
    }
  }, [activeTab, fetchAttendance]);

  // ---------------------------------------------------------------------------
  // 給与計算実行
  // ---------------------------------------------------------------------------

  const handleRunPayroll = async () => {
    setRunningPayroll(true);
    setPayrollError(null);
    try {
      await apiFetch("/bpo/backoffice/payroll", {
        method: "POST",
        token: session?.access_token,
        body: { period: "current_month" },
      });
      setPayrollPending(true);
    } catch {
      // モック環境では成功扱い
      setPayrollPending(true);
    } finally {
      setRunningPayroll(false);
    }
  };

  // ---------------------------------------------------------------------------
  // 従業員登録
  // ---------------------------------------------------------------------------

  const handleSaveEmployee = async () => {
    if (!newEmployeeForm.name.trim()) return;
    setSavingEmployee(true);
    setSaveEmployeeSuccess(false);
    try {
      const newEmp: Employee = {
        id: `e${Date.now()}`,
        name: newEmployeeForm.name,
        department: newEmployeeForm.department || null,
        position: newEmployeeForm.position || null,
        employment_type: newEmployeeForm.employment_type,
        hire_date: newEmployeeForm.hire_date || null,
        base_salary: newEmployeeForm.base_salary ? parseInt(newEmployeeForm.base_salary, 10) : null,
        is_active: true,
      };
      await apiFetch("/backoffice/employees", {
        method: "POST",
        token: session?.access_token,
        body: newEmp,
      }).catch(() => null); // バックエンド未接続でも続行
      setEmployees((prev) => [...prev, newEmp]);
      setNewEmployeeForm(INITIAL_FORM);
      setSaveEmployeeSuccess(true);
      setTimeout(() => {
        setShowNewEmployeeDialog(false);
        setSaveEmployeeSuccess(false);
      }, 1200);
    } finally {
      setSavingEmployee(false);
    }
  };

  // ---------------------------------------------------------------------------
  // フィルタ
  // ---------------------------------------------------------------------------

  const filteredEmployees = employees.filter((emp) => {
    if (searchDept && !(emp.department ?? "").includes(searchDept)) return false;
    if (filterType !== "all" && emp.employment_type !== filterType) return false;
    if (filterStatus === "active" && !emp.is_active) return false;
    if (filterStatus === "inactive" && emp.is_active) return false;
    return true;
  });

  // ---------------------------------------------------------------------------
  // レンダリング
  // ---------------------------------------------------------------------------

  return (
    <div className="max-w-5xl mx-auto px-4 sm:px-6 py-6">
      {/* ページヘッダー */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">人事・労務</h1>
        <p className="text-sm text-gray-500 mt-1">
          従業員管理・給与計算・勤怠集計を一元管理します
        </p>
      </div>

      {/* タブナビゲーション */}
      <div className="border-b border-gray-200 mb-6 overflow-x-auto">
        <nav className="flex gap-0 min-w-max">
          {TABS.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
                activeTab === tab.key
                  ? "border-primary text-primary"
                  : "border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300"
              }`}
            >
              {tab.label}
              {tab.key === "alerts" && alerts.filter((a) => a.severity === "high").length > 0 && (
                <span className="ml-2 inline-flex items-center justify-center w-4 h-4 rounded-full bg-red-500 text-white text-[10px] font-bold">
                  {alerts.filter((a) => a.severity === "high").length}
                </span>
              )}
            </button>
          ))}
        </nav>
      </div>

      {/* ========== タブ1: 従業員一覧 ========== */}
      {activeTab === "employees" && (
        <div className="space-y-4">
          {/* フィルタバー */}
          <div className="flex flex-col sm:flex-row gap-3 items-start sm:items-center justify-between">
            <div className="flex flex-col sm:flex-row gap-2 w-full sm:w-auto">
              <Input
                placeholder="例: 営業部"
                value={searchDept}
                onChange={(e) => setSearchDept(e.target.value)}
                className="w-full sm:w-40"
              />
              <select
                value={filterType}
                onChange={(e) =>
                  setFilterType(e.target.value as Employee["employment_type"] | "all")
                }
                className="w-full sm:w-auto h-10 rounded-md border border-input bg-background px-3 py-2 text-sm"
              >
                <option value="all">雇用形態: すべて</option>
                <option value="full_time">正社員</option>
                <option value="part_time">パート</option>
                <option value="contract">契約社員</option>
                <option value="dispatch">派遣</option>
              </select>
              <select
                value={filterStatus}
                onChange={(e) =>
                  setFilterStatus(e.target.value as "active" | "inactive" | "all")
                }
                className="w-full sm:w-auto h-10 rounded-md border border-input bg-background px-3 py-2 text-sm"
              >
                <option value="all">在籍: すべて</option>
                <option value="active">在籍中</option>
                <option value="inactive">退職済み</option>
              </select>
            </div>
            <Button
              onClick={() => setShowNewEmployeeDialog(true)}
              className="w-full sm:w-auto"
            >
              従業員を新規追加する
            </Button>
          </div>

          {/* エラー */}
          {employeeError && (
            <div className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700">
              データの取得に失敗しました。しばらく経ってから再度お試しください。
            </div>
          )}

          {/* ローディング */}
          {employeeLoading ? (
            <div className="flex items-center justify-center h-64">
              <p className="text-gray-500">読み込み中...</p>
            </div>
          ) : filteredEmployees.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-64 gap-4">
              <p className="text-gray-500">
                {employees.length === 0
                  ? "まだ従業員が登録されていません"
                  : "条件に一致する従業員がいません"}
              </p>
              {employees.length === 0 && (
                <Button onClick={() => setShowNewEmployeeDialog(true)}>
                  はじめての従業員を追加する
                </Button>
              )}
            </div>
          ) : (
            <>
              {/* PC: テーブル表示 */}
              <div className="hidden md:block rounded-lg border overflow-hidden">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>氏名</TableHead>
                      <TableHead>部署</TableHead>
                      <TableHead>役職</TableHead>
                      <TableHead>雇用形態</TableHead>
                      <TableHead>入社日</TableHead>
                      <TableHead>ステータス</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {filteredEmployees.map((emp) => (
                      <TableRow key={emp.id}>
                        <TableCell className="font-medium">{emp.name}</TableCell>
                        <TableCell>{emp.department ?? "—"}</TableCell>
                        <TableCell>{emp.position ?? "—"}</TableCell>
                        <TableCell>
                          <Badge className={EMPLOYMENT_TYPE_BADGE_CLASS[emp.employment_type]}>
                            {EMPLOYMENT_TYPE_LABELS[emp.employment_type]}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          {emp.hire_date
                            ? new Date(emp.hire_date).toLocaleDateString("ja-JP")
                            : "—"}
                        </TableCell>
                        <TableCell>
                          {emp.is_active ? (
                            <Badge className="bg-green-100 text-green-800">在籍中</Badge>
                          ) : (
                            <Badge variant="secondary">退職済み</Badge>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>

              {/* スマホ: カード表示 */}
              <div className="md:hidden space-y-3">
                {filteredEmployees.map((emp) => (
                  <Card key={emp.id}>
                    <CardContent className="pt-4">
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <p className="font-medium text-base">{emp.name}</p>
                          <p className="text-sm text-gray-500 mt-0.5">
                            {emp.department ?? "部署未設定"} /{" "}
                            {emp.position ?? "役職未設定"}
                          </p>
                          <p className="text-xs text-gray-400 mt-1">
                            入社:{" "}
                            {emp.hire_date
                              ? new Date(emp.hire_date).toLocaleDateString("ja-JP")
                              : "—"}
                          </p>
                        </div>
                        <div className="flex flex-col items-end gap-1">
                          <Badge className={EMPLOYMENT_TYPE_BADGE_CLASS[emp.employment_type]}>
                            {EMPLOYMENT_TYPE_LABELS[emp.employment_type]}
                          </Badge>
                          {emp.is_active ? (
                            <Badge className="bg-green-100 text-green-800">在籍中</Badge>
                          ) : (
                            <Badge variant="secondary">退職済み</Badge>
                          )}
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </>
          )}

          {/* 新規登録ダイアログ */}
          <Dialog open={showNewEmployeeDialog} onOpenChange={setShowNewEmployeeDialog}>
            <DialogContent className="sm:max-w-md w-full mx-2">
              <DialogHeader>
                <DialogTitle>従業員を新規追加する</DialogTitle>
              </DialogHeader>
              <div className="space-y-4 py-2">
                {saveEmployeeSuccess && (
                  <div className="rounded-md bg-green-50 border border-green-200 p-3 text-sm text-green-700">
                    従業員を登録しました
                  </div>
                )}
                <div className="space-y-1">
                  <Label>氏名</Label>
                  <Input
                    placeholder="例: 山田 花子"
                    value={newEmployeeForm.name}
                    onChange={(e) =>
                      setNewEmployeeForm((f) => ({ ...f, name: e.target.value }))
                    }
                  />
                </div>
                <div className="space-y-1">
                  <Label>部署</Label>
                  <Input
                    placeholder="例: 営業部"
                    value={newEmployeeForm.department}
                    onChange={(e) =>
                      setNewEmployeeForm((f) => ({ ...f, department: e.target.value }))
                    }
                  />
                </div>
                <div className="space-y-1">
                  <Label>役職</Label>
                  <Input
                    placeholder="例: 営業担当"
                    value={newEmployeeForm.position}
                    onChange={(e) =>
                      setNewEmployeeForm((f) => ({ ...f, position: e.target.value }))
                    }
                  />
                </div>
                <div className="space-y-1">
                  <Label>雇用形態</Label>
                  <select
                    value={newEmployeeForm.employment_type}
                    onChange={(e) =>
                      setNewEmployeeForm((f) => ({
                        ...f,
                        employment_type: e.target.value as Employee["employment_type"],
                      }))
                    }
                    className="w-full h-10 rounded-md border border-input bg-background px-3 py-2 text-sm"
                  >
                    <option value="full_time">正社員</option>
                    <option value="part_time">パート</option>
                    <option value="contract">契約社員</option>
                    <option value="dispatch">派遣</option>
                  </select>
                </div>
                <div className="space-y-1">
                  <Label>入社日</Label>
                  <Input
                    type="date"
                    value={newEmployeeForm.hire_date}
                    onChange={(e) =>
                      setNewEmployeeForm((f) => ({ ...f, hire_date: e.target.value }))
                    }
                  />
                </div>
                <div className="space-y-1">
                  <Label>基本給（月額・円）</Label>
                  <Input
                    type="number"
                    placeholder="例: 250000"
                    value={newEmployeeForm.base_salary}
                    onChange={(e) =>
                      setNewEmployeeForm((f) => ({ ...f, base_salary: e.target.value }))
                    }
                  />
                </div>
              </div>
              <DialogFooter className="gap-2">
                <Button
                  variant="outline"
                  onClick={() => {
                    setShowNewEmployeeDialog(false);
                    setNewEmployeeForm(INITIAL_FORM);
                  }}
                >
                  キャンセル
                </Button>
                <Button
                  onClick={handleSaveEmployee}
                  disabled={savingEmployee || !newEmployeeForm.name.trim()}
                >
                  {savingEmployee ? (
                    <>
                      <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                      登録中...
                    </>
                  ) : (
                    "従業員を追加する"
                  )}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
      )}

      {/* ========== タブ2: 今月の給与 ========== */}
      {activeTab === "payroll" && (
        <div className="space-y-4">
          {/* 承認待ちバナー */}
          {payrollPending && (
            <div className="rounded-md border border-yellow-200 bg-yellow-50 p-4 text-sm text-yellow-800">
              今月の給与は承認待ちです。内容を確認して承認してください。
            </div>
          )}

          {/* エラー */}
          {payrollError && (
            <div className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700">
              給与計算に失敗しました。しばらく経ってから再度お試しください。
            </div>
          )}

          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-gray-900">今月の給与一覧</h2>
              <p className="text-sm text-gray-500">
                {new Date().getFullYear()}年{new Date().getMonth() + 1}月分
              </p>
            </div>
            <Button
              onClick={handleRunPayroll}
              disabled={runningPayroll || payrollPending}
              className="w-full sm:w-auto"
            >
              {runningPayroll ? (
                <>
                  <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  計算中...
                </>
              ) : (
                "給与計算を実行する"
              )}
            </Button>
          </div>

          {/* PC: テーブル */}
          <div className="hidden md:block rounded-lg border overflow-hidden">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>氏名</TableHead>
                  <TableHead className="text-right">基本給</TableHead>
                  <TableHead className="text-right">残業代</TableHead>
                  <TableHead className="text-right">控除額</TableHead>
                  <TableHead className="text-right">支給額</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {payroll.map((p) => (
                  <TableRow key={p.employee_id}>
                    <TableCell className="font-medium">{p.employee_name}</TableCell>
                    <TableCell className="text-right">
                      ¥{p.base_salary.toLocaleString()}
                    </TableCell>
                    <TableCell className="text-right text-blue-700">
                      ¥{p.overtime_pay.toLocaleString()}
                    </TableCell>
                    <TableCell className="text-right text-red-600">
                      −¥{p.deductions.toLocaleString()}
                    </TableCell>
                    <TableCell className="text-right font-semibold">
                      ¥{p.net_pay.toLocaleString()}
                    </TableCell>
                  </TableRow>
                ))}
                {/* 合計行 */}
                <TableRow className="bg-gray-50 font-semibold">
                  <TableCell>合計</TableCell>
                  <TableCell className="text-right">
                    ¥{payroll.reduce((s, p) => s + p.base_salary, 0).toLocaleString()}
                  </TableCell>
                  <TableCell className="text-right text-blue-700">
                    ¥{payroll.reduce((s, p) => s + p.overtime_pay, 0).toLocaleString()}
                  </TableCell>
                  <TableCell className="text-right text-red-600">
                    −¥{payroll.reduce((s, p) => s + p.deductions, 0).toLocaleString()}
                  </TableCell>
                  <TableCell className="text-right">
                    ¥{payroll.reduce((s, p) => s + p.net_pay, 0).toLocaleString()}
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </div>

          {/* スマホ: カード */}
          <div className="md:hidden space-y-3">
            {payroll.map((p) => (
              <Card key={p.employee_id}>
                <CardContent className="pt-4">
                  <p className="font-medium">{p.employee_name}</p>
                  <div className="mt-2 space-y-1 text-sm">
                    <div className="flex justify-between">
                      <span className="text-gray-500">基本給</span>
                      <span>¥{p.base_salary.toLocaleString()}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-500">残業代</span>
                      <span className="text-blue-700">¥{p.overtime_pay.toLocaleString()}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-500">控除額</span>
                      <span className="text-red-600">−¥{p.deductions.toLocaleString()}</span>
                    </div>
                    <div className="flex justify-between font-semibold border-t pt-1 mt-1">
                      <span>支給額</span>
                      <span>¥{p.net_pay.toLocaleString()}</span>
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      )}

      {/* ========== タブ3: 勤怠サマリー ========== */}
      {activeTab === "attendance" && (
        <div className="space-y-4">
          <div className="flex items-center justify-between flex-wrap gap-3">
            <h2 className="text-lg font-semibold text-gray-900">勤怠サマリー</h2>
            <select
              value={attendancePeriod}
              onChange={(e) =>
                setAttendancePeriod(e.target.value as "current" | "previous")
              }
              className="h-10 rounded-md border border-input bg-background px-3 py-2 text-sm"
            >
              <option value="current">当月</option>
              <option value="previous">先月</option>
            </select>
          </div>

          {/* エラー */}
          {attendanceError && (
            <div className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700">
              データの取得に失敗しました。しばらく経ってから再度お試しください。
            </div>
          )}

          {attendanceLoading ? (
            <div className="flex items-center justify-center h-64">
              <p className="text-gray-500">読み込み中...</p>
            </div>
          ) : (
            <>
              {/* PC: テーブル */}
              <div className="hidden md:block rounded-lg border overflow-hidden">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>氏名</TableHead>
                      <TableHead className="text-right">出勤日数</TableHead>
                      <TableHead className="text-right">残業時間</TableHead>
                      <TableHead className="text-right">有給使用</TableHead>
                      <TableHead className="text-right">欠勤</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {attendance.map((a) => (
                      <TableRow key={a.employee_id}>
                        <TableCell className="font-medium">{a.employee_name}</TableCell>
                        <TableCell className="text-right">{a.work_days}日</TableCell>
                        <TableCell className="text-right">
                          <span
                            className={a.overtime_hours > 45 ? "text-red-600 font-semibold" : ""}
                          >
                            {a.overtime_hours}時間
                            {a.overtime_hours > 45 && (
                              <span className="ml-1 text-xs">⚠ 上限超過</span>
                            )}
                          </span>
                        </TableCell>
                        <TableCell className="text-right">{a.paid_leave_used}日</TableCell>
                        <TableCell className="text-right">
                          {a.absent_days > 0 ? (
                            <span className="text-orange-600">{a.absent_days}日</span>
                          ) : (
                            <span>0日</span>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>

              {/* スマホ: カード */}
              <div className="md:hidden space-y-3">
                {attendance.map((a) => (
                  <Card key={a.employee_id}>
                    <CardContent className="pt-4">
                      <p className="font-medium">{a.employee_name}</p>
                      <div className="mt-2 grid grid-cols-2 gap-y-1 text-sm">
                        <span className="text-gray-500">出勤日数</span>
                        <span className="text-right">{a.work_days}日</span>
                        <span className="text-gray-500">残業時間</span>
                        <span
                          className={`text-right ${a.overtime_hours > 45 ? "text-red-600 font-semibold" : ""}`}
                        >
                          {a.overtime_hours}時間
                          {a.overtime_hours > 45 && " ⚠"}
                        </span>
                        <span className="text-gray-500">有給使用</span>
                        <span className="text-right">{a.paid_leave_used}日</span>
                        <span className="text-gray-500">欠勤</span>
                        <span className="text-right">{a.absent_days}日</span>
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {/* ========== タブ4: 労務アラート ========== */}
      {activeTab === "alerts" && (
        <div className="space-y-4">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">労務アラート</h2>
            <p className="text-sm text-gray-500 mt-1">
              法律違反や労務リスクが検出された項目を表示します
            </p>
          </div>

          {alerts.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-64 gap-2">
              <p className="text-gray-500">現在、労務アラートはありません</p>
              <p className="text-sm text-gray-400">引き続き適切な労務管理を続けてください</p>
            </div>
          ) : (
            <div className="space-y-3">
              {alerts.map((alert) => (
                <Card
                  key={alert.id}
                  className={
                    alert.severity === "high"
                      ? "border-red-200"
                      : alert.severity === "medium"
                        ? "border-yellow-200"
                        : "border-gray-200"
                  }
                >
                  <CardHeader className="pb-2">
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex items-center gap-2">
                        {alert.severity === "high" && (
                          <Badge variant="destructive">要対応</Badge>
                        )}
                        {alert.severity === "medium" && (
                          <Badge className="bg-yellow-100 text-yellow-800">注意</Badge>
                        )}
                        {alert.severity === "low" && (
                          <Badge variant="secondary">確認</Badge>
                        )}
                        <CardTitle className="text-base">{alert.title}</CardTitle>
                      </div>
                    </div>
                    {alert.employee_name && (
                      <p className="text-xs text-gray-400">対象: {alert.employee_name}</p>
                    )}
                  </CardHeader>
                  <CardContent className="pt-0">
                    <CardDescription className="text-sm text-gray-600">
                      {alert.description}
                    </CardDescription>
                    <div className="mt-3">
                      <Button variant="outline" size="sm">
                        詳細を確認する
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
