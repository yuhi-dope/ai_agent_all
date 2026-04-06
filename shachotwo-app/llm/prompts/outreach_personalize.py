"""outreach_personalize — 企業ごとのメールパーソナライズプロンプト。"""


def build_initial_email_prompt(
    company_name: str,
    industry: str,
    pain_points: list[str],
    lp_url: str,
    sender_name: str,
    company_brand: str,
    unsubscribe_url: str = "",
) -> list[dict[str, str]]:
    """初回営業メール生成用のメッセージリストを構築する。

    Returns:
        llm/client.py の LLMTask.messages に渡す形式
    """
    pain_text = "\n".join(f"- {p}" for p in pain_points) if pain_points else "（痛みデータなし）"

    system_prompt = """あなたは営業メールの専門家です。
以下の要件で短く効果的な営業メールを生成してください。

ルール:
- 件名は15文字以内（企業名を含む）
- 本文は「突然のご連絡失礼いたします」で始める
- 企業の痛みに触れつつ、LP（職務経歴書）への誘導をする
- 短く簡潔に（200文字以内）
- 必ず JSON のみを返す（説明文不要）
"""

    user_prompt = f"""企業名: {company_name}
業種: {industry}
推定ペイン:
{pain_text}

LP URL: {lp_url}
送信者: {sender_name}（{company_brand}）
配信停止: {unsubscribe_url}

JSON形式で返してください:
{{"subject": "件名", "body_html": "HTML本文"}}"""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_followup_email_prompt(
    company_name: str,
    industry: str,
    sequence_num: int,
    previous_action: str = "",
) -> list[dict[str, str]]:
    """フォローアップメール生成用のメッセージリストを構築する。

    Args:
        sequence_num: 1=3日後フォロー, 2=7日後最終案内
        previous_action: 前回のアクション（例: "lp_viewed", "doc_downloaded"）

    Returns:
        llm/client.py の LLMTask.messages に渡す形式
    """
    if sequence_num == 1:
        context = "3日前にメールを送付。反応を見てフォローアップ。"
        if previous_action == "lp_viewed":
            context += "LPを閲覧済み（関心あり）。"
        elif previous_action == "doc_downloaded":
            context += "資料をダウンロード済み（高関心）。"
    else:
        context = "7日前に初回メール、その後フォロー済み。最終案内。"

    system_prompt = """あなたは営業メールの専門家です。
フォローアップメールを生成してください。

ルール:
- 件名は20文字以内
- しつこくならないトーン
- 具体的な事例や数字を含める
- 必ず JSON のみを返す
"""

    user_prompt = f"""企業名: {company_name}
業種: {industry}
状況: {context}
シーケンス: {sequence_num}回目

JSON形式で返してください:
{{"subject": "件名", "body_html": "HTML本文"}}"""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_fallback_email_html(
    company_name: str,
    lp_url: str,
    sender_name: str,
    company_brand: str,
) -> str:
    """LLM がJSON返却に失敗した場合のフォールバックHTML"""
    return f"""<p>{company_name}<br>ご担当者様</p>
<p>突然のご連絡失礼いたします。<br>
AI業務アシスタント「シャチョツー」の{sender_name}と申します。</p>
<p>御社専用のAI社員がどんな業務をお手伝いできるか、<br>
1枚の「職務経歴書」にまとめました。</p>
<p><a href="{lp_url}">AI社員の職務経歴書を見る（30秒で読めます）</a></p>
<hr>
<p>{sender_name}<br>{company_brand}</p>"""
