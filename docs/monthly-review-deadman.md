# Monthly review dead-man follow-up

The existing independent `monthly_review_deadman.yml` uses GitHub Actions history
and bot-owned issue notifications. It already has the needed `actions:read`,
`contents:read` and `issues:write` permissions. This draft changes only its
monitor script, fake tests and this document: no secret, model, Dot route,
publication workflow, trading configuration, IAM or server change is required.

Expected natural cycles come from the existing 03:15 UTC first-of-month schedule,
starting November 2026, with two hours of completion grace. The monitor now counts
missing slots, failed/skipped reviews and stalled queued/running cycles together.
Three consecutive unhealthy natural cycles alert even when the failures are a
mixture of those conditions. Successful manual/probe runs cannot hide a missing
natural review. Manual failures and overdue manual runs are observed separately;
intentional manual absence is not a missed schedule.

The alert marker stays stable throughout one outage and changes after a verified
successful review breaks that streak. Complete run history is paginated from the
feature's introduction; partial/duplicate/inconsistent histories fail the monitor
visibly. Exactly one issue POST is attempted, with no retry after unknown write
outcomes. A later run reads existing bot-owned markers before writing again.

No API model or production workflow is invoked by validation. Fake tests cover
mixed missing/failed cycles, skipped reviews, stalled runs, grace, manual masking,
recovery, partial history, pagination, deduplication, and ambiguous notification
writes. Operational alerts describe a failed or missing review; they do not imply
a confirmed financial finding or authorize publication, rerun, repair or orders.

This PR remains draft. CI proves source behavior, not that the monitor has been
adopted or that any monthly model route/secret is available. An outage of GitHub or
of the independent monitor itself still needs an external existing monitor; this
change cannot deliver a GitHub notification while GitHub is unavailable.
