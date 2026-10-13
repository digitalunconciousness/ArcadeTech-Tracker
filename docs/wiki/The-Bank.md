# The bank

Money → Bank. Owners do the work; viewers can look.

1. **Add each account:** checking, savings, cards, and wallets such as Venmo, PayPal and
   Cash App. Give its starting date and the balance at the end of the day before.
2. **Import a CSV** exported from the bank. The first time, say which column is which; the
   app remembers for next time. Lines already imported are skipped.
3. **Match each line** to what the app already knows:
   - a suggested match (a deposit can be several payments together): confirm it;
   - *Find…* to pick a record yourself;
   - *Set aside* for transfers, interest and fees;
   - *Expense* to turn a charge into an expense on the spot.
4. **Reconcile** against the statement: enter its end date and ending balance. When it
   balances, those lines are locked.

## Locked lines

Reconciled lines are locked in the database, not just in the app.

- **To fix one:** undo the latest reconciliation, with a reason, then reconcile again.
  Only the latest can be undone, so to reach an older one, undo them one at a time, newest
  first.
- **"Changed since reconciled":** a matched payment or expense was edited after
  reconciling. The line stays locked; the flag shows the amount now.
