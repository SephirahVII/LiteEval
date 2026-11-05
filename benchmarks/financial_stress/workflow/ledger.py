"""Deterministic GBP settlement engine for the LDI incident benchmark."""

from copy import deepcopy
from heapq import heappop, heappush

class Rejected(ValueError):
    """An invalid business instruction, distinct from an engine failure."""


class FinancialDesk:
    def __init__(self, terms, scenario):
        self.terms = terms
        self.now = 0
        self.cash = terms['opening_cash_pence']
        self.fee = 0
        self.forecast = None
        self.obligations = {}
        self.orders = {}
        self.approvals = {}
        self.requests = {}
        self.events = []
        self.journal = []
        self.queue = []
        self.sequence = 0
        self.repo_available = True
        self.handover = None
        self.schedule(0, 'forecast', forecast_id='F0', amount_pence=terms['opening_forecast_pence'])
        self.schedule(600, 'forecast', forecast_id='F1', amount_pence=900_000_000)
        self.schedule(1200, 'forecast', forecast_id='F2', amount_pence=scenario['forecast_pence'])
        self.schedule(2520, 'repo_status', available=not scenario['repo_rejected'])
        for call in scenario['margin_calls']:
            self.schedule(call['publish_s'], 'margin_call', obligation=call)
            self.schedule(call['due_s'], 'payment_due', obligation_id=call['id'])
        self.schedule(5700, 'notice_repeat', obligation_id=scenario['margin_calls'][-1]['id'])
        self.schedule(7200, 'review')
        self.schedule(terms['end_s'], 'shift_end')

    def schedule(self, at, kind, **data):
        self.sequence += 1
        heappush(self.queue, (at, self.sequence, kind, data))

    def record(self, kind, **data):
        self.journal.append(deepcopy({'kind': kind, 'at_s': self.now, **data}))

    def event(self, kind, **data):
        row = {'event_id': len(self.events) + 1, 'kind': kind, 'at_s': self.now, **data}
        self.events.append(deepcopy(row))
        self.record('event', event=row)
        return row

    def advance(self, at):
        if at < self.now:
            raise ValueError('Business time cannot go backwards')
        while self.queue and self.queue[0][0] <= at:
            self.now, _, kind, data = heappop(self.queue)
            if kind == 'forecast':
                self.forecast = self.event(kind, due_s=4200, status='estimated', **data)
            elif kind == 'margin_call':
                call = data['obligation']
                self.obligations[call['id']] = {k: call[k] for k in ('id', 'amount_pence', 'due_s')}
                self.obligations[call['id']]['status'] = 'open'
                self.event(kind, obligation=self.obligations[call['id']])
            elif kind == 'repo_status':
                self.repo_available = data['available']
                if not self.repo_available:
                    for order in self.orders.values():
                        if order['kind'] == 'repo' and order['status'] == 'pending':
                            order['status'] = 'rejected'
                            self.event('order_rejected', order=order)
                self.event(kind, **data)
            elif kind == 'approval':
                approval = self.approvals[data['approval_id']]
                approval['status'] = 'approved'
                self.event('approval_ready', approval=approval)
            elif kind == 'funding':
                order = self.orders[data['order_id']]
                if order['status'] == 'pending':
                    order['status'] = 'settled'
                    self.cash += order['amount_pence']
                    self.fee += order['fee_pence']
                    self.event('funding_settled', order=order, cash_pence=self.cash)
            elif kind == 'payment_due':
                self._settle_payment(data['obligation_id'])
            else:
                self.event(kind, **data)
        self.now = at

    def _settle_payment(self, key):
        obligation = self.obligations[key]
        order = next((o for o in self.orders.values() if o.get('obligation_id') == key), None)
        paid = order is not None and self.cash >= obligation['amount_pence']
        obligation['status'] = 'settled' if paid else 'missed'
        if order:
            order['status'] = 'settled' if paid else 'rejected'
        if paid:
            self.cash -= obligation['amount_pence']
        self.event('payment_result', obligation=obligation, order_id=order['id'] if order else None,
                   cash_pence=self.cash)

    def snapshot(self):
        return deepcopy({'now_s': self.now, 'cash_pence': self.cash, 'fee_pence': self.fee,
                         'forecast': self.forecast, 'repo_available': self.repo_available,
                         'obligations': self.obligations, 'orders': self.orders, 'approvals': self.approvals})

    @staticmethod
    def amount(action, field='amount_pence', zero=False):
        value = action.get(field)
        if type(value) is not int or not int(not zero) <= value <= 10**12:
            raise Rejected(f'{field} requires an integer amount in pence')
        return value

    def act(self, action):
        name = action.get('action')
        if name == 'snapshot':
            return self.snapshot()
        if name == 'approval':
            amount = self.amount(action)
            line = self.terms['funding']['line']
            if amount > line['limit_pence'] or self.now + line['approval_delay_s'] >= line['cutoff_s']:
                raise Rejected('Approval exceeds facility limit or cannot arrive before cutoff')
            key = f'A{len(self.approvals) + 1}'
            item = {'id': key, 'remaining_pence': amount, 'status': 'pending'}
            self.approvals[key] = item
            self.schedule(self.now + line['approval_delay_s'], 'approval', approval_id=key)
            return item
        if name == 'submit':
            return self._submit(action)
        if name == 'risk_report':
            if action.get('forecast_id') != self.forecast['forecast_id'] or action.get('status') != 'estimated':
                raise Rejected('Identify the current forecast and its estimated status')
            if not isinstance(action.get('next_step'), str) or not action['next_step'].strip():
                raise Rejected('Supply a next step')
            gap = self.amount(action, 'gap_pence', zero=True)
            buffered = self.amount(action, 'buffered_gap_pence', zero=True)
            expected = self.forecast['amount_pence'] - self.cash
            return {'correct': gap == max(0, expected) and buffered == max(0, expected + self.terms['buffer_pence']),
                    'shortfall': expected > 0, 'reported_shortfall': gap > 0}
        if name == 'ack':
            key = action.get('obligation_id')
            call = self.obligations.get(key) if isinstance(key, str) else None
            if not call or self.amount(action) != call['amount_pence'] or action.get('due_s') != call['due_s']:
                raise Rejected('Confirmation must match a published obligation')
            if not isinstance(action.get('next_step'), str) or not action['next_step'].strip():
                raise Rejected('Supply a next step')
            return {'obligation_id': key, 'confirmed': True}
        if name == 'handover':
            if self.now < 7200:
                raise Rejected('Handover opens after the 10:30 review')
            self.handover = deepcopy(action)
            return {'recorded': True}
        raise Rejected('Unknown action')

    def _submit(self, action):
        key = action.get('idempotency_key')
        if not isinstance(key, str) or not key or len(key) > 100:
            raise Rejected('Submit requires a bounded idempotency key')
        if key in self.requests:
            previous, order_id = self.requests[key]
            if action != previous:
                raise Rejected('Idempotency key reused with different terms')
            return self.orders[order_id]
        kind = action.get('kind')
        if not isinstance(kind, str):
            raise Rejected('Invalid order kind')
        amount = self.amount(action)
        order = {'id': f'I{len(self.orders) + 1}', 'kind': kind, 'amount_pence': amount, 'status': 'pending'}
        if kind == 'payment':
            obligation_id = action.get('obligation_id')
            call = self.obligations.get(obligation_id) if isinstance(obligation_id, str) else None
            if not call or call['status'] != 'open' or self.now >= call['due_s'] or amount != call['amount_pence']:
                raise Rejected('Payment requires an open matching obligation before cutoff')
            if any(o.get('obligation_id') == obligation_id for o in self.orders.values()):
                raise Rejected('Obligation already has a payment instruction')
            order['obligation_id'] = obligation_id
        else:
            terms = self.terms['funding'].get(kind)
            if not terms or self.now >= terms['cutoff_s'] or (kind == 'repo' and not self.repo_available):
                raise Rejected('Funding route unavailable or cutoff passed')
            existing = [o for o in self.orders.values() if o['kind'] == kind and o['status'] != 'rejected']
            if sum(o['amount_pence'] for o in existing) + amount > terms['limit_pence']:
                raise Rejected('Cumulative route limit exceeded')
            if kind == 'repo':
                divisor = 10_000 - terms['haircut_bps']
                collateral = (amount * 10_000 + divisor - 1) // divisor
                if collateral + sum(o['collateral_pence'] for o in existing) > terms['collateral_pence']:
                    raise Rejected('Eligible collateral exhausted')
                order['collateral_pence'] = collateral
            if kind == 'line':
                approval_id = action.get('approval_id')
                approval = self.approvals.get(approval_id) if isinstance(approval_id, str) else None
                if not approval or approval['status'] != 'approved' or amount > approval['remaining_pence']:
                    raise Rejected('Valid approval with enough remaining amount required')
                approval['remaining_pence'] -= amount
                order['approval_id'] = approval_id
            order['fee_pence'] = (amount * terms['fee_bps'] + 9999) // 10_000
            self.schedule(terms['settle_s'], 'funding', order_id=order['id'])
        self.orders[order['id']] = order
        self.requests[key] = (deepcopy(action), order['id'])
        return order
