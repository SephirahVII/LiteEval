from controller import main,TOOLS
from source_engine import SourceDesk

SOURCE_TOOLS=TOOLS.replace('accounts, orders, obligations, approvals, collateral, suspense',
                         'accounts, orders, obligations, approvals, collateral, suspense, catalog, risk_book')
SOURCE_TOOLS+='''
accounts returns bank book balances; internal availability is not calculated by this view.
calculate(expression): bounded arithmetic with numbers, parentheses, +, -, *, /, //. No names or functions.
Returns an integer value when integral, otherwise a decimal string. Costs30 business seconds.
sql(query): read-only SQLite SELECT over currently published CSVs; table names and columns are in query(catalog).
Numeric integer source columns use INTEGER, identifiers/text use TEXT; types are in the catalog. SQLite casts/aggregates are available.
Maximum100 returned rows, with a truncation flag.
External files, ATTACH, extensions and modifications are prohibited. SQL costs30 business seconds.
risk_report(forecast_by_csa, available_cash_minor, as_of_s, sources optional): write the treasury riskbook.
forecast_by_csa maps CSA IDs to expected additional GBP cash VM in minor units; available_cash_minor is P-GBP usable cash.
Omitted CSA IDs mean zero additional VM. as_of_s specifies the business time of the risk snapshot.
sources is a list of public document paths. The tool records reports without evaluating their accuracy. Costs30 seconds.
'''

if __name__=='__main__':
    main(factory=SourceDesk,instructions=SOURCE_TOOLS)
