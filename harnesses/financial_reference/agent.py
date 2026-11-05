"""Reference strategy for the financial workflow; receives only visible state."""
import json
import sys

class RuleStrategy:
    """Reference policy driven by visible ledger state and funding terms."""

    def __init__(self):
        self.reported = set()
        self.confirmed = set()

    def choose(self, state, terms, handover_done):
        now, cash = state['now_s'], state['cash_pence']
        forecast = state['forecast']
        routes = terms['funding']
        buffer = terms['buffer_pence']
        if not state['obligations'] and forecast['amount_pence'] > cash:
            forecast_id = forecast['forecast_id']
            if forecast_id not in self.reported:
                self.reported.add(forecast_id)
                return {'action': 'risk_report', 'forecast_id': forecast_id, 'status': 'estimated',
                        'gap_pence': forecast['amount_pence'] - cash,
                        'buffered_gap_pence': forecast['amount_pence'] + buffer - cash,
                        'next_step': 'Prepare standby approval and verify formal call'}
            if not state['approvals']:
                return {'action': 'approval', 'amount_pence': routes['line']['limit_pence']}

        orders = list(state['orders'].values())
        cumulative = 0
        for call in state['obligations'].values():
            if call['status'] != 'open':
                continue
            key = call['id']
            cumulative += call['amount_pence']
            if key not in self.confirmed:
                self.confirmed.add(key)
                return {'action': 'ack', 'obligation_id': key, 'amount_pence': call['amount_pence'],
                        'due_s': call['due_s'], 'next_step': 'Check funding and schedule payment'}
            if not any(o.get('obligation_id') == key for o in orders):
                return self.submit('payment', call['amount_pence'], key, obligation_id=key)
            pending = sum(o['amount_pence'] for o in orders if o['kind'] in routes and o['status'] == 'pending'
                          and routes[o['kind']]['settle_s'] <= call['due_s'])
            gap = cumulative + buffer - cash - pending
            if gap <= 0:
                continue
            if key == 'M1':
                if state['repo_available'] and now < routes['repo']['cutoff_s']:
                    return self.submit('repo', gap, key)
                for approval in state['approvals'].values():
                    if approval['status'] == 'approved' and approval['remaining_pence'] >= gap and now < routes['line']['cutoff_s']:
                        return self.submit('line', gap, key, approval_id=approval['id'])
            elif now < routes['redeem']['cutoff_s']:
                return self.submit('redeem', gap, key)

        if now >= 7200 and not handover_done:
            return {'action': 'handover', 'cash_pence': cash,
                    'financing_ids': [o['id'] for o in orders if o['kind'] in ('repo', 'line') and o['status'] == 'settled'],
                    'unresolved_ids': [c['id'] for c in state['obligations'].values() if c['status'] != 'settled'],
                    'owner': 'Next treasury duty officer',
                    'next_step': 'Reconcile funding repayments, collateral releases and remaining calls'}
        return {'action': 'wait'}

    @staticmethod
    def submit(kind, amount, key, **fields):
        return {'action': 'submit', 'kind': kind, 'amount_pence': amount,
                'idempotency_key': f'{kind}-{key}', **fields}



if __name__ == '__main__':
    request = json.load(sys.stdin)
    strategy = RuleStrategy()
    memory = request.get('state') or {}
    strategy.reported = set(memory.get('reported', []))
    strategy.confirmed = set(memory.get('confirmed', []))
    view = request['observation']
    action = strategy.choose(view['ledger'], view['terms'], view['handover_done'])
    print(json.dumps({'schema_version': 1, 'output': action, 'state': {
        'reported': sorted(strategy.reported), 'confirmed': sorted(strategy.confirmed)}}))
