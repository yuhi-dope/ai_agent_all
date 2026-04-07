/**
 * 自動マーケティング学習エンジン
 * パフォーマンスデータを分析し、頭のDB重要度を自動調整、
 * 勝ちパターンを抽出して次週の戦略を提案する。
 *
 * 毎週月曜9:00に自動実行（週次レポート集計と統合）
 */

/**
 * 週次学習ループ（メイン関数）
 * 1. パフォーマンスデータ収集
 * 2. 頭のDB重要度を自動調整
 * 3. Geminiで勝ちパターン分析
 * 4. 次週戦略をダッシュボードに表示
 */
function 週次学習ループ() {
  const 設定 = 設定を取得();
  const ss = SpreadsheetApp.openById(設定.スプレッドシートID);

  Logger.log('=== 週次学習ループ開始 ===');

  // 1. 各媒体のパフォーマンスデータを収集
  const X成績 = 媒体パフォーマンスを取得(ss, 設定.シート_X投稿管理);
  const note成績 = 媒体パフォーマンスを取得(ss, 設定.シート_note記事管理);
  const LI成績 = 媒体パフォーマンスを取得(ss, 設定.シート_LinkedIn投稿管理);

  const 全成績 = [...X成績, ...note成績, ...LI成績];
  Logger.log('パフォーマンスデータ: ' + 全成績.length + '件');

  if (全成績.length === 0) {
    Logger.log('パフォーマンスデータなし。学習スキップ。');
    return;
  }

  // 2. 頭のDB重要度を自動調整
  頭のDB重要度を調整(ss, 設定, 全成績);

  // 3. Geminiで勝ちパターンを分析
  const 分析結果 = 勝ちパターンを分析(全成績, 設定);

  // 4. ダッシュボードに学習結果を表示
  学習結果をダッシュボードに表示(ss, 設定, 分析結果, 全成績);

  Logger.log('=== 週次学習ループ完了 ===');
}

/**
 * 媒体別のパフォーマンスデータを取得する
 * 投稿済みステータスの投稿からいいね数等を取得
 */
function 媒体パフォーマンスを取得(ss, シート名) {
  const シート = ss.getSheetByName(シート名);
  if (!シート || シート.getLastRow() <= 1) return [];

  const データ = シート.getDataRange().getValues();
  const ヘッダー = データ[0];

  const タイプ列 = ヘッダー.indexOf('タイプ');
  const 本文列 = ヘッダー.indexOf('本文');
  const ステータス列 = ヘッダー.indexOf('ステータス');
  const いいね列 = ヘッダー.indexOf('いいね') !== -1 ? ヘッダー.indexOf('いいね') : ヘッダー.indexOf('スキ数');
  const imp列 = ヘッダー.indexOf('imp数') !== -1 ? ヘッダー.indexOf('imp数') : ヘッダー.indexOf('PV数');

  const 結果 = [];
  for (let i = 1; i < データ.length; i++) {
    const ステータス = データ[i][ステータス列];
    if (ステータス !== '投稿済') continue;

    const いいね = parseInt(データ[i][いいね列]) || 0;
    const imp = parseInt(データ[i][imp列]) || 0;
    const エンゲージメント率 = imp > 0 ? (いいね / imp * 100) : 0;

    結果.push({
      媒体: シート名.replace('投稿管理', '').replace('記事管理', ''),
      タイプ: データ[i][タイプ列] || '',
      本文: (データ[i][本文列] || '').substring(0, 200),
      いいね: いいね,
      imp: imp,
      エンゲージメント率: エンゲージメント率,
      行番号: i + 1,
    });
  }

  return 結果;
}

/**
 * 頭のDB重要度を自動調整する
 * 高パフォーマンス投稿で使われた知見 → 重要度UP
 * 低パフォーマンス投稿で使われた知見 → 重要度DOWN
 */
