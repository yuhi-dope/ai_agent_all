"""製造業向けアウトリーチメール/フォームテンプレート。

サブ業種（金属加工/樹脂加工/機械製造等）×企業規模×ペインで
最適なメッセージを生成する。

特定電子メール法準拠:
  - 送信者名・会社名・連絡先を必ず含める
  - オプトアウト（配信停止）案内を本文末尾に含める
  - 広告・宣伝目的の送信である旨を明示
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class OutreachMessage:
    """アウトリーチメッセージ"""
    subject: str
    body_text: str      # プレーンテキスト版（フォーム用）
    body_html: str      # HTML版（メール用）
    cta_url: str = ""   # CTA（資料DL等）のURL


# ---------------------------------------------------------------------------
# 特定電子メール法準拠フッター（全テンプレート共通）
# ---------------------------------------------------------------------------

_OPT_OUT_FOOTER_TEXT = """
---
【広告】本メールは、シャチョツー（社長2号）によるサービスご案内です。
配信停止をご希望の場合は、件名に「配信停止」とご記入の上、
本メールへ返信いただくか、下記URLよりお手続きください。
https://shachotwo.com/unsubscribe

シャチョツー（社長2号）
運営: 株式会社シャチョツー
所在地: 東京都
お問い合わせ: info@shachotwo.com
"""

_OPT_OUT_FOOTER_FORM = """
---
※本メッセージはBtoB向けサービスご案内です。
ご不要の場合はその旨ご返信いただければ以後ご連絡いたしません。
シャチョツー（社長2号）/ info@shachotwo.com
"""

# ---------------------------------------------------------------------------
# HTML共通スタイル（メール互換インラインスタイル）
# ---------------------------------------------------------------------------

_HTML_HEADER = """\
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
</head>
<body style="font-family:'Hiragino Sans','Meiryo',sans-serif;color:#333;line-height:1.8;max-width:600px;margin:0 auto;padding:20px;background:#f9fafb;">
<div style="background:#fff;border-radius:8px;padding:28px 24px;">
"""

_HTML_FOOTER = """\
<hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0;">
<p style="font-size:12px;color:#999;">
【広告】本メールはシャチョツー（社長2号）によるサービスご案内です。<br>
配信停止をご希望の場合は <a href="https://shachotwo.com/unsubscribe" style="color:#999;">こちら</a> または本メールへ「配信停止」とご返信ください。<br>
シャチョツー（社長2号）/ 株式会社シャチョツー / info@shachotwo.com
</p>
</div>
</body>
</html>
"""


def _wrap_html(body_inner: str) -> str:
    """HTMLヘッダー/フッターで本文を囲む。"""
    return _HTML_HEADER + body_inner + _HTML_FOOTER


# ---------------------------------------------------------------------------
# サブ業種別テンプレート定義
# ---------------------------------------------------------------------------

MANUFACTURING_OUTREACH_TEMPLATES: dict[str, dict] = {

    # ------------------------------------------------------------------
    # 金属加工
    # ------------------------------------------------------------------
    "金属加工": {
        "pain_primary": "見積回答に時間がかかり、受注機会を逃していませんか？",
        "solution": "AIが過去の類似案件から即座に概算見積を生成。回答時間を数日→数分に短縮します。",
        "subjects": [
            "【{company_name}様】見積回答を10倍速くする方法",
            "【金属加工業の方へ】熟練者の見積ノウハウをAIが学習",
            "【{company_name}様】多品種少量の原価管理、AIで解決しませんか",
        ],
        "body_template": """\
{representative}様

突然のご連絡失礼いたします。
中小製造業向けAI業務支援「シャチョツー」の{sender_name}と申します。

{company_name}様のホームページを拝見し、{sub_industry_detail}の分野で
高い技術力をお持ちと存じ、ご連絡いたしました。

━━━━━━━━━━━━━━━━━━━━
■ こんなお悩みはありませんか？
━━━━━━━━━━━━━━━━━━━━
・見積回答に数日かかり、スピード勝負で受注を逃している
・図面・仕様書の解読と積算が熟練者依存で属人化している
・多品種少量で品番ごとの原価が把握できていない
・加工費単価を感覚で決めており、値上げ交渉に根拠が持てない

━━━━━━━━━━━━━━━━━━━━
■ AIで解決できます
━━━━━━━━━━━━━━━━━━━━
シャチョツーは、御社の過去の見積実績・加工単価・材料費をAIが学習し、
新規引合に対して数分で概算見積を自動生成します。

【導入効果（実績）】
・見積回答時間: 平均80%短縮（3日→当日）
・受注率: 15%向上（スピード競合での勝率アップ）
・新人でも熟練者と同水準の見積が可能に

━━━━━━━━━━━━━━━━━━━━
■ まずは無料デモをご体験ください
━━━━━━━━━━━━━━━━━━━━
15分のオンラインデモで、御社の加工品目に合わせた
AIデモをご覧いただけます。

デモ予約はこちら（所要15分・無料）:
{cta_url}

ご都合のよい日時があれば、このメールへご返信いただくだけでもOKです。

{sender_name}
{sender_title}
シャチョツー（社長2号）
""",
        "form_template": """\
{representative}様

突然のご連絡失礼いたします。
中小製造業向けAI業務支援「シャチョツー」の{sender_name}と申します。

{company_name}様のHPを拝見し、{sub_industry_detail}での高い技術力をお持ちと存じ、ご連絡いたしました。

見積回答の属人化・原価管理の課題をAIで解決するサービスをご提供しております。
導入企業では見積回答時間が平均80%短縮、受注率が15%向上しています。

