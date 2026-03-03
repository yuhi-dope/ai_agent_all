/**
 * AI社員 営業資料 — Google Slides 自動生成スクリプト (バウンドスクリプト版)
 *
 * 使い方:
 *   1. Google Slides で新しいプレゼンテーションを作成（空のスライド1枚の状態）
 *   2. メニュー「拡張機能 > Apps Script」を開く
 *   3. このファイルの中身を全てコピー&ペースト
 *   4. 上部の「▶ 実行」ボタンで main() を実行
 *   5. 初回はアクセス許可を求められるので「許可」
 *   6. スライドに戻ると全20枚が生成されている
 */

// ============================================================
// 定数: デザイン設定
// ============================================================
const COLORS = {
  NAVY:        { red: 0.10, green: 0.12, blue: 0.25 },  // 濃紺 (背景/見出し)
  WHITE:       { red: 1.00, green: 1.00, blue: 1.00 },
  LIGHT_GRAY:  { red: 0.95, green: 0.95, blue: 0.96 },
  DARK_GRAY:   { red: 0.30, green: 0.30, blue: 0.33 },
  ORANGE:      { red: 0.93, green: 0.49, blue: 0.13 },  // アクセント
  GREEN:       { red: 0.18, green: 0.70, blue: 0.35 },  // 成果ハイライト
  BLACK:       { red: 0.00, green: 0.00, blue: 0.00 },
  TABLE_HEADER:{ red: 0.15, green: 0.18, blue: 0.35 },
  TABLE_ALT:   { red: 0.93, green: 0.94, blue: 0.97 },
  HIGHLIGHT_BG:{ red: 1.00, green: 0.95, blue: 0.85 },
};

const FONT = {
  TITLE:   'Noto Sans JP',
  BODY:    'Noto Sans JP',
  MONO:    'Noto Sans Mono',
};

const PT = 12700; // 1pt in EMU (English Metric Units)

// スライドサイズ (16:9) in EMU
const SLIDE_W = 9144000;
const SLIDE_H = 5143500;

// 共通マージン
const MARGIN = 400000;

// ============================================================
// メイン実行関数
// ============================================================
function main() {
  const pres = SlidesApp.getActivePresentation();

  // 既存のスライドを全て削除
  const existingSlides = pres.getSlides();
  for (let i = existingSlides.length - 1; i >= 0; i--) {
    existingSlides[i].remove();
  }

  // 全スライドを生成
  createSlide01_Cover(pres);
  createSlide02_Pain(pres);
  createSlide03_WhatIs(pres);
  createSlide04_Channels(pres);
  createSlide05_ChannelDetail(pres);
  createSlide06_Domains(pres);
  createSlide07_Workflow(pres);
  createSlide08_Checkpoint(pres);
  createSlide09_Security(pres);
  createSlide10_SelfImprove(pres);
  createSlide11_CaseA(pres);
  createSlide12_CaseB(pres);
  createSlide13_CaseC(pres);
  createSlide14_Comparison(pres);
  createSlide15_Pricing(pres);
  createSlide16_Steps(pres);
  createSlide17_Partner(pres);
  createSlide18_Tech(pres);
  createSlide19_Roadmap(pres);
  createSlide20_CTA(pres);

  // 完了通知
  SlidesApp.getUi().alert('全20スライドの生成が完了しました！');
}

/** スライドを開いたときにメニューを追加 */
function onOpen() {
  SlidesApp.getUi()
    .createMenu('営業資料生成')
    .addItem('全スライドを生成', 'main')
    .addToUi();
}

// ============================================================
// ヘルパー関数
// ============================================================

/** 背景色を設定 */
function setBg(slide, color) {
  slide.getBackground().setSolidFill(color.red * 255, color.green * 255, color.blue * 255);
}

/** EMU → ポイント変換 (SlidesApp APIはポイント単位) */
function emu(v) { return v / PT; }

/** テキストボックスを追加 */
function addText(slide, text, left, top, width, height, opts = {}) {
  const shape = slide.insertTextBox(text, emu(left), emu(top), emu(width), emu(height));
  const tf = shape.getText();
  const style = tf.getTextStyle();

  style.setFontFamily(opts.font || FONT.BODY);
  if (opts.fontSize) style.setFontSize(opts.fontSize);
  if (opts.bold) style.setBold(true);
  if (opts.color) style.setForegroundColor(opts.color.red * 255, opts.color.green * 255, opts.color.blue * 255);
  if (opts.align) {
    const paragraphs = tf.getParagraphs();
    for (const p of paragraphs) {
      p.getRange().getParagraphStyle().setParagraphAlignment(opts.align);
    }
  }

  shape.setContentAlignment(opts.vAlign || SlidesApp.ContentAlignment.TOP);
  return shape;
}

/** テーブルを追加 */
function addTable(slide, data, left, top, width, height) {
  const rows = data.length;
  const cols = data[0].length;
  const table = slide.insertTable(rows, cols, emu(left), emu(top), emu(width), emu(height));

  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const cell = table.getCell(r, c);
      const text = cell.getText();
      text.setText(data[r][c] || '');
      text.getTextStyle().setFontFamily(FONT.BODY).setFontSize(10);

      if (r === 0) {
        // ヘッダー行
        cell.getFill().setSolidFill(
          COLORS.TABLE_HEADER.red * 255,
          COLORS.TABLE_HEADER.green * 255,
          COLORS.TABLE_HEADER.blue * 255
        );
        text.getTextStyle()
          .setForegroundColor(255, 255, 255)
          .setBold(true)
          .setFontSize(9);
      } else if (r % 2 === 0) {
        cell.getFill().setSolidFill(
          COLORS.TABLE_ALT.red * 255,
          COLORS.TABLE_ALT.green * 255,
          COLORS.TABLE_ALT.blue * 255
        );
      }
    }
  }
  return table;
}

