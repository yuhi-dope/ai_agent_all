/**
 * Gemini API 画像生成 + Mermaid図解レンダリング
 */

/**
 * Gemini APIでアイキャッチ画像を生成する
 * @param {string} プロンプト - 画像生成プロンプト（日本語テキストを含めない）
 * @returns {Blob|null} 生成された画像、失敗時はnull
 */
function Geminiで画像生成(プロンプト) {
  const 設定 = 設定を取得();
  const APIキー = 設定.GEMINI_APIキー;
  const モデル = 'gemini-2.0-flash';

  const url = `https://generativelanguage.googleapis.com/v1beta/models/${モデル}:generateContent?key=${APIキー}`;

  const payload = {
    contents: [{
      parts: [{ text: プロンプト }]
    }],
    generationConfig: {
      responseModalities: ['TEXT', 'IMAGE'],
    }
  };

  try {
    const response = UrlFetchApp.fetch(url, {
      method: 'post',
      contentType: 'application/json',
      payload: JSON.stringify(payload),
      muteHttpExceptions: true,
    });

    const ステータス = response.getResponseCode();
    if (ステータス !== 200) {
      Logger.log(`画像生成APIエラー (${ステータス})`);
      return null;
    }

    const 本文 = JSON.parse(response.getContentText());
    const パーツ = 本文.candidates[0].content.parts;

    for (const パーツ1つ of パーツ) {
      if (パーツ1つ.inlineData) {
        const 画像データ = Utilities.base64Decode(パーツ1つ.inlineData.data);
        return Utilities.newBlob(画像データ, パーツ1つ.inlineData.mimeType, 'アイキャッチ.png');
      }
    }

    Logger.log('画像パートが見つかりませんでした');
    return null;
  } catch (e) {
    Logger.log('画像生成エラー: ' + e.message);
    return null;
  }
}

/**
 * GeminiでMermaidコードを生成する
 * @param {string} テーマ - 図解のテーマ
 * @returns {string} Mermaidコード
 */
function Mermaidコードを生成(テーマ) {
  const システム指示 = `あなたはMermaid図解の専門家です。
与えられたテーマに対して、Mermaidコード（graph TD or flowchart TD）を生成してください。
ルール:
- ノードは3〜7個（複雑すぎない）
- ラベルは日本語
- 白背景に映えるシンプルなスタイル
- コードブロック(\`\`\`)なしで、Mermaidコードのみ返してください`;

  return Geminiでテキスト生成(
    `テーマ: ${テーマ}\n\nMermaidコードを生成してください。`,
    システム指示,
    { 温度: 0.2, 最大トークン: 1000 }
  );
}

/**
 * Mermaidコードを画像に変換する（kroki.io API）
 * @param {string} Mermaidコード
 * @returns {Blob|null} PNG画像
 */
function Mermaidを画像に変換(mermaidコード) {
  try {
    const エンコード済み = Utilities.base64Encode(mermaidコード);
    const url = `https://kroki.io/mermaid/png/${エンコード済み}`;

    const response = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
    if (response.getResponseCode() === 200) {
      return response.getBlob().setName('図解.png');
    }
    return null;
  } catch (e) {
    Logger.log('Mermaid変換エラー: ' + e.message);
    return null;
  }
}
