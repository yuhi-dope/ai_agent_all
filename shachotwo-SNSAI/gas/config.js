/**
 * 設定値管理
 * APIキー等の機密情報はGASプロパティ（PropertiesService）で管理。
 * このファイルにはハードコードしない。
 */

/** GASプロパティから設定値を取得 */
function 設定を取得() {
  const props = PropertiesService.getScriptProperties();
  return {
    // Gemini API
    GEMINI_APIキー: props.getProperty('GEMINI_API_KEY'),
    GEMINIモデル: 'gemini-2.0-flash',

    // Slack
    SLACK_WEBHOOK_URL: props.getProperty('SLACK_WEBHOOK_URL'),

    // Apify
    APIFY_TOKEN: props.getProperty('APIFY_TOKEN'),

    // スプレッドシート
    スプレッドシートID: props.getProperty('SPREADSHEET_ID'),

    // シート名
    シート_業務改善ネタ: '業務改善ネタ',
    シート_頭のDB: '頭のDB',
    シート_バズポスト収集: 'バズポスト収集',
    シート_ニュース収集: 'ニュース収集',
    シート_リード管理: 'リード管理',
    シート_X投稿管理: 'X投稿管理',
    シート_Xパフォーマンス: 'Xパフォーマンス',
    シート_note記事管理: 'note記事管理',
    シート_SEOキーワード管理: 'SEO_AIOキーワード管理',
    シート_アフィリエイト管理: 'アフィリエイト管理',
    シート_LinkedIn投稿管理: 'LinkedIn投稿管理',
    シート_つながり管理: 'つながり管理',
    シート_ダッシュボード: 'ダッシュボード',

    // Gemini API設定
    温度_X長文: 0.7,
    温度_引用RT: 0.5,
    温度_Mermaid: 0.2,
    温度_note: 0.6,
    温度_LinkedIn: 0.5,
    最大トークン_X長文: 2000,
    最大トークン_引用RT: 500,
    最大トークン_note: 6000,
    最大トークン_LinkedIn: 1500,
  };
}