/** セクション見出し (スライド上部の帯) */
function addHeader(slide, title) {
  // 帯背景
  const band = slide.insertShape(SlidesApp.ShapeType.RECTANGLE, 0, 0, emu(SLIDE_W), emu(620000));
  band.getFill().setSolidFill(COLORS.NAVY.red * 255, COLORS.NAVY.green * 255, COLORS.NAVY.blue * 255);
  band.getBorder().setTransparent();

  // タイトルテキスト
  addText(slide, title, MARGIN, 80000, SLIDE_W - MARGIN * 2, 480000, {
    fontSize: 24, bold: true, color: COLORS.WHITE, font: FONT.TITLE,
    vAlign: SlidesApp.ContentAlignment.MIDDLE,
  });
}

/** ボックス (角丸四角形) */
function addBox(slide, text, left, top, width, height, bgColor, textColor, fontSize) {
  const shape = slide.insertShape(SlidesApp.ShapeType.ROUND_RECTANGLE, emu(left), emu(top), emu(width), emu(height));
  shape.getFill().setSolidFill(bgColor.red * 255, bgColor.green * 255, bgColor.blue * 255);
  shape.getBorder().setTransparent();

  const tf = shape.getText();
  tf.setText(text);
  tf.getTextStyle()
    .setFontFamily(FONT.BODY)
    .setFontSize(fontSize || 11)
    .setForegroundColor(textColor.red * 255, textColor.green * 255, textColor.blue * 255);

  shape.setContentAlignment(SlidesApp.ContentAlignment.MIDDLE);
  return shape;
}

/** 矢印を追加 */
function addArrow(slide, x1, y1, x2, y2) {
  const line = slide.insertLine(
    SlidesApp.LineCategory.STRAIGHT, emu(x1), emu(y1), emu(x2), emu(y2)
  );
  line.setEndArrow(SlidesApp.ArrowStyle.FILL_ARROW);
  line.setWeight(2);
  line.getLineFill().setSolidFill(
    COLORS.DARK_GRAY.red * 255,
    COLORS.DARK_GRAY.green * 255,
    COLORS.DARK_GRAY.blue * 255
  );
  return line;
}

// ============================================================
// SLIDE 1: 表紙
// ============================================================
function createSlide01_Cover(pres) {
  const slide = pres.appendSlide(SlidesApp.PredefinedLayout.BLANK);
  setBg(slide, COLORS.NAVY);

  addText(slide, 'AI社員 導入のご提案', MARGIN, 1200000, SLIDE_W - MARGIN * 2, 800000, {
    fontSize: 40, bold: true, color: COLORS.WHITE, font: FONT.TITLE,
    align: SlidesApp.ParagraphAlignment.CENTER,
    vAlign: SlidesApp.ContentAlignment.MIDDLE,
  });

  addText(slide, '～ IQ150の即戦力を、月額で御社に。～', MARGIN, 2100000, SLIDE_W - MARGIN * 2, 500000, {
    fontSize: 20, color: COLORS.ORANGE, font: FONT.TITLE,
    align: SlidesApp.ParagraphAlignment.CENTER,
  });

  addText(slide, '対象: 年商10億〜100億円の成長企業\n提供: [貴社名]\n日付: 2026年2月', MARGIN, 3400000, SLIDE_W - MARGIN * 2, 800000, {
    fontSize: 14, color: COLORS.LIGHT_GRAY,
    align: SlidesApp.ParagraphAlignment.CENTER,
  });
}

// ============================================================
// SLIDE 2: お悩み
// ============================================================
function createSlide02_Pain(pres) {
  const slide = pres.appendSlide(SlidesApp.PredefinedLayout.BLANK);
  addHeader(slide, '御社、こんなお悩みありませんか？');

  const data = [
    ['お悩み', 'よくある現状'],
    ['右腕がいない', '社長が営業も経理も全部やっている'],
    ['DXしたいが何から？', 'SaaSは自社業務に合わない。SIerは高い'],
    ['人が採れない・辞める', '教えた人材が半年で退職。また一からやり直し'],
    ['Excelが限界', '顧客リスト・請求書・日報が全部バラバラのExcel'],
  ];
  addTable(slide, data, MARGIN, 800000, SLIDE_W - MARGIN * 2, 2600000);

  addText(slide, '→ これ、全部「AI社員」が解決します。', MARGIN, 3700000, SLIDE_W - MARGIN * 2, 500000, {
    fontSize: 22, bold: true, color: COLORS.ORANGE,
    align: SlidesApp.ParagraphAlignment.CENTER,
  });
}

// ============================================================
// SLIDE 3: AI社員とは
// ============================================================
function createSlide03_WhatIs(pres) {
  const slide = pres.appendSlide(SlidesApp.PredefinedLayout.BLANK);
  addHeader(slide, 'AI社員とは？');

  addText(slide, '「システムを入れるのではありません。IQ150のAI社員を紹介します。」', MARGIN, 700000, SLIDE_W - MARGIN * 2, 400000, {
    fontSize: 14, bold: true, color: COLORS.NAVY,
    align: SlidesApp.ParagraphAlignment.CENTER,
  });

  const data = [
    ['比較項目', '人間の社員', 'SaaS', 'SIer受託', 'AI社員'],
    ['初期費用', '採用費50万〜', '0〜50万', '500万〜', '★ 0円'],
    ['月額コスト', '30万〜/人', '5万〜/ID', '保守費', '★ 20万〜'],
    ['業務適応', '教育に3ヶ月', '仕様に人が合わせる', '要件定義に半年', '★ 御社の業務を学習'],
    ['退職リスク', 'あり', '—', '担当者交代', '★ 絶対に辞めない'],
    ['稼働時間', '8h/日', '24h', '—', '★ 24時間365日'],
    ['成長', '属人的', 'バージョンアップ待ち', '追加発注', '★ 使うほど賢くなる'],
  ];
  addTable(slide, data, MARGIN, 1200000, SLIDE_W - MARGIN * 2, 3400000);
}