15分のオンラインデモ（無料）をご用意しております。
ご興味がございましたら、ご返信いただけますと幸いです。
デモ予約: {cta_url}

{sender_name} / シャチョツー（社長2号）
""",
        "body_html_template": """\
<p>{representative}様</p>
<p>突然のご連絡失礼いたします。<br>
中小製造業向けAI業務支援「シャチョツー」の<strong>{sender_name}</strong>と申します。</p>
<p>{company_name}様のホームページを拝見し、<strong>{sub_industry_detail}</strong>の分野で
高い技術力をお持ちと存じ、ご連絡いたしました。</p>

<div style="background:#f0f7ff;border-left:4px solid #2563eb;padding:12px 16px;border-radius:0 8px 8px 0;margin:16px 0;">
<p style="font-weight:bold;margin:0 0 8px;">こんなお悩みはありませんか？</p>
<ul style="margin:0;padding-left:20px;">
<li>見積回答に数日かかり、スピード勝負で受注を逃している</li>
<li>図面・仕様書の解読と積算が熟練者依存で属人化している</li>
<li>多品種少量で品番ごとの原価が把握できていない</li>
<li>加工費単価を感覚で決めており、値上げ交渉に根拠が持てない</li>
</ul>
</div>

<div style="background:#ecfdf5;border-left:4px solid #10b981;padding:12px 16px;border-radius:0 8px 8px 0;margin:16px 0;">
<p style="font-weight:bold;margin:0 0 8px;">AIで解決できます</p>
<p style="margin:0;">シャチョツーは、御社の過去の見積実績・加工単価をAIが学習し、新規引合に対して数分で概算見積を自動生成します。</p>
<ul style="margin:8px 0 0;padding-left:20px;">
<li>見積回答時間: 平均<strong>80%短縮</strong>（3日→当日）</li>
<li>受注率: <strong>15%向上</strong>（スピード競合での勝率アップ）</li>
<li>新人でも熟練者と同水準の見積が可能に</li>
</ul>
</div>

<p style="text-align:center;margin:24px 0;">
<a href="{cta_url}" style="display:inline-block;background:#2563eb;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:bold;font-size:15px;">
無料デモを予約する（15分）
</a>
</p>
<p style="font-size:13px;color:#666;">ご都合のよい日時があれば、このメールへご返信いただくだけでもOKです。</p>
<p>{sender_name}<br>{sender_title}<br>シャチョツー（社長2号）</p>
""",
    },

    # ------------------------------------------------------------------
    # 樹脂加工
    # ------------------------------------------------------------------
    "樹脂加工": {
        "pain_primary": "金型管理・成形条件の属人化に困っていませんか？",
        "solution": "AIが成形条件・金型履歴を一元管理。ベテラン不在でも品質を維持します。",
        "subjects": [
            "【{company_name}様】成形条件の属人化、AIで解決しませんか",
            "【樹脂成形業の方へ】金型管理をAIで効率化",
            "【{company_name}様】多品種少量の段取り替え時間を50%短縮",
        ],
        "body_template": """\
{representative}様

突然のご連絡失礼いたします。
中小製造業向けAI業務支援「シャチョツー」の{sender_name}と申します。

{company_name}様のホームページを拝見し、{sub_industry_detail}の分野で
確かな技術力をお持ちと存じ、ご連絡いたしました。

━━━━━━━━━━━━━━━━━━━━
■ こんなお悩みはありませんか？
━━━━━━━━━━━━━━━━━━━━
・成形条件（射出圧力・温度・サイクル）がベテラン任せで属人化している
・金型の保全履歴が紙やExcelに散在し、トラブル時に情報が出てこない
・樹脂材料の切替時、試作に時間がかかり段取りロスが大きい
・不良率のトレンドを把握できず、異常の早期発見が難しい

━━━━━━━━━━━━━━━━━━━━
■ AIで解決できます
━━━━━━━━━━━━━━━━━━━━
シャチョツーは、御社の成形条件データ・金型履歴・不良記録をAIが学習し、
最適な成形条件の提案と金型メンテナンスのタイミングを自動でお知らせします。

【導入効果（実績）】
・段取り替え時間: 平均50%短縮
・不良率: 20〜30%低減（早期異常検知により）
・金型寿命: 平均15%延長（適切なメンテナンスサイクル管理により）

━━━━━━━━━━━━━━━━━━━━
■ まずは無料デモをご体験ください
━━━━━━━━━━━━━━━━━━━━
15分のオンラインデモで、御社の成形品目・金型数に合わせた
AIデモをご覧いただけます。

デモ予約はこちら（所要15分・無料）:
{cta_url}

ご都合のよい日時があれば、このメールへご返信いただくだけでもOKです。

{sender_name}
{sender_title}
シャチョツー（社長2号）
""",
        "form_template": """\
{representative}様

突然のご連絡失礼いたします。
中小製造業向けAI業務支援「シャチョツー」の{sender_name}と申します。

{company_name}様のHPを拝見し、{sub_industry_detail}での確かな技術力をお持ちと存じ、ご連絡いたしました。

成形条件の属人化・金型管理の課題をAIで解決するサービスをご提供しております。
導入企業では段取り替え時間が50%短縮、不良率が20〜30%低減しています。

15分のオンラインデモ（無料）をご用意しております。
ご興味がございましたら、ご返信いただけますと幸いです。
デモ予約: {cta_url}

{sender_name} / シャチョツー（社長2号）
""",
        "body_html_template": """\
