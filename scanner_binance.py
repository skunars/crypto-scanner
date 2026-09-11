import json, os, time, math
from datetime import datetime, timezone
from pathlib import Path
import requests

VERSION="BINANCE_PERP_MOMENTUM_V1_1"
BASE="https://fapi.binance.com"
STATE=Path(__file__).with_name("paper_trades.json")
START=1000.0
STAKE=100.0
MAX_OPEN=10
INTERVAL="15m"
HTF="1h"
LOOKBACK=120
BREAKOUT=20
ATR_N=14
EMA_FAST=20
EMA_SLOW=50
HTF_FAST=20
HTF_SLOW=50
MIN_VOL_RATIO=1.05
MIN_MOVE_PCT=0.10
MIN_ATR_PCT=0.15
MAX_ATR_PCT=8.0
OI_LOOKBACK=3
MIN_OI_CHANGE=0.0
STOP_ATR=1.8
TRAIL_ATR=2.5
BE_R=0.75
FEE=0.0005
SLIP=0.0003
TOP_N=80
TIMEOUT=15

S=requests.Session()
S.headers["User-Agent"]="BinancePerpMomentumV1.1/1.0"

def now(): return datetime.now(timezone.utc).isoformat()
def f(x,d=0.0):
    try:
        v=float(x); return v if math.isfinite(v) else d
    except: return d

def get(path, params=None):
    r=S.get(BASE+path,params=params,timeout=TIMEOUT); r.raise_for_status()
    j=r.json()
    if isinstance(j,dict) and j.get("code",0) not in (0,"0"):
        raise RuntimeError(j.get("msg","binance error"))
    return j

def klines(symbol,interval,limit=LOOKBACK):
    a=get("/fapi/v1/klines",{"symbol":symbol,"interval":interval,"limit":limit})
    return [{"t":int(x[0]),"o":f(x[1]),"h":f(x[2]),"l":f(x[3]),"c":f(x[4]),"v":f(x[5]),"tb":f(x[9])} for x in a]

def ema(vals,n):
    if len(vals)<n: return None
    k=2/(n+1); e=sum(vals[:n])/n
    for v in vals[n:]: e=v*k+e*(1-k)
    return e

def atr(rows,n=ATR_N):
    if len(rows)<n+1: return None
    trs=[]
    for i in range(1,len(rows)):
        c=rows[i]; p=rows[i-1]
        trs.append(max(c["h"]-c["l"],abs(c["h"]-p["c"]),abs(c["l"]-p["c"])))
    a=sum(trs[:n])/n
    for tr in trs[n:]: a=((n-1)*a+tr)/n
    return a

def state_load():
    if STATE.exists():
        try:
            x=json.loads(STATE.read_text())
            if x.get("version")==VERSION: return x
        except: pass
    return {"version":VERSION,"balance":START,"trades":[]}

def state_save(s):
    t=STATE.with_suffix(".tmp")
    t.write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding="utf-8")
    t.replace(STATE)

def open_trades(s): return [x for x in s["trades"] if x["status"]=="OPEN"]

def close_trade(s,t,price,reason):
    p=price*(1+SLIP if t["side"]=="SHORT" else 1-SLIP)
    raw=(t["entry_price"]-p if t["side"]=="SHORT" else p-t["entry_price"])*t["qty"]
    fees=(t["entry_price"]*t["qty"]+p*t["qty"])*FEE
    net=raw-fees
    s["balance"] += STAKE+net
    t.update(status="CLOSED",exit_time=now(),exit_price=p,realized_pnl=net,exit_reason=reason)
    print(f"CLOSE {t['symbol']} {t['side']} {reason} {net:+.2f} TL")

def update_trade(s,t,rows):
    if len(rows)<ATR_N+3: return
    c=rows[-2]; a=atr(rows)
    if not a or a<=0: return
    if t["side"]=="LONG":
        t["peak"]=max(t["peak"],c["h"])
        r=abs(t["entry_price"]-t["initial_sl"])
        if c["l"]<=t["current_sl"]: close_trade(s,t,t["current_sl"],"STOP_TRAIL"); return
        if t["peak"]>=t["entry_price"]+BE_R*r: t["current_sl"]=max(t["current_sl"],t["entry_price"]*(1+FEE))
        trail=t["peak"]-TRAIL_ATR*a
        if trail>t["current_sl"]: t["current_sl"]=trail; t["trailing"]=True
        if c["c"]<t["breakout"]: close_trade(s,t,c["c"],"FAILED_BREAKOUT")
    else:
        t["trough"]=min(t["trough"],c["l"])
        r=abs(t["entry_price"]-t["initial_sl"])
        if c["h"]>=t["current_sl"]: close_trade(s,t,t["current_sl"],"STOP_TRAIL"); return
        if t["trough"]<=t["entry_price"]-BE_R*r: t["current_sl"]=min(t["current_sl"],t["entry_price"]*(1-FEE))
        trail=t["trough"]+TRAIL_ATR*a
        if trail<t["current_sl"]: t["current_sl"]=trail; t["trailing"]=True
        if c["c"]>t["breakout"]: close_trade(s,t,c["c"],"FAILED_BREAKOUT")

