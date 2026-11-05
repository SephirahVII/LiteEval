"""Lot ownership, eligibility and shared encumbrance for the seventh design."""
from copy import deepcopy

M=100_000_000


class CollateralBook:
    def __init__(self,retained=False):
        rows=[('G1','P','GBP','government_bond',17,0,0,99999),
              ('G2','P','GBP','government_bond',12,0,0,99999),
              ('G3','P','GBP','government_bond',9,9,0,99999),
              ('G4','Q','GBP','government_bond',22,0,0,99999),
              ('T1','P','GBP','short_bill',4,0,0,99999),
              ('X1','P','GBP','corporate_bond',40,0,0,99999),
              ('G5','P','USD','government_bond',30,0,0,99999),
              ('G6','P','GBP','government_bond',10,0,5400,99999),
              ('G7','P','GBP','government_bond',8,0,0,2000),
              ('T2','Q','GBP','short_bill',6,0,0,99999)]
        self.lots={r[0]:dict(zip(('id','owner','currency','asset_class','market_value','external_encumbrance','settled_s','maturity_s'),r)) for r in rows}
        for row in self.lots.values():
            row['market_value']*=M
            row['external_encumbrance']*=M
            row['pledged']=0
        self.sold=0
        self.retained=retained

    def revalue(self):
        self.lots['G1']['market_value']=(16 if self.retained else 17)*M
        self.lots['G2']['market_value']=(8 if self.retained else 7)*M

    def reserve(self,allocations,principal,now):
        if not isinstance(allocations,list) or not allocations:
            raise ValueError('Repo requires collateral [{lot_id, market_value_minor}, ...]')
        chosen={}
        credit=0
        for item in allocations:
            if not isinstance(item,dict):
                raise ValueError('Collateral entries must be objects')
            lot=self.lots.get(item.get('lot_id'))
            amount=item.get('market_value_minor')
            if lot is None or type(amount) is not int or amount<=0 or lot['id'] in chosen:
                raise ValueError('Invalid or duplicate collateral allocation')
            if lot['owner']!='P' or lot['currency']!='GBP' or lot['asset_class'] not in ('government_bond','short_bill') or lot['settled_s']>now or lot['maturity_s']<=now:
                raise ValueError('Collateral lot is not eligible for this repo')
            available=lot['market_value']-lot['external_encumbrance']-lot['pledged']-(self.sold if lot['id']=='T1' else 0)
            if amount>available:
                raise ValueError('Collateral allocation exceeds unencumbered market value')
            chosen[lot['id']]=amount
            credit+=amount*(9500 if lot['asset_class']=='short_bill' else 8500)//10000
        if principal>credit:
            raise ValueError('Repo principal exceeds allocated collateral lending value')
        for lot_id,amount in chosen.items():
            self.lots[lot_id]['pledged']+=amount
        return chosen

    def sell_bills(self,amount):
        if amount+self.sold+self.lots['T1']['pledged']>4*M:
            raise ValueError('Short bills are sold or pledged; insufficient free T1 value')
        self.sold+=amount

    def release(self,allocations):
        for lot_id,amount in allocations.items():
            self.lots[lot_id]['pledged']-=amount

    def snapshot(self):
        rows=deepcopy(list(self.lots.values()))
        for row in rows:
            row['sold_market_value']=self.sold if row['id']=='T1' else 0
        return rows
