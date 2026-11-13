# coding=utf-8
import os, json, csv, html, sys, time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import deque
import requests

BASE='https://api.tikhub.io'
PROFILE=BASE+'/api/v1/douyin/web/handler_user_profile_v2'
SHORT=BASE+'/api/v1/douyin/web/fetch_user_profile_by_short_id'
UID=BASE+'/api/v1/douyin/web/handler_user_profile_v3'
POSTS=BASE+'/api/v1/douyin/app/v3/fetch_user_post_videos'
DS='https://api.deepseek.com/chat/completions'
MODEL='deepseek-flash'
CONFIG=Path('config/marketing_accounts.json')
OUT=Path(os.getenv('MARKETING_OUTPUT_DIR','output/marketing_trend'))
WEIGHTS={'category_fit_score':.30,'overseas_suitability_score':.40,'story_clarity_score':.20,'visual_potential_score':.10}
MAX_PAGES=3

def as_int(x):
    try: return int(x) if x not in (None,'') else None
    except (TypeError,ValueError): return None

def walk(root):
    q,seen=deque([root]),set()
    while q:
        x=q.popleft()
        if isinstance(x,dict):
            if id(x) in seen: continue
            seen.add(id(x)); yield x
            q.extend(v for v in x.values() if isinstance(v,(dict,list)))
        elif isinstance(x,list): q.extend(v for v in x if isinstance(v,(dict,list)))

def get(s,key,url,params):
    r=s.get(url,params=params,headers={'Authorization':'Bearer '+key},timeout=60)
    try: d=r.json()
    except ValueError: raise RuntimeError('TikHub non-JSON HTTP '+str(r.status_code))
    if r.status_code!=200: raise RuntimeError('TikHub HTTP '+str(r.status_code)+': '+str(d)[:350])
    if isinstance(d,dict) and d.get('code') not in (None,0,200,'0','200'): raise RuntimeError('TikHub API error: '+str(d)[:350])
    return d

def resolve(s,key,identifier):
    ident=str(identifier).strip()
    tests=[(PROFILE,{'unique_id':ident},'抖音号')]
    if ident.isdigit(): tests=[(SHORT,{'short_id':ident},'短ID'),(UID,{'uid':ident},'UID')]+tests
    errors=[]
    for url,params,label in tests:
        try:
            d=get(s,key,url,params)
            for x in walk(d):
                sec=x.get('sec_uid') or x.get('sec_user_id')
                if sec:return {'sec_user_id':str(sec),'nickname':str(x.get('nickname') or ''),'resolved_by':label}
            errors.append(label+': sec_user_id missing')
        except Exception as e: errors.append(label+': '+str(e)[:100])
    raise RuntimeError('账号解析失败：'+'；'.join(errors))

def find_posts(d):
    for x in walk(d):
        for k in ('aweme_list','item_list','items','videos'):
            a=x.get(k)
            if isinstance(a,list) and (not a or isinstance(a[0],dict)):return a
    return []

def norm(raw,account,stamp):
    x=raw
    for _ in range(5):
        if isinstance(x.get('aweme_info'),dict):x=x['aweme_info'];break
        if isinstance(x.get('data'),dict):x=x['data']
        else:break
    st=x.get('statistics') or x.get('stats') or {}; au=x.get('author') or {}
    vid=str(x.get('aweme_id') or x.get('item_id') or '').strip(); ts=as_int(x.get('create_time'))
    dt=datetime.fromtimestamp(ts,timezone.utc) if ts else None