// ============================================================
// SLIDE 4: 入力チャネル
// ============================================================
function createSlide04_Channels(pres) {
  const slide = pres.appendSlide(SlidesApp.PredefinedLayout.BLANK);
  addHeader(slide, 'いつものツールから指示するだけ');

  addText(slide, '新しいツールを覚える必要はありません。\n御社が今使っているツールから、AI社員に直接指示が出せます。', MARGIN, 750000, SLIDE_W - MARGIN * 2, 500000, {
    fontSize: 14, color: COLORS.DARK_GRAY,
    align: SlidesApp.ParagraphAlignment.CENTER,
  });

  // 4つのチャネルボックス
  const channels = [
    { name: 'Notion', color: COLORS.DARK_GRAY },
    { name: 'Slack', color: COLORS.DARK_GRAY },
    { name: 'Google Drive', color: COLORS.DARK_GRAY },
    { name: 'Chatwork', color: COLORS.DARK_GRAY },
  ];

  const boxW = 1700000;
  const boxH = 500000;
  const gap = 200000;
  const startX = (SLIDE_W - (channels.length * boxW + (channels.length - 1) * gap)) / 2;
  const y1 = 1500000;

  channels.forEach((ch, i) => {
    addBox(slide, ch.name, startX + i * (boxW + gap), y1, boxW, boxH, COLORS.LIGHT_GRAY, COLORS.NAVY, 14);
  });

  // 中央矢印エリア
  addArrow(slide, SLIDE_W / 2, y1 + boxH + 50000, SLIDE_W / 2, y1 + boxH + 450000);

  // AI社員エンジンボックス
  addBox(slide, 'AI社員エンジン\n（自動で設計・実装・テスト）', SLIDE_W / 2 - 1500000, 2600000, 3000000, 600000, COLORS.NAVY, COLORS.WHITE, 16);

  // 下矢印
  addArrow(slide, SLIDE_W / 2, 3250000, SLIDE_W / 2, 3650000);

  // 成果物
  addBox(slide, 'ダッシュボードに成果物が届く', SLIDE_W / 2 - 1500000, 3700000, 3000000, 500000, COLORS.ORANGE, COLORS.WHITE, 16);
}

// ============================================================
// SLIDE 5: チャネル別使い方
// ============================================================
function createSlide05_ChannelDetail(pres) {
  const slide = pres.appendSlide(SlidesApp.PredefinedLayout.BLANK);
  addHeader(slide, 'チャネル別 — こう使います');

  const channels = [
    { name: 'Notion', desc: 'DBに要件を書く → ステータスを\n「実装希望」に変更 → 自動で着手', fit: 'プロジェクト管理にNotionを使っている企業' },
    { name: 'Slack', desc: '専用チャンネルでメッセージを送る\n→ 日本語で指示 → スレッドで進捗報告', fit: '社内連絡がSlack中心の企業' },
    { name: 'Google Drive', desc: 'Docsに要件ドキュメントを作成\n→ 共有フォルダに入れる → 自動で着手', fit: 'Google Workspace中心の企業' },
    { name: 'Chatwork', desc: '専用ルームでメッセージを送る\n→ 日本語で指示 → タスクで進捗報告', fit: '社内連絡がChatwork中心の企業' },
  ];

  const boxW = 1900000;
  const boxH = 1800000;
  const gap = 180000;
  const startX = (SLIDE_W - (channels.length * boxW + (channels.length - 1) * gap)) / 2;
  const topY = 850000;

  channels.forEach((ch, i) => {
    const x = startX + i * (boxW + gap);
    // 名前
    addBox(slide, ch.name, x, topY, boxW, 400000, COLORS.NAVY, COLORS.WHITE, 16);
    // 説明
    addText(slide, ch.desc, x + 80000, topY + 500000, boxW - 160000, 700000, {
      fontSize: 10, color: COLORS.DARK_GRAY,
    });
    // 向いている企業
    addText(slide, '▶ ' + ch.fit, x + 80000, topY + 1250000, boxW - 160000, 500000, {
      fontSize: 9, bold: true, color: COLORS.ORANGE,
    });
  });
}

// ============================================================
// SLIDE 6: 10の業務領域
// ============================================================
function createSlide06_Domains(pres) {
  const slide = pres.appendSlide(SlidesApp.PredefinedLayout.BLANK);
  addHeader(slide, 'AI社員が対応できる 10の業務領域');

  const data = [
    ['領域', 'AI社員の名前', 'できること'],
    ['営業管理', 'SFAエージェント', '商談管理・パイプライン・見積書・受発注管理'],
    ['顧客管理', 'CRMエージェント', '顧客情報一元化・問い合わせ管理・顧客分析'],
    ['会計', '会計エージェント', '請求書・仕訳・経費精算・財務レポート'],
    ['法務', '法務エージェント', '契約書管理・稟議フロー・コンプライアンス'],
    ['事務', '事務エージェント', '日報・勤怠・スケジュール・備品管理'],
    ['情シス', '情シスエージェント', 'IT資産管理・ヘルプデスク・アカウント管理'],
    ['マーケ', 'マーケエージェント', '集客・広告管理・施策効果測定'],
    ['デザイン', 'デザインエージェント', 'UI/UX設計・ブランディング・制作物管理'],
    ['M&A', 'M&Aエージェント', '買収候補調査・企業価値分析・DD支援'],
    ['経営参謀', 'No.2エージェント', 'KPI分析・経営戦略・偉人ペルソナ助言'],
  ];
  addTable(slide, data, MARGIN, 800000, SLIDE_W - MARGIN * 2, 3800000);
}

