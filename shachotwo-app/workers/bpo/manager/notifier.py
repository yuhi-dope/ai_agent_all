"""BPO Manager — Notifier。パイプライン完了/承認待ち/エラー時に通知を送る。

通知チャネル:
- メール (主): GmailConnector 経由。NOTIFICATION_EMAIL_TO が設定されている場合に送信。
- Slack (補助): SlackConnector 経由。SLACK_NOTIFICATION_CHANNEL が設定されている場合に送信。
- どちらも未設定の場合は log-only にフォールバック。
"""
import html
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# event_type -> (件名プレフィックス, 絵文字, 日本語ラベル)
_EVENT_META: dict[str, tuple[str, str, str]] = {
    "completed":       ("✅ BPO完了",       "✅", "完了"),
    "approval_needed": ("🔔 BPO承認待ち",    "🔔", "承認待ち"),
    "error":           ("❌ BPOエラー",      "❌", "エラー"),
    "degradation":     ("⚠️ 精度劣化検知",   "⚠️", "精度劣化"),
    "circuit_breaker": ("🚨 Circuit Breaker発動", "🚨", "CB発動"),
}


def _build_subject(pipeline: str, event_type: str) -> str:
    """メール件名を生成する。"""
    prefix, _, _ = _EVENT_META.get(event_type, ("ℹ️ BPOイベント", "ℹ️", event_type))
    return f"{prefix}: {pipeline}"


def _build_html_body(
    company_id: str,
    pipeline: str,
    event_type: str,
    details: dict[str, Any] | None,
) -> str:
    """HTML形式のメール本文を生成する。"""
    _, emoji, label = _EVENT_META.get(event_type, ("ℹ️ BPOイベント", "ℹ️", event_type))

    # details テーブル行の生成
    detail_rows = ""
    if details:
        for key, value in details.items():
            if value is None:
                continue
            escaped_key = html.escape(str(key))
            if key == "cost_yen":
                escaped_value = f"¥{value:,.0f}"
            elif key == "approval_url":
                escaped_url = html.escape(str(value))
                escaped_value = f'<a href="{escaped_url}" style="color:#2563EB;">承認画面を開く</a>'
            elif key == "error":
                escaped_value = f'<span style="color:#DC2626;">{html.escape(str(value)[:500])}</span>'
            else:
                escaped_value = html.escape(str(value)[:500])
            detail_rows += f"""
                <tr>
                  <td style="padding:6px 12px;font-weight:600;white-space:nowrap;color:#374151;">{escaped_key}</td>
                  <td style="padding:6px 12px;color:#1F2937;">{escaped_value}</td>
                </tr>"""

    details_section = ""
    if detail_rows:
        details_section = f"""
        <table style="border-collapse:collapse;width:100%;margin-top:16px;border:1px solid #E5E7EB;border-radius:6px;overflow:hidden;">
          <thead>
            <tr style="background:#F3F4F6;">
              <th style="padding:8px 12px;text-align:left;color:#6B7280;font-size:12px;">項目</th>
              <th style="padding:8px 12px;text-align:left;color:#6B7280;font-size:12px;">内容</th>
            </tr>
          </thead>
          <tbody>{detail_rows}
          </tbody>
        </table>"""

    company_id_display = html.escape(company_id[:8] + "...")

    return f"""<!DOCTYPE html>
<html lang="ja">
<head><meta charset="utf-8" /></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#F9FAFB;margin:0;padding:24px;">
  <div style="max-width:600px;margin:0 auto;background:#FFFFFF;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1);">
    <div style="padding:20px 24px;background:#1E293B;">
      <p style="margin:0;font-size:20px;font-weight:700;color:#FFFFFF;">{emoji} {html.escape(label)}</p>
      <p style="margin:4px 0 0;font-size:13px;color:#94A3B8;">シャチョツー BPO 通知</p>
    </div>
    <div style="padding:24px;">
      <table style="border-collapse:collapse;width:100%;">
        <tr>
          <td style="padding:4px 0;font-weight:600;color:#374151;width:100px;">パイプライン</td>
          <td style="padding:4px 0;color:#1F2937;">{html.escape(pipeline)}</td>
        </tr>
        <tr>
          <td style="padding:4px 0;font-weight:600;color:#374151;">会社ID</td>
          <td style="padding:4px 0;color:#6B7280;font-family:monospace;">{company_id_display}</td>
        </tr>
      </table>
      {details_section}
    </div>
    <div style="padding:12px 24px;background:#F1F5F9;border-top:1px solid #E5E7EB;">
      <p style="margin:0;font-size:11px;color:#9CA3AF;">このメールはシャチョツーが自動送信しました。</p>
    </div>
  </div>
</body>
</html>"""


def _build_slack_message(
    company_id: str,
    pipeline: str,
    event_type: str,
    details: dict[str, Any] | None,
) -> str:
    """Slack メッセージテキストを生成する。"""
    _, emoji, label = _EVENT_META.get(event_type, ("ℹ️ BPOイベント", "ℹ️", event_type))
    message = f"{emoji} *BPO {label}*: `{pipeline}`\n会社ID: `{company_id[:8]}...`"
    if details:
        if details.get("error"):
            message += f"\nエラー: {str(details['error'])[:200]}"
        if details.get("cost_yen"):
            message += f"\nコスト: ¥{details['cost_yen']:,.0f}"
        if details.get("approval_url"):
            message += f"\n<{details['approval_url']}|承認画面を開く>"
    return message