function 頭のDB重要度を調整(ss, 設定, 全成績) {
  const 頭のDBシート = ss.getSheetByName(設定.シート_頭のDB);
  if (!頭のDBシート || 頭のDBシート.getLastRow() <= 1) return;

  const DBデータ = 頭のDBシート.getDataRange().getValues();
  const ヘッダー = DBデータ[0];
  const テキスト列 = ヘッダー.indexOf('テキスト');
  const 重要度列 = ヘッダー.indexOf('重要度');
  const タグ列 = ヘッダー.indexOf('タグ');

  // 平均エンゲージメント率を計算
  const 有効成績 = 全成績.filter(s => s.エンゲージメント率 > 0);
  if (有効成績.length === 0) return;
  const 平均率 = 有効成績.reduce((sum, s) => sum + s.エンゲージメント率, 0) / 有効成績.length;

  // 高パフォーマンス投稿（平均の1.5倍以上）
  const 勝ち投稿 = 有効成績.filter(s => s.エンゲージメント率 >= 平均率 * 1.5);
  // 低パフォーマンス投稿（平均の0.5倍以下）
  const 負け投稿 = 有効成績.filter(s => s.エンゲージメント率 <= 平均率 * 0.5);

  let 調整件数 = 0;

  for (let i = 1; i < DBデータ.length; i++) {
    const DBテキスト = (DBデータ[i][テキスト列] || '').toLowerCase();
    const DBタグ = (DBデータ[i][タグ列] || '').toLowerCase();
    const 現在の重要度 = parseInt(DBデータ[i][重要度列]) || 3;

    // 勝ち投稿の本文にDBエントリのキーワードが含まれているか
    const 勝ちマッチ = 勝ち投稿.some(投稿 => {
      const 投稿テキスト = 投稿.本文.toLowerCase();
      return DBタグ.split(',').some(タグ => タグ.trim() && 投稿テキスト.includes(タグ.trim()));
    });

    const 負けマッチ = 負け投稿.some(投稿 => {
      const 投稿テキスト = 投稿.本文.toLowerCase();
      return DBタグ.split(',').some(タグ => タグ.trim() && 投稿テキスト.includes(タグ.trim()));
    });

    let 新重要度 = 現在の重要度;
    if (勝ちマッチ && !負けマッチ) {
      新重要度 = Math.min(5, 現在の重要度 + 1);
    } else if (負けマッチ && !勝ちマッチ) {
      新重要度 = Math.max(1, 現在の重要度 - 1);
    }

    if (新重要度 !== 現在の重要度) {
      頭のDBシート.getRange(i + 1, 重要度列 + 1).setValue(新重要度);
      調整件数++;
      Logger.log(`BRAIN-${String(i).padStart(3, '0')}: 重要度 ${現在の重要度}→${新重要度}`);
    }
  }

  Logger.log('頭のDB重要度調整: ' + 調整件数 + '件変更');
}

/**
 * Geminiで勝ちパターンを分析する
 * @returns {Object} { 勝ちパターン, 負けパターン, 来週の戦略, 新規知見 }
 */
function 勝ちパターンを分析(全成績, 設定) {
  if (全成績.length < 3) {
    return { 勝ちパターン: 'データ不足（3件以上必要）', 負けパターン: '', 来週の戦略: '', 新規知見: [] };
  }

  const 成績テキスト = 全成績
    .sort((a, b) => b.エンゲージメント率 - a.エンゲージメント率)
    .map((s, i) => `${i + 1}. [${s.媒体}/${s.タイプ}] エンゲージメント率:${s.エンゲージメント率.toFixed(2)}% いいね:${s.いいね} 本文:${s.本文}`)
    .join('\n');

  const システム指示 = `あなたはSNSマーケティング分析AIです。
投稿パフォーマンスデータを分析し、以下の4項目をJSON形式で返してください。

ターゲット: 中小企業の経営者（建設業・製造業・歯科・介護・士業等）
発信テーマ: AI × 業務改善 × 属人化解消

【出力JSON形式（厳守）】
{
  "勝ちパターン": "エンゲージメント率が高い投稿の共通点を3つ",
  "負けパターン": "エンゲージメント率が低い投稿の共通点を3つ",
  "来週の戦略": "来週の投稿で意識すべきこと3つ",
  "新規知見": [
    {"テキスト": "分析から得られた知見1", "タグ": "タグ1,タグ2", "重要度": 4},
    {"テキスト": "分析から得られた知見2", "タグ": "タグ1,タグ2", "重要度": 4}
  ]
}

JSONのみ返してください。マークダウンのコードブロックは不要です。`;

  try {
    const 結果テキスト = Geminiでテキスト生成(
      `以下の投稿パフォーマンスデータを分析してください:\n\n${成績テキスト}`,
      システム指示,
      { 温度: 0.3, 最大トークン: 2000 }
    );

    // JSONパース（コードブロックが付いている場合に対応）
    const jsonStr = 結果テキスト.replace(/```json\n?/g, '').replace(/```\n?/g, '').trim();
    const 分析 = JSON.parse(jsonStr);
    Logger.log('Gemini分析完了: 新規知見' + (分析.新規知見 ? 分析.新規知見.length : 0) + '件');
    return 分析;
  } catch (e) {
    Logger.log('Gemini分析エラー: ' + e.message);
    return { 勝ちパターン: '分析エラー', 負けパターン: '', 来週の戦略: '', 新規知見: [] };
  }
}

