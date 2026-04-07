/**
 * Gemini API テキスト生成
 * リトライ・レート制限対応
 */

/**
 * Gemini APIでテキストを生成する
 * @param {string} プロンプト - ユーザープロンプト
 * @param {string} システム指示 - システムインストラクション
 * @param {Object} オプション - { 温度, 最大トークン }
 * @returns {string} 生成されたテキスト
 */
function Geminiでテキスト生成(プロンプト, システム指示, オプション = {}) {
  const 設定 = 設定を取得();
  const APIキー = 設定.GEMINI_APIキー;
  const モデル = 設定.GEMINIモデル;
  const 温度 = オプション.温度 || 0.7;
  const 最大トークン = オプション.最大トークン || 2000;

  const url = `https://generativelanguage.googleapis.com/v1beta/models/${モデル}:generateContent?key=${APIキー}`;

  const payload = {
    system_instruction: {
      parts: [{ text: システム指示 }]
    },
    contents: [{
      parts: [{ text: プロンプト }]
    }],
    generationConfig: {
      temperature: 温度,
      maxOutputTokens: 最大トークン,
    }
  };

  const 最大リトライ = 3;
  for (let i = 0; i < 最大リトライ; i++) {
    try {
      const response = UrlFetchApp.fetch(url, {
        method: 'post',
        contentType: 'application/json',
        payload: JSON.stringify(payload),
        muteHttpExceptions: true,
      });

      const ステータス = response.getResponseCode();
      const 本文 = JSON.parse(response.getContentText());

      if (ステータス === 200) {
        return 本文.candidates[0].content.parts[0].text;
      }

      if (ステータス === 429) {
        // レート制限: 指数バックオフ
        Utilities.sleep((i + 1) * 5000);
        continue;
      }

      Logger.log(`Gemini APIエラー (${ステータス}): ${JSON.stringify(本文)}`);
      throw new Error(`Gemini APIが${ステータス}を返しました`);
    } catch (e) {
      if (i === 最大リトライ - 1) throw e;
      Utilities.sleep((i + 1) * 3000);
    }
  }
}
