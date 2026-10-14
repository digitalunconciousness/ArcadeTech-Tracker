# Fixing mistakes

Issued invoices can't be edited, by design: the database itself refuses. Corrections are
new records that sit beside the old ones.

| What happened | What to do |
|---|---|
| The invoice is wrong altogether | *Void this invoice* (with a reason), then issue a new one |
| Part of it was wrong, or a goodwill discount | *Issue a credit memo*: it reduces what they owe, or becomes credit on their account |
| A bounced check, or a payment entered by mistake | open the payment and *Reverse it* |
| Giving money back | *Refund the credit left* on the payment |

Every change to money records is kept in **Settings → Audit**. Each work order, invoice,
estimate, customer, part and payment also has a **History** link (owners).