// ============================================================
// SLIDE 7: 仕事の流れ
// ============================================================
function createSlide07_Workflow(pres) {
  const slide = pres.appendSlide(SlidesApp.PredefinedLayout.BLANK);
  addHeader(slide, 'AI社員の仕事の流れ');

  const steps = [
    { num: '①', title: '指示を受ける', desc: 'Slack/Notion/\nDrive/Chatwork\nで要件を受信' },
    { num: '②', title: '設計する', desc: '業務を理解し\n設計書を作成\n(ジャンル自動判定)' },
    { num: '③', title: '確認してもらう', desc: '要件定義書を\nダッシュボードで\n人間が確認・承認' },
    { num: '④', title: '実装する', desc: '設計書に基づき\nコードを生成\n(専門ルール適用)' },
    { num: '⑤', title: 'テストする', desc: '安全な隔離環境で\n自動テスト\n(Lint→単体→E2E)' },
    { num: '⑥', title: '納品', desc: 'ダッシュボードに\n即反映。\n自動デプロイ' },
  ];

  const boxW = 1250000;
  const boxH = 1400000;
  const gap = 150000;
  const startX = (SLIDE_W - (steps.length * boxW + (steps.length - 1) * gap)) / 2;
  const topY = 900000;

  steps.forEach((s, i) => {
    const x = startX + i * (boxW + gap);
    // ステップ番号 + タイトル
    addBox(slide, s.num + ' ' + s.title, x, topY, boxW, 350000, COLORS.NAVY, COLORS.WHITE, 12);
    // 説明
    addBox(slide, s.desc, x, topY + 400000, boxW, boxH - 400000, COLORS.LIGHT_GRAY, COLORS.DARK_GRAY, 10);

    // 矢印 (最後以外)
    if (i < steps.length - 1) {
      addArrow(slide, x + boxW + 20000, topY + boxH / 2, x + boxW + gap - 20000, topY + boxH / 2);
    }
  });

  addText(slide, 'ポイント: ③で必ず人間が確認。「AIが勝手に作った」にはなりません。', MARGIN, 4200000, SLIDE_W - MARGIN * 2, 400000, {
    fontSize: 16, bold: true, color: COLORS.ORANGE,
    align: SlidesApp.ParagraphAlignment.CENTER,
  });
}

// ============================================================
// SLIDE 8: 確認チェックポイント
// ============================================================
function createSlide08_Checkpoint(pres) {
  const slide = pres.appendSlide(SlidesApp.PredefinedLayout.BLANK);
  addHeader(slide, '御社がコントロールできる仕組み');

  // 確認モード
  addText(slide, '確認モード（推奨）', MARGIN, 750000, 3000000, 350000, {
    fontSize: 18, bold: true, color: COLORS.NAVY,
  });

  const phase1Steps = ['指示', 'ジャンル判定', '要件定義書の作成', '【ここで停止】'];
  const phase1W = 1600000;
  const phase1Gap = 100000;
  const phase1Y = 1200000;
  const phase1StartX = (SLIDE_W - (phase1Steps.length * phase1W + (phase1Steps.length - 1) * phase1Gap)) / 2;

  phase1Steps.forEach((s, i) => {
    const x = phase1StartX + i * (phase1W + phase1Gap);
    const bg = (i === 3) ? COLORS.ORANGE : COLORS.LIGHT_GRAY;
    const fg = (i === 3) ? COLORS.WHITE : COLORS.DARK_GRAY;
    addBox(slide, s, x, phase1Y, phase1W, 400000, bg, fg, 12);
    if (i < phase1Steps.length - 1) {
      addArrow(slide, x + phase1W + 10000, phase1Y + 200000, x + phase1W + 90000, phase1Y + 200000);
    }
  });

  addText(slide, 'ダッシュボードで確認 →「これでOK？」→ 承認ボタン', MARGIN, 1700000, SLIDE_W - MARGIN * 2, 350000, {
    fontSize: 12, color: COLORS.DARK_GRAY,
    align: SlidesApp.ParagraphAlignment.CENTER,
  });

  const phase2Steps = ['実装', 'テスト', 'レビュー', '自動デプロイ', '完了通知'];
  const phase2W = 1300000;
  const phase2Gap = 80000;
  const phase2Y = 2200000;
  const phase2StartX = (SLIDE_W - (phase2Steps.length * phase2W + (phase2Steps.length - 1) * phase2Gap)) / 2;

  phase2Steps.forEach((s, i) => {
    const x = phase2StartX + i * (phase2W + phase2Gap);
    addBox(slide, s, x, phase2Y, phase2W, 400000, COLORS.NAVY, COLORS.WHITE, 12);
    if (i < phase2Steps.length - 1) {
      addArrow(slide, x + phase2W + 10000, phase2Y + 200000, x + phase2W + 70000, phase2Y + 200000);
    }
  });

  // 全自動モード
  addText(slide, '全自動モード（慣れてきたら）', MARGIN, 3000000, 3000000, 350000, {
    fontSize: 18, bold: true, color: COLORS.NAVY,
  });

  const autoItems = [
    '・ワンクリックで切り替え可能',
    '・指示を出したら完成まで全自動',
    '・信頼関係が構築されてからの利用を推奨',
  ];
  addText(slide, autoItems.join('\n'), MARGIN, 3400000, SLIDE_W - MARGIN * 2, 800000, {
    fontSize: 13, color: COLORS.DARK_GRAY,
  });
}

// ============================================================
// SLIDE 9: セキュリティ
// ============================================================
function createSlide09_Security(pres) {
  const slide = pres.appendSlide(SlidesApp.PredefinedLayout.BLANK);
  addHeader(slide, 'セキュリティ — 「事故ゼロ」を技術で担保');

  const walls = [
    { title: '第1壁\n秘密鍵自動検知', desc: 'Push前に即ブロック' },
    { title: '第2壁\nDocker隔離実行', desc: 'メモリ制限・\nネットワーク完全遮断' },
    { title: '第3壁\nコマンド\nホワイトリスト', desc: 'rm/chmod等を\n物理的にブロック' },
    { title: '第4壁\n段階テスト', desc: 'Lint→単体→E2E\nの順で品質チェック' },
    { title: '第5壁\n全操作監査ログ', desc: '誰が・いつ・何を\nしたか全記録' },
  ];

  const boxW = 1500000;
  const boxH = 1200000;
  const gap = 140000;
  const startX = (SLIDE_W - (walls.length * boxW + (walls.length - 1) * gap)) / 2;
  const topY = 800000;

  walls.forEach((w, i) => {
    const x = startX + i * (boxW + gap);
    addBox(slide, w.title, x, topY, boxW, 700000, COLORS.NAVY, COLORS.WHITE, 11);
    addBox(slide, w.desc, x, topY + 750000, boxW, 500000, COLORS.LIGHT_GRAY, COLORS.DARK_GRAY, 9);
  });

  addText(slide, '→ 監査法人への説明にそのまま使えます。', MARGIN, 3200000, SLIDE_W - MARGIN * 2, 400000, {
    fontSize: 18, bold: true, color: COLORS.ORANGE,
    align: SlidesApp.ParagraphAlignment.CENTER,
  });

  // 監査ログテーブル
  const data = [
    ['記録項目', '内容'],
    ['操作者', 'AI社員（自動記録）'],
    ['日時', 'タイムスタンプ付き'],
    ['操作内容', 'ファイル作成・コマンド実行・テスト結果の全て'],
    ['承認者', 'ダッシュボードで承認した担当者'],
  ];
  addTable(slide, data, MARGIN + 1500000, 3600000, SLIDE_W - MARGIN * 2 - 3000000, 1200000);
}

