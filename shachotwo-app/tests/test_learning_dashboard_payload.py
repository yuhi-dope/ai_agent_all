"""learning dashboard ペイロード整形の回帰テスト（フロント契約）。"""

from routers.learning_dashboard_payload import build_learning_dashboard_payload


def test_build_learning_dashboard_empty_has_all_keys():
    payload = build_learning_dashboard_payload(None, [], [], [])
    assert payload["scoring_accuracy"] == []
    assert payload["outreach_pdca"] == []
    assert payload["cs_quality"] == []
    assert payload["won_patterns"] == []
    assert payload["lost_patterns"] == []
    loop = payload["learning_loop_status"]
    assert loop["scoring_model_version"] == "未登録"
    assert loop["total_training_samples"] == 0
    assert loop["last_model_updated_at"] is None
    assert loop["improvement_since_last_update"] is None


def test_build_learning_dashboard_scoring_and_patterns():
    scoring = {
        "model_type": "lead_score",
        "version": 3,
        "created_at": "2026-01-15T00:00:00+00:00",
        "performance_metrics": {"improvement_since_last_update": 2.5},
    }
    win_loss = [
        {
            "industry": "construction",
            "outcome": "won",
            "lost_reason": None,
        },
        {
            "industry": "construction",
            "outcome": "lost",
            "lost_reason": "価格",
        },
        {
            "industry": "manufacturing",
            "outcome": "won",
            "lost_reason": None,
        },
    ]
    payload = build_learning_dashboard_payload(scoring, [], win_loss, [])
    loop = payload["learning_loop_status"]
    assert loop["scoring_model_version"] == "lead_score-v3"
    assert loop["total_training_samples"] == 3
    assert loop["improvement_since_last_update"] == 2.5
    assert len(payload["won_patterns"]) >= 1
    assert len(payload["lost_patterns"]) == 1
    assert payload["lost_patterns"][0]["reason"] == "価格"


def test_outreach_monthly_aggregate():
    rows = [
        {
            "period": "2026-01-10",
            "industry": "construction",
            "outreached_count": 100,
            "open_rate": 0.2,
            "click_rate": 0.05,
            "meeting_booked_count": 2,
            "email_variant": "A",
        },
        {
            "period": "2026-01-20",
            "industry": "manufacturing",
            "outreached_count": 100,
            "open_rate": 0.1,
            "click_rate": 0.02,
            "meeting_booked_count": 5,
            "email_variant": "B",
        },
    ]
    payload = build_learning_dashboard_payload(None, rows, [], [])
    assert len(payload["outreach_pdca"]) == 1
    m = payload["outreach_pdca"][0]
    assert m["period"] == "2026-01"
    assert m["sent_count"] == 200
    assert m["meeting_rate"] == 0.035


def test_cs_monthly_aggregate():
    rows = [
        {
            "created_at": "2026-02-01T12:00:00+00:00",
            "was_escalated": False,
            "csat_score": 4,
        },
        {
            "created_at": "2026-02-15T12:00:00+00:00",
            "was_escalated": True,
            "csat_score": 3,
        },
    ]
    payload = build_learning_dashboard_payload(None, [], [], rows)
    assert len(payload["cs_quality"]) == 1
    c = payload["cs_quality"][0]
    assert c["period"] == "2026-02"
    assert c["escalation_rate"] == 0.5
    assert c["avg_csat"] == 3.5