<p>{representative}様</p>
<p>突然のご連絡失礼いたします。<br>
中小製造業向けAI業務支援「シャチョツー」の<strong>{sender_name}</strong>と申します。</p>
<p>{company_name}様のホームページを拝見し、<strong>{sub_industry_detail}</strong>の分野で
確かな技術力をお持ちと存じ、ご連絡いたしました。</p>

<div style="background:#f0f7ff;border-left:4px solid #2563eb;padding:12px 16px;border-radius:0 8px 8px 0;margin:16px 0;">
<p style="font-weight:bold;margin:0 0 8px;">こんなお悩みはありませんか？</p>
<ul style="margin:0;padding-left:20px;">
<li>成形条件（射出圧力・温度・サイクル）がベテラン任せで属人化している</li>
<li>金型の保全履歴が紙やExcelに散在し、トラブル時に情報が出てこない</li>
<li>樹脂材料の切替時、試作に時間がかかり段取りロスが大きい</li>
<li>不良率のトレンドを把握できず、異常の早期発見が難しい</li>
</ul>
</div>

<div style="background:#ecfdf5;border-left:4px solid #10b981;padding:12px 16px;border-radius:0 8px 8px 0;margin:16px 0;">
<p style="font-weight:bold;margin:0 0 8px;">AIで解決できます</p>
<p style="margin:0;">成形条件データ・金型履歴・不良記録をAIが学習し、最適な成形条件の提案と金型メンテナンスのタイミングを自動でお知らせします。</p>
<ul style="margin:8px 0 0;padding-left:20px;">
<li>段取り替え時間: 平均<strong>50%短縮</strong></li>
<li>不良率: <strong>20〜30%低減</strong>（早期異常検知により）</li>
<li>金型寿命: 平均<strong>15%延長</strong></li>
</ul>
</div>

<p style="text-align:center;margin:24px 0;">
<a href="{cta_url}" style="display:inline-block;background:#2563eb;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:bold;font-size:15px;">
無料デモを予約する（15分）
</a>
</p>
<p style="font-size:13px;color:#666;">ご都合のよい日時があれば、このメールへご返信いただくだけでもOKです。</p>
<p>{sender_name}<br>{sender_title}<br>シャチョツー（社長2号）</p>
""",
    },

    # ------------------------------------------------------------------
    # 機械製造
    # ------------------------------------------------------------------
    "機械製造": {
        "pain_primary": "受注生産の生産計画・工程管理に手間がかかっていませんか？",
        "solution": "AIが受注→部品展開→工程計画を自動化。リードタイム短縮と納期遵守を両立します。",
        "subjects": [
            "【{company_name}様】生産計画の自動化で納期遵守率99%へ",
            "【機械メーカー様向け】設計変更→工程への反映をAIで即時化",
            "【{company_name}様】受注から出荷まで、工程管理の手戻りをなくす方法",
        ],
        "body_template": """\
{representative}様

突然のご連絡失礼いたします。
中小製造業向けAI業務支援「シャチョツー」の{sender_name}と申します。

{company_name}様のホームページを拝見し、{sub_industry_detail}の分野で
精密な機械づくりをされていると存じ、ご連絡いたしました。

━━━━━━━━━━━━━━━━━━━━
■ こんなお悩みはありませんか？
━━━━━━━━━━━━━━━━━━━━
・受注ごとに異なる仕様で、BOM展開と工程計画に多大な工数がかかる
・設計変更が発生すると、工程への影響確認と再調整に時間を取られる
・協力会社への外注管理が煩雑で、進捗把握に遅れが生じる
・納期遅延の原因分析ができず、同じ問題が繰り返されている

━━━━━━━━━━━━━━━━━━━━
■ AIで解決できます
━━━━━━━━━━━━━━━━━━━━
シャチョツーは、受注情報からBOM展開・工程計画作成・外注手配を
AIが一気通貫で自動化します。

【導入効果（実績）】
・生産計画立案時間: 70%削減（1日→2時間）
・設計変更の工程反映: 手作業ゼロ（AIが自動伝播）
・納期遵守率: 92%→99%に改善

━━━━━━━━━━━━━━━━━━━━
■ まずは無料デモをご体験ください
━━━━━━━━━━━━━━━━━━━━
15分のオンラインデモで、御社の受注品目・工程に合わせた
AIデモをご覧いただけます。

デモ予約はこちら（所要15分・無料）:
{cta_url}

ご都合のよい日時があれば、このメールへご返信いただくだけでもOKです。

{sender_name}
{sender_title}
シャチョツー（社長2号）
""",
        "form_template": """\
{representative}様

突然のご連絡失礼いたします。
中小製造業向けAI業務支援「シャチョツー」の{sender_name}と申します。

{company_name}様のHPを拝見し、{sub_industry_detail}での精密な機械づくりをされていると存じ、ご連絡いたしました。

受注生産の生産計画・工程管理の課題をAIで解決するサービスをご提供しております。
導入企業では生産計画立案時間が70%削減、納期遵守率が99%に改善しています。

15分のオンラインデモ（無料）をご用意しております。
ご興味がございましたら、ご返信いただけますと幸いです。
デモ予約: {cta_url}

{sender_name} / シャチョツー（社長2号）
""",
        "body_html_template": """\
<p>{representative}様</p>
<p>突然のご連絡失礼いたします。<br>
中小製造業向けAI業務支援「シャチョツー」の<strong>{sender_name}</strong>と申します。</p>
<p>{company_name}様のホームページを拝見し、<strong>{sub_industry_detail}</strong>の分野で
精密な機械づくりをされていると存じ、ご連絡いたしました。</p>

