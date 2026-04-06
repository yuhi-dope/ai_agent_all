# シャチョツー フロントエンド UI/UX ルール

> **このルールはすべてのページ・コンポーネント実装に必須。**
> 新規ページを作る前に必ず読むこと。

---

## 対象ユーザー像

**IT・AIリテラシーのない中小企業の経営者・現場スタッフ**が主な利用者。
「英語がわからない」「プログラミングを知らない」「AIを使い慣れていない」前提で設計する。

---

## 1. 用語ルール（絶対守る）

### 禁止ワード → 代替語

| 禁止（使ってはいけない） | 代わりに使う |
|---|---|
| BPO | 業務自動化 |
| パイプライン | 業務フロー / 自動化 |
| デジタルツイン | 会社の状態 |
| ナレッジ（単体で使う場合） | 会社のルール・ノウハウ（初出時）、以降はナレッジ可 |
| confidence / 信頼度 XX% | 回答の確度：高 / 中 / 参考情報としてご確認ください |
| 関連度 XX% | 非表示にする |
| model_used（gemini-xxx等） | 非表示にする |
| トークン / token | 非表示にする |
| セッションID / session_id | 非表示にする |
| execution_id | 非表示にする |
| UUID / ID（末尾8文字等） | 非表示にする（管理者専用画面のみ可） |
| cost_yen（LLMコスト） | 管理者ダッシュボードのみ表示。一般ユーザー画面では非表示 |

### カテゴリ・区分の日本語化

- `pricing` → `価格・見積`
- `workflow` → `業務フロー`
- `hr` → `人事・労務`
- `compliance` → `コンプライアンス`
- `finance` → `経理・財務`
- `safety` → `安全管理`

---

## 2. フォーム・入力UIルール

### placeholder は必ず日本語の具体例

```tsx
// ❌ NG
placeholder="例: pricing, workflow"
placeholder="Enter text..."
placeholder="category"

// ✅ OK
placeholder="例: 価格・見積、業務フロー"
placeholder="例: 見積書の有効期限は30日間"
placeholder="例: 営業部"
```

### ラベルは日本語のみ。英語を括弧で補足しない

```tsx
// ❌ NG
label="ヒト (people)"
label="コスト (cost)"

// ✅ OK
label="ヒト"
label="コスト"
```

### バリデーションエラーは日本語でやさしく

```tsx
// ❌ NG
"Field required"
"Invalid format"
"Request failed with status 422"

// ✅ OK
"入力してください"
"メールアドレスの形式で入力してください"
"入力内容を確認してください"
```

---

## 3. ローディング・空の状態

### ローディング中は必ずメッセージを表示

```tsx
// ❌ NG
<div className="animate-spin" />

// ✅ OK
<div className="animate-spin" />
<p>AIが分析しています。少しお待ちください...</p>
```

### 空の状態には必ず次のアクションを誘導

```tsx
// ❌ NG
<div>データがありません</div>

// ✅ OK
<div>
  <p>まだナレッジが登録されていません</p>
  <Button onClick={() => router.push("/knowledge/input")}>
    はじめてのナレッジを入力する
  </Button>
</div>
```

---

## 4. エラー表示ルール

- スタックトレース・エラーコードをユーザーに見せない
- エラーメッセージは日本語で原因と対処を伝える
- 致命的エラー以外はページ全体を壊さない（インライン表示）

```tsx
// ❌ NG
<div>{error.message}</div>  // "Internal Server Error: ..."
<div>{String(e)}</div>

// ✅ OK
<div className="text-destructive">
  データの取得に失敗しました。しばらく経ってから再度お試しください。
</div>
```

---

## 5. ナビゲーション・ページタイトル

### サイドバーの表示名

| パス | 表示名 | tooltip |
|---|---|---|
| /dashboard | ダッシュボード | — |
| /knowledge | ナレッジ管理 | — |
| /knowledge/input | ナレッジ入力 | — |
| /knowledge/qa | AIに質問 | — |
| /bpo | 業務自動化 | AIが業務を自動でこなします |
| /twin | 会社の状態 | ヒト・プロセス・コストの現状を可視化 |
| /proposals | AI提案 | AIからの改善提案・リスクアラート |
| /settings | 設定 | — |

### ページ h1 タイトルは機能説明型にする

```
// ❌ NG
"BPO実行"
"Digital Twin"
"Knowledge Management"

// ✅ OK
"業務自動化を実行"
"会社の状態"
"ナレッジ管理"
```

---

## 6. データ表示ルール

### 数値・パーセントに文脈を付ける

```tsx
// ❌ NG
<span>87%</span>
<span>{Math.round(confidence * 100)}%</span>

// ✅ OK
<span>回答の確度：高</span>
// または
confidence >= 0.8 ? "確度：高" : confidence >= 0.5 ? "確度：中" : "参考情報"
```

