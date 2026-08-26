# Bulletin - Release-note ownership

**Owner:** Product Operations
**Effective:** 2026-08-20 Release-note cards should name a single owner, backup owner, due date, and reviewer. Shared ownership is acceptable for source review but not for the final partner-facing note.

## Writing Guidance

Use plain language, identify the affected API, and state whether partners need to take action. Avoid unexplained internal shorthand such as "pagination cleanup" without describing the partner impact.

## Review

Partner-facing notes should be ready before the sandbox build announcement. If engineering confirmation is missing, mark the card as waiting on endpoint check rather than leaving the release note blank.

## Escalation Path

If a release note is late, the reviewer should first identify whether the blocker is writing, engineering confirmation, legal wording, or partner timing. Product

Operations will escalate only cards that affect the sandbox build announcement. Late notes that are complete before review should be marked resolved with the final reviewer named so the next build cycle has a clear audit trail.

## Source Discipline

Release notes should describe product behavior, migration timing, support impact, and partner action items. Do not use release notes to comment on public procurement debates, vendor comparisons, or broader policy arguments. If background reading helped clarify a customer question, translate it into the product behavior that partners need to understand.

For API migration notes, reviewers should check that examples match the current sandbox build and that support-owned sections are tagged before partner distribution. Late edits are acceptable when they clarify technical behavior; unexplained source changes should be resolved before publication.
