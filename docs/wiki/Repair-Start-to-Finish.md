# A repair, start to finish

1. **Customer and machine.** Find the customer (search takes a phone number in any format)
   or add one. Under them, add the machine. A board pulled from a machine is added with
   "Inside" set to that machine.

2. **Estimate.** Start an estimate for the machine. Add lines: labor or a service, a fixed
   price, parts, fees, sublets, discounts. *Fill from a job template* adds a standard set
   of lines in one go. Parts the customer will buy themselves go under "Parts you'll buy",
   with a link to the seller's page.
   - *Customer signs on this phone*: hand them your phone.
   - *Send link*: they approve and sign from their own phone.
   - *They approved by phone or text*: records an OK you got some other way.

   Once it's approved, *Open the work order*.

3. **Work order.** Each machine or board is a job. On the job page:
   - **Complaint · cause · correction**, and *Intake: how it came in*.
   - **Lines:** labor (start the timer, or *Add time by hand*), *Add a part from stock*,
     *Add the customer's own part*, fees, sublets, discounts, fixed prices.
   - **Readings** against spec, **photos**, and any **GATBOX traces** from that machine.
   - The **claim ticket** prints from the work order.

4. **Parts it needs.**
   - **In stock:** *Set a part aside* for the job.
   - **Not in stock:** *Order for this job*. It goes on the shopping list, and receiving
     it later sets it aside for this job (see [Parts and buying](Parts-and-Buying)).
   - **The customer is buying it:** it shows under "Parts the customer is buying". Mark it
     **ordered** (with dates if you know them), then **arrived** when it's in the shop.
     While parts are outstanding the work order can wait in *Waiting on parts*. When
     nothing is left outstanding, it offers *Back to In progress*.

5. **Status.** New → Scheduled → In progress → Waiting on parts / Waiting on approval →
   Ready → Completed. On a computer the **Board** shows every open work order as a card:
   drag one to change its status. **Dispatch** books visits on a calendar, one lane per
   tech.

6. **Invoice.** From the work order, *Issue an invoice* (owners). Pick the lines; the
   customer's terms and the site's tax rate (else the shop's) apply.
   - Any credit on their account can go on it.
   - An issue date in the past needs a reason.
   - The number is assigned at issue and never skips.
   - The PDF is frozen as issued, and the service report is issued with it.

7. **Send it.** *Send link* on the invoice gives the customer a page to view and download
   it. Links expire, can be revoked, and show the invoice read-only.

8. **Get paid.** *Record a payment* on the invoice. A partial payment leaves a balance;
   paying more than the balance leaves credit on their account. A deposit taken on the
   estimate is applied when the job is invoiced.

Something wrong after it's issued? See [Fixing mistakes](Fixing-Mistakes).
