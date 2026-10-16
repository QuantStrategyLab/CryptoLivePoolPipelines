# P1 hosted monthly upstream runner — draft only

Monthly Publish's upstream monthly-publish job moves to ubuntu-latest. It
already checks out source, installs Python/dependencies and authenticates with
existing OIDC; it must not rely on a VPS cache or preinstalled environment.
All steps, production release/profile, guards, timeout and concurrency stay
unchanged. The downstream dual API review keeps 16k/2.5k token limits, 60s,
zero retries, one call/provider and USD 1 per UTC month plus reservation state.

**Do not merge this PR under the P1 research-only exception.** The upstream
publishes production release/Firestore data consumed by live trading. Even a
runner-only change requires owner approval of the publishing cutover window,
network/resource compatibility and a synthetic/non-publishing acceptance run.
PR CI never dispatches Monthly Publish, calls a model or authenticates to GCP.
Historical monthly workflow failure is a separate business acceptance item.
Revert through a new PR; on cutover failure pause the new publishing schedule
with owner approval and retain the last verified release, never publish twice.
