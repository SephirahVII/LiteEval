"""Raw-source interface over the validated treasury settlement engine."""
if __package__:
    from benchmarks.treasury_network.workflow.engine import Treasury,Rejected
else:
    from engine import Treasury,Rejected
try:
    from .data_view import PublicView
    from .calculator import calculate
except ImportError:
    from data_view import PublicView
    from calculator import calculate


class SourceDesk(Treasury):
    def __init__(self,fixture,files):
        self.view=PublicView(files,fixture.get('release_times',{}))
        self.risk_reports=[]
        super().__init__(fixture)
        for _,_,kind,data in self.queue:
            if kind=='notice' and data.get('id')=='M1':
                data['amount']=fixture['m1_amount_minor']

    def emit(self,kind,data):
        if kind=='risk_update':
            kind='document_received'
            data={**data,'document':'inbox/MB-025.csv' if data.get('as_of_s')==1200 else 'risk/forecast-open.json'}
        return super().emit(kind,data)

    def snapshot(self,table):
        if table=='catalog':
            return self.view.catalog(self.now)
        if table=='risk_book':
            return list(self.risk_reports)
        result=super().snapshot(table)
        if table=='accounts':
            return [{k:v for k,v in row.items() if k not in ('available','restricted','reserved')} for row in result]
        return result

    def act(self,action):
        if action.get('action')=='calculate':
            try:
                return calculate(action.get('expression'))
            except ValueError as exc:
                raise Rejected(str(exc)) from None
        if action.get('action')=='sql':
            try:
                return self.view.query(action.get('query'),self.now)
            except ValueError as exc:
                raise Rejected(str(exc)) from None
        if action.get('action')=='risk_report':
            amounts=action.get('forecast_by_csa')
            cash=action.get('available_cash_minor')
            if not isinstance(amounts,dict) or any(not isinstance(k,str) or type(v) is not int or v<0 for k,v in amounts.items()) or type(cash) is not int:
                raise Rejected('forecast_by_csa must map CSA IDs to nonnegative minor-unit integers; available_cash_minor must be an integer')
            sources=action.get('sources',[])
            if not isinstance(sources,list) or any(not isinstance(path,str) for path in sources):
                raise Rejected('sources must be a list of public document paths')
            record={'at_s':self.now,'forecast_by_csa':dict(amounts),'available_cash_minor':cash,
                    'as_of_s':action.get('as_of_s',self.now),'sources':sources}
            self.risk_reports.append(record)
            return {'recorded_id':len(self.risk_reports)}
        return super().act(action)

    def export(self):
        return {**super().export(),'risk_reports':list(self.risk_reports)}
