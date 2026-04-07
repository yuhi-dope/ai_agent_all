# GCP Cloud Run デプロイ手順書

## 初回セットアップ

### GitHub Secrets に登録が必要なシークレット一覧

| シークレット名 | 説明 |
|---|---|
| `GCP_PROJECT_ID` | GCPプロジェクトID |
| `GCP_SA_KEY` | サービスアカウントキー（JSON、Base64エンコード不要） |
| `SUPABASE_URL` | Supabase プロジェクトURL |
| `SUPABASE_SERVICE_KEY` | Supabase サービスロールキー |
| `SUPABASE_ANON_KEY` | Supabase 匿名キー |
| `GEMINI_API_KEY` | Google Gemini API キー |
| `ANTHROPIC_API_KEY` | Anthropic Claude API キー |
| `VOYAGE_API_KEY` | Voyage AI API キー（Embedding用） |
| `JWT_SECRET` | JWT署名シークレット |
| `VERCEL_TOKEN` | Vercel デプロイトークン |
| `VERCEL_ORG_ID` | Vercel 組織ID |
| `VERCEL_PROJECT_ID` | Vercel プロジェクトID |

---

### GCP側の初回設定コマンド（ローカルで実行）

```bash
export PROJECT_ID=your-project-id

# APIの有効化
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  --project=$PROJECT_ID

# Artifact Registry リポジトリ作成
gcloud artifacts repositories create shachotwo \
  --repository-format=docker \
  --location=asia-northeast1 \
  --project=$PROJECT_ID

# サービスアカウント作成
gcloud iam service-accounts create shachotwo-deploy \
  --display-name="Shachotwo Deploy SA" \
  --project=$PROJECT_ID

# 権限付与
for role in roles/run.admin roles/artifactregistry.writer roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:shachotwo-deploy@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="$role"
done

# サービスアカウントキー生成（GitHub Secretsに登録）
gcloud iam service-accounts keys create sa-key.json \
  --iam-account=shachotwo-deploy@$PROJECT_ID.iam.gserviceaccount.com

# Secret Manager にシークレット登録
echo -n "https://xxxx.supabase.co" | gcloud secrets create SUPABASE_URL --data-file=- --project=$PROJECT_ID
echo -n "your-service-key" | gcloud secrets create SUPABASE_SERVICE_KEY --data-file=- --project=$PROJECT_ID
echo -n "your-anon-key" | gcloud secrets create SUPABASE_ANON_KEY --data-file=- --project=$PROJECT_ID
echo -n "your-gemini-key" | gcloud secrets create GEMINI_API_KEY --data-file=- --project=$PROJECT_ID
echo -n "your-anthropic-key" | gcloud secrets create ANTHROPIC_API_KEY --data-file=- --project=$PROJECT_ID
echo -n "your-voyage-key" | gcloud secrets create VOYAGE_API_KEY --data-file=- --project=$PROJECT_ID
echo -n "your-jwt-secret" | gcloud secrets create JWT_SECRET --data-file=- --project=$PROJECT_ID
```

> **注意**: `sa-key.json` は機密情報です。GitHub Secretsに登録後、ローカルから削除してください。
> ```bash
> rm sa-key.json
> ```

---

## DBマイグレーション

Supabaseダッシュボード（https://app.supabase.com）のSQL Editorで以下のファイルを順番に実行してください。

```
001_initial_schema.sql
002_vector_search_function.sql
003_...
...
018_bpo_hitl_all_pipelines.sql
```

マイグレーションファイルは `shachotwo-app/db/migrations/` に格納されています。

---

## CI/CD フロー

### API デプロイ（`.github/workflows/deploy-api.yml`）

`main` ブランチへの push で `shachotwo-app/**` に変更があった場合に自動実行。

```
1. Checkout
2. Google Auth（GCP_SA_KEY を使用）
3. Cloud SDK セットアップ
4. Docker 認証（asia-northeast1-docker.pkg.dev）
5. Docker ビルド（./shachotwo-app）
6. Artifact Registry にプッシュ
7. Cloud Run にデプロイ
8. サービスURL出力
```

### フロントエンドデプロイ（`.github/workflows/deploy-frontend.yml`）

`main` ブランチへの push で `shachotwo-app/frontend/**` に変更があった場合に自動実行。

```
1. Checkout
2. Node.js 20 セットアップ
3. npm ci（./shachotwo-app/frontend）
4. npm run build
5. Vercel に本番デプロイ（--prod）
```

---

## ヘルスチェック確認

```bash
curl https://your-cloud-run-url/health
```

正常時のレスポンス例:
```json
{"status": "ok"}
```

---

## トラブルシューティング

### Cloud Run ログ確認

```bash
gcloud run services logs read shachotwo-api \
  --region asia-northeast1 \
  --project=$PROJECT_ID \
  --limit 100
```

### イメージの手動デプロイ（緊急時）

```bash
export PROJECT_ID=your-project-id
export IMAGE_TAG=your-sha

gcloud run deploy shachotwo-api \
  --image asia-northeast1-docker.pkg.dev/$PROJECT_ID/shachotwo/api:$IMAGE_TAG \
  --region asia-northeast1 \
  --platform managed \
  --project=$PROJECT_ID
```

### Secret Manager のシークレット更新

```bash
echo -n "new-value" | gcloud secrets versions add SECRET_NAME --data-file=- --project=$PROJECT_ID
```

更新後は Cloud Run サービスを再デプロイするか、新しいリビジョンをトリガーしてください。