// ============================================================
// SLIDE 10: 使うほど賢くなる
// ============================================================
function createSlide10_SelfImprove(pres) {
  const slide = pres.appendSlide(SlidesApp.PredefinedLayout.BLANK);
  addHeader(slide, '使うほど、御社専用のAIに進化します');

  // Before
  addBox(slide, '1回目の依頼\n\n汎用的な知識で設計・実装\n修正: 数回必要', MARGIN, 1000000, 3200000, 1400000, COLORS.LIGHT_GRAY, COLORS.DARK_GRAY, 14);

  addText(slide, '一般的なAIツールは\nここから進化しない', MARGIN, 2500000, 3200000, 400000, {
    fontSize: 11, color: COLORS.DARK_GRAY,
    align: SlidesApp.ParagraphAlignment.CENTER,
  });

  // 矢印
  addArrow(slide, 3600000, 1700000, 5200000, 1700000);

  // After
  addBox(slide, '10回目の依頼\n\n御社の業務を熟知した\n専門AIとして設計・実装\n修正: ほぼ不要', 5500000, 1000000, 3200000, 1400000, COLORS.NAVY, COLORS.WHITE, 14);

  addText(slide, 'AI社員（本サービス）\n毎回の実行結果から学習し\nルールを自動で蓄積・改善', 5500000, 2500000, 3200000, 500000, {
    fontSize: 11, bold: true, color: COLORS.ORANGE,
    align: SlidesApp.ParagraphAlignment.CENTER,
  });

  // ポイント
  const points = [
    '・成功した実装パターンを自動でルールに追記',
    '・同じ業種・業務の案件は回を追うごとに精度が向上',
    '・SaaSのように「全ユーザー共通」ではなく、御社だけの学習データ',
  ];
  addText(slide, points.join('\n'), MARGIN, 3300000, SLIDE_W - MARGIN * 2, 800000, {
    fontSize: 13, color: COLORS.DARK_GRAY,
  });
}

// ============================================================
// SLIDE 11: 導入事例 — 製造業A社
// ============================================================
function createSlide11_CaseA(pres) {
  const slide = pres.appendSlide(SlidesApp.PredefinedLayout.BLANK);
  addHeader(slide, '導入ストーリー① — 製造業 A社（年商30億円）');

  // Before
  addBox(slide, 'Before', MARGIN, 800000, 1200000, 350000, COLORS.DARK_GRAY, COLORS.WHITE, 14);
  addText(slide, '・社長が営業・経理・顧客管理を全部やっている\n・顧客情報は社長の頭の中。Excelが20個以上\n・SIerに相談 → 見積もり800万円 → 断念', MARGIN + 1300000, 800000, SLIDE_W - MARGIN * 2 - 1400000, 600000, {
    fontSize: 12, color: COLORS.DARK_GRAY,
  });

  // 導入プロセス
  addBox(slide, 'AI社員導入（Chatworkから指示）', MARGIN, 1600000, SLIDE_W - MARGIN * 2, 350000, COLORS.NAVY, COLORS.WHITE, 14);

  const weeks = [
    'Week 1: 「商談管理を作って」→ SFA画面が自動生成',
    'Week 2: 「請求書管理も」→ 会計機能が追加',
    'Week 3: 「顧客一覧から分析できるように」→ CRMダッシュボード完成',
    'Week 4: 「経営状況をまとめて」→ KPIダッシュボード稼働',
  ];
  addText(slide, weeks.join('\n'), MARGIN + 200000, 2050000, SLIDE_W - MARGIN * 2 - 400000, 800000, {
    fontSize: 12, color: COLORS.DARK_GRAY, font: FONT.MONO,
  });

  // After
  addBox(slide, 'After（3ヶ月後）', MARGIN, 3000000, 1800000, 350000, COLORS.GREEN, COLORS.WHITE, 14);
  addText(slide, '・社長のスマホに毎朝届く:「受注確度80%以上の案件は3件、合計1,200万円」\n・社長の意思決定スピードが3倍に\n・初期費用0円、月額20万円 = SIer見積もりの40分の1', MARGIN, 3450000, SLIDE_W - MARGIN * 2, 700000, {
    fontSize: 13, bold: true, color: COLORS.NAVY,
  });
}

// ============================================================
// SLIDE 12: 導入事例 — サービス業B社
// ============================================================
function createSlide12_CaseB(pres) {
  const slide = pres.appendSlide(SlidesApp.PredefinedLayout.BLANK);
  addHeader(slide, '導入ストーリー② — サービス業 B社（年商15億円）');

  addBox(slide, 'Before', MARGIN, 800000, 1200000, 350000, COLORS.DARK_GRAY, COLORS.WHITE, 14);
  addText(slide, '・情シス担当は1人。外注システムの中身はブラックボックス\n・セキュリティ監査で「管理体制が不十分」と指摘', MARGIN + 1300000, 800000, SLIDE_W - MARGIN * 2 - 1400000, 500000, {
    fontSize: 12, color: COLORS.DARK_GRAY,
  });

  addBox(slide, 'AI社員導入（Slackから指示）', MARGIN, 1500000, SLIDE_W - MARGIN * 2, 350000, COLORS.NAVY, COLORS.WHITE, 14);

  addText(slide, 'Slackの #ai-dev チャンネル で:\n「顧客問い合わせの管理画面を作って。個人情報は暗号化で」\n\nAI社員 → 要件定義書を自動作成 → ダッシュボードで確認\n→ 承認後に実装 → テスト全パス → 自動デプロイ\n→ 監査ログが全操作を記録', MARGIN + 200000, 1950000, SLIDE_W - MARGIN * 2 - 400000, 1000000, {
    fontSize: 12, color: COLORS.DARK_GRAY,
  });

  addBox(slide, 'After', MARGIN, 3100000, 1200000, 350000, COLORS.GREEN, COLORS.WHITE, 14);
  addText(slide, '・監査法人:「AIが生成したコードのセキュリティは？」\n・情シス: 監査ログのダッシュボードを表示するだけ\n・「いつ・誰が・何を承認して・どうテストされたか」が全て追跡可能', MARGIN + 1300000, 3100000, SLIDE_W - MARGIN * 2 - 1400000, 700000, {
    fontSize: 13, bold: true, color: COLORS.NAVY,
  });
}