def signal(symbol,m,h,oi):
    if len(m)<BREAKOUT+ATR_N+5 or len(h)<HTF_SLOW+5 or len(oi)<1: return None
    c=m[-2]; a=atr(m)
    if not a or c["c"]<=0: return None
    atrpct=a/c["c"]*100
    if not(MIN_ATR_PCT<=atrpct<=MAX_ATR_PCT): return None
    closes=[x["c"] for x in m[:-1]]; ef=ema(closes,EMA_FAST); es=ema(closes,EMA_SLOW)
    hc=[x["c"] for x in h[:-1]]; hf=ema(hc,HTF_FAST); hs=ema(hc,HTF_SLOW)
    avgvol=sum(x["v"] for x in m[-22:-2])/20
    vr=c["v"]/avgvol if avgvol>0 else 0
    move=(c["c"]/m[-5]["c"]-1)*100
    up=max(x["h"] for x in m[-(BREAKOUT+2):-2]); dn=min(x["l"] for x in m[-(BREAKOUT+2):-2])
    oi_change=(oi[-1]-oi[0])/oi[0] if len(oi)>=2 and oi[0]>0 else 0
    local_long=ef>es
    local_short=ef<es
    htf_long=hf>hs
    htf_short=hf<hs
    long_break=c["c"]>up
    short_break=c["c"]<dn
    vol_ok=vr>=MIN_VOL_RATIO
    long_score=int(local_long)+int(htf_long)+int(vol_ok)+int(move>=MIN_MOVE_PCT)+int(oi_change>=MIN_OI_CHANGE)
    short_score=int(local_short)+int(htf_short)+int(vol_ok)+int(move<=-MIN_MOVE_PCT)+int(oi_change>=MIN_OI_CHANGE)
    long_ok=long_break and long_score>=3
    short_ok=short_break and short_score>=3
    if not(long_ok or short_ok): return None
    if long_ok and (not short_ok or long_score>=short_score):
        side="LONG"; score=long_score; level=up
        sl=c["c"]-STOP_ATR*a
    else:
        side="SHORT"; score=short_score; level=dn
        sl=c["c"]+STOP_ATR*a
    return {"symbol":symbol,"side":side,"entry":c["c"],"atr":a,"sl":sl,"breakout":level,"vol_ratio":vr,"move_pct":move,"oi_change":oi_change,"score":score}

def main():
    s=state_load(); info=get("/fapi/v1/exchangeInfo")
    symbols=[x["symbol"] for x in info["symbols"] if x.get("status")=="TRADING" and x.get("contractType")=="PERPETUAL" and x.get("quoteAsset")=="USDT"]
    tick=get("/fapi/v1/ticker/24hr"); ranked=sorted([x for x in tick if x["symbol"] in symbols],key=lambda x:f(x.get("quoteVolume")),reverse=True)[:TOP_N]
    opened=0; candidates=0; print(f"=== {VERSION} {len(ranked)} symbols ===")
    for item in ranked:
        sym=item["symbol"]
        try:
            m=klines(sym,INTERVAL); h=klines(sym,HTF)
            oi_raw=get("/futures/data/openInterestHist",{"symbol":sym,"period":INTERVAL,"limit":OI_LOOKBACK})
            oi=[f(x.get("sumOpenInterestValue")) for x in oi_raw]
            for t in list(open_trades(s)):
                if t["symbol"]==sym: update_trade(s,t,m)
            if len(open_trades(s))<MAX_OPEN and not any(t["symbol"]==sym for t in open_trades(s)):
                q=signal(sym,m,h,oi)
                if q:
                    candidates+=1
                    if candidates<=20: print(f"CANDIDATE {sym} {q['side']} score={q['score']}/5 vol={q['vol_ratio']:.2f} move={q['move_pct']:.2f}% OI={q['oi_change']*100:.2f}%")
                    if q["score"]>=3:
                        ep=q["entry"]*(1+SLIP if q["side"]=="LONG" else 1-SLIP); qty=STAKE/ep; s["balance"]-=STAKE
                        s["trades"].append({"id":f"{sym}-{m[-2]['t']}-{q['side']}","strategy":VERSION,"symbol":sym,"side":q["side"],"status":"OPEN","entry_time":now(),"entry_price":ep,"qty":qty,"stake_tl":STAKE,"initial_sl":q["sl"],"current_sl":q["sl"],"breakout":q["breakout"],"peak":ep,"trough":ep,"trailing":False,"signal":q})
                        opened+=1; print(f"OPEN {sym} {q['side']} entry={ep:.8g} score={q['score']}/5 vol={q['vol_ratio']:.2f} move={q['move_pct']:.2f}% OI={q['oi_change']*100:.2f}%")
        except Exception as e: print("ERR",sym,e)
        time.sleep(0.03)
    state_save(s); closed=[t for t in s["trades"] if t["status"]=="CLOSED"]; wins=[f(t.get("realized_pnl",0)) for t in closed if f(t.get("realized_pnl",0))>0]; losses=[f(t.get("realized_pnl",0)) for t in closed if f(t.get("realized_pnl",0))<0]; pf=sum(wins)/abs(sum(losses)) if losses else 0; wr=len(wins)/len(closed)*100 if closed else 0
    print(f"NEW={opened} CANDIDATES={candidates} OPEN={len(open_trades(s))} CLOSED={len(closed)} BAL={s['balance']:.2f} WR={wr:.1f}% NET={sum(wins)+sum(losses):+.2f} PF={pf:.2f}")

if __name__=="__main__": main()