/**
 * 学習結果をダッシュボードに表示 + 新規知見を頭のDBに追加
 */
function 学習結果をダッシュボードに表示(ss, 設定, 分析結果, 全成績) {
  const ダッシュボード = ss.getSheetByName(設定.シート_ダッシュボード);

  // ダッシュボードの最終行を探す
  let 行 = ダッシュボード.getLastRow() + 2;

  // === 学習結果セクション ===
  ダッシュボード.getRange(行, 1).setValue('【AI学習レポート（自動生成）】');
  ダッシュボード.getRange(行, 1).setFontWeight('bold').setFontSize(14);
  行++;

  ダッシュボード.getRange(行, 1).setValue('更新日時: ' + new Date().toLocaleString('ja-JP'));
  ダッシュボード.getRange(行, 1).setFontColor('#9e9e9e');
  行 += 2;

  // 勝ちパターン
  ダッシュボード.getRange(行, 1).setValue('勝ちパターン:');
  ダッシュボード.getRange(行, 1).setFontWeight('bold');
  行++;
  ダッシュボード.getRange(行, 1).setValue(分析結果.勝ちパターン || 'データ不足');
  行 += 2;

  // 負けパターン
  ダッシュボード.getRange(行, 1).setValue('負けパターン:');
  ダッシュボード.getRange(行, 1).setFontWeight('bold');
  行++;
  ダッシュボード.getRange(行, 1).setValue(分析結果.負けパターン || 'データ不足');
  行 += 2;

  // 来週の戦略
  ダッシュボード.getRange(行, 1).setValue('来週の戦略:');
  ダッシュボード.getRange(行, 1).setFontWeight('bold').setFontColor('#1565c0');
  行++;
  ダッシュボード.getRange(行, 1).setValue(分析結果.来週の戦略 || 'データ不足');
  行 += 2;

  // KPIサマリー
  const 有効成績 = 全成績.filter(s => s.いいね > 0);
  if (有効成績.length > 0) {
    const 平均いいね = (有効成績.reduce((s, x) => s + x.いいね, 0) / 有効成績.length).toFixed(0);
    const 最高いいね = Math.max(...有効成績.map(x => x.いいね));
    ダッシュボード.getRange(行, 1).setValue(`今週の成績: 投稿${全成績.length}件 / 平均いいね${平均いいね} / 最高いいね${最高いいね}`);
    行 += 2;
  }

  // === 新規知見を頭のDBに自動追加 ===
  if (分析結果.新規知見 && 分析結果.新規知見.length > 0) {
    const 頭のDBシート = ss.getSheetByName(設定.シート_頭のDB);
    const 最終行 = 頭のDBシート.getLastRow();

    // 現在の最大IDを取得
    let 最大番号 = 0;
    if (最終行 > 1) {
      const ID一覧 = 頭のDBシート.getRange(2, 1, 最終行 - 1, 1).getValues();
      ID一覧.forEach(行 => {
        const match = (行[0] || '').match(/BRAIN-(\d+)/);
        if (match) 最大番号 = Math.max(最大番号, parseInt(match[1]));
      });
    }

    let 追加件数 = 0;
    for (const 知見 of 分析結果.新規知見) {
      最大番号++;
      const ID = 'BRAIN-' + String(最大番号).padStart(3, '0');
      頭のDBシート.appendRow([
        ID,
        'フレームワーク',
        知見.テキスト || '',
        知見.タグ || 'AI学習,パフォーマンス分析',
        知見.重要度 || 4,
      ]);
      追加件数++;
      Logger.log('頭のDB自動追加: ' + ID + ' = ' + (知見.テキスト || '').substring(0, 50));
    }

    ダッシュボード.getRange(行, 1).setValue('頭のDBに' + 追加件数 + '件の知見を自動追加しました');
    ダッシュボード.getRange(行, 1).setFontColor('#2e7d32');
  }

  Logger.log('ダッシュボード更新完了');
}