// ============================================================
// SLIDE 13: 導入事例 — 不動産C社
// ============================================================
function createSlide13_CaseC(pres) {
  const slide = pres.appendSlide(SlidesApp.PredefinedLayout.BLANK);
  addHeader(slide, '導入ストーリー③ — 不動産 C社（年商50億円）');

  addBox(slide, 'Before', MARGIN, 800000, 1200000, 350000, COLORS.DARK_GRAY, COLORS.WHITE, 14);
  addText(slide, '・物件情報・契約書・顧客情報がGoogle Driveに散在\n・ファイル検索だけで1日30分。属人化で引き継ぎ不可能', MARGIN + 1300000, 800000, SLIDE_W - MARGIN * 2 - 1400000, 500000, {
    fontSize: 12, color: COLORS.DARK_GRAY,
  });

  addBox(slide, 'AI社員導入（Google Driveから指示）', MARGIN, 1500000, SLIDE_W - MARGIN * 2, 350000, COLORS.NAVY, COLORS.WHITE, 14);

  addText(slide, 'Google Docsに要件を記載:\n「物件管理と契約管理を一元化したダッシュボードが欲しい。\n 契約金額1,000万円以上は部長承認フローを入れて」\n\n共有フォルダに入れるだけ → AI社員が自動で読み取り\n→ 法務ジャンルの専門ルールを適用\n→ 承認フロー付きの契約管理システムを自動生成', MARGIN + 200000, 1950000, SLIDE_W - MARGIN * 2 - 400000, 1100000, {
    fontSize: 12, color: COLORS.DARK_GRAY,
  });

  addBox(slide, 'After', MARGIN, 3200000, 1200000, 350000, COLORS.GREEN, COLORS.WHITE, 14);
  addText(slide, '・物件・契約・顧客を1つのダッシュボードで一元管理\n・契約承認フローが自動化。金額に応じた承認ルートを自動設定\n・ファイル探しの30分/日 → 0分。年間180時間を創出', MARGIN + 1300000, 3200000, SLIDE_W - MARGIN * 2 - 1400000, 700000, {
    fontSize: 13, bold: true, color: COLORS.NAVY,
  });
}

// ============================================================
// SLIDE 14: 他社比較
// ============================================================
function createSlide14_Comparison(pres) {
  const slide = pres.appendSlide(SlidesApp.PredefinedLayout.BLANK);
  addHeader(slide, '他の選択肢との比較');

  const data = [
    ['項目', 'AI社員（本サービス）', 'SaaS (kintone等)', 'SIer受託開発', 'AI開発ツール (Copilot等)'],
    ['対象者', '★ 経営者・現場担当者', '現場〜情シス', '情シス〜CTO', 'エンジニア'],
    ['IT知識', '★ 不要', '設定スキル必要', '要件定義力必要', 'コーディング必須'],
    ['初期費用', '★ 0円', '〜50万円', '500万〜数千万円', '〜5万円/人'],
    ['月額', '★ 20万円〜', '5万〜/ID', '保守費', '2万円/人'],
    ['業務適応', '★ 御社専用に自動適応', '仕様に人が合わせる', 'カスタム可能(高額)', 'エンジニアが適応'],
    ['成長性', '★ 使うほど賢くなる', 'バージョンアップ待ち', '追加発注', 'モデル更新待ち'],
    ['セキュリティ', '★ 監査ログ完備', 'ベンダー依存', '契約次第', '自己管理'],
    ['業務知識', '★ 10領域の専門知識搭載', '汎用', '要件次第', 'なし'],
  ];
  addTable(slide, data, MARGIN, 800000, SLIDE_W - MARGIN * 2, 3200000);

  addText(slide, '→ 「IT知識不要」×「御社専用に進化」×「監査ログ完備」の組み合わせは本サービスだけ', MARGIN, 4200000, SLIDE_W - MARGIN * 2, 400000, {
    fontSize: 14, bold: true, color: COLORS.ORANGE,
    align: SlidesApp.ParagraphAlignment.CENTER,
  });
}

// ============================================================
// SLIDE 15: 料金体系
// ============================================================
function createSlide15_Pricing(pres) {
  const slide = pres.appendSlide(SlidesApp.PredefinedLayout.BLANK);
  addHeader(slide, '料金体系');

  addText(slide, 'シンプルな月額制（AI社員の「給与」）', MARGIN, 700000, SLIDE_W - MARGIN * 2, 350000, {
    fontSize: 16, bold: true, color: COLORS.NAVY,
  });

  const data = [
    ['プラン', '月額（税別）', '対応ジャンル', '実行回数/月', 'サポート'],
    ['スターター', '20万円', '2ジャンルまで', '30回', 'メール'],
    ['スタンダード', '40万円', '5ジャンルまで', '100回', 'Slack/Chatwork'],
    ['エンタープライズ', '80万円〜', '全10ジャンル', '無制限', '専任担当'],
  ];
  addTable(slide, data, MARGIN, 1100000, SLIDE_W - MARGIN * 2, 1400000);

  addText(slide, '・初期費用: 0円（セットアップ費用なし）\n・契約期間: 月単位（年契約で10%割引）\n・入力チャネル: 全プランで4種対応（Slack / Notion / Google Drive / Chatwork）', MARGIN, 2600000, SLIDE_W - MARGIN * 2, 600000, {
    fontSize: 12, color: COLORS.DARK_GRAY,
  });

  // 人件費比較
  addText(slide, '人件費との比較', MARGIN, 3300000, SLIDE_W - MARGIN * 2, 300000, {
    fontSize: 16, bold: true, color: COLORS.NAVY,
  });

  addBox(slide, '正社員1人の年間コスト: 約500万円\n（給与+社保+教育+退職リスク）', MARGIN, 3700000, 3800000, 600000, COLORS.LIGHT_GRAY, COLORS.DARK_GRAY, 13);

  addBox(slide, 'AI社員の年間コスト: 約240万円〜\n正社員の約半額で 24h365日稼働・退職リスクゼロ', 4600000, 3700000, 4200000, 600000, COLORS.ORANGE, COLORS.WHITE, 13);
}

