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
    share=x.get('share_info') or {}; url=x.get('share_url') or share.get('share_url') or ('https://www.douyin.com/video/'+vid if vid else '')
    return {'video_id':vid,'title':str(x.get('desc') or x.get('title') or '').strip(),'author':str(au.get('nickname') or account['name']),
      'account_name':account['name'],'account_identifier':account['identifier'],'likes':as_int(st.get('digg_count') or st.get('like_count')),
      'collects':as_int(st.get('collect_count') or st.get('favorite_count')),'comments':as_int(st.get('comment_count')),
      'shares':as_int(st.get('share_count')),'publish_timestamp':ts,'publish_time_utc':dt.isoformat() if dt else '',
      'video_url':str(url or ''),'captured_at_utc':stamp}

def collect(s,key,account,profile,cutoff,stamp):
    out=[]; cur=0; pages=0; older=False; missing=0
    for _ in range(MAX_PAGES):
        d=get(s,key,POSTS,{'sec_user_id':profile['sec_user_id'],'max_cursor':cur,'count':20,'sort_type':0})
        pages+=1; arr=find_posts(d)
        if not arr:break
        for raw in arr:
            v=norm(raw,account,stamp)
            if not v['video_id'] or not v['publish_timestamp']:continue
            dt=datetime.fromtimestamp(v['publish_timestamp'],timezone.utc)
            if dt<cutoff:older=True;continue
            if v['collects'] is None:missing+=1;continue
            if v['likes'] is not None and v['likes']>2000 and v['collects']>100:out.append(v)
        nxt=None; more=False
        for x in walk(d):
            if x.get('max_cursor') is not None:nxt=x['max_cursor']
            if x.get('has_more') is not None:more=str(x['has_more']).lower() in ('1','true','yes')
        if older or not more or nxt is None or str(nxt)==str(cur):break
        cur=nxt;time.sleep(.25)
    return out,{'pages_checked':pages,'qualified_videos':len(out),'missing_collect_count':missing,'reached_older_than_7_days':older}

def parse_json(txt):
    txt=(txt or '').strip()
    if txt.startswith(chr(96)*3):txt=txt.split('\n',1)[-1].removesuffix(chr(96)*3).strip()
    a,b=txt.find('{'),txt.rfind('}')
    if a<0 or b<a:raise ValueError('DeepSeek JSON parse failed')
    return json.loads(txt[a:b+1])

def score(videos,cats):
    key=os.getenv('DEEPSEEK_API_KEY','').strip()
    if not key:raise RuntimeError('Missing DEEPSEEK_API_KEY')
    s=requests.Session(); calls=scored=failed=0
    sysmsg=('你是面向英语受众的短视频选题编辑。只能依据标题/文案和互动数字判断，没有观看画面或评论，不得编造。'
      +'类目只能从：'+'、'.join(cats)+'。四项0-100整数：category_fit_score类目匹配30%、overseas_suitability_score海外适配40%、'
      +'story_clarity_score故事清晰20%、visual_potential_score视觉潜力10%。程序计算加权总分。'
      +'subject_summary中文一句最多35字，具体说明人物/事件是什么；overseas_reason中文一句最多35字；content_angle中文最多30字；'
      +'english_hook简短自然英文开场；confidence为high/medium/low。资料不足须说明，不可猜。只输出JSON：'
      +'{"items":[{"id":0,"categories":[],"primary_category":"","category_fit_score":0,"overseas_suitability_score":0,"story_clarity_score":0,"visual_potential_score":0,"subject_summary":"","overseas_reason":"","content_angle":"","english_hook":"","confidence":"low"}]}')
    for start in range(0,len(videos),12):
        batch=videos[start:start+12]; rows=[{'id':i,'account':v['account_name'],'title':v['title'],'likes':v['likes'],'collects':v['collects'],'comments':v['comments']} for i,v in enumerate(batch)]
        try:
            r=s.post(DS,headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},json={'model':MODEL,'messages':[{'role':'system','content':sysmsg},{'role':'user','content':json.dumps(rows,ensure_ascii=False)}],'thinking':{'type':'disabled'},'temperature':.2,'max_tokens':8192,'response_format':{'type':'json_object'}},timeout=120)
            calls+=1;r.raise_for_status(); ans=parse_json(r.json()['choices'][0]['message']['content']); byid={int(x['id']):x for x in ans.get('items',[]) if 'id' in x}
            for i,v in enumerate(batch):
                a=byid.get(i)
                if not a:v['ai_status']='missing';continue
                for k in WEIGHTS:v[k]=max(0,min(100,as_int(a.get(k)) or 0))
                v['overall_score']=round(sum(v[k]*w for k,w in WEIGHTS.items()))
                v['categories']=[x for x in a.get('categories',[]) if x in cats]; primary=a.get('primary_category')
                v['primary_category']=primary if primary in cats else (v['categories'][0] if v['categories'] else '未分类')
                for k in ('subject_summary','overseas_reason','content_angle','english_hook'):v[k]=str(a.get(k) or '')[:200]
                conf=str(a.get('confidence') or 'low').lower();v['confidence']=conf if conf in ('high','medium','low') else 'low';v['ai_status']='completed';scored+=1
        except Exception as e:
            failed+=1;print('DeepSeek批次失败：'+str(e),file=sys.stderr)
            for v in batch:v['ai_status']='failed'
    return {'calls':calls,'scored':scored,'failed_batches':failed,'model':MODEL}

