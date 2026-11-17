# AI-5: Monthly Research Hook Log-Only Mode

English | [简体中文](ai-5-shadow.zh-CN.md)

`config/ai-hook-shadow.json` targets `QuantAIService` v2 exclusively; all binding values are injected through configuration and existing approved secret references, with no defaults. Live organization deployments still require approved endpoints and valid inputs; this document does not assert that secret injection is already active.

Workflow URL, audience, profile, and model values are selected through `AI_HOOK_URL_SECRET`, `AI_HOOK_AUDIENCE_SECRET`, `AI_HOOK_PROFILE_SECRET`, and `AI_HOOK_MODEL_SECRET` configuration selectors referencing pre-approved secrets rather than printing plain variable values into runner output. Selectors are not literal secret names; missing selectors or backing secrets fail closed without creating credentials or expanding permissions.

`apply_monthly_loop` no longer constructs `DotGateway`, acquires workload tokens, submits or polls remote tasks, closes issues, or dispatches trading notifications; it only validates two existing review opinions and writes a sanitized local record. Passing `issue` or `dot` arguments does not re-enable those capabilities. On missing values or target/mode mismatches, it records `blocked_configuration`, makes no fallback calls, performs no production actions, and exits with `finished=false`. Existing initial dual-API review and release workflows are not enabled, rerun, or modified in capital permissions here.

Change `#214` addresses cross-month budget reservation replay rather than the hook implementation; the hook itself lives in `scripts/monthly_review_loop.py`. The generic flow in `scripts/advisory_review_loop.py` is not called by this hook, and behavior for other callers is preserved.

The service repository record (`docs/AI-5-Q04-SHADOW.zh-CN.md`) tracks the seven-day observation plan, daily checkpoints, and templates. Until the full schedule finishes, status stays shadow-in-progress; source presence, configuration presence, observation startup, native Q04 completion, and production activation are verified separately rather than substituted for one another.
