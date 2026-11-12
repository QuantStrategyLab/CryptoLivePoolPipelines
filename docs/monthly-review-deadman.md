# Monthly review dead-man follow-up

The existing independent `monthly_review_deadman.yml` uses GitHub Actions history
and bot-owned issue notifications. It already has the needed `actions:read`,
`contents:read` and `issues:write` permissions. This draft changes only its
monitor script, fake tests and this document: no secret, model, Dot route,
publication workflow, trading configuration, IAM or server change is required.

Natural previews run at 03:23 UTC on day 1, with a fallback at 03:41 UTC on day 2.
Starting November 2026, the monitor waits until 05:41 UTC on day 2 before judging
the month. Three consecutive unhealthy natural cycles alert by default, including
missing, failed or stalled previews. Manual previews do not affect that history.
Failed or overdue production publications and their review jobs are observed
separately.

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

The monitor uses `GITHUB_REPOSITORY` and defaults the notification owner to that
repository owner; `REVIEW_OWNER_LOGIN` can override the recipient. CI proves source
behavior, not that any monthly model route is available. An outage of GitHub or
of the independent monitor itself still needs an external existing monitor; this
change cannot deliver a GitHub notification while GitHub is unavailable.