### 技術的な識別子を隠す

```tsx
// ❌ NG（一般ユーザー画面）
<span>ID: {item.id.slice(0, 8)}...</span>
<span>model: gemini-2.5-flash-001</span>
<span>session: {session_id}</span>

// ✅ OK
// → 表示しない。管理者専用画面のみ可
```

### 金額表示

```tsx
// ❌ NG
¥12.3456
¥0.044

// ✅ OK
¥12  // 整数に丸める（ユーザー向け）
// LLMコストは管理者ダッシュボードのみ表示
```

---

## 7. アクションボタンのコピー

動詞で始まる、具体的な表現にする。

```
// ❌ NG
"OK"
"Submit"
"実行"
"保存"（単体で文脈がない場合）

// ✅ OK
"テンプレートを適用する"
"AIに送信"
"ナレッジを保存する"
"この提案を承認する"
```

---

## 8. 機能の出し分け（admin / editor）

| 機能 | admin | editor |
|---|---|---|
| BPO実行 | ✅ | ❌ 非表示 |
| LLMコスト表示 | ✅ | ❌ 非表示 |
| メンバー招待 | ✅ | ❌ 非表示 |
| ナレッジ削除 | ✅ | ❌ 非表示 |
| ナレッジ閲覧・入力 | ✅ | ✅ |
| Q&A | ✅ | ✅ |
| AI提案の閲覧 | ✅ | ✅ |

---

## 9. オンボーディング・初回体験ルール

- **15分以内に「使えた」と感じさせる**のが目標
- 登録直後は必ずオンボーディング画面に誘導
- 完了後の主動線は **Q&A（AIに質問）** 一択にする
- 「まず試してみましょう」系の質問例を必ず5件以上表示する
- 未実装・準備中の機能は **グレーアウト＋「準備中」バッジ** で表示（非表示にしない）

---

## 10. 成功フィードバック

ユーザーの操作結果は必ず **即座に・日本語で** 伝える。

### 使い分け

| 操作 | フィードバック方法 |
|---|---|
| 保存・更新・送信 | インラインメッセージ（フォーム上部）またはトースト |
| 削除 | 確認ダイアログ → 完了後インラインで「削除しました」 |
| 長時間処理（AI分析・ファイル取込） | ローディング画面 → 完了後「完了しました」 |
| ページ遷移が発生しない軽い操作 | トースト（右下、3秒で消える） |
| ページ遷移が発生する操作 | 遷移先ページの上部にバナー |

### コピーの書き方

```tsx
// ❌ NG
"Success"
"Done"
"Saved"
"エラーが発生しました"（原因・対処なし）

// ✅ OK
"ナレッジを保存しました"
"テンプレートを適用しました。Q&Aを試してみましょう！"
"メンバーを招待しました。メールが送信されます"
"保存に失敗しました。入力内容を確認してもう一度お試しください"
```

### 確認ダイアログは破壊的操作のみ

削除・取り消し不能な操作にのみ使う。保存・送信には不要。

```tsx
// 確認ダイアログが必要な操作
- ナレッジ削除
- メンバー削除
- テンプレートの上書き適用

// 確認ダイアログ不要（即実行してよい）
- ナレッジ保存・編集
- Q&A送信
- 提案の承認・却下
```

---

## 11. モバイル対応

**対象デバイス：スマートフォン（iPhone/Android）を必ずサポートする。**
現場スタッフがスマホから使うケースを想定。

### ブレイクポイント（Tailwind標準）

| prefix | 幅 | 用途 |
|---|---|---|
| （なし） | ~640px | スマホ（基準） |
| `sm:` | 640px~ | タブレット縦 |
| `md:` | 768px~ | タブレット横 |
| `lg:` | 1024px~ | PC |

**モバイルファーストで書く。** `grid-cols-1 sm:grid-cols-2` のように小さい画面を基準にする。

### モバイルで必須の対応

```tsx
// ボタンはスマホで最低44pxのタップ領域
<Button size="lg" className="w-full sm:w-auto">送信</Button>

// グリッドはスマホ1列
<div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">

// テキスト入力はスマホでフルwidth
<Input className="w-full" />

// サイドバーはスマホで非表示 or ハンバーガーメニュー
// → 現在の layout.tsx のサイドバーはスマホ対応を確認すること

// モーダルはスマホでフルスクリーン近く
<DialogContent className="sm:max-w-md w-full mx-2">
```

### スマホで禁止のパターン

```tsx
// ❌ 横スクロールが必要なテーブル → カード表示に切り替える
<Table>...</Table>  // スマホでは代わりにカードリストを使う

// ❌ 小さすぎるテキスト
<span className="text-[10px]">  // スマホでは最低 text-xs (12px)

// ❌ hover依存のUI（スマホにhoverはない）
className="opacity-0 hover:opacity-100"  // タップで代替手段を用意する
```

