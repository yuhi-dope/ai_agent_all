/**
 * Slack通知ユーティリティ
 * Incoming Webhookでチャンネルに通知を送る
 *
 * セットアップ:
 * 1. Slack App作成 → Incoming Webhooks有効化
 * 2. Webhook URLをGASスクリプトプロパティに設定:
 *    プロパティ名: SLACK_WEBHOOK_URL
 *    値: https://hooks.slack.com/services/T.../B.../...
 */

/**
 * Slackにメッセージを送信する
 * @param {string} メッセージ - Slack mrkdwn形式のテキスト
 * @param {string} チャンネル指定 - 省略時はWebhookのデフォルトチャンネル
 */
function Slackに通知(メッセージ, チャンネル指定) {
  const webhookUrl = PropertiesService.getScriptProperties().getProperty('SLACK_WEBHOOK_URL');

  if (!webhookUrl) {
    Logger.log('SLACK_WEBHOOK_URLが未設定。Slack通知をスキップ。');
    return;
  }

  const payload = { text: メッセージ };
  if (チャンネル指定) payload.channel = チャンネル指定;

  try {
    const response = UrlFetchApp.fetch(webhookUrl, {
      method: 'post',
      contentType: 'application/json',
      payload: JSON.stringify(payload),
      muteHttpExceptions: true,
    });

    const ステータス = response.getResponseCode();
    if (ステータス !== 200) {
      Logger.log(`Slack通知失敗 (${ステータス}): ${response.getContentText()}`);
    }
  } catch (e) {
    Logger.log('Slack通知エラー: ' + e.message);
  }
}

/**
 * 下書き確認リマインダーを送信する
 * 未確認の下書きがある場合にSlackで催促
 */
function 下書き確認リマインダー() {
  const 設定 = 設定を取得();
  const ss = SpreadsheetApp.openById(設定.スプレッドシートID);

  const X件数 = ステータス件数をカウント(ss.getSheetByName(設定.シート_X投稿管理), '未確認');
  const note件数 = ステータス件数をカウント(ss.getSheetByName(設定.シート_note記事管理), '未確認');
  const LI件数 = ステータス件数をカウント(ss.getSheetByName(設定.シート_LinkedIn投稿管理), '未確認');

  const 合計 = X件数 + note件数 + LI件数;
  if (合計 === 0) return;

  Slackに通知(
    `*未確認の下書きが${合計}件あります*\n` +
    `X: ${X件数}件 / note: ${note件数}件 / LinkedIn: ${LI件数}件\n` +
    `スプレッドシートのダッシュボードで確認してください`
  );
}