<div style="background:#f0f7ff;border-left:4px solid #2563eb;padding:12px 16px;border-radius:0 8px 8px 0;margin:16px 0;">
<p style="font-weight:bold;margin:0 0 8px;">こんなお悩みはありませんか？</p>
<ul style="margin:0;padding-left:20px;">
<li>受注ごとに異なる仕様で、BOM展開と工程計画に多大な工数がかかる</li>
<li>設計変更が発生すると、工程への影響確認と再調整に時間を取られる</li>
<li>協力会社への外注管理が煩雑で、進捗把握に遅れが生じる</li>
<li>納期遅延の原因分析ができず、同じ問題が繰り返されている</li>
</ul>
</div>

<div style="background:#ecfdf5;border-left:4px solid #10b981;padding:12px 16px;border-radius:0 8px 8px 0;margin:16px 0;">
<p style="font-weight:bold;margin:0 0 8px;">AIで解決できます</p>
<p style="margin:0;">受注情報からBOM展開・工程計画作成・外注手配をAIが一気通貫で自動化します。</p>
<ul style="margin:8px 0 0;padding-left:20px;">
<li>生産計画立案時間: <strong>70%削減</strong>（1日→2時間）</li>
<li>設計変更の工程反映: <strong>手作業ゼロ</strong>（AIが自動伝播）</li>
<li>納期遵守率: <strong>92%→99%</strong>に改善</li>
</ul>
</div>

<p style="text-align:center;margin:24px 0;">
<a href="{cta_url}" style="display:inline-block;background:#2563eb;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:bold;font-size:15px;">
無料デモを予約する（15分）
</a>
</p>
<p style="font-size:13px;color:#666;">ご都合のよい日時があれば、このメールへご返信いただくだけでもOKです。</p>
<p>{sender_name}<br>{sender_title}<br>シャチョツー（社長2号）</p>
""",
    },

    # ------------------------------------------------------------------
    # 電子部品
    # ------------------------------------------------------------------
    "電子部品": {
        "pain_primary": "トレーサビリティ管理・検査記録の工数が大きすぎませんか？",
        "solution": "AIがロット追跡・検査データを自動収集。顧客要求のトレーサビリティ報告を即座に生成します。",
        "subjects": [
            "【{company_name}様】トレーサビリティ報告書をAIで自動生成",
            "【電子部品メーカー様向け】検査記録の工数を80%削減する方法",
            "【{company_name}様】顧客監査に備えたトレーサビリティ、準備できていますか",
        ],
        "body_template": """\
{representative}様

突然のご連絡失礼いたします。
中小製造業向けAI業務支援「シャチョツー」の{sender_name}と申します。

{company_name}様のホームページを拝見し、{sub_industry_detail}の分野で
高品質な部品をご提供されていると存じ、ご連絡いたしました。

━━━━━━━━━━━━━━━━━━━━
■ こんなお悩みはありませんか？
━━━━━━━━━━━━━━━━━━━━
・顧客からのトレーサビリティ要求が年々厳しくなり、対応工数が増えている
・検査データの記録・集計が手作業で、報告書作成に多大な時間がかかる
・ロット不良発生時の影響範囲特定に時間がかかり、顧客対応が遅れる
・ISOや顧客監査の対応で、書類準備に毎回多大な工数が発生している

━━━━━━━━━━━━━━━━━━━━
■ AIで解決できます
━━━━━━━━━━━━━━━━━━━━
シャチョツーは、製造ロット・検査記録・出荷先をAIが一元管理し、
顧客要求のトレーサビリティ報告書を自動生成します。

【導入効果（実績）】
・検査記録工数: 80%削減（手入力→自動収集）
・トレーサビリティ報告書: 1件あたり30分→5分に短縮
・ロット不良時の影響範囲特定: 半日→15分に短縮

━━━━━━━━━━━━━━━━━━━━
■ まずは無料デモをご体験ください
━━━━━━━━━━━━━━━━━━━━
15分のオンラインデモで、御社の検査項目・顧客要件に合わせた
AIデモをご覧いただけます。

デモ予約はこちら（所要15分・無料）:
{cta_url}

ご都合のよい日時があれば、このメールへご返信いただくだけでもOKです。

{sender_name}
{sender_title}
シャチョツー（社長2号）
""",
        "form_template": """\
{representative}様

突然のご連絡失礼いたします。
中小製造業向けAI業務支援「シャチョツー」の{sender_name}と申します。

{company_name}様のHPを拝見し、{sub_industry_detail}での高品質な部品提供をされていると存じ、ご連絡いたしました。

トレーサビリティ管理・検査記録の課題をAIで解決するサービスをご提供しております。
導入企業では検査記録工数が80%削減、ロット不良時の影響範囲特定が15分で完了しています。

15分のオンラインデモ（無料）をご用意しております。
ご興味がございましたら、ご返信いただけますと幸いです。
デモ予約: {cta_url}

{sender_name} / シャチョツー（社長2号）
""",
        "body_html_template": """\
<p>{representative}様</p>
<p>突然のご連絡失礼いたします。<br>
中小製造業向けAI業務支援「シャチョツー」の<strong>{sender_name}</strong>と申します。</p>
<p>{company_name}様のホームページを拝見し、<strong>{sub_industry_detail}</strong>の分野で
高品質な部品をご提供されていると存じ、ご連絡いたしました。</p>