async def _send_email_notification(
    company_id: str,
    pipeline: str,
    event_type: str,
    details: dict[str, Any] | None,
) -> bool:
    """Gmail経由でメール通知を送信する。失敗しても例外を上げない。"""
    email_to_raw = os.environ.get("NOTIFICATION_EMAIL_TO", "").strip()
    if not email_to_raw:
        return False

    recipients = [addr.strip() for addr in email_to_raw.split(",") if addr.strip()]
    if not recipients:
        return False

    email_from = os.environ.get("NOTIFICATION_EMAIL_FROM", "noreply@shachotwo.com").strip()
    subject = _build_subject(pipeline, event_type)
    body_html = _build_html_body(company_id, pipeline, event_type, details)

    try:
        from workers.connector.base import ConnectorConfig
        from workers.connector.email import GmailConnector

        connector = GmailConnector(ConnectorConfig(
            tool_name="gmail",
            credentials={
                # GOOGLE_CREDENTIALS_PATH / SENDER_EMAIL は環境変数から自動取得
                # sender_email を上書きしたい場合のみ明示設定
                "sender_email": email_from,
            },
        ))
        await connector.write_record("send", {
            "to": recipients,
            "subject": subject,
            "body_html": body_html,
        })
        logger.info(
            "notifier: メール通知送信 to=%s event=%s pipeline=%s",
            ",".join(recipients),
            event_type,
            pipeline,
        )
        return True
    except ImportError:
        logger.warning("notifier: GmailConnector が利用できません。メール通知をスキップします。")
        return False
    except Exception as e:
        logger.warning(
            "notifier: メール通知失敗 (%s)。ログのみ記録します。", e
        )
        return False


async def _send_slack_notification(
    company_id: str,
    pipeline: str,
    event_type: str,
    details: dict[str, Any] | None,
) -> bool:
    """Slack経由で通知を送信する。失敗しても例外を上げない。"""
    channel = os.environ.get("SLACK_NOTIFICATION_CHANNEL", "").strip()
    bot_token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if not channel or not bot_token:
        return False

    message = _build_slack_message(company_id, pipeline, event_type, details)

    try:
        from workers.connector.base import ConnectorConfig
        from workers.connector.slack import SlackConnector

        connector = SlackConnector(ConnectorConfig(
            tool_name="slack",
            credentials={"bot_token": bot_token},
        ))
        await connector.write_record("message", {
            "channel": channel,
            "text": message,
        })
        logger.info(
            "notifier: Slack通知送信 channel=%s event=%s pipeline=%s",
            channel,
            event_type,
            pipeline,
        )
        return True
    except ImportError:
        logger.warning("notifier: SlackConnector が利用できません。Slack通知をスキップします。")
        return False
    except Exception as e:
        logger.warning("notifier: Slack通知失敗 (%s)、ログのみ記録", e)
        return False


async def notify_pipeline_event(
    company_id: str,
    pipeline: str,
    event_type: str,  # "completed" | "approval_needed" | "error" | "degradation" | "circuit_breaker"
    details: dict[str, Any] | None = None,
) -> bool:
    """パイプラインイベントを通知する。

    通知優先度:
    1. メール (主): NOTIFICATION_EMAIL_TO が設定されている場合
    2. Slack (補助): SLACK_NOTIFICATION_CHANNEL + SLACK_BOT_TOKEN が設定されている場合
    3. どちらも未設定: log-only にフォールバック

    Args:
        company_id: 会社ID
        pipeline: パイプライン名
        event_type: イベント種別
            "completed"       — BPO処理完了
            "approval_needed" — 人間による承認待ち
            "error"           — エラー発生
            "degradation"     — 精度劣化検知
            "circuit_breaker" — Circuit Breaker発動
        details: 追加情報 dict（任意）
            error (str):        エラーメッセージ
            cost_yen (float):   コスト（円）
            approval_url (str): 承認画面URL

    Returns:
        True: 少なくとも1チャネルで通知成功（またはlog-only）
        False: 全通知チャネルが失敗
    """
    _, emoji, label = _EVENT_META.get(event_type, ("ℹ️ BPOイベント", "ℹ️", event_type))
    log_message = (
        f"notifier: {emoji} BPO {label} | pipeline={pipeline} "
        f"company={company_id[:8]}..."
    )
    if details:
        if details.get("error"):
            log_message += f" | error={str(details['error'])[:100]}"
        if details.get("cost_yen"):
            log_message += f" | cost=¥{details['cost_yen']:,.0f}"
    logger.info(log_message)

    email_sent = await _send_email_notification(
        company_id, pipeline, event_type, details
    )
    slack_sent = await _send_slack_notification(
        company_id, pipeline, event_type, details
    )

    # どちらも未設定 → log-only（成功扱い）
    if not email_sent and not slack_sent:
        email_configured = bool(os.environ.get("NOTIFICATION_EMAIL_TO", "").strip())
        slack_configured = bool(
            os.environ.get("SLACK_NOTIFICATION_CHANNEL", "").strip()
            and os.environ.get("SLACK_BOT_TOKEN", "").strip()
        )
        if not email_configured and not slack_configured:
            logger.info("notifier (log only): 通知チャネル未設定のためログのみ記録しました。")
            return True
        # 設定はあるが送信失敗
        return False

    return True