---

## 12. ローディング：スケルトン vs スピナーの使い分け

| 状況 | 使うもの | 理由 |
|---|---|---|
| ページ初期ロード（一覧・詳細） | スケルトン | レイアウトシフトを防ぐ |
| ボタン押下後の処理（保存・送信） | スピナー（ボタン内） | ユーザーが起こした操作 |
| AI処理（Q&A・抽出・分析） | スピナー + メッセージ | 時間がかかることを伝える |
| ページ内の部分更新 | スピナー（該当箇所のみ） | ページ全体を壊さない |

```tsx
// ボタン内スピナー（保存・送信）
<Button disabled={loading}>
  {loading ? (
    <>
      <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
      保存中...
    </>
  ) : "ナレッジを保存する"}
</Button>

// AI処理中（Q&A等）
<div className="flex flex-col items-center gap-3 py-8">
  <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
  <p className="text-sm text-muted-foreground">AIが回答を生成しています...</p>
</div>
```

---

## 13. モーダル・確認ダイアログの基準

### 使うとき / 使わないとき

```
使う（Dialog）:
- 削除など取り消せない破壊的操作の確認
- フォームが複数項目ある追加操作（メンバー招待等）
- 詳細プレビュー（別ページに飛ばしたくない場合）

使わない:
- 単純な保存・更新（インラインで完結）
- エラー表示（インラインで表示）
- 成功通知（トーストで十分）
```

### 確認ダイアログの文言ルール

```tsx
// ❌ NG
<DialogTitle>確認</DialogTitle>
<DialogDescription>削除しますか？</DialogDescription>

// ✅ OK
<DialogTitle>ナレッジを削除しますか？</DialogTitle>
<DialogDescription>
  「{item.title}」を削除します。この操作は取り消せません。
</DialogDescription>
<Button variant="outline">キャンセル</Button>
<Button variant="destructive">削除する</Button>
```

---

## 14. カラー・デザインシステム

shadcn/ui のセマンティックカラーを使う。生のHEX・RGB値は使わない。

### 用途別カラー

| 用途 | クラス | 例 |
|---|---|---|
| 主要アクション | `bg-primary text-primary-foreground` | 保存・送信ボタン |
| 補助アクション | `bg-secondary` / `variant="outline"` | キャンセル・戻る |
| エラー・削除 | `text-destructive` / `variant="destructive"` | 削除ボタン・エラー文 |
| 警告 | `text-yellow-600 bg-yellow-50` | 準備中・注意 |
| 成功 | `text-green-600 bg-green-50` | 完了・承認済み |
| 情報 | `text-muted-foreground` | 補足説明・メタ情報 |

### ステータスバッジの統一

```tsx
// リスク・エラー系
<Badge variant="destructive">リスク</Badge>

// 処理中・警告系
<Badge className="bg-yellow-100 text-yellow-800">準備中</Badge>

// 完了・成功系
<Badge className="bg-green-100 text-green-800">完了</Badge>

// 情報・中立系
<Badge variant="secondary">改善</Badge>
<Badge variant="outline">機会</Badge>
```

---

## 15. テキスト階層

| 用途 | クラス | サイズ |
|---|---|---|
| ページタイトル（h1） | `text-2xl font-bold` | 24px |
| セクションタイトル（h2） | `text-lg font-semibold` | 18px |
| カードタイトル | `text-base font-medium` | 16px |
| 本文 | `text-sm` | 14px |
| 補足・メタ情報 | `text-xs text-muted-foreground` | 12px |
| 最小テキスト（ラベル等） | `text-[11px]` | 11px（これ以下は使わない） |

**スマホでの最小フォントは `text-xs`（12px）。`text-[10px]` 以下は使わない。**

---

## 10. チェックリスト（実装後に確認）

新規ページを実装したら以下を確認する：

**言語・用語**
- [ ] 英語のUI文字列がゼロか
- [ ] model_used / session_id / cost_yen が非表示か（管理者画面除く）
- [ ] placeholder が日本語の具体例か
- [ ] 禁止ワードリストに引っかかるものがないか

**インタラクション**
- [ ] 空の状態に次のアクションボタンがあるか
- [ ] ローディング中にメッセージがあるか（スピナー裸で放置しない）
- [ ] 保存・送信後に成功フィードバックがあるか
- [ ] 破壊的操作（削除等）に確認ダイアログがあるか
- [ ] エラーメッセージが日本語でやさしいか

**モバイル**
- [ ] スマホ（375px）で横スクロールが出ないか
- [ ] ボタンのタップ領域が十分か（最低44px）
- [ ] テキストが `text-[10px]` 以下になっていないか

**デザイン**
- [ ] カラーは shadcn/ui のセマンティックカラーを使っているか
- [ ] HEX・RGB値をベタ書きしていないか
- [ ] ステータスバッジは統一ルールに沿っているか
