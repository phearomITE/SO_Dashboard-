import argparse
from app.config import DATA,TOKEN
from app.imports import read_roster,read_plan,read_dealer_locations
from app.rules import is_reporter
from app.diagnostics import check_ocr,database_message

def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',action='store_true')
    parser.add_argument('--require-ocr',action='store_true')
    parser.add_argument('--require-token',action='store_true')
    args=parser.parse_args(argv);failures=[]
    try:
        rows=read_roster(DATA/'List_SO.xlsx');plan=read_plan(DATA/'Plan_SO.xlsx');dealer_locations=read_dealer_locations(DATA/'Dealer Location With sales coverage.xlsx')
        print(f'Roster: {len(rows)} employees; {sum(is_reporter(r["position"]) for r in rows)} monitored SO/SS')
        print(f'Plan: {len(plan)} scheduled commune/date rows')
        print(f'Dealer origins: {len({r["code"] for r in dealer_locations})} dealers from {len(dealer_locations)} source rows')
    except Exception as exc:
        failures.append(f'Input workbook cannot be read ({type(exc).__name__}). Check roster, plan and dealer-location workbooks in data/.')
    ready,message=check_ocr();print(f'OCR: {message}')
    if args.require_ocr and not ready:failures.append('Photo timestamp OCR is not ready.')
    token_ready=bool(TOKEN and TOKEN!='PUT_NEW_BOT_TOKEN_HERE')
    print('Bot token: configured (not verified with Telegram)' if token_ready else 'Bot token: set TELEGRAM_BOT_TOKEN in .env')
    if args.require_token and not token_ready:failures.append('Bot token is missing.')
    try:
        from app.geo import load_boundaries
        n=len(load_boundaries());print(f'Boundary polygons: {n}'+(' (Plan Check stays Not checked until boundaries are provided)' if not n else ''))
    except Exception as exc:failures.append(f'Boundary file could not be read ({type(exc).__name__}); check data/boundaries.')
    if args.db:
        from app import db
        try:
            if not db.probe_connection():failures.append('Database probe did not return the expected result.')
            else:print('Database connection: ready')
        except Exception as exc:
            try:url=db.database_url()
            except Exception:url=None
            failures.append(database_message(exc,url))
    for failure in failures:print(f'NOT READY: {failure}')
    print('Checks failed; fix the items above before starting.' if failures else 'Requested checks passed.')
    print('No Telegram messages sent. No database tables or records changed.')
    return 1 if failures else 0
if __name__=='__main__':raise SystemExit(main())