def write_reports(videos,summary):
    OUT.mkdir(parents=True,exist_ok=True); videos.sort(key=lambda v:(v.get('overall_score',-1),v.get('likes',-1)),reverse=True)
    jp,cp,hp=OUT/'marketing_trend.json',OUT/'marketing_trend.csv',OUT/'marketing_trend.html'
    jp.write_text(json.dumps({'summary':summary,'videos':videos,'weights':WEIGHTS},ensure_ascii=False,indent=2),encoding='utf-8')
    cols=['rank','overall_score','primary_category','account_name','author','title','likes','collects','publish_time_utc','video_url','category_fit_score','overseas_suitability_score','story_clarity_score','visual_potential_score','subject_summary','overseas_reason','content_angle','english_hook','confidence']
    with cp.open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=cols,extrasaction='ignore');w.writeheader()
        for rank,v in enumerate(videos,1):row=dict(v);row['rank']=rank;w.writerow(row)
    cards=[]
    for rank,v in enumerate(videos,1):
        url=html.escape(v.get('video_url') or '',quote=True); link='<a href="'+url+'" target="_blank">打开抖音视频 ↗</a>' if url else ''
        cards.append('<article><b>#'+str(rank)+' · '+str(v.get('overall_score','—'))+'分 · '+html.escape(v.get('primary_category','未分类'))+'</b><h2>'+html.escape(v.get('title') or '（无标题）')+'</h2><small>'+html.escape(v.get('account_name',''))+' · '+html.escape(v.get('author',''))+' · '+format(v.get('likes') or 0,',')+'赞 · '+format(v.get('collects') or 0,',')+'收藏</small><p><b>内容：</b>'+html.escape(v.get('subject_summary') or '标题信息不足，需人工核实。')+'</p><p><b>海外适配：</b>'+html.escape(v.get('overseas_reason') or '')+'</p>'+link+'<details><summary>评分细项 / 英文开场</summary><p>类目 '+str(v.get('category_fit_score','—'))+' · 海外 '+str(v.get('overseas_suitability_score','—'))+' · 故事 '+str(v.get('story_clarity_score','—'))+' · 视觉 '+str(v.get('visual_potential_score','—'))+'</p><p>'+html.escape(v.get('content_angle') or '')+'</p><p>'+html.escape(v.get('english_hook') or '')+'</p></details></article>')
    doc='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>营销号趋势</title><style>body{font-family:Arial;background:#f5f7fb;color:#192432;max-width:900px;margin:auto;padding:24px}article{background:white;border:1px solid #ddd;border-radius:12px;padding:16px;margin:12px 0}small,details{color:#64748b;font-size:12px}h2{font-size:17px}p{line-height:1.6}a{color:#315efb}</style><h1>营销号趋势</h1><p>指定账号 · 最近7天 · 点赞&gt;2000 · 收藏&gt;100 · AI综合分降序</p><p>监控 '+str(summary['account_count'])+' 个账号 · 解析成功 '+str(summary['accounts_resolved'])+' · 达标 '+str(summary['qualified_videos'])+' · 已评分 '+str(summary['scored_videos'])+'</p>'+(''.join(cards) if cards else '<p>没有符合条件的视频。</p>')+'<footer>每个账号最多检查3页（每页20条）；AI只分析标题和互动数据，没有观看画面。</footer></html>'
    hp.write_text(doc,encoding='utf-8');return [jp,cp,hp]

def main():
    if not CONFIG.exists():raise FileNotFoundError(str(CONFIG))
    cfg=json.loads(CONFIG.read_text(encoding='utf-8'));key=os.getenv('TIKHUB_API_KEY','').strip()
    if not key:raise RuntimeError('Missing TIKHUB_API_KEY')
    stamp=datetime.now(timezone.utc).isoformat();cutoff=datetime.now(timezone.utc)-timedelta(days=7);s=requests.Session()
    found={}; statuses=[];profile_calls=pages=0
    for a in cfg['accounts']:
        row={'name':a['name'],'identifier':a['identifier'],'status':'failed'}
        try:
            profile_calls+=1;p=resolve(s,key,a['identifier']);posts,meta=collect(s,key,a,p,cutoff,stamp);row.update(meta);row['status']='ok';row['resolved_nickname']=p['nickname'];row['resolved_by']=p['resolved_by']
            for v in posts:found.setdefault(v['video_id'],v)
            pages+=meta['pages_checked'];print(a['name']+'：达标 '+str(len(posts))+' 条')
        except Exception as e:row['error']=str(e)[:500];print('账号失败 '+a['name']+'：'+str(e),file=sys.stderr)
        statuses.append(row);time.sleep(.25)
    videos=list(found.values());ai=score(videos,cfg['categories']) if videos else {'calls':0,'scored':0,'failed_batches':0,'model':MODEL}
    summary={'detector':'营销号趋势','captured_at_utc':stamp,'account_count':len(cfg['accounts']),'accounts_resolved':sum(x['status']=='ok' for x in statuses),'qualified_videos':len(videos),'scored_videos':ai['scored'],'profile_requests':profile_calls,'post_page_requests':pages,'ai_analysis':ai,'accounts':statuses,'filters':{'lookback_days':7,'likes_gt':2000,'collects_gt':100},'score_weights':WEIGHTS}
    for p in write_reports(videos,summary):print('REPORT: '+str(p))
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    if summary['accounts_resolved']==0:raise RuntimeError('所有账号解析失败')

if __name__=='__main__':
    try:main()
    except Exception as e:print('营销号趋势失败：'+str(e),file=sys.stderr);raise SystemExit(1)
