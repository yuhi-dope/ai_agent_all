/**
 * Apify Webhook受信・パース
 *
 * フォールバック戦略（Apify停止時）:
 * 1. RSSフォールバック: Google News RSS + はてなブックマークRSS で代替
 *    → 手動で「RSSフォールバックを実行」を呼ぶ
 * 2. 手動キュレーション: バズポスト収集シートに直接URLを貼り付け
 *    → 「手動バズポストを処理」を呼ぶとGeminiで構造化
 */

/**
 * Apifyからのデータを処理する
 * デフォルトWebhookペイロード（resource内にdefaultDatasetId）に対応。
 * Apify Dataset APIからツイートデータを取得する。
 *
 * @param {Object} データ - Webhook POSTデータ（デフォルトテンプレート）
 * @returns {Object} { タイプ: 'バズ'|'ニュース', 行リスト: Array }
 */
function Apifyデータを処理(データ) {
  const 設定 = 設定を取得();
  const ss = SpreadsheetApp.openById(設定.スプレッドシートID);

  Logger.log('Apifyデータ処理開始。キー: ' + Object.keys(データ).join(', '));

  // デフォルトペイロードからdatasetIdを取得
  const datasetId = (データ.resource && データ.resource.defaultDatasetId)
    || (データ.eventData && データ.eventData.defaultDatasetId)
    || データ.defaultDatasetId;

  Logger.log('datasetId: ' + (datasetId || 'なし'));

  if (datasetId) {
    const items = Apifyデータセットを取得(datasetId);
    Logger.log('Dataset APIから取得: ' + items.length + '件');

    if (items.length === 0) {
      Logger.log('Dataset APIから0件。ペイロードをダンプ: ' + JSON.stringify(データ).substring(0, 300));
      return { タイプ: 'なし', 行リスト: [] };
    }

    Logger.log('最初のアイテムのキー: ' + Object.keys(items[0]).join(', '));
    return バズポストを処理(ss, items, 設定);
  }

  // フォールバック: 直接itemsが含まれている場合
  if (データ.actorId && データ.items) {
    const items = データ.items || [];
    Logger.log('直接items: ' + items.length + '件');
    if (items.length === 0) return { タイプ: 'なし', 行リスト: [] };
    return バズポストを処理(ss, items, 設定);
  }

  // フォールバック: 生データ配列
  if (Array.isArray(データ)) {
    Logger.log('生データ配列: ' + データ.length + '件');
    return バズポストを処理(ss, データ, 設定);
  }

  Logger.log('不明なWebhookデータ形式: ' + JSON.stringify(データ).substring(0, 500));
  return { タイプ: '不明', 行リスト: [] };
}

/**
 * Apify Dataset APIからアイテムを取得する
 * @param {string} datasetId - ApifyのデータセットID
 * @returns {Array} アイテム一覧
 */
function Apifyデータセットを取得(datasetId) {
  const APIFY_TOKEN = PropertiesService.getScriptProperties().getProperty('APIFY_TOKEN');
  let url = `https://api.apify.com/v2/datasets/${datasetId}/items?format=json`;
  if (APIFY_TOKEN) url += `&token=${APIFY_TOKEN}`;

  try {
    const response = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
    const ステータス = response.getResponseCode();
    if (ステータス !== 200) {
      Logger.log(`Apify Dataset API エラー (${ステータス}): ${response.getContentText().substring(0, 200)}`);
      return [];
    }
    return JSON.parse(response.getContentText());
  } catch (e) {
    Logger.log('Apify Dataset取得エラー: ' + e.message);
    return [];
  }
}

/**
 * バズポストをシートに書き込む
 */
function バズポストを処理(ss, items, 設定) {
  const シート = ss.getSheetByName(設定.シート_バズポスト収集);
  Logger.log('バズポスト処理開始: ' + items.length + '件, シート: ' + (シート ? シート.getName() : 'null'));
  const 行リスト = [];

  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    // authorがオブジェクトの場合があるので展開する
    let 著者 = '';
    if (typeof item.author === 'object' && item.author) {
      著者 = item.author.userName || item.author.name || item.author.screenName || '';
    } else {
      著者 = item.user_name || item.author || item.screenName || '';
    }
    // URLからスクリーンネームを抽出（フォールバック）
    if (!著者 && item.url) {
      const match = item.url.match(/x\.com\/([^\/]+)\//);
      if (match) 著者 = '@' + match[1];
    }
    const テキスト = item.full_text || item.text || '';
    const いいね数 = item.likeCount || item.favorite_count || item.likes || 0;
    const URL = item.url || item.twitterUrl || '';

    Logger.log(`[${i}] いいね=${いいね数}, テキスト=${テキスト.substring(0, 50)}...`);

    // フィルタ: いいね800以上（Apifyはコスト高いので本当にバズった投稿だけ）
    if (いいね数 < 800) {
      Logger.log(`[${i}] スキップ: いいね${いいね数} < 800`);
      continue;
    }

    // 言語判定
    const 日本語 = 日本語か判定(テキスト);
    const 言語 = 日本語 ? 'ja' : 'en';

    const ID = Utilities.getUuid().slice(0, 8);
    const 検索クエリ = item.searchQuery || item.query || '';

    Logger.log(`[${i}] 書き込み: ID=${ID}, いいね=${いいね数}, 言語=${言語}`);
    シート.appendRow([ID, URL, 著者, テキスト, いいね数, new Date(), '未処理', 言語, 検索クエリ]);
    行リスト.push({ ID, URL, 著者, テキスト, いいね数, 言語 });
  }

  Logger.log('バズポスト処理完了: ' + 行リスト.length + '件書き込み');
  return { タイプ: 'バズ', 行リスト };
}

