"""Read-only presentation of the business's saved price list."""
from decimal import Decimal,InvalidOperation

def read_prices(settings,query=''):
    currency=str(settings.get('currency','R')).strip()
    if not currency or len(currency)>8: raise ValueError('The saved currency is invalid. Ask staff to check Price settings.')
    raw=settings.get('prices',[])
    if not isinstance(raw,list): raise ValueError('The saved price list is invalid. Ask staff to check Price settings.')
    rows=[]
    for item in raw:
        try:
            name=item['name'].strip();value=Decimal(str(item['price']))
            if not name or len(name)>80 or not value.is_finite() or value<0 or value>10000000 or value.as_tuple().exponent < -2: raise ValueError()
            rows.append({'name':name,'price':format(value,'.2f')})
        except (KeyError,TypeError,AttributeError,InvalidOperation,ValueError):
            raise ValueError('A saved price is invalid. Ask staff to check Price settings.')
    needle=query.strip().casefold()
    return currency,[row for row in rows if needle in row['name'].casefold()]

def as_text(business,currency,rows):
    if not rows: raise ValueError('There are no prices to save yet.')
    return business+'\nPRICE LIST\n\n'+'\n'.join(f"{item['name']}  —  {currency} {item['price']}" for item in rows)
