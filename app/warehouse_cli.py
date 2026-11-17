import argparse,json
from app import db
from app.config import DATA
from app.warehouse import import_locations,refresh_warehouse,latest_status

def main():
    parser=argparse.ArgumentParser(description='SO Alert Bot small data-engineering pipeline')
    parser.add_argument('action',choices=['import-location','refresh','full','status'])
    parser.add_argument('--file',default=str(DATA/'Location_All.xlsx'))
    args=parser.parse_args();db.connect()
    with db.session() as s:
        if args.action in ('import-location','full'):
            count,_=import_locations(s,args.file);print(f'Location rows loaded: {count}')
        if args.action in ('refresh','full'):
            run_id,status,metrics,inserted,updated,unchanged,rejected=refresh_warehouse(s,'full' if args.action=='full' else 'incremental')
            print(json.dumps({'run_id':run_id,'status':status,'inserted':inserted,'updated':updated,'unchanged':unchanged,'dq_errors':rejected,**metrics},indent=2,default=str))
        if args.action=='status':print(json.dumps(latest_status(s),indent=2,default=str))

if __name__=='__main__':main()
