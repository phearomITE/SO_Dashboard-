from datetime import date
from fastapi import HTTPException

def reporting_period(day=None,start=None,end=None):
    if start is None and end is None and day is not None:return day,day
    if start is None or end is None:
        raise HTTPException(422,'Choose both From date and To date.')
    if end < start or (end-start).days >= 92:
        raise HTTPException(422,'Choose an ordered date range of 1 to 92 days.')
    return start,end
