import json, math, time
from datetime import datetime, timezone
from pathlib import Path
import requests

VERSION = "EARLY_FLOW_RADAR_V1"
START_BALANCE = 1000.0
MIN_STAKE = 40.0
MAX_STAKE = 200.0
MAX_OPEN = 6
MAX_ALLOCATED = 650.0
MIN_SCORE = 55
INTERVAL = "15m"
TOP_N = 100
ATR_N = 14
STOP_ATR = 1.8
TRAIL_ATR = 2.4
BE_R = 0.8
FEE = 0.0005
SLIP = 0.0003
TIMEOUT = 12
STATE = Path(__file__).with_name("early_flow_radar_paper.json")

# GitHub Actions runners can receive HTTP 451 from Binance Futures depending on
# the runner's region. Early Flow therefore uses Bybit linear perpetual market
# data as its execution-independent market-data source. This keeps the paper
# strategy running even when Binance blocks the runner geographically.
BYBIT = "https://api.bybit.com"
S = requests.Session()
S.headers["User-Agent"] = "EarlyFlowRadarV1/1.1"

def now(): return datetime.now(timezone.utc).isoformat()
def f(x, d=0.0):
    try:
        v=float(x); return v if math.isfinite(v) else d
    except Exception: return d

def get(base,path,params=None):
    r=S.get(base+path,params=params,timeout=TIMEOUT); r.raise_for_status(); return r.json()

def ema(values,n):
    if len(values)<n:return None
    k=2/(n+1); e=sum(values[:n])/n
    for v in values[n:]: e=v*k+e*(1-k)
    return e

def atr(rows,n=ATR_N):
    if len(rows)<n+1:return None
    trs=[]
    for i in range(1,len(rows)):
        c,p=rows[i],rows[i-1]
        trs.append(max(c['h']-c['l'],abs(c['h']-p['c']),abs(c['l']-p['c'])))
    a=sum(trs[:n])/n
    for tr in trs[n:]: a=((n-1)*a+tr)/n
    return a

def state_load():
    if STATE.exists():
        try:
            x=json.loads(STATE.read_text(encoding='utf-8'))
            if x.get('version')==VERSION:return x
        except Exception:pass
    return {'version':VERSION,'balance':START_BALANCE,'trades':[],'runs':0}

def state_save(s):
    tmp=STATE.with_suffix('.tmp'); tmp.write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(STATE)
def opens(s): return [t for t in s['trades'] if t['status']=='OPEN']
def allocated(s): return sum(f(t.get('stake_tl')) for t in opens(s))
def stake_for(score):
    if score<55:return 0.0
    if score<65:return 40.0
    if score<75:return 70.0
    if score<85:return 100.0
    if score<95:return 150.0
    return 200.0

def close_trade(s,t,raw_price,reason):
    if t['side']=='LONG':
        exit_price=raw_price*(1-SLIP); raw=(exit_price-t['entry_price'])*t['qty']
    else:
        exit_price=raw_price*(1+SLIP); raw=(t['entry_price']-exit_price)*t['qty']
    fees=(t['entry_price']*t['qty']+exit_price*t['qty'])*FEE; net=raw-fees
    s['balance']+=t['stake_tl']+net
    t.update(status='CLOSED',exit_time=now(),exit_price=exit_price,realized_pnl=net,exit_reason=reason)
    print(f"CLOSE {t['symbol']} {t['side']} {reason} {net:+.2f} TL score={t['score']}")

def update_trade(s,t,rows):
    a=atr(rows)
    if not a:return
    c=rows[-2]
    if t['side']=='LONG':
        t['peak']=max(t['peak'],c['h']); r=abs(t['entry_price']-t['initial_sl'])
        if c['l']<=t['current_sl']:close_trade(s,t,t['current_sl'],'STOP_OR_TRAIL');return
        if t['peak']>=t['entry_price']+BE_R*r:t['current_sl']=max(t['current_sl'],t['entry_price']*(1+FEE))
        trail=t['peak']-TRAIL_ATR*a
        if trail>t['current_sl']:t['current_sl']=trail;t['trailing']=True
    else:
        t['trough']=min(t['trough'],c['l']); r=abs(t['entry_price']-t['initial_sl'])
        if c['h']>=t['current_sl']:close_trade(s,t,t['current_sl'],'STOP_OR_TRAIL');return
        if t['trough']<=t['entry_price']-BE_R*r:t['current_sl']=min(t['current_sl'],t['entry_price']*(1-FEE))
        trail=t['trough']+TRAIL_ATR*a
        if trail<t['current_sl']:t['current_sl']=trail;t['trailing']=True

def bybit_symbols():
    info=get(BYBIT,'/v5/market/instruments-info',{'category':'linear','limit':1000})
    valid={x['symbol'] for x in info.get('result',{}).get('list',[]) if x.get('status')=='Trading' and x.get('contractType')=='LinearPerpetual' and x.get('quoteCoin')=='USDT'}
    tick=get(BYBIT,'/v5/market/tickers',{'category':'linear'}).get('result',{}).get('list',[])
    ranked=sorted([x for x in tick if x.get('symbol') in valid],key=lambda x:f(x.get('turnover24h')),reverse=True)
    return [x['symbol'] for x in ranked[:TOP_N]]

def klines_binance(symbol,interval=INTERVAL,limit=80):
    # Kept under the old function name to preserve the strategy interface.
    raw=get(BYBIT,'/v5/market/kline',{'category':'linear','symbol':symbol,'interval':'15','limit':limit}).get('result',{}).get('list',[])
    raw=sorted(raw,key=lambda x:int(x[0]))
    return [{'t':int(x[0]),'o':f(x[1]),'h':f(x[2]),'l':f(x[3]),'c':f(x[4]),'v':f(x[5]),'tb':0.0} for x in raw]

