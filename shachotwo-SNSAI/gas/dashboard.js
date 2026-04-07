/**
 * ダッシュボードシート更新
 * 人間が毎日最初に見るシート。未確認の下書き一覧・KPI・アラートを集約。
 */

/**
 * ダッシュボードを更新する
 * 各シートから「未確認」ステータスの下書きを集めて表示
 */
function ダッシュボードを更新() {
  const 設定 = 設定を取得();
  const ss = SpreadsheetApp.openById(設定.スプレッドシートID);
  const ダッシュボード = ss.getSheetByName(設定.シート_ダッシュボード);

  // ダッシュボードをクリア（ヘッダー以外）
  if (ダッシュボード.getLastRow() > 1) {
    ダッシュボード.getRange(2, 1, ダッシュボード.getLastRow() - 1, ダッシュボード.getLastColumn()).clearContent();
  }

  let 現在行 = 2;

  // === セクション1: 未確認の下書き ===
  ダッシュボード.getRange(現在行, 1).setValue('【未確認の下書き】');
  ダッシュボード.getRange(現在行, 1).setFontWeight('bold').setFontSize(14);
  現在行++;

  ダッシュボード.getRange(現在行, 1, 1, 6).setValues([['媒体', 'タイプ', '本文（冒頭100字）', '生成日時', 'ステータス', '操作メモ']]);
  ダッシュボード.getRange(現在行, 1, 1, 6).setFontWeight('bold').setBackground('#e8eaf6');
  現在行++;

  現在行 = 未確認下書きを追加(ss, 設定.シート_X投稿管理, 'X', ダッシュボード, 現在行);
  現在行 = 未確認下書きを追加(ss, 設定.シート_note記事管理, 'note', ダッシュボード, 現在行);
  現在行 = 未確認下書きを追加(ss, 設定.シート_LinkedIn投稿管理, 'LinkedIn', ダッシュボード, 現在行);

  if (現在行 === 4) {
    ダッシュボード.getRange(現在行, 1).setValue('（未確認の下書きはありません）');
    現在行++;
  }

  現在行 += 2;

  // === セクション2: 今週のKPIサマリー ===
  ダッシュボード.getRange(現在行, 1).setValue('【今週のKPIサマリー】');
  ダッシュボード.getRange(現在行, 1).setFontWeight('bold').setFontSize(14);
  現在行++;

  ダッシュボード.getRange(現在行, 1, 1, 4).setValues([['媒体', '投稿数', 'imp合計', 'リード数']]);
  ダッシュボード.getRange(現在行, 1, 1, 4).setFontWeight('bold').setBackground('#e8f5e9');
  現在行++;

  ダッシュボード.getRange(現在行, 1, 3, 4).setValues([
    ['X', '', '', ''],
    ['note', '', '', ''],
    ['LinkedIn', '', '', ''],
  ]);
  現在行 += 4;

  // === セクション3: アフィリエイト収益（月次） ===
  ダッシュボード.getRange(現在行, 1).setValue('【アフィリエイト収益（今月）】');
  ダッシュボード.getRange(現在行, 1).setFontWeight('bold').setFontSize(14);
  現在行++;

  ダッシュボード.getRange(現在行, 1, 1, 3).setValues([['ASP', '成約数', '収益']]);
  ダッシュボード.getRange(現在行, 1, 1, 3).setFontWeight('bold').setBackground('#fff3e0');
  現在行++;

  ダッシュボード.getRange(現在行, 1).setValue('（月次レポート実行後に表示）');
  現在行 += 2;

  // === セクション4: 未対応リード ===
  ダッシュボード.getRange(現在行, 1).setValue('【未対応リード】');
  ダッシュボード.getRange(現在行, 1).setFontWeight('bold').setFontSize(14);
  現在行++;

  const リードシート = ss.getSheetByName(設定.シート_リード管理);
  if (リードシート && リードシート.getLastRow() > 1) {
    const リードデータ = リードシート.getDataRange().getValues();
    const ヘッダー = リードデータ[0];
    const ステータス列 = ヘッダー.indexOf('ステータス');
    const 相手名列 = ヘッダー.indexOf('相手名');
    const 企業名列 = ヘッダー.indexOf('企業名');
    const 経路列 = ヘッダー.indexOf('経路');

    ダッシュボード.getRange(現在行, 1, 1, 4).setValues([['相手名', '企業名', '経路', 'ステータス']]);
    ダッシュボード.getRange(現在行, 1, 1, 4).setFontWeight('bold').setBackground('#fce4ec');
    現在行++;

    for (let i = 1; i < リードデータ.length; i++) {
      const ステータス = リードデータ[i][ステータス列];
      if (ステータス === '初回DM' || ステータス === 'ヒアリング済') {
        ダッシュボード.getRange(現在行, 1, 1, 4).setValues([[
          リードデータ[i][相手名列],
          リードデータ[i][企業名列],
          リードデータ[i][経路列] || '',
          ステータス,
        ]]);
        現在行++;
      }
    }
  }

  // === セクション5: アラート ===
  現在行 += 2;
  ダッシュボード.getRange(現在行, 1).setValue('【アラート】');
  ダッシュボード.getRange(現在行, 1).setFontWeight('bold').setFontSize(14);
  現在行++;

  // ネタ残数チェック
  const ネタシート = ss.getSheetByName(設定.シート_業務改善ネタ);
  const ネタ残数 = ステータス件数をカウント(ネタシート, '未処理');
  if (ネタ残数 < 5) {
    ダッシュボード.getRange(現在行, 1).setValue(`注意: ネタ残り${ネタ残数}件（20件以上を推奨）`);
    ダッシュボード.getRange(現在行, 1).setFontColor('#d32f2f');
    現在行++;
  } else {
    ダッシュボード.getRange(現在行, 1).setValue(`ネタ残り${ネタ残数}件（OK）`);
    現在行++;
  }

  // 最終更新日時
  現在行 += 2;
  ダッシュボード.getRange(現在行, 1).setValue('最終更新: ' + new Date().toLocaleString('ja-JP'));
  ダッシュボード.getRange(現在行, 1).setFontColor('#9e9e9e');
}

/**
 * 指定シートの未確認下書きをダッシュボードに追加する
 */
function 未確認下書きを追加(ss, シート名, 媒体名, ダッシュボード, 開始行) {
  const シート = ss.getSheetByName(シート名);
  if (!シート || シート.getLastRow() <= 1) return 開始行;

  const データ = シート.getDataRange().getValues();
  let 行 = 開始行;

  for (let i = 1; i < データ.length; i++) {
    if (データ[i][3] === '未確認') {
      const プレビュー = (データ[i][2] || '').toString().substring(0, 100) + '...';
      ダッシュボード.getRange(行, 1, 1, 6).setValues([[
        媒体名,
        データ[i][1],     // タイプ
        プレビュー,       // 本文冒頭100字
        データ[i][4],     // 生成日時
        データ[i][3],     // ステータス
        '',               // 操作メモ
      ]]);
      行++;
    }
  }
  return 行;
}

/**
 * シート内の指定ステータスの件数をカウントする
 */
function ステータス件数をカウント(シート, ステータス) {
  if (!シート || シート.getLastRow() <= 1) return 0;
  const データ = シート.getDataRange().getValues();
  const ヘッダー = データ[0];
  const ステータス列 = ヘッダー.indexOf('ステータス');
  let 件数 = 0;
  for (let i = 1; i < データ.length; i++) {
    if (データ[i][ステータス列] === ステータス) 件数++;
  }
  return 件数;
}
