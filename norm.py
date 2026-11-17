import re
FILL={'with','hybrid','forged','wheel','wheels','rims','rim'}
SYN={'matt':'matte','ark':'dark','grey':'grey','gray':'grey','gunmetal':'gunmetal'}
def norm(s):
    s=s.lower().replace('&',' ').replace('.',' ')
    s=re.sub(r'\bgf\s*s(\d+)',r'gfs\1',s)
    s=re.sub(r'\bhf\s*(\d)\s+1\b',r'hf\g<1>1',s)
    s=re.sub(r'\bhf(\d)\s*1\b',r'hf\g<1>1',s)
    s=re.sub(r'[^a-z0-9 ]',' ',s)
    w=[SYN.get(t,t) for t in s.split() if t not in FILL]
    return ' '.join(w)
