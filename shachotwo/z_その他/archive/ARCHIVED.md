# アーカイブ済みプロジェクト

これらのプロジェクトは `shachotwo-app` に吸収統合済みです。参照用にのみ残しています。

## shachotwo-マーケAI → shachotwo-app 移植先

| 移植元 | 移植先 |
|---|---|
| research/enricher.py | workers/micro/company_researcher.py |
| signals/detector.py + auto_followup.py | workers/micro/signal_detector.py |
| scheduling/calendar_api.py | workers/micro/calendar_booker.py |
| research/gbizinfo.py | workers/connector/gbizinfo.py |
| outreach/form_sender.py | workers/connector/playwright_form.py |
| sheet/reader.py + writer.py | workers/connector/google_sheets.py |
| outreach/personalize.py | llm/prompts/outreach_personalize.py |
| resume/generator.py + templates/ | workers/bpo/sales/templates/resume_templates/ |

## shachotwo-契約AI → shachotwo-app 移植先

| 移植元 | 移植先 |
|---|---|
| contract/cloudsign.py | workers/connector/cloudsign.py |
| contract/estimate.py (WeasyPrint) | workers/micro/pdf_generator.py |
| templates/estimate_template.html | workers/bpo/sales/templates/quotation_template.html |
| templates/contract_template.html | workers/bpo/sales/templates/contract_template.html |
| contract/onboarding.py (メールテンプレ) | workers/bpo/sales/templates/welcome_email.html 等 |

## アーカイブ日

2026-03-22
