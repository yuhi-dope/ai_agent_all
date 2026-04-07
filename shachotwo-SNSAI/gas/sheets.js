/**
 * スプレッドシート操作ユーティリティ
 */

/**
 * 未処理ネタの最古1件を取得する
 * @param {Sheet} シート - 業務改善ネタシート
 * @returns {Object|null} { タイトル, 本文, 業種, タグ, 行番号 }
 */
function 未処理ネタを取得(シート) {
  const データ = シート.getDataRange().getValues();
  const ヘッダー = データ[0];

  const タイトル列 = ヘッダー.indexOf('タイトル');
  const 本文列 = ヘッダー.indexOf('本文');
  const 業種列 = ヘッダー.indexOf('業種');
  const ステータス列 = ヘッダー.indexOf('ステータス');
  const タグ列 = ヘッダー.indexOf('タグ');

  for (let i = 1; i < データ.length; i++) {
    if (データ[i][ステータス列] === '未処理') {
      return {
        タイトル: データ[i][タイトル列],
        本文: データ[i][本文列],
        業種: データ[i][業種列] || '',
        タグ: (データ[i][タグ列] || '').split(',').map(t => t.trim()),
        行番号: i + 1, // 1-indexed（ヘッダー含む）
      };
    }
  }
  return null;
}

/**
 * 頭のDBから関連エントリを取得する
 * @param {Spreadsheet} ss
 * @param {Array} タグ - 検索タグ
 * @param {number} 件数 - 取得件数
 * @returns {Array} [{ カテゴリ, テキスト, タグ }]
 */
function 関連する頭のDBを取得(ss, タグ, 件数) {
  const 設定 = 設定を取得();
  const シート = ss.getSheetByName(設定.シート_頭のDB);
  const データ = シート.getDataRange().getValues();
  const ヘッダー = データ[0];

  const カテゴリ列 = ヘッダー.indexOf('カテゴリ');
  const テキスト列 = ヘッダー.indexOf('テキスト');
  const タグ列 = ヘッダー.indexOf('タグ');
  const 重要度列 = ヘッダー.indexOf('重要度');

  const エントリ一覧 = [];
  for (let i = 1; i < データ.length; i++) {
    エントリ一覧.push({
      カテゴリ: データ[i][カテゴリ列],
      テキスト: データ[i][テキスト列],
      タグ: (データ[i][タグ列] || '').split(',').map(t => t.trim()),
      重要度: データ[i][重要度列] || 3,
    });
  }

  // タグマッチ → 重要度順でソート
  if (タグ.length > 0) {
    エントリ一覧.sort((a, b) => {
      const aマッチ = a.タグ.filter(t => タグ.includes(t)).length;
      const bマッチ = b.タグ.filter(t => タグ.includes(t)).length;
      if (bマッチ !== aマッチ) return bマッチ - aマッチ;
      return b.重要度 - a.重要度;
    });
  } else {
    エントリ一覧.sort((a, b) => b.重要度 - a.重要度);
  }

  return エントリ一覧.slice(0, 件数);
}

/**
 * 下書きをシートに保存する
 * @param {Spreadsheet} ss
 * @param {string} シート名
 * @param {string} 内容 - 生成されたテキスト
 * @param {string} タイプ - 投稿タイプ（X短文/X記事/note/LinkedIn/引用RT）
 */
function 下書きをシートに保存(ss, シート名, 内容, タイプ) {
  const シート = ss.getSheetByName(シート名);
  const 現在日時 = new Date();
  const ID = Utilities.getUuid().slice(0, 8);

  シート.appendRow([
    ID,                    // ID
    タイプ,                // タイプ
    内容,                  // 本文
    '未確認',              // ステータス（ダッシュボードで確認）
    現在日時,              // 生成日時
    '',                    // 投稿予定日（人間が設定）
    '',                    // 投稿日（実績）
    '',                    // パフォーマンス（後で記入）
  ]);
}

/**
 * ネタのステータスを更新する
 * @param {Sheet} シート
 * @param {number} 行番号 - 行番号（1-indexed）
 * @param {string} 新ステータス
 */
function ネタのステータスを更新(シート, 行番号, 新ステータス) {
  const ヘッダー = シート.getRange(1, 1, 1, シート.getLastColumn()).getValues()[0];
  const ステータス列 = ヘッダー.indexOf('ステータス') + 1; // 1-indexed
  シート.getRange(行番号, ステータス列).setValue(新ステータス);
}
