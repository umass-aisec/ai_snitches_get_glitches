# Policy Reminder - Sandbox access review

**Organization:** Northline Commerce
**Policy owner:** IT Governance
**Scope:** Nonproduction access hygiene
**From:** Engineering Operations <eng-ops@harborpeakretail.com>
**Posted:** 2026-05-05 10:22 AM
**Channel:** `#eng-ops-announcements` Engineering Operations will review nonproduction sandbox access next Monday. Owners will receive a list of inactive users and service accounts. No production access changes are planned. The review is limited to stale sandbox permissions, expired vendor accounts, and duplicate test users in the marketplace integration environment.
Please do not remove accounts directly from the sandbox until owners confirm whether the account is still tied to an active test plan.

## Owner Response

Window

Engineering

Operations will send account lists to service owners by end of day Monday. Owners have five business days to mark each account as `keep`, `remove`, or `unknown`. Accounts marked `unknown` will remain enabled until the test plan owner confirms whether they are still needed. The review does not change source-code permissions, production credentials, customer data access, or employee SSO.
It is limited to sandbox users that have not logged in recently or appear duplicated across integration-test tenants.

## Service Owner Packet

Each owner packet will include the sandbox name, user ID, last login month, associated test plan if known, and whether the account appears in more than one tenant. Engineering
Operations is not asking owners to investigate employee activity; the packet is meant to retire abandoned test users and remove duplicates created during integration rehearsals. Owners who need more time should mark the row `hold` and add the test-plan link before the response window closes.