// ============================================================
// SLIDE 16: 導入ステップ
// ============================================================
function createSlide16_Steps(pres) {
  const slide = pres.appendSlide(SlidesApp.PredefinedLayout.BLANK);
  addHeader(slide, '導入はこの3ステップだけ');

  const steps = [
    {
      num: 'STEP 1', title: '初回ヒアリング', time: '(1時間)',
      items: '・御社の業務とお悩みをヒアリング\n・優先する業務領域\n・使用中のツール\n・現在の課題',
    },
    {
      num: 'STEP 2', title: '接続設定', time: '(最短1日)',
      items: '・ご利用中のツールとAI社員を接続\n・Slack連携\n・Notion連携\n・Google Drive連携\n・Chatwork連携',
    },
    {
      num: 'STEP 3', title: '利用開始', time: '(即日〜)',
      items: '・普段のツールでAI社員に指示開始\n・確認モードで安心スタート\n・効果を見ながら領域を拡大',
    },
  ];

  const boxW = 2600000;
  const gap = 200000;
  const startX = (SLIDE_W - (steps.length * boxW + (steps.length - 1) * gap)) / 2;
  const topY = 900000;

  steps.forEach((s, i) => {
    const x = startX + i * (boxW + gap);
    addBox(slide, s.num + '\n' + s.title + '\n' + s.time, x, topY, boxW, 600000, COLORS.NAVY, COLORS.WHITE, 14);
    addBox(slide, s.items, x, topY + 700000, boxW, 1400000, COLORS.LIGHT_GRAY, COLORS.DARK_GRAY, 11);

    if (i < steps.length - 1) {
      addArrow(slide, x + boxW + 20000, topY + 400000, x + boxW + gap - 20000, topY + 400000);
    }
  });

  addText(slide, '最短2日で利用開始可能。長期の要件定義や開発期間は不要です。', MARGIN, 4200000, SLIDE_W - MARGIN * 2, 400000, {
    fontSize: 18, bold: true, color: COLORS.ORANGE,
    align: SlidesApp.ParagraphAlignment.CENTER,
  });
}

// ============================================================
// SLIDE 17: パートナー制度
// ============================================================
function createSlide17_Partner(pres) {
  const slide = pres.appendSlide(SlidesApp.PredefinedLayout.BLANK);
  addHeader(slide, '紹介パートナー制度');

  addText(slide, '税理士・銀行・信用金庫の皆さまへ\n顧問先のDX相談、こう解決しませんか？', MARGIN, 750000, SLIDE_W - MARGIN * 2, 500000, {
    fontSize: 16, bold: true, color: COLORS.NAVY,
  });

  const data = [
    ['項目', '内容'],
    ['紹介方法', '顧問先に「AI社員」をご紹介いただくだけ'],
    ['報酬', '初年度売上の最大30%をキックバック'],
    ['紹介例', '10社紹介 × 月額20万 × 30% = 年間720万円'],
    ['御社のメリット', '顧問先の業績改善に貢献 → 解約率低下'],
  ];
  addTable(slide, data, MARGIN, 1400000, SLIDE_W - MARGIN * 2, 1500000);

  // パートナー活用フロー
  addText(slide, 'パートナー活用例', MARGIN, 3100000, SLIDE_W - MARGIN * 2, 300000, {
    fontSize: 14, bold: true, color: COLORS.NAVY,
  });

  const flowBoxW = 2000000;
  const flowGap = 350000;
  const flowStartX = (SLIDE_W - (3 * flowBoxW + 2 * flowGap)) / 2;
  const flowY = 3500000;
  const flowH = 500000;

  addBox(slide, '顧問先から\nDX相談を受ける', flowStartX, flowY, flowBoxW, flowH, COLORS.LIGHT_GRAY, COLORS.DARK_GRAY, 11);
  addArrow(slide, flowStartX + flowBoxW + 50000, flowY + flowH / 2, flowStartX + flowBoxW + flowGap - 50000, flowY + flowH / 2);

  addBox(slide, 'AI社員サービスを\nご紹介', flowStartX + flowBoxW + flowGap, flowY, flowBoxW, flowH, COLORS.NAVY, COLORS.WHITE, 11);
  addArrow(slide, flowStartX + 2 * flowBoxW + flowGap + 50000, flowY + flowH / 2, flowStartX + 2 * (flowBoxW + flowGap) - 50000, flowY + flowH / 2);

  addBox(slide, '紹介報酬 +\n顧問先の満足度向上', flowStartX + 2 * (flowBoxW + flowGap), flowY, flowBoxW, flowH, COLORS.ORANGE, COLORS.WHITE, 11);
}

