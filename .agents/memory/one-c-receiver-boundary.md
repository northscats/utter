---
name: Incoming 1C boundary
description: Why the raw incoming pilot remains separate from business analytics and how abuse accounting must stay isolated.
---

Treat the incoming 1C pilot as raw evidence for one existing company per credential pair, not as an ОСВ import or an OData connection.

**Why:** The supplied example combines six companies, but the sender has not confirmed the business meaning or units of «Значение». Inferring balances, currency, or automatically distributing a mixed packet would cross the agreed company boundary.

**How to apply:** Require explicit new agreement before business-field mapping, analytical calculations, or multi-company credentials. Preserve arbitrary analytics and exact source values in extensions.

Keep unauthenticated abuse accounting separate from authenticated channel quotas; overload logging itself must have a bounded cost.

**Why:** Counting every journal entry against a channel lets callers who know only its public ID block the real sender. Writing every over-limit rejection synchronously can defeat the resource protection.

**How to apply:** Revisit both quota attribution and journal load when changing throttles or introducing more receiver instances.