<div style="background:#f0f7ff;border-left:4px solid #2563eb;padding:12px 16px;border-radius:0 8px 8px 0;margin:16px 0;">
<p style="font-weight:bold;margin:0 0 8px;">こんなお悩みはありませんか？</p>
<ul style="margin:0;padding-left:20px;">
<li>顧客からのトレーサビリティ要求が年々厳しくなり、対応工数が増えている</li>
<li>検査データの記録・集計が手作業で、報告書作成に多大な時間がかかる</li>
<li>ロット不良発生時の影響範囲特定に時間がかかり、顧客対応が遅れる</li>
<li>ISOや顧客監査の対応で、書類準備に毎回多大な工数が発生している</li>
</ul>
</div>

<div style="background:#ecfdf5;border-left:4px solid #10b981;padding:12px 16px;border-radius:0 8px 8px 0;margin:16px 0;">
<p style="font-weight:bold;margin:0 0 8px;">AIで解決できます</p>
<p style="margin:0;">製造ロット・検査記録・出荷先をAIが一元管理し、顧客要求のトレーサビリティ報告書を自動生成します。</p>
<ul style="margin:8px 0 0;padding-left:20px;">
<li>検査記録工数: <strong>80%削減</strong>（手入力→自動収集）</li>
<li>トレーサビリティ報告書: <strong>30分→5分</strong>に短縮</li>
<li>ロット不良の影響範囲特定: <strong>半日→15分</strong>に短縮</li>
</ul>
</div>

<p style="text-align:center;margin:24px 0;">
<a href="{cta_url}" style="display:inline-block;background:#2563eb;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:bold;font-size:15px;">
無料デモを予約する（15分）
</a>
</p>
<p style="font-size:13px;color:#666;">ご都合のよい日時があれば、このメールへご返信いただくだけでもOKです。</p>
<p>{sender_name}<br>{sender_title}<br>シャチョツー（社長2号）</p>
""",
    },

    # ------------------------------------------------------------------
    # 食品製造
    # ------------------------------------------------------------------
    "食品製造": {
        "pain_primary": "衛生管理記録・アレルゲン管理の手間が大きすぎませんか？",
        "solution": "AIがHACCP記録・アレルゲン情報を自動管理。監査対応を大幅に効率化します。",
        "subjects": [
            "【{company_name}様】HACCP記録をAIで自動化し、監査準備ゼロへ",
            "【食品製造業様向け】アレルゲン管理の工数を90%削減する方法",
            "【{company_name}様】食品表示・原材料管理の属人化を解消しませんか",
        ],
        "body_template": """\
{representative}様

突然のご連絡失礼いたします。
中小製造業向けAI業務支援「シャチョツー」の{sender_name}と申します。

{company_name}様のホームページを拝見し、{sub_industry_detail}の分野で
安全・安心な食品づくりをされていると存じ、ご連絡いたしました。

━━━━━━━━━━━━━━━━━━━━
■ こんなお悩みはありませんか？
━━━━━━━━━━━━━━━━━━━━
・HACCP記録が手書き・Excel管理で、集計・報告に工数がかかりすぎる
・アレルゲン情報の管理が煩雑で、表示ミスのリスクが不安
・原材料の仕入先変更時に、レシピ・成分表の更新漏れが心配
・行政検査・顧客監査のたびに、書類準備で現場が疲弊している

━━━━━━━━━━━━━━━━━━━━
■ AIで解決できます
━━━━━━━━━━━━━━━━━━━━
シャチョツーは、HACCP記録の自動収集・アレルゲン情報の一元管理・
食品表示の自動チェックをAIが担います。

【導入効果（実績）】
・HACCP記録工数: 90%削減（手書き→自動入力）
・アレルゲン表示ミスリスク: ほぼゼロ（自動照合）
・監査対応書類準備: 1週間→当日対応可能に

━━━━━━━━━━━━━━━━━━━━
■ まずは無料デモをご体験ください
━━━━━━━━━━━━━━━━━━━━
15分のオンラインデモで、御社の製品ラインナップ・管理項目に合わせた
AIデモをご覧いただけます。

デモ予約はこちら（所要15分・無料）:
{cta_url}

ご都合のよい日時があれば、このメールへご返信いただくだけでもOKです。

{sender_name}
{sender_title}
シャチョツー（社長2号）
""",
        "form_template": """\
{representative}様

突然のご連絡失礼いたします。
中小製造業向けAI業務支援「シャチョツー」の{sender_name}と申します。

{company_name}様のHPを拝見し、{sub_industry_detail}での安全・安心な食品づくりをされていると存じ、ご連絡いたしました。

HACCP記録・アレルゲン管理の課題をAIで解決するサービスをご提供しております。
導入企業ではHACCP記録工数が90%削減、監査対応書類準備が当日対応可能になっています。

15分のオンラインデモ（無料）をご用意しております。
ご興味がございましたら、ご返信いただけますと幸いです。
デモ予約: {cta_url}

{sender_name} / シャチョツー（社長2号）
""",
        "body_html_template": """\
<p>{representative}様</p>
<p>突然のご連絡失礼いたします。<br>
中小製造業向けAI業務支援「シャチョツー」の<strong>{sender_name}</strong>と申します。</p>
<p>{company_name}様のホームページを拝見し、<strong>{sub_industry_detail}</strong>の分野で
安全・安心な食品づくりをされていると存じ、ご連絡いたしました。</p>

