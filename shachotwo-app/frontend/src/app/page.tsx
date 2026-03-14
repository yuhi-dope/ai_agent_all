export default function Home() {
  return (
    <div className="flex items-center justify-center min-h-screen p-8">
      <div className="w-full max-w-md rounded-lg border p-6 shadow-sm">
        <h1 className="text-2xl font-bold">シャチョツー（社長2号）</h1>
        <p className="mt-1 text-sm text-gray-500">会社のデジタルツインSaaS</p>
        <p className="mt-4 text-sm text-gray-600">
          社長の頭の中をデジタル化し、Q&A・能動提案・BPO自動化を実現します。
        </p>
        <div className="mt-6 flex gap-2">
          <a
            href="/login"
            className="rounded-md bg-black px-4 py-2 text-sm text-white hover:bg-gray-800"
          >
            ログイン
          </a>
          <a
            href="/register"
            className="rounded-md border px-4 py-2 text-sm hover:bg-gray-50"
          >
            新規登録
          </a>
        </div>
      </div>
    </div>
  );
}
