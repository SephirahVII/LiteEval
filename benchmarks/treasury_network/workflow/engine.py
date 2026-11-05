"""Benchmark-owned, integer-money treasury simulator. No model credentials here."""
from copy import deepcopy
import heapq
try:
    from .resources import CollateralBook
except ImportError:
    from resources import CollateralBook

M = 100_000_000  # one million currency units in minor units


class Rejected(ValueError):
    pass


class Treasury:
    def __init__(self, fixture):
        self.fixture = fixture
        self.level = fixture['level']
        retained = fixture.get('variant') == 'retained'
        self.now = 0
        self.accounts = {'P-GBP': 20*M, 'P-USD': 12*M, 'Q-GBP': 15*M}
        self.restricted = {'P-GBP': 6*M, 'P-USD': 0, 'Q-GBP': 0}
        if retained:
            self.accounts['P-GBP'] += M
            self.restricted['P-GBP'] += M
            self.accounts['P-USD'] += M
        self.holds = {k: 0 for k in self.accounts}
        self.orders, self.approvals, self.quotes, self.obligations = {}, {}, {}, {}
        self.keys, self.events, self.journal, self.due_states = {}, [], [], []
        self.queue, self.serial = [], 0
        self.handover, self.fees = None, 0
        self.pool = 29*M
        self.haircut_bps = 1000
        self.repo_used = 0
        self.pool_version = 'V1'
        self.ssi_version = 'SSI1'
        self.margin_returned = False
        self.peak_financing = 0
        self.collateral=CollateralBook(retained)
        self._schedule(0, 'opening', {'document': 'desk/index.md'})
        self._schedule(0, 'notice', {'id': 'U1', 'version': 1, 'account': 'P-USD', 'amount': (6 if retained else 5)*M, 'due_s': 4800})
        self._schedule(1200, 'risk_update', {'document': 'risk/forecast-0850.json', 'as_of_s': 1200})
        self._schedule(2520 if self.level>=7 else 3120 if self.level>=6 else 1800, 'notice', {'id': 'M1', 'version': 1, 'account': 'P-GBP', 'amount': ((45 if retained else 44) if self.level>=6 else (41 if retained else 40))*M, 'due_s': 4200})
        self._schedule(2100, 'valuation', {'document': 'collateral/revaluation-0905.json'})
        if self.level >= 2:
            if self.level<6:
                self._schedule(2280, 'notice', {'id': 'M1', 'version': 2, 'account': 'P-GBP', 'amount': (45 if retained else 44)*M, 'due_s': 4200})
            self._schedule(2460, 'risk_update', {'document': 'risk/delayed-estimate.json', 'as_of_s': 1500})
        if self.level >= 3:
            self._schedule(2760, 'fx_match_failure', {'batch': 'FX-AM'})
        if self.level >= 5:
            self._schedule(2640, 'ssi_change', {'document': 'operations/ssi-amendment.json'})
            self._schedule(2820, 'collateral_call', {})
        self._schedule(3600, 'notice', {'id': 'M2', 'version': 1, 'account': 'P-GBP', 'amount': (9 if retained else 8)*M, 'due_s': 7200})
        if self.level>=6:
            self._schedule(3480,'leverage_review',{})
            self._schedule(6600,'inflow',{'account':'P-GBP','amount':(3 if retained else 2)*M,'reference':'INTEREST-RECEIPT'})
        self._schedule(5700, 'notice_copy', {'id': 'M1', 'version': 1, 'original_s': 2520 if self.level>=7 else 3120 if self.level>=6 else 1800})
        self._schedule(7800, 'inflow', {'account': 'P-GBP', 'amount': 23*M, 'reference': 'BOND-REDEMPTION'})
        self._schedule(9300, 'inflow', {'account': 'P-GBP', 'amount': 12*M, 'reference': 'COUPON-SETTLEMENT'})
        for i in range(9):
            self._schedule(420+i*840, 'market_digest', {'document': f'news/digest-{i+1:02}.txt'})
        self._schedule(10800, 'shift_end', {})
        self.advance(0)

    def record(self, kind, **fields):
        self.journal.append({'at_s': self.now, 'kind': kind, **deepcopy(fields)})

    def _schedule(self, at, kind, data):
        self.serial += 1
        heapq.heappush(self.queue, (at, self.serial, kind, deepcopy(data)))

    def emit(self, kind, data):
        event = {'id': f'E{len(self.events)+1}', 'at_s': self.now, 'kind': kind, **deepcopy(data)}
        self.events.append(event)
        self.record('event', event=event)

    def available(self, account):
        return self.accounts[account] - self.restricted[account] - self.holds[account]

    def _cash(self, account, amount, reference):
        self.accounts[account] += amount
        self.record('cash', account=account, delta=amount, reference=reference, balance=self.accounts[account])

    def advance(self, target):
        if target < self.now:
            raise ValueError('Clock cannot move backwards')
        while self.queue and self.queue[0][0] <= target:
            at, _, kind, data = heapq.heappop(self.queue)
            self.now = at
            if kind == 'notice':
                old = self.obligations.get(data['id'])
                self.obligations[data['id']] = {**data, 'status': 'open'}
                if not old:
                    self._schedule(data['due_s'], 'payment_due', {'id': data['id']})
            elif kind == 'collateral_call':
                if not any(o['route']=='repo' for o in self.orders.values()):
                    continue
                data={'id':'C1','version':1,'account':'P-GBP','amount':3*M,'due_s':3720,
                      'category':'cash_collateral','source':'REPO-A','authenticated':True}
                self.obligations['C1']={**data,'status':'open'}
                self._schedule(3720,'payment_due',{'id':'C1'})
            elif kind == 'valuation':
                self.pool, self.haircut_bps, self.pool_version = 24*M, 1500, 'V2'
                self.collateral.revalue()
            elif kind == 'approval_ready':
                self.approvals[data['id']]['status'] = 'ready'
            elif kind == 'ssi_change':
                self.ssi_version = 'SSI2'
            elif kind == 'fx_match_failure':
                for order in self.orders.values():
                    if order['route'] == 'fx' and order['status'] == 'pending':
                        self.holds['P-USD'] -= order['amount']
                        order['status'] = 'failed'
            elif kind == 'funding_settle':
                order = self.orders[data['id']]
                if order['status'] != 'pending':
                    continue
                if order['route'] in ('fx','fx_b'):
                    self.holds['P-USD'] -= order['amount']
                    self._cash('P-USD', -order['amount'], order['id'])
                if self.level >= 4 and order['route'] == 'repo':
                    order['status'] = 'suspense'
                    data = {'batch': 'CR-0925', 'status': 'booked', 'document': 'operations/bank-0925.csv'}
                    self.emit('bank_batch', data)
                    continue
                self._settle(order)
            elif kind == 'payment_due':
                obligation = self.obligations[data['id']]
                orders = [o for o in self.orders.values() if o['route'] == 'payment' and o['obligation_id'] == data['id'] and o['status'] == 'pending']
                correct = [o for o in orders if o['version'] == obligation['version'] and o['amount'] == obligation['amount'] and o['ssi_version'] == self.ssi_version]
                success = bool(correct and self.available(obligation['account']) >= obligation['amount'])
                if success:
                    self._cash(obligation['account'], -obligation['amount'], data['id'])
                    obligation['status'] = 'paid'
                    correct[0]['status'] = 'settled'
                else:
                    obligation['status'] = 'missed'
                for order in orders:
                    if order['status'] == 'pending':
                        order['status'] = 'expired'
                self.due_states.append({'id': data['id'], 'paid': success, 'available': self.available(obligation['account']), 'amount': obligation['amount']})
                data = deepcopy(obligation)
            elif kind == 'inflow':
                self._cash(data['account'], data['amount'], data['reference'])
            elif kind == 'debt_due':
                order = self.orders[data['id']]
                if order['status'] not in ('settled', 'suspense'):
                    continue
                repayment = [o for o in self.orders.values() if o['route'] == 'repay' and o['debt_id'] == order['id'] and o['status'] == 'pending']
                success = bool(repayment and order['status'] == 'settled' and self.available('P-GBP') >= order['credit'])
                if success:
                    self._cash('P-GBP', -order['credit'], 'REPAY-'+order['id'])
                    order['status'] = 'repaid'
                    repayment[0]['status'] = 'settled'
                    if order['route'] == 'repo':
                        self.repo_used -= order['amount']
                        if self.level>=7:
                            self.collateral.release(order['collateral'])
                        margin=self.obligations.get('C1')
                        if margin and margin['status']=='paid' and not self.margin_returned and all(o['status']=='repaid' for o in self.orders.values() if o['route']=='repo'):
                            self._cash('P-GBP',margin['amount'],'COLLATERAL-RETURN')
                            self.margin_returned=True
                else:
                    order['defaulted'] = True
                data = {'id': order['id'], 'repaid': success, 'amount': order['credit'], 'route': order['route']}
            self.capture_financing()
            self.emit(kind, data)
        self.now = target

    def _settle(self, order):
        self._cash('P-GBP', order['credit'], order['id'])
        order['status'] = 'settled'
        fee_bps = {'repo': 5, 'line': 25, 'fx': 1, 'fx_b':8, 'sale': 3, 'redeem': 3,'early_redeem':5}[order['route']]
        self.fees += (order['credit'] * fee_bps + 9999)//10000

    def snapshot(self, table):
        if table == 'accounts':
            return [{'id': k, 'entity': k.split('-')[0], 'currency': k.split('-')[1], 'book': v, 'restricted': self.restricted[k], 'reserved': self.holds[k], 'available': self.available(k)} for k,v in self.accounts.items()]
        if table == 'orders':
            return list(deepcopy(self.orders).values())
        if table == 'obligations':
            return list(deepcopy(self.obligations).values())
        if table == 'approvals':
            return list(deepcopy(self.approvals).values())
        if table == 'collateral':
            if self.level>=7:
                return self.collateral.snapshot()
            return {'eligible_market_value': self.pool, 'haircut_bps': self.haircut_bps, 'version': self.pool_version, 'pledged_principal': self.repo_used}
        if table == 'suspense':
            return [{'order_id': o['id'], 'reference': 'CR-'+o['id'], 'amount': o['credit'], 'currency': 'GBP', 'beneficiary_entity': 'P', 'remitter': 'REPO-A'} for o in self.orders.values() if o['status'] == 'suspense']
        raise Rejected('Unknown table; accounts, orders, obligations, approvals, collateral, suspense are supported')

    def act(self, action):
        kind = action.get('action')
        if kind == 'query':
            return self.snapshot(action.get('table'))
        if kind == 'quote':
            route = action.get('route')
            if route not in ('repo', 'fx', 'fx_b', 'sale'):
                raise Rejected('Quotes available for repo, fx, fx_b, sale')
            if route=='fx_b' and (self.level<5 or self.now<2820):
                raise Rejected('Dealer B is not quoting at this time')
            if route == 'repo' and self.now < 2160:
                raise Rejected('Firm repo market opens at 09:06')
            q = {'id': f'Q{len(self.quotes)+1}', 'route': route, 'expires_s': self.now+180,
                 'version': self.pool_version if route == 'repo' else 'RATE1'}
            if route == 'repo':
                if self.level>=7:
                    q.update(haircuts_bps={'government_bond':1500,'short_bill':500},eligible_currency='GBP',lender_cash_limit_minor=30*M)
                else:
                    q['maximum_cash_minor'] = self.pool*(10000-self.haircut_bps)//10000-self.repo_used
            else:
                q.update(rate_num=8 if route=='fx' else 76 if route=='fx_b' else 98,rate_den=10 if route=='fx' else 100)
            self.quotes[q['id']] = q
            return deepcopy(q)
        if kind == 'approval':
            route = action.get('route')
            amount = self._amount(action)
            if route != 'line' or amount > 12*M:
                raise Rejected('Standby B supports line authorizations up to GBP12m')
            item = {'id': f'A{len(self.approvals)+1}', 'route': route, 'remaining': amount, 'ready_s': self.now+480, 'status': 'pending'}
            self.approvals[item['id']] = item
            self._schedule(item['ready_s'], 'approval_ready', {'id': item['id']})
            return deepcopy(item)
        if kind == 'cancel':
            order = self.orders.get(action.get('order_id'))
            if not order or order['status'] != 'pending' or order['route'] not in ('payment', 'repay'):
                raise Rejected('Only pending payment/repay orders can be cancelled')
            order['status'] = 'cancelled'
            return deepcopy(order)
        if kind == 'reconcile':
            order = self.orders.get(action.get('order_id'))
            if not order or order['status'] != 'suspense' or action.get('account') != 'P-GBP' or action.get('reference') != 'CR-'+order['id']:
                raise Rejected('Suspense reference, beneficiary and account must match the statement')
            self._settle(order)
            return deepcopy(order)
        if kind == 'handover':
            self.handover = deepcopy(action)
            return {'recorded': True}
        if kind != 'submit':
            raise Rejected('Unknown action')
        key = action.get('key')
        if not isinstance(key, str) or not key:
            raise Rejected('A nonempty idempotency key is required')
        if key in self.keys:
            previous, order_id = self.keys[key]
            if previous != action:
                raise Rejected('Idempotency key payload differs')
            return deepcopy(self.orders[order_id])
        route, amount = action.get('route'), self._amount(action)
        order = {'id': f'T{len(self.orders)+1}', 'route': route, 'amount': amount, 'status': 'pending', 'submitted_s': self.now}
        if route == 'payment':
            obligation = self.obligations.get(action.get('obligation_id'))
            if not obligation or obligation['status'] != 'open' or self.now >= obligation['due_s']:
                raise Rejected('No open payable obligation at this time')
            if action.get('version') != obligation['version'] or amount != obligation['amount'] or action.get('account') != obligation['account']:
                raise Rejected('Payment must match the currently published obligation')
            if action.get('ssi_version') != self.ssi_version:
                raise Rejected('Settlement instruction version is not current')
            order.update(obligation_id=obligation['id'], version=obligation['version'], ssi_version=action['ssi_version'])
        elif route == 'repay':
            debt = self.orders.get(action.get('debt_id'))
            if not debt or debt['route'] not in ('repo','line') or debt['status'] != 'settled' or amount != debt['credit']:
                raise Rejected('Repayment must reference an outstanding settled debt principal')
            due = 9000 if debt['route']=='repo' else 9600
            cutoff=7500 if self.level>=8 else due
            if self.now >= cutoff:
                raise Rejected('Repayment instruction cutoff has passed')
            order['debt_id'] = debt['id']
        else:
            cutoffs = {'repo': 2700, 'line': 3000, 'fx': 2700, 'fx_b':3300, 'sale': 3420, 'redeem': 5100,'early_redeem':3240}
            settlements = {'repo': 3300, 'line': 3480, 'fx': 3600, 'fx_b':4080, 'sale': 3900, 'redeem': 6300,'early_redeem':3960}
            limits = {'repo': self.pool*(10000-self.haircut_bps)//10000, 'line': 12*M, 'fx': self.available('P-USD'),'fx_b':self.available('P-USD'), 'sale': 4*M, 'redeem': 10*M,'early_redeem':4*M}
            if self.level>=7:
                limits['repo']=30*M
            if route not in cutoffs or self.now >= cutoffs[route]:
                raise Rejected('Route unavailable or cutoff passed')
            if route=='early_redeem' and (self.level<6 or self.now<2400):
                raise Rejected('Early money fund window is not open')
            if route in ('redeem','early_redeem') and amount+sum(o['amount'] for o in self.orders.values() if o['route'] in ('redeem','early_redeem'))>10*M:
                raise Rejected('Money fund windows share the same GBP10m asset pool')
            used = sum(o['amount'] for o in self.orders.values() if o['route']==route and o['status'] not in ('failed','cancelled')) if route not in ('fx','fx_b') else 0
            if amount + used > limits[route]:
                raise Rejected('Route capacity exceeded')
            credit = amount
            if route in ('repo','fx','fx_b','sale'):
                quote = self.quotes.get(action.get('quote_id'))
                if not quote or quote['route'] != route or self.now > quote['expires_s'] or (route=='repo' and quote['version'] != self.pool_version):
                    raise Rejected('Valid current quote required')
                if route in ('fx','fx_b','sale'):
                    credit = amount*quote['rate_num']//quote['rate_den']
            if route == 'line':
                approval = self.approvals.get(action.get('approval_id'))
                if not approval or approval['status']!='ready' or approval['remaining'] < amount:
                    raise Rejected('Insufficient ready line authorization')
                approval['remaining'] -= amount
            if route == 'repo':
                if self.level>=7:
                    try:
                        order['collateral']=self.collateral.reserve(action.get('collateral'),amount,self.now)
                    except ValueError as exc:
                        raise Rejected(str(exc)) from None
                self.repo_used += amount
            if route=='sale' and self.level>=7:
                try:
                    self.collateral.sell_bills(amount)
                except ValueError as exc:
                    raise Rejected(str(exc)) from None
            if route in ('fx','fx_b'):
                self.holds['P-USD'] += amount
            order.update(credit=credit, settle_s=settlements[route])
            self._schedule(settlements[route], 'funding_settle', {'id': order['id']})
        self.orders[order['id']] = order
        if route in ('repo','line'):
            self._schedule(9000 if route=='repo' else 9600,'debt_due',{'id':order['id']})
        self.capture_financing()
        self.keys[key] = (deepcopy(action), order['id'])
        return deepcopy(order)

    @staticmethod
    def _amount(action):
        amount = action.get('amount')
        if isinstance(amount, bool) or not isinstance(amount,int) or amount<=0:
            raise Rejected('amount must be a positive integer in currency minor units')
        return amount

    def capture_financing(self):
        if self.now>=3480:
            principal=sum(o['credit'] for o in self.orders.values() if o['route'] in ('repo','line') and o['status'] not in ('failed','cancelled','repaid'))
            self.peak_financing=max(self.peak_financing,principal)

    def export(self):
        return {'schema_version': 1, 'completed': self.now == 10800, 'level': self.level,
                'journal': self.journal, 'accounts': self.snapshot('accounts'), 'orders': self.snapshot('orders'),
                'obligations': self.snapshot('obligations'), 'due_states': self.due_states,
                'fees': self.fees, 'repo_used': self.repo_used, 'peak_financing':self.peak_financing,'handover': self.handover}