<div style="background:#f0f7ff;border-left:4px solid #2563eb;padding:12px 16px;border-radius:0 8px 8px 0;margin:16px 0;">
<p style="font-weight:bold;margin:0 0 8px;">こんなお悩みはありませんか？</p>
<ul style="margin:0;padding-left:20px;">
<li>HACCP記録が手書き・Excel管理で、集計・報告に工数がかかりすぎる</li>
<li>アレルゲン情報の管理が煩雑で、表示ミスのリスクが不安</li>
<li>原材料の仕入先変更時に、レシピ・成分表の更新漏れが心配</li>
<li>行政検査・顧客監査のたびに、書類準備で現場が疲弊している</li>
</ul>
</div>

<div style="background:#ecfdf5;border-left:4px solid #10b981;padding:12px 16px;border-radius:0 8px 8px 0;margin:16px 0;">
<p style="font-weight:bold;margin:0 0 8px;">AIで解決できます</p>
<p style="margin:0;">HACCP記録の自動収集・アレルゲン情報の一元管理・食品表示の自動チェックをAIが担います。</p>
<ul style="margin:8px 0 0;padding-left:20px;">
<li>HACCP記録工数: <strong>90%削減</strong>（手書き→自動入力）</li>
<li>アレルゲン表示ミスリスク: <strong>ほぼゼロ</strong>（自動照合）</li>
<li>監査対応書類準備: <strong>1週間→当日対応</strong>可能に</li>
</ul>
</div>

<p style="text-align:center;margin:24px 0;">
<a href="{cta_url}" style="display:inline-block;background:#2563eb;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:bold;font-size:15px;">
無料デモを予約する（15分）
</a>
</p>
<p style="font-size:13px;color:#666;">ご都合のよい日時があれば、このメールへご返信いただくだけでもOKです。</p>
<p>{sender_name}<br>{sender_title}<br>シャチョツー（社長2号）</p>
""",
    },

    # ------------------------------------------------------------------
    # 化学製品
    # ------------------------------------------------------------------
    "化学製品": {
        "pain_primary": "SDS管理・法規制対応の工数が大きすぎませんか？",
        "solution": "AIがSDS・化学物質管理を自動化。法規制改正への対応も即時に行います。",
        "subjects": [
            "【{company_name}様】SDS管理・化学物質法規制対応をAIで自動化",
            "【化学製品メーカー様向け】法規制改正への対応コストを80%削減",
            "【{company_name}様】化管法・安衛法の届出管理、AIに任せませんか",
        ],
        "body_template": """\
{representative}様

突然のご連絡失礼いたします。
中小製造業向けAI業務支援「シャチョツー」の{sender_name}と申します。

{company_name}様のホームページを拝見し、{sub_industry_detail}の分野で
専門的な化学製品をご提供されていると存じ、ご連絡いたしました。

━━━━━━━━━━━━━━━━━━━━
■ こんなお悩みはありませんか？
━━━━━━━━━━━━━━━━━━━━
・化管法・労安法・GHS対応のSDS作成・更新に多大な工数がかかっている
・法規制改正のたびに、対象製品の洗い出しと書類更新が大変
・顧客からのSDS/TDS要求への対応が遅れ、受注機会を逃している
・原材料の成分情報が分散管理されており、配合変更時の影響確認が難しい

━━━━━━━━━━━━━━━━━━━━
■ AIで解決できます
━━━━━━━━━━━━━━━━━━━━
シャチョツーは、御社の製品成分・法規制DBをAIが一元管理し、
法規制改正時の影響製品の自動抽出とSDS更新を自動化します。

【導入効果（実績）】
・SDS作成・更新工数: 80%削減
・法規制改正対応: 数週間→即日対応可能に
・顧客へのSDS提供: 当日対応（即時PDF出力）

━━━━━━━━━━━━━━━━━━━━
■ まずは無料デモをご体験ください
━━━━━━━━━━━━━━━━━━━━
15分のオンラインデモで、御社の製品ラインナップ・対応法規制に合わせた
AIデモをご覧いただけます。

デモ予約はこちら（所要15分・無料）:
{cta_url}

ご都合のよい日時があれば、このメールへご返信いただくだけでもOKです。

{sender_name}
{sender_title}
シャチョツー（社長2号）
""",
        "form_template": """\
{representative}様

突然のご連絡失礼いたします。
中小製造業向けAI業務支援「シャチョツー」の{sender_name}と申します。

{company_name}様のHPを拝見し、{sub_industry_detail}での専門的な化学製品提供をされていると存じ、ご連絡いたしました。

SDS管理・化学物質法規制対応の課題をAIで解決するサービスをご提供しております。
導入企業ではSDS作成・更新工数が80%削減、法規制改正対応が即日で可能になっています。

15分のオンラインデモ（無料）をご用意しております。
ご興味がございましたら、ご返信いただけますと幸いです。
デモ予約: {cta_url}

{sender_name} / シャチョツー（社長2号）
""",
        "body_html_template": """\
<p>{representative}様</p>
<p>突然のご連絡失礼いたします。<br>
中小製造業向けAI業務支援「シャチョツー」の<strong>{sender_name}</strong>と申します。</p>
<p>{company_name}様のホームページを拝見し、<strong>{sub_industry_detail}</strong>の分野で
専門的な化学製品をご提供されていると存じ、ご連絡いたしました。</p>

<div style="background:#f0f7ff;border-left:4px solid #2563eb;padding:12px 16px;border-radius:0 8px 8px 0;margin:16px 0;">
<p style="font-weight:bold;margin:0 0 8px;">こんなお悩みはありませんか？</p>
<ul style="margin:0;padding-left:20px;">
<li>化管法・労安法・GHS対応のSDS作成・更新に多大な工数がかかっている</li>
<li>法規制改正のたびに、対象製品の洗い出しと書類更新が大変</li>
<li>顧客からのSDS/TDS要求への対応が遅れ、受注機会を逃している</li>
<li>原材料の成分情報が分散管理されており、配合変更時の影響確認が難しい</li>
</ul>
</div>

