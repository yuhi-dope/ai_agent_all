# shachotwo-frontend（Next.js）

## API バックエンド接続

- **`NEXT_PUBLIC_API_URL`** を **未設定**（または空）にすると、クライアントは同一オリジン上の **`/api/v1`**（例: `http://localhost:3000/api/v1/...`）へリクエストします。`next.config.ts` の **rewrites** が、既定値の **FastAPI**（`BACKEND_URL`、未設定時は `http://127.0.0.1:8000`）へプロキシします。ローカルでフロントと API を同じオリジンにまとめ、直叩き失敗を減らす用途です。
- 本番などで **API の URL を明示**する場合は、`NEXT_PUBLIC_API_URL` に `https://.../api/v1` のように **フル URL**（末尾スラッシュなし推奨）を設定します。このときはブラウザがその URL へ直接アクセスします。
- **`BACKEND_URL`** は Next の rewrites の転送先のみに使います（ビルド時 / サーバー側）。フロントの実行時に `NEXT_PUBLIC_*` として公開する必要はありません。

### AI提案（`/proposals`）description パースのデバッグ

- **`next dev`（開発モード）では既定で** DevTools **Console** に **`[proactive-parse]`** が出ます（フェンス一致・JSON パース段階・フィルタ後件数など）。うるさい場合は `.env.local` に **`NEXT_PUBLIC_DEBUG_PROPOSAL_PARSE=0`** を書いて **`next dev` を再起動**。
- 本番ビルドでは出ません。開発で明示的に有効にしたいだけのときは **`NEXT_PUBLIC_DEBUG_PROPOSAL_PARSE=1`** でも可。

### トラブルシュート（Network で `proposals` が pending）

- **`NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1` のように別ポートへ直指定**していると、ブラウザは `localhost:3000` から `localhost:8000` へ **クロスオリジン**になり、`Authorization` 付きの `fetch` で **OPTIONS プリフライト**が走ります。バックエンド未起動・到達不可・遅いと **pending** のままになりやすい。
- ローカルでは **`NEXT_PUBLIC_API_URL` を未設定**（またはコメントアウト）にし、`http://localhost:3000/api/v1/...` へ同一オリジンでリクエストする。`next.config.ts` の rewrites が **`BACKEND_URL`（既定 `http://127.0.0.1:8000`）** へ転送する。
- 変更後は **`next dev` を再起動**して環境変数を読み直す。