def oi_binance(symbol):
    raw=get(BYBIT,'/v5/market/open-interest',{'category':'linear','symbol':symbol,'intervalTime':'15min','limit':4}).get('result',{}).get('list',[])
    raw=sorted(raw,key=lambda x:int(x.get('timestamp',0)))
    return [f(x.get('openInterest')) for x in raw]

def bybit_snapshot(symbol):
    a=get(BYBIT,'/v5/market/tickers',{'category':'linear','symbol':symbol}); rows=a.get('result',{}).get('list',[]); return rows[0] if rows else {}

def funding_binance(symbol): return f(bybit_snapshot(symbol).get('fundingRate'))

def signal(symbol,rows,oi,funding,bybit):
    if len(rows)<30 or len(oi)<2:return None
    c=rows[-2]; price=c['c']; a=atr(rows)
    if not a or price<=0:return None
    closes=[x['c'] for x in rows[:-1]]; ef,es=ema(closes,9),ema(closes,21)
    avgvol=sum(x['v'] for x in rows[-22:-2])/20; vr=c['v']/avgvol if avgvol>0 else 0
    move15=(price/rows[-3]['c']-1)*100; move60=(price/rows[-6]['c']-1)*100
    oi_change=(oi[-1]/oi[0]-1)*100 if oi[0]>0 else 0
    # Bybit kline does not expose taker-buy volume in the same shape, so use
    # price/volume confirmation instead of fabricating a buy-flow value.
    flow=0.5
    bp=f(bybit.get('lastPrice')); cross=(price/bp-1)*100 if bp>0 else 0
    lp=sp=0.0
    if oi_change>=.6:lp+=18;sp+=18
    elif oi_change>=.25:lp+=10;sp+=10
    if vr>=2:lp+=18;sp+=18
    elif vr>=1.35:lp+=12;sp+=12
    elif vr>=1.10:lp+=7;sp+=7
    if .25<=move15<=2.5:lp+=12
    if -2.5<=move15<=-.25:sp+=12
    if .4<=move60<=4:lp+=8
    if -4<=move60<=-.4:sp+=8
    if funding<=-.0004:lp+=10
    elif funding>=.0004:sp+=10
    if bp>0:
        if cross<=-.15:lp+=10
        if cross>=.15:sp+=10
    if ef and es:
        if ef>es:lp+=8
        if ef<es:sp+=8
    side='LONG' if lp>=sp else 'SHORT'; score=max(lp,sp)
    if side=='LONG' and (move15>2.5 or move60>5):return None
    if side=='SHORT' and (move15<-2.5 or move60<-5):return None
    if score<MIN_SCORE:return None
    sl=price-STOP_ATR*a if side=='LONG' else price+STOP_ATR*a
    return {'symbol':symbol,'side':side,'score':round(score,1),'entry':price,'atr':a,'sl':sl,'vol_ratio':vr,'move15':move15,'move60':move60,'oi_change':oi_change,'flow':flow,'funding':funding,'cross_pct':cross}

def main():
    s=state_load(); s['runs']+=1; symbols=bybit_symbols(); candidates=opened=0
    print(f"=== EARLY FLOW RADAR V1.1 | BYBIT LINEAR | {len(symbols)} symbols | balance={s['balance']:.2f} TL ===")
    for t in list(opens(s)):
        try:update_trade(s,t,klines_binance(t['symbol']))
        except Exception as e:print('UPDATE_ERR',t['symbol'],e)
    for symbol in symbols:
        try:
            if any(t['symbol']==symbol and t['status']=='OPEN' for t in s['trades']):continue
            if len(opens(s))>=MAX_OPEN or allocated(s)>=MAX_ALLOCATED:break
            rows=klines_binance(symbol); q=signal(symbol,rows,oi_binance(symbol),funding_binance(symbol),bybit_snapshot(symbol))
            if not q:continue
            candidates+=1; stake=min(stake_for(q['score']),s['balance'],MAX_ALLOCATED-allocated(s))
            if stake<MIN_STAKE:continue
            ep=q['entry']*(1+SLIP if q['side']=='LONG' else 1-SLIP); qty=stake/ep; s['balance']-=stake
            s['trades'].append({'id':f"{symbol}-{rows[-2]['t']}-{q['side']}",'strategy':VERSION,'symbol':symbol,'side':q['side'],'status':'OPEN','entry_time':now(),'entry_price':ep,'qty':qty,'stake_tl':stake,'initial_sl':q['sl'],'current_sl':q['sl'],'peak':ep,'trough':ep,'trailing':False,'score':q['score'],'signal':q})
            opened+=1; print(f"OPEN {symbol} {q['side']} score={q['score']:.1f}/100 stake={stake:.0f}TL")
        except Exception as e:print('SCAN_ERR',symbol,e)
        time.sleep(.03)
    state_save(s); closed=[t for t in s['trades'] if t['status']=='CLOSED']; pnls=[f(t.get('realized_pnl')) for t in closed]; wins=[x for x in pnls if x>0]; losses=[x for x in pnls if x<0]; net=sum(pnls); wr=len(wins)/len(closed)*100 if closed else 0; pf=sum(wins)/abs(sum(losses)) if losses else 0
    print(f"CANDIDATES={candidates} NEW={opened} OPEN={len(opens(s))} CLOSED={len(closed)} BAL={s['balance']:.2f} NET={net:+.2f} WR={wr:.1f}% PF={pf:.2f} ALLOC={allocated(s):.2f}")

if __name__=='__main__':main()