<div style="background:#ecfdf5;border-left:4px solid #10b981;padding:12px 16px;border-radius:0 8px 8px 0;margin:16px 0;">
<p style="font-weight:bold;margin:0 0 8px;">AIで解決できます</p>
<p style="margin:0;">製品成分・法規制DBをAIが一元管理し、法規制改正時の影響製品の自動抽出とSDS更新を自動化します。</p>
<ul style="margin:8px 0 0;padding-left:20px;">
<li>SDS作成・更新工数: <strong>80%削減</strong></li>
<li>法規制改正対応: <strong>数週間→即日</strong>対応可能に</li>
<li>顧客へのSDS提供: <strong>当日対応</strong>（即時PDF出力）</li>
</ul>
</div>

<p style="text-align:center;margin:24px 0;">
<a href="{cta_url}" style="display:inline-block;background:#2563eb;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:bold;font-size:15px;">
無料デモを予約する（15分）
</a>
</p>
<p style="font-size:13px;color:#666;">ご都合のよい日時があれば、このメールへご返信いただくだけでもOKです。</p>
<p>{sender_name}<br>{sender_title}<br>シャチョツー（社長2号）</p>
""",
    },

    # ------------------------------------------------------------------
    # 自動車部品
    # ------------------------------------------------------------------
    "自動車部品": {
        "pain_primary": "IATF16949対応・工程FMEA管理の工数が膨大になっていませんか？",
        "solution": "AIが品質マネジメント文書・FMEA・4M変更管理を自動化。ティア1監査への対応コストを大幅削減します。",
        "subjects": [
            "【{company_name}様】IATF16949対応をAIで効率化し、監査コストを削減",
            "【自動車部品サプライヤー様向け】4M変更管理・工程FMEAをAIで自動更新",
            "【{company_name}様】品質記録・トレーサビリティ管理の工数を80%削減",
        ],
        "body_template": """\
{representative}様

突然のご連絡失礼いたします。
中小製造業向けAI業務支援「シャチョツー」の{sender_name}と申します。

{company_name}様のホームページを拝見し、{sub_industry_detail}の分野で
自動車業界の厳しい品質基準に対応されていると存じ、ご連絡いたしました。

━━━━━━━━━━━━━━━━━━━━
■ こんなお悩みはありませんか？
━━━━━━━━━━━━━━━━━━━━
・IATF16949/TS16949の維持・更新審査への準備が毎回大変
・設計変更・4M変更のたびに工程FMEA・管理計画書の更新工数が大きい
・ティア1からのデータ要求（PPM・納入遵守率等）への報告対応が煩雑
・品質問題発生時の8D報告書作成に時間がかかり、顧客対応が遅れる

━━━━━━━━━━━━━━━━━━━━
■ AIで解決できます
━━━━━━━━━━━━━━━━━━━━
シャチョツーは、IATF16949要求書類・工程FMEA・4M変更記録を
AIが自動管理し、ティア1要求への即時対応を実現します。

【導入効果（実績）】
・品質書類管理工数: 80%削減
・8D報告書作成: 2日→4時間に短縮
・ティア1監査対応: 準備期間1ヶ月→当週対応可能に

━━━━━━━━━━━━━━━━━━━━
■ まずは無料デモをご体験ください
━━━━━━━━━━━━━━━━━━━━
15分のオンラインデモで、御社の製品・品質要求に合わせた
AIデモをご覧いただけます。

デモ予約はこちら（所要15分・無料）:
{cta_url}

ご都合のよい日時があれば、このメールへご返信いただくだけでもOKです。

{sender_name}
{sender_title}
シャチョツー（社長2号）
""",
        "form_template": """\
{representative}様

突然のご連絡失礼いたします。
中小製造業向けAI業務支援「シャチョツー」の{sender_name}と申します。

{company_name}様のHPを拝見し、{sub_industry_detail}での自動車業界品質基準への対応をされていると存じ、ご連絡いたしました。

IATF16949対応・4M変更管理・品質記録の課題をAIで解決するサービスをご提供しております。
導入企業では品質書類管理工数が80%削減、8D報告書作成が2日→4時間に短縮しています。

15分のオンラインデモ（無料）をご用意しております。
ご興味がございましたら、ご返信いただけますと幸いです。
デモ予約: {cta_url}

{sender_name} / シャチョツー（社長2号）
""",
        "body_html_template": """\
<p>{representative}様</p>
<p>突然のご連絡失礼いたします。<br>
中小製造業向けAI業務支援「シャチョツー」の<strong>{sender_name}</strong>と申します。</p>
<p>{company_name}様のホームページを拝見し、<strong>{sub_industry_detail}</strong>の分野で
自動車業界の厳しい品質基準に対応されていると存じ、ご連絡いたしました。</p>

<div style="background:#f0f7ff;border-left:4px solid #2563eb;padding:12px 16px;border-radius:0 8px 8px 0;margin:16px 0;">
<p style="font-weight:bold;margin:0 0 8px;">こんなお悩みはありませんか？</p>
<ul style="margin:0;padding-left:20px;">
<li>IATF16949/TS16949の維持・更新審査への準備が毎回大変</li>
<li>設計変更・4M変更のたびに工程FMEA・管理計画書の更新工数が大きい</li>
<li>ティア1からのデータ要求（PPM・納入遵守率等）への報告対応が煩雑</li>
<li>品質問題発生時の8D報告書作成に時間がかかり、顧客対応が遅れる</li>
</ul>
</div>

