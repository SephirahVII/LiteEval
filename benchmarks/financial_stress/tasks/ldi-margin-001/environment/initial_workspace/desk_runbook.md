# Shift procedure

Reconcile available GBP cash against projected calls and pending settlements.
Forecasts are estimates. A formal call carries an obligation ID, amount and
deadline. Confirm it and schedule payment before the deadline. A funding
instruction is pending until a settlement event changes cash. A rejected
instruction contributes no cash and releases its reserved collateral.

Target GBP 5m remaining available cash after payments. Requesting standby
authorization incurs no charge. Pending and settled draws consume the facility
limit. An authorization can be partly used until the draw cutoff. Costs accrue
on settled funding and are booked separately after this shift.

Each obligation can have one payment instruction. A repeated notice with the
same obligation ID refers to the same payment. Use unique idempotency keys for
new instructions and the same key with identical parameters when retrying.
Payment is scheduled for its due time and requires enough cash then.

After the 10:30 review, submit the reconciled cash, settled financing order
IDs, unresolved obligation IDs, responsible role and follow-up.