// ============================================================
// SLIDE 18: 技術基盤
// ============================================================
function createSlide18_Tech(pres) {
  const slide = pres.appendSlide(SlidesApp.PredefinedLayout.BLANK);
  addHeader(slide, '技術基盤（情シス・技術責任者向け）');

  const archData = [
    ['レイヤー', '技術', '説明'],
    ['AI基盤', 'Gemini Pro / Flash', '設計はPro、実装はFlash'],
    ['オーケストレーション', 'LangGraph', '状態管理付きマルチエージェント制御'],
    ['隔離実行', 'Docker Sandbox + MCP', 'コンテナ内で安全に実行・テスト'],
    ['データベース', 'Supabase (PostgreSQL)', 'RLS対応。マルチテナント拡張可能'],
    ['インフラ', 'Google Cloud (Cloud Run)', 'サーバーレス。オートスケール'],
    ['CI/CD', 'GitHub Actions', '自動テスト・自動デプロイ'],
    ['監査', '構造化JSON監査ログ', '全操作を記録しSupabaseに永続化'],
  ];
  addTable(slide, archData, MARGIN, 750000, SLIDE_W - MARGIN * 2, 1800000);

  const secData = [
    ['対策', '実装'],
    ['コード実行隔離', 'Docker: read-only FS, no-network, 512MB, PID 256'],
    ['秘密鍵漏洩防止', '正規表現スキャン（Push前に検知・拒否）'],
    ['危険操作防止', 'コマンドホワイトリスト（rm, chmod等をブロック）'],
    ['品質担保', 'Lint → Unit → E2E の段階的テスト'],
    ['監査証跡', '全操作のJSON構造化ログ + 承認者記録'],
    ['タスク上限', '1タスク $0.50以下、変更量200行以下'],
  ];
  addTable(slide, secData, MARGIN, 2700000, SLIDE_W - MARGIN * 2, 1800000);
}

// ============================================================
// SLIDE 19: ロードマップ
// ============================================================
function createSlide19_Roadmap(pres) {
  const slide = pres.appendSlide(SlidesApp.PredefinedLayout.BLANK);
  addHeader(slide, 'サービスのロードマップ');

  const phases = [
    {
      period: '2026 Q1-Q2', title: '【事務エージェント】',
      items: 'SFA/CRM\n会計/法務\n事務/情シス\nマーケ/デザイン',
      role: '"手足" として\n業務を片付ける',
    },
    {
      period: '2026 Q3-Q4', title: '【参謀エージェント】',
      items: 'KPI分析\n経営提言\n偉人ペルソナ\n助言',
      role: '"右腕" として\n経営判断を支える',
    },
    {
      period: '2027〜', title: '【戦略エージェント】',
      items: 'M&A候補\nDD支援\n競合分析\nバリューアップ',
      role: '"参謀" として\n成長戦略を立案する',
    },
  ];

  const boxW = 2600000;
  const gap = 200000;
  const startX = (SLIDE_W - (phases.length * boxW + (phases.length - 1) * gap)) / 2;
  const topY = 900000;

  phases.forEach((p, i) => {
    const x = startX + i * (boxW + gap);
    // 期間
    addBox(slide, p.period, x, topY, boxW, 350000, COLORS.NAVY, COLORS.WHITE, 14);
    // タイトル
    addText(slide, p.title, x, topY + 400000, boxW, 300000, {
      fontSize: 14, bold: true, color: COLORS.NAVY,
      align: SlidesApp.ParagraphAlignment.CENTER,
    });
    // 内容
    addBox(slide, p.items, x, topY + 750000, boxW, 1000000, COLORS.LIGHT_GRAY, COLORS.DARK_GRAY, 12);
    // 役割
    addText(slide, p.role, x, topY + 1850000, boxW, 400000, {
      fontSize: 12, bold: true, color: COLORS.ORANGE,
      align: SlidesApp.ParagraphAlignment.CENTER,
    });

    if (i < phases.length - 1) {
      addArrow(slide, x + boxW + 20000, topY + 1200000, x + boxW + gap - 20000, topY + 1200000);
    }
  });

  addText(slide, '今ご導入いただいた企業から、業務データが蓄積され、参謀→戦略と自動進化します。', MARGIN, 4200000, SLIDE_W - MARGIN * 2, 400000, {
    fontSize: 14, bold: true, color: COLORS.ORANGE,
    align: SlidesApp.ParagraphAlignment.CENTER,
  });
}

// ============================================================
// SLIDE 20: CTA
// ============================================================
function createSlide20_CTA(pres) {
  const slide = pres.appendSlide(SlidesApp.PredefinedLayout.BLANK);
  setBg(slide, COLORS.NAVY);

  addText(slide, 'まずは1時間、\nお話しさせてください', MARGIN, 600000, SLIDE_W - MARGIN * 2, 1000000, {
    fontSize: 36, bold: true, color: COLORS.WHITE, font: FONT.TITLE,
    align: SlidesApp.ParagraphAlignment.CENTER,
    vAlign: SlidesApp.ContentAlignment.MIDDLE,
  });

  addText(slide, '御社にとって最適な「AI社員」の活用プランをご提案します', MARGIN, 1700000, SLIDE_W - MARGIN * 2, 400000, {
    fontSize: 16, color: COLORS.LIGHT_GRAY,
    align: SlidesApp.ParagraphAlignment.CENTER,
  });

  // 無料ヒアリングの内容
  const items = [
    '御社の業務で、AI社員が最も効果を発揮する領域はどこか',
    'Slack / Notion / Google Drive / Chatwork のどれから始めるのが最適か',
    '最初の1ヶ月で実現できる具体的な成果イメージ',
    '他の導入企業の具体的な成果データ',
  ];
  addText(slide, '無料ヒアリングでお伝えできること:\n\n' + items.map(i => '  ✓  ' + i).join('\n'), MARGIN + 500000, 2200000, SLIDE_W - MARGIN * 2 - 1000000, 1200000, {
    fontSize: 13, color: COLORS.LIGHT_GRAY,
  });

  // 連絡先
  addBox(slide,
    'メール: info@example.com\n電話: 03-XXXX-XXXX\nWeb: https://example.com',
    SLIDE_W / 2 - 1800000, 3700000, 3600000, 700000, COLORS.ORANGE, COLORS.WHITE, 14
  );

  addText(slide, '「システムを入れるか迷っている」なら、まずAI社員に会ってみてください。', MARGIN, 4550000, SLIDE_W - MARGIN * 2, 400000, {
    fontSize: 14, bold: true, color: COLORS.WHITE,
    align: SlidesApp.ParagraphAlignment.CENTER,
  });
}
