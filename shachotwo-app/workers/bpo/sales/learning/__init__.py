"""workers/bpo/sales/learning — 学習AI社員。

パイプライン:
    win_loss_feedback_pipeline  — 学習⑧  受注/失注フィードバック学習
    cs_feedback_pipeline        — 学習⑨  CS対応品質フィードバック学習
"""
from workers.bpo.sales.learning.win_loss_feedback_pipeline import (
    run_win_loss_feedback_pipeline,
    WinLossFeedbackResult,
    StepResult as WinLossStepResult,
)
from workers.bpo.sales.learning.cs_feedback_pipeline import (
    run_cs_feedback_pipeline,
    CsFeedbackPipelineResult,
    StepResult as CsFeedbackStepResult,
)

__all__ = [
    "run_win_loss_feedback_pipeline",
    "WinLossFeedbackResult",
    "WinLossStepResult",
    "run_cs_feedback_pipeline",
    "CsFeedbackPipelineResult",
    "CsFeedbackStepResult",
]
