"""Lossless offline measurement packets; distinct from application wire ABIs."""
def transfer(value):
    """Explicit measurement packet tags preserve integers without record collisions."""
    if value is None:return ['null']
    if type(value)is bool:return ['bool',value]
    if type(value)is int:return ['integer',str(value)]
    if type(value)is float:return ['number',repr(value)]
    if isinstance(value,str):return ['string',value]
    if isinstance(value,list):return ['array',[transfer(x)for x in value]]
    if isinstance(value,dict):return ['object',[[k,transfer(v)]for k,v in value.items()]]
    raise ValueError('measurement transfer value')