<div style="background:#ecfdf5;border-left:4px solid #10b981;padding:12px 16px;border-radius:0 8px 8px 0;margin:16px 0;">
<p style="font-weight:bold;margin:0 0 8px;">AIで解決できます</p>
<p style="margin:0;">IATF16949要求書類・工程FMEA・4M変更記録をAIが自動管理し、ティア1要求への即時対応を実現します。</p>
<ul style="margin:8px 0 0;padding-left:20px;">
<li>品質書類管理工数: <strong>80%削減</strong></li>
<li>8D報告書作成: <strong>2日→4時間</strong>に短縮</li>
<li>ティア1監査対応: <strong>準備1ヶ月→当週対応</strong>可能に</li>
</ul>
</div>

<p style="text-align:center;margin:24px 0;">
<a href="{cta_url}" style="display:inline-block;background:#2563eb;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:bold;font-size:15px;">
無料デモを予約する（15分）
</a>
</p>
<p style="font-size:13px;color:#666;">ご都合のよい日時があれば、このメールへご返信いただくだけでもOKです。</p>
<p>{sender_name}<br>{sender_title}<br>シャチョツー（社長2号）</p>
""",
    },
}

# ---------------------------------------------------------------------------
# デフォルト値
# ---------------------------------------------------------------------------

DEFAULT_SENDER_NAME = "杉本"
DEFAULT_SENDER_TITLE = "代表"
DEFAULT_CTA_URL = "https://shachotwo.com/demo"

# サポートするサブ業種の一覧（外部から参照用）
SUPPORTED_SUB_INDUSTRIES: list[str] = list(MANUFACTURING_OUTREACH_TEMPLATES.keys())


# ---------------------------------------------------------------------------
# メッセージ生成関数
# ---------------------------------------------------------------------------

def generate_outreach_message(
    company_name: str,
    sub_industry: str,
    representative: str = "ご担当者",
    sub_industry_detail: str = "",
    template_variant: int = 0,
    use_form_template: bool = False,
    sender_name: str = DEFAULT_SENDER_NAME,
    sender_title: str = DEFAULT_SENDER_TITLE,
    cta_url: str = DEFAULT_CTA_URL,
) -> OutreachMessage:
    """サブ業種に最適なアウトリーチメッセージを生成する。

    Args:
        company_name: 企業名
        sub_industry: サブ業種キー（"金属加工" / "樹脂加工" 等）
        representative: 代表者名または役職（わかれば）
        sub_industry_detail: より詳細なサブ業種説明（例: "精密切削加工"）。
            空文字の場合は sub_industry がそのまま使われる。
        template_variant: 件名バリエーション 0〜2（A/Bテスト用）。
            範囲外は 0 にフォールバック。
        use_form_template: True ならフォーム用の短縮テンプレートを使う（500文字以内）。
            False ならメール用の詳細テンプレートを使う。
        sender_name: 送信者名
        sender_title: 送信者役職
        cta_url: CTA（デモ予約等）のURL

    Returns:
        OutreachMessage: 件名・本文テキスト・HTML版を含むメッセージ

    Raises:
        ValueError: sub_industry が未対応の場合
    """
    if sub_industry not in MANUFACTURING_OUTREACH_TEMPLATES:
        supported = "、".join(SUPPORTED_SUB_INDUSTRIES)
        raise ValueError(
            f"未対応のサブ業種: '{sub_industry}'。"
            f"対応業種: {supported}"
        )

    template = MANUFACTURING_OUTREACH_TEMPLATES[sub_industry]

    # sub_industry_detail が空の場合は sub_industry をそのまま使う
    detail = sub_industry_detail if sub_industry_detail else sub_industry

    # 件名バリエーション（範囲外は 0 にフォールバック）
    subjects: list[str] = template["subjects"]
    variant_idx = template_variant if 0 <= template_variant < len(subjects) else 0
    subject = subjects[variant_idx].format(
        company_name=company_name,
        representative=representative,
        sub_industry_detail=detail,
        sender_name=sender_name,
        cta_url=cta_url,
    )

    # 本文フォーマット用変数
    fmt_vars = {
        "company_name": company_name,
        "representative": representative,
        "sub_industry_detail": detail,
        "sender_name": sender_name,
        "sender_title": sender_title,
        "cta_url": cta_url,
    }

    # テキスト本文
    if use_form_template:
        body_text_raw = template["form_template"].format(**fmt_vars)
        body_text = body_text_raw.strip() + "\n" + _OPT_OUT_FOOTER_FORM
    else:
        body_text_raw = template["body_template"].format(**fmt_vars)
        body_text = body_text_raw.strip() + "\n" + _OPT_OUT_FOOTER_TEXT

    # HTML本文
    body_html_inner = template["body_html_template"].format(**fmt_vars)
    body_html = _wrap_html(body_html_inner)

    return OutreachMessage(
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        cta_url=cta_url,
    )


def list_sub_industries() -> list[str]:
    """対応サブ業種の一覧を返す。"""
    return SUPPORTED_SUB_INDUSTRIES


def get_pain_point(sub_industry: str) -> Optional[str]:
    """サブ業種の主要ペインポイント文を返す。

    Args:
        sub_industry: サブ業種キー

    Returns:
        ペインポイント文字列。未対応業種の場合は None。
    """
    tmpl = MANUFACTURING_OUTREACH_TEMPLATES.get(sub_industry)
    if tmpl is None:
        return None
    return tmpl.get("pain_primary")
