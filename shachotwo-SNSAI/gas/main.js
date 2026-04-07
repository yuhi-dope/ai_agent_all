/**
 * shachotwo-SNSAI メインエントリポイント
 *
 * トリガー:
 * - doPost(e): Apify Webhook受信（これだけ英語名が必須）
 * - 毎朝の下書き一括生成(): 毎日6:00
 * - パフォーマンス記録を準備(): 毎日22:00
 * - 週次レポート集計(): 毎週月曜9:00
 * - アフィリエイト月次集計(): 毎月1日9:00
 */

/**
 * Apify Webhook受信（POST）
 * ※ doPostはGASの予約関数名なので英語のまま
 */
function doPost(e) {
  try {
    const rawBody = e.postData.contents;
    Logger.log('doPost受信: ' + rawBody.substring(0, 500));

    const data = JSON.parse(rawBody);
    const 結果 = Apifyデータを処理(data);

    Logger.log('処理結果: タイプ=' + 結果.タイプ + ', 件数=' + 結果.行リスト.length);

    // 引用RT候補があれば下書き生成
    if (結果.タイプ === 'バズ' && 結果.行リスト.length > 0) {
      Logger.log('引用RT下書き生成開始: ' + 結果.行リスト.length + '件');
      結果.行リスト.forEach(行 => {
        引用RT下書きを生成(行);
      });
      ダッシュボードを更新();
    }

    return ContentService.createTextOutput(JSON.stringify({ status: 'ok', count: 結果.行リスト.length }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    Logger.log('doPostエラー: ' + err.message + '\n' + err.stack);
    return ContentService.createTextOutput(JSON.stringify({ status: 'error', message: err.message }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

/**
 * 毎日6:00: トリガーチェーンのStep 1を起動
 * 6分制限回避のため、処理を4ステップに分割しトリガーで連鎖実行する。
 *
 * Step 1: ネタ取得 + 頭のDB取得 → PropertiesServiceに一時保存
 * Step 2: 3媒体テキスト生成（1分後に自動実行）
 * Step 3: 画像生成 + シート書き込み（1分後に自動実行）
 * Step 4: ダッシュボード更新 + Slack通知（1分後に自動実行）
 */
function 毎朝の下書き一括生成() {
  const 設定 = 設定を取得();
  const ss = SpreadsheetApp.openById(設定.スプレッドシートID);
  const ネタシート = ss.getSheetByName(設定.シート_業務改善ネタ);

  // 未処理ネタの最古1件を取得
  const ネタ = 未処理ネタを取得(ネタシート);
  if (!ネタ) {
    Logger.log('未処理ネタなし。スキップ。');
    return;
  }

  // 頭のDBから関連エントリを取得
  const 関連知見 = 関連する頭のDBを取得(ss, ネタ.タグ, 5);

  // 一時保存（次ステップで使う）
  const props = PropertiesService.getScriptProperties();
  props.setProperty('CHAIN_ネタ', JSON.stringify(ネタ));
  props.setProperty('CHAIN_関連知見', JSON.stringify(関連知見));

  Logger.log('Step 1 完了: ネタ取得「' + ネタ.タイトル + '」');

  // Step 2を1分後にスケジュール
  次のステップをスケジュール('下書き生成_Step2');
}

/** Step 2: 3媒体テキスト生成 */
function 下書き生成_Step2() {
  const props = PropertiesService.getScriptProperties();
  const ネタ = JSON.parse(props.getProperty('CHAIN_ネタ'));
  const 関連知見 = JSON.parse(props.getProperty('CHAIN_関連知見'));

  if (!ネタ) { Logger.log('Step 2: ネタなし。中断。'); return; }

  // 3媒体 + X記事ダイジェスト = 4本を生成
  const 下書き = 全媒体の下書きを生成(ネタ, 関連知見);

  props.setProperty('CHAIN_下書き', JSON.stringify(下書き));

  Logger.log('Step 2 完了: 4本の下書き生成');
  次のステップをスケジュール('下書き生成_Step3');
}

/** Step 3: 画像生成 + シート書き込み */
function 下書き生成_Step3() {
  const props = PropertiesService.getScriptProperties();
  const 設定 = 設定を取得();
  const ss = SpreadsheetApp.openById(設定.スプレッドシートID);
  const ネタ = JSON.parse(props.getProperty('CHAIN_ネタ'));
  const 下書き = JSON.parse(props.getProperty('CHAIN_下書き'));

  if (!ネタ || !下書き) { Logger.log('Step 3: データなし。中断。'); return; }

  // 画像生成
  const 画像 = 画像を生成(ネタ);

  // 各シートに書き込み
  下書きをシートに保存(ss, 設定.シート_X投稿管理, 下書き.X短文, 'X短文');
  下書きをシートに保存(ss, 設定.シート_X投稿管理, 下書き.X記事, 'X記事');
  下書きをシートに保存(ss, 設定.シート_note記事管理, 下書き.note, 'note');
  下書きをシートに保存(ss, 設定.シート_LinkedIn投稿管理, 下書き.LinkedIn, 'LinkedIn');

  // ネタのステータスを「生成済」に更新
  const ネタシート = ss.getSheetByName(設定.シート_業務改善ネタ);
  ネタのステータスを更新(ネタシート, ネタ.行番号, '生成済');

  Logger.log('Step 3 完了: シート書き込み');
  次のステップをスケジュール('下書き生成_Step4');
}

/** Step 4: ダッシュボード更新 */
function 下書き生成_Step4() {
  const props = PropertiesService.getScriptProperties();

  // ダッシュボード更新
  ダッシュボードを更新();

  // 一時データをクリア
  props.deleteProperty('CHAIN_ネタ');
  props.deleteProperty('CHAIN_関連知見');
  props.deleteProperty('CHAIN_下書き');

  Logger.log('Step 4 完了: 全処理終了');
}

/**
 * 次のステップを1分後にスケジュールする（トリガーチェーン）
 * @param {string} 関数名
 */
function 次のステップをスケジュール(関数名) {
  ScriptApp.newTrigger(関数名)
    .timeBased()
    .after(60 * 1000) // 1分後
    .create();
}

/**
 * 完了した1回限りのトリガーを掃除する（トリガー上限対策）
 * 毎日の最終ステップ or 手動で実行
 */
function 使い捨てトリガーを掃除() {
  const 保持する関数 = [
    '毎朝の下書き一括生成', 'パフォーマンス記録を準備',
    '週次レポート集計', 'アフィリエイト月次集計',
  ];
  ScriptApp.getProjectTriggers().forEach(t => {
    if (!保持する関数.includes(t.getHandlerFunction())) {
      ScriptApp.deleteTrigger(t);
    }
  });
}

/**
 * テスト関数: Apify Dataset APIから直接取得してシートに書き込む
 * GASエディタで手動実行し、ログを確認する
 * 最新のApify実行のdatasetIdを使用
 */
function テスト_Apify取得() {
  // 最新のApify Run（10:35頃の実行）のactorRunIdからdatasetIdを取得
  // Apify コンソール → Runs → 最新のRun → More details → defaultDatasetId をここに貼る
  // とりあえずApify APIで最新Runを取得する
  const APIFY_TOKEN = PropertiesService.getScriptProperties().getProperty('APIFY_TOKEN');
  Logger.log('APIFY_TOKEN: ' + (APIFY_TOKEN ? '設定済み(' + APIFY_TOKEN.substring(0, 5) + '...)' : '未設定'));

  // テスト用: Apifyの直近の実行結果を手動で貼り付けてテスト
  const テストデータ = [
    {
      url: 'https://x.com/muzedevelopment/status/2032864563113156832',
      text: 'Most small businesses don\'t need a "brand experience." They need reviews, trust signals, and consistency.',
      likeCount: 198,
      createdAt: 'Sat Mar 14 17:00:55 +0000 2026',
    },
    {
      url: 'https://x.com/tailopez/status/1963739108322189312',
      text: 'If I had to make money fast in 2025: I\'d sell AI marketing automation to small business owners.',
      likeCount: 1156,
      createdAt: 'Thu Sep 04 23:01:02 +0000 2025',
    }
  ];

  const 設定 = 設定を取得();
  Logger.log('スプレッドシートID: ' + 設定.スプレッドシートID);

  const ss = SpreadsheetApp.openById(設定.スプレッドシートID);
  const シート = ss.getSheetByName(設定.シート_バズポスト収集);
  Logger.log('シート: ' + (シート ? シート.getName() + ' (行数: ' + シート.getLastRow() + ')' : 'null'));

  const 結果 = バズポストを処理(ss, テストデータ, 設定);
  Logger.log('結果: タイプ=' + 結果.タイプ + ', 件数=' + 結果.行リスト.length);
  Logger.log('テスト完了。バズポスト収集シートを確認してください。');
}

/**
 * 毎日22:00: パフォーマンス記録行を準備
 */
function パフォーマンス記録を準備() {
  // TODO: 実装
}

/**
 * 毎週月曜9:00: 3媒体横断の週間KPI集計 + AI学習ループ
 */
function 週次レポート集計() {
  // ダッシュボード更新（最新の未確認下書き・リード等）
  ダッシュボードを更新();

  // AI学習ループ（パフォーマンス分析→頭のDB調整→戦略提案）
  週次学習ループ();

  Logger.log('週次レポート + 学習ループ完了');
}

/**
 * 毎月1日9:00: アフィリエイト月次収益をダッシュボードに集計
 */
function アフィリエイト月次集計() {
  // TODO: 実装
}
