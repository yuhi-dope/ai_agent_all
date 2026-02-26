# Publish ルール

GitHub Publisher が main ブランチへ直接 push する際のルール。

- コミットメッセージ: `Agent: <要件の冒頭 72 文字>`
- push 先: `HEAD:main`（HTTPS + GITHUB_TOKEN）