/**
 * ニュースをシートに書き込む
 */
function ニュースを処理(ss, items, 設定) {
  const シート = ss.getSheetByName(設定.シート_ニュース収集);
  const 行リスト = [];

  for (const item of items) {
    const タイトル = item.title || '';
    const URL = item.url || '';
    const ソース = item.source || '';
    const 要約 = item.description || item.summary || '';

    const ID = Utilities.getUuid().slice(0, 8);
    シート.appendRow([ID, タイトル, URL, ソース, 要約, new Date(), '未処理']);
    行リスト.push({ ID, タイトル, URL, ソース, 要約 });
  }

  return { タイプ: 'ニュース', 行リスト };
}

/**
 * 毎日のRSSネタ収集（メイン情報ソース）
 * 毎日7:00にトリガーで自動実行。
 * Google News / はてブ / Zenn / note から業界ニュース・技術記事を収集。
 */
function 毎日のRSS収集() {
  const 設定 = 設定を取得();
  const ss = SpreadsheetApp.openById(設定.スプレッドシートID);
  const シート = ss.getSheetByName(設定.シート_ニュース収集);

  // Google News RSS URLヘルパー
  const gn = (q, lang) => {
    const hl = lang === 'en' ? 'en' : 'ja';
    const gl = lang === 'en' ? 'US' : 'JP';
    return `https://news.google.com/rss/search?q=${encodeURIComponent(q)}&hl=${hl}&gl=${gl}`;
  };

  const RSSフィード一覧 = [
    // === 共通 ===
    { url: gn('中小企業 AI'), ソース: 'Google News JP', 業種: '共通', 件数: 3 },
    { url: gn('業務自動化 AI'), ソース: 'Google News JP', 業種: '共通', 件数: 3 },
    { url: gn('DX 経営'), ソース: 'Google News JP', 業種: '共通', 件数: 2 },
    { url: gn('属人化 業務改善'), ソース: 'Google News JP', 業種: '共通', 件数: 2 },

    // === 建設業 ===
    { url: gn('建設業 DX'), ソース: 'Google News JP', 業種: '建設業', 件数: 2 },
    { url: gn('建設 AI 積算'), ソース: 'Google News JP', 業種: '建設業', 件数: 2 },

    // === 製造業 ===
    { url: gn('製造業 AI'), ソース: 'Google News JP', 業種: '製造業', 件数: 2 },
    { url: gn('製造 品質管理 自動化'), ソース: 'Google News JP', 業種: '製造業', 件数: 2 },

    // === 歯科 ===
    { url: gn('歯科 IT DX'), ソース: 'Google News JP', 業種: '歯科', 件数: 2 },

    // === 介護福祉 ===
    { url: gn('介護 ICT AI'), ソース: 'Google News JP', 業種: '介護福祉', 件数: 2 },
    { url: gn('介護 記録 自動化'), ソース: 'Google News JP', 業種: '介護福祉', 件数: 2 },

    // === 士業 ===
    { url: gn('税理士 DX AI'), ソース: 'Google News JP', 業種: '士業', 件数: 2 },
    { url: gn('社労士 業務効率化'), ソース: 'Google News JP', 業種: '士業', 件数: 2 },

    // === 不動産 ===
    { url: gn('不動産 AI DX'), ソース: 'Google News JP', 業種: '不動産', 件数: 2 },

    // === 物流運送 ===
    { url: gn('物流 AI 自動化'), ソース: 'Google News JP', 業種: '物流運送', 件数: 2 },

    // === 飲食業 ===
    { url: gn('飲食店 DX IT'), ソース: 'Google News JP', 業種: '飲食業', 件数: 2 },

    // === 医療クリニック ===
    { url: gn('クリニック 電子カルテ AI'), ソース: 'Google News JP', 業種: '医療', 件数: 2 },

    // === 薬局 ===
    { url: gn('調剤薬局 DX'), ソース: 'Google News JP', 業種: '薬局', 件数: 2 },

    // === 美容エステ ===
    { url: gn('美容 エステ 予約 DX'), ソース: 'Google News JP', 業種: '美容', 件数: 2 },

    // === 自動車整備 ===
    { url: gn('自動車整備 IT DX'), ソース: 'Google News JP', 業種: '自動車整備', 件数: 2 },

    // === ホテル旅館 ===
    { url: gn('ホテル 旅館 DX AI'), ソース: 'Google News JP', 業種: 'ホテル', 件数: 2 },

    // === EC小売 ===
    { url: gn('EC 小売 AI 自動化'), ソース: 'Google News JP', 業種: 'EC小売', 件数: 2 },

    // === 人材派遣 ===
    { url: gn('人材派遣 DX AI'), ソース: 'Google News JP', 業種: '人材派遣', 件数: 2 },

    // === 建築設計 ===
    { url: gn('建築設計 BIM AI'), ソース: 'Google News JP', 業種: '建築設計', 件数: 2 },

    // === 英語: 主要業種 ===
    { url: gn('small business AI automation', 'en'), ソース: 'Google News EN', 業種: '共通', 件数: 2 },
    { url: gn('construction AI technology', 'en'), ソース: 'Google News EN', 業種: '建設業', 件数: 2 },
    { url: gn('manufacturing AI automation', 'en'), ソース: 'Google News EN', 業種: '製造業', 件数: 2 },
    { url: gn('healthcare clinic AI', 'en'), ソース: 'Google News EN', 業種: '医療', 件数: 2 },

    // === テック系 ===
    { url: 'https://b.hatena.ne.jp/hotentry/it.rss', ソース: 'はてブ IT', 業種: '共通', 件数: 5 },
    { url: 'https://b.hatena.ne.jp/search/tag?q=AI&mode=rss', ソース: 'はてブ AI', 業種: '共通', 件数: 3 },
    { url: 'https://zenn.dev/feed', ソース: 'Zenn', 業種: '共通', 件数: 5 },
  ];

  // 重複チェック用: 既存URLを一括取得（ループ内で毎回取得しない）
  const 既存データ = シート.getLastRow() > 1 ? シート.getDataRange().getValues() : [];
  const 既存URL一覧 = new Set(既存データ.map(行 => 行[2]));

  let 追加件数 = 0;

  for (const フィード of RSSフィード一覧) {
    try {
      const response = UrlFetchApp.fetch(フィード.url, { muteHttpExceptions: true });
      if (response.getResponseCode() !== 200) {
        Logger.log('RSS取得失敗 (' + フィード.ソース + '): HTTP ' + response.getResponseCode());
        continue;
      }

      const contentText = response.getContentText();
      let items = [];

      try {
        const xml = XmlService.parse(contentText);
        const root = xml.getRootElement();
        const ns = root.getNamespace();

        // RSS 2.0 形式
        const channel = root.getChild('channel', ns);
        if (channel) {
          items = channel.getChildren('item', ns);
        }

        // Atom 形式のフォールバック（Zenn等）
        if (items.length === 0) {
          const atomNs = XmlService.getNamespace('http://www.w3.org/2005/Atom');
          items = root.getChildren('entry', atomNs);
          if (items.length === 0) {
            items = root.getChildren('entry');
          }
        }
      } catch (parseErr) {
        Logger.log('XMLパースエラー (' + フィード.ソース + '): ' + parseErr.message);
        continue;
      }

      const 取得上限 = フィード.件数 || 5;
      let フィード追加数 = 0;

      for (const item of items) {
        if (フィード追加数 >= 取得上限) break;

        const ns = item.getNamespace();
        const タイトル = item.getChildText('title', ns) || item.getChildText('title') || '';
        const URL = item.getChildText('link', ns) || item.getChildText('link') || '';
        const 要約 = (item.getChildText('description', ns) || item.getChildText('summary') || item.getChildText('content') || '').substring(0, 500);

        if (!URL || 既存URL一覧.has(URL)) continue;

        const ID = Utilities.getUuid().slice(0, 8);
        const 業種 = フィード.業種 || '共通';
        シート.appendRow([ID, タイトル, URL, フィード.ソース, 要約, new Date(), '未処理', 業種]);
        既存URL一覧.add(URL);
        追加件数++;
        フィード追加数++;
      }

      Logger.log(フィード.ソース + ': ' + フィード追加数 + '件追加');
    } catch (e) {
      Logger.log('RSS取得エラー (' + フィード.ソース + '): ' + e.message);
    }
  }

  Logger.log('毎日のRSS収集完了: 合計' + 追加件数 + '件追加');
  // ダッシュボード更新（収集結果を反映）
  if (追加件数 > 0) {
    ダッシュボードを更新();
  }
}

/** RSSフォールバックを実行（旧名。互換性のため残す） */
function RSSフォールバックを実行() {
  毎日のRSS収集();
}

/** 日本語が含まれているか判定する */
function 日本語か判定(テキスト) {
  return /[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9faf]/.test(テキスト);
}

/** 直近24時間以内か判定する */
function 直近24時間以内か判定(日時文字列) {
  try {
    const 日時 = new Date(日時文字列);
    const 現在 = new Date();
    return (現在 - 日時) < 24 * 60 * 60 * 1000;
  } catch (e) {
    return true; // パース失敗時は通す
  }
}
