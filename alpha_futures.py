import os, json, math
from datetime import datetime, timezone, timedelta
import requests
import pandas as pd
import numpy as np

BASE = "https://www.okx.com"
STATE_FILE = "alpha_paper.json"
EQUITY_START = 1000.0
MARGIN_PER_TRADE = 100.0
LEVERAGE = 3.0
MAX_POSITIONS = 4
MAX_SCAN = 40
VERSION = "ALPHA_REGIME_V1"
FEE = float(os.getenv("ALPHA_TAKER_FEE", "0.0005"))
SLIP = float(os.getenv("ALPHA_SLIPPAGE", "0.0004"))


def now(): return datetime.now(timezone.utc).isoformat()

def num(x, d=0.0):
    try: return float(x)
    except Exception: return d

def api(path, params):
    r = requests.get(BASE + path, params=params, timeout=20)
    r.raise_for_status(); d = r.json()
    if d.get("code") != "0": raise RuntimeError(d.get("msg", "OKX API error"))
    return d.get("data", [])

def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def atr(df, n=14):
    pc = df.close.shift(1)
    tr = pd.concat([(df.high-df.low), (df.high-pc).abs(), (df.low-pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

def rsi(s, n=14):
    d=s.diff(); up=d.clip(lower=0).ewm(alpha=1/n,adjust=False).mean(); dn=(-d.clip(upper=0)).ewm(alpha=1/n,adjust=False).mean()
    rs=up/dn.replace(0,np.nan); return (100-100/(1+rs)).fillna(50)

def adx(df,n=14):
    up=df.high.diff(); dn=-df.low.diff(); plus=up.where((up>dn)&(up>0),0.0); minus=dn.where((dn>up)&(dn>0),0.0)
    a=atr(df,n).replace(0,np.nan); p=100*plus.ewm(alpha=1/n,adjust=False).mean()/a; m=100*minus.ewm(alpha=1/n,adjust=False).mean()/a
    dx=100*(p-m).abs()/(p+m).replace(0,np.nan); return dx.ewm(alpha=1/n,adjust=False).mean().fillna(0)

def candles(inst, bar, limit=300):
    raw=api('/api/v5/market/candles',{'instId':inst,'bar':bar,'limit':str(limit)}); rows=[]
    for x in raw:
        if len(x)>=9 and x[8]=='1': rows.append({'ts':int(x[0]),'open':num(x[1]),'high':num(x[2]),'low':num(x[3]),'close':num(x[4]),'volume':num(x[5])})
    return pd.DataFrame(rows).sort_values('ts').drop_duplicates('ts').reset_index(drop=True) if rows else pd.DataFrame()

def enrich(df):
    if len(df)<210:return df
    df=df.copy(); df['ema20']=ema(df.close,20); df['ema50']=ema(df.close,50); df['ema200']=ema(df.close,200); df['atr']=atr(df,14); df['rsi']=rsi(df.close,14); df['adx']=adx(df,14)
    df['macd']=ema(df.close,12)-ema(df.close,26); df['signal']=ema(df.macd,9); df['hist']=df.macd-df.signal
    df['vol_ratio']=df.volume/df.volume.rolling(20).mean().replace(0,np.nan); df['hh20']=df.high.shift(1).rolling(20).max(); df['ll20']=df.low.shift(1).rolling(20).min()
    mid=df.close.rolling(20).mean(); sd=df.close.rolling(20).std(); df['bb_mid']=mid; df['bb_up']=mid+2*sd; df['bb_dn']=mid-2*sd
    return df.dropna().reset_index(drop=True)

def load():
    try:
        with open(STATE_FILE,encoding='utf-8') as f:return json.load(f)
    except:return {'equity':EQUITY_START,'positions':[],'history':[],'version':VERSION}

def save(s):
    with open(STATE_FILE,'w',encoding='utf-8') as f:json.dump(s,f,ensure_ascii=False,indent=2)

def instruments():
    data=api('/api/v5/public/instruments',{'instType':'SWAP'}); out=[]
    for x in data:
        s=x.get('instId','')
        if x.get('state')=='live' and s.endswith('-USDT-SWAP'): out.append(s)
    return out

def tickers():
    out={}
    for x in api('/api/v5/market/tickers',{'instType':'SWAP'}):
        s=x.get('instId','')
        if s.endswith('-USDT-SWAP'): out[s]={'last':num(x.get('last')),'vol':num(x.get('volCcy24h'))}
    return out

def funding(inst):
    try:return num(api('/api/v5/public/funding-rate',{'instId':inst})[0].get('fundingRate'))
    except:return 0.0

def close_position(state,p,price,reason):
    entry=num(p['entry']); side=p['side']; move=(price-entry)/entry if side=='LONG' else (entry-price)/entry
    notional=MARGIN_PER_TRADE*LEVERAGE; gross=notional*move; costs=notional*(FEE*2+SLIP); pnl=gross-costs; state['equity']+=pnl
    p.update({'status':'CLOSED','exit':price,'exit_time':now(),'pnl_pct':move,'net_pnl':pnl,'reason':reason}); state['history'].append(p.copy()); state['positions'].remove(p)

def manage(state, prices):
    for p in state['positions'][:]:
        price=prices.get(p['symbol']);
        if not price: continue
        entry=num(p['entry']); side=p['side']; move=(price-entry)/entry if side=='LONG' else (entry-price)/entry; p['current_pct']=move; p['peak_pct']=max(num(p.get('peak_pct')),move)
        if (side=='LONG' and price<=num(p['stop'])) or (side=='SHORT' and price>=num(p['stop'])): close_position(state,p,price,'HARD_STOP'); continue
        r=num(p['risk_pct'])
        if p['peak_pct']>=r:
            lock=max(0.002,p['peak_pct']*0.45); p['trail']=entry*(1+lock) if side=='LONG' else entry*(1-lock)
            if (side=='LONG' and price<=p['trail']) or (side=='SHORT' and price>=p['trail']): close_position(state,p,price,'PROFIT_TRAIL'); continue
        if p['peak_pct']>=2*r: p['trail'] = entry*(1+0.75*p['peak_pct']) if side=='LONG' else entry*(1-0.75*p['peak_pct'])

def signal(m,h,regime,fr):
    x=m.iloc[-1]; prev=m.iloc[-2]
    trend_up=h.iloc[-1].close>h.iloc[-1].ema200 and h.iloc[-1].ema50>h.iloc[-1].ema200; trend_dn=h.iloc[-1].close<h.iloc[-1].ema200 and h.iloc[-1].ema50<h.iloc[-1].ema200
    long_ok=(x.ema20>x.ema50>x.ema200 and x.adx>=22 and x.rsi>=52 and x.rsi<=68 and x['hist']>0 and x.vol_ratio>=1.05 and x.close>x.hh20 and x.close<=x.ema20+1.8*x.atr and trend_up and fr<0.0008)
    short_ok=(x.ema20<x.ema50<x.ema200 and x.adx>=22 and x.rsi>=32 and x.rsi<=48 and x['hist']<0 and x.vol_ratio>=1.05 and x.close<x.ll20 and x.close>=x.ema20-1.8*x.atr and trend_dn and fr>-0.0008)
    range_long=(regime=='RANGE' and x.rsi<29 and x.close<x.bb_dn and x.close>prev.close and fr<0.001); range_short=(regime=='RANGE' and x.rsi>71 and x.close>x.bb_up and x.close<prev.close and fr>-0.001)
    if long_ok:return 'LONG','TREND'
    if short_ok:return 'SHORT','TREND'
    if range_long:return 'LONG','MEAN_REVERT'
    if range_short:return 'SHORT','MEAN_REVERT'
    return None,None

def main():
    state=load(); tk=tickers(); universe=sorted([s for s in instruments() if s in tk],key=lambda s:tk[s]['vol'],reverse=True)[:MAX_SCAN]; prices={s:tk[s]['last'] for s in universe}
    btc=candles('BTC-USDT-SWAP','1H',300); btc=enrich(btc)
    if btc.empty: raise RuntimeError('BTC regime unavailable')
    b=btc.iloc[-1]; regime='TREND_UP' if b.close>b.ema200 and b.ema50>b.ema200 and b.adx>=20 else ('TREND_DOWN' if b.close<b.ema200 and b.ema50<b.ema200 and b.adx>=20 else 'RANGE')
    manage(state,prices); existing={p['symbol'] for p in state['positions']}; opened=0; candidates=[]
    for s in universe:
        if len(state['positions'])+opened>=MAX_POSITIONS or s in existing: continue
        try:
            m=enrich(candles(s,'15m',300)); h=enrich(candles(s,'1H',250))
            if len(m)<210 or len(h)<210: continue
            fr=funding(s); side,mode=signal(m,h,regime,fr)
            if not side: continue
            x=m.iloc[-1]; stop_dist=max(0.012,min(0.028,1.35*x.atr/x.close)); score=0
            score += 30 if x.adx>=25 else 20; score += 20 if x.vol_ratio>=1.25 else 10; score += 20 if abs(fr)<0.0004 else 10; score += 20 if (side=='LONG' and x['hist']>0) or (side=='SHORT' and x['hist']<0) else 0; score += 10 if mode=='TREND' else 5
            if score<70: continue
            candidates.append((score,s,side,mode,x.close,stop_dist,fr))
        except Exception as e: print(s,e)
    candidates.sort(reverse=True)
    for score,s,side,mode,entry,sd,fr in candidates[:max(0,MAX_POSITIONS-len(state['positions']))]:
        stop=entry*(1-sd) if side=='LONG' else entry*(1+sd); state['positions'].append({'strategy':VERSION,'symbol':s,'side':side,'mode':mode,'entry':entry,'entry_time':now(),'stop':stop,'risk_pct':sd,'peak_pct':0.0,'current_pct':0.0,'funding_at_entry':fr,'score':score,'margin_tl':MARGIN_PER_TRADE,'leverage':LEVERAGE}); opened+=1
    closed=state['history']; wins=[x for x in closed if num(x.get('net_pnl'))>0]; losses=[x for x in closed if num(x.get('net_pnl'))<=0]; net=sum(num(x.get('net_pnl')) for x in closed)
    print('='*72); print(f'ALPHA REGIME V1 | regime={regime} scan={len(universe)} candidates={len(candidates)} opened={opened}'); print(f'equity={state["equity"]:.2f} positions={len(state["positions"])} closed={len(closed)} wins={len(wins)} losses={len(losses)} win_rate={(100*len(wins)/len(closed) if closed else 0):.2f}% net={net:.2f} TL'); print('='*72)
    save(state)

if __name__=='__main__': main()
