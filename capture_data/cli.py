"""查看已接收资料的身份与本地引用，或沿清单采集可回放的版本闭包。"""
import argparse,json
from pathlib import Path
from .io import load
from .domain import summarize,PROTOCOL
from .journal import connect,receive
from .projection import overview,closure_view
from .fetch import TokenStore,ScriptedClient,Walker,FetchError

def walk_command(plan,journal_path):
    tokens=TokenStore()
    for t in plan.get('tokens',[]):tokens.register(t['label'],t['token'],t['origins'])
    client=ScriptedClient(plan.get('session',{}))
    refresh_plan=plan.get('refresh',{})
    def refresher(store,label):
        spec=refresh_plan.get(label)
        if not spec:raise FetchError('无刷新令牌:'+label)
        store.refresh(label,spec['token']);client.session.update(spec.get('session_updates',{}))
    db=connect(journal_path)
    summary=Walker(client,tokens,db,plan['collection'],refresher=refresher if refresh_plan else None).walk(plan['manifest_url'])
    view=closure_view(db,plan['collection']);db.close()
    return {'walk':summary,'verify':view['verify'],'business':view['business']}

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['inspect','walk','closure']);p.add_argument('file');p.add_argument('extra',nargs='?');a=p.parse_args()
    path=Path(a.file)
    if a.command=='inspect':
        v,digest=load(path);summary=summarize(v,path.parent)
        c=connect(':memory:');source=receive(c,v,path.parent)
        print(json.dumps({'manifest_sha256':digest,'source_sha256':source,'summary':summary,'overview':overview(c,v['collection'])},ensure_ascii=False,indent=2))
    elif a.command=='walk':
        plan,_=load(path)
        if plan.get('kind')!='walk-plan' or plan.get('protocol')!=PROTOCOL:raise SystemExit('计划协议不匹配')
        print(json.dumps(walk_command(plan,a.extra or ':memory:'),ensure_ascii=False,indent=2))
    else:
        if not a.extra:raise SystemExit('需要集合名')
        c=connect(path);print(json.dumps(closure_view(c,a.extra),ensure_ascii=False,indent=2));c.close()
if __name__=='__main__':main()
