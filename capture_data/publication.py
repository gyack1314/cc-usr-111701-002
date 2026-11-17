"""区分画布的引用闭包和资源字节闭包。"""
def assess(rows):
    resources=[r for r in rows if r['kind']=='resource']
    absent=[r['payload']['body']['id'] for r in resources if r['payload'].get('body',{}).get('id') and not r['payload'].get('body_verified')]
    return {'publishable':not absent,'reasons':['图片字节尚未核验:'+url for url in absent],
            'resource_count':len(resources)}

def assess_closure(closures,items):
    """只有最新闭包完整且逐项无缺口才可发布；缺口逐项列明，不得宣称整件封存。"""
    if not closures:return {'publishable':False,'sealed':False,'reasons':['尚未形成版本闭包'],'resource_count':0}
    latest=closures[-1];version=latest['manifest_version']
    mine=[i for i in items if i['manifest_version']==version]
    gaps=[i for i in mine if i['status']=='gap']
    reasons=['缺口:%s %s %s'%(g['kind'],g['id'],g['gap']) for g in gaps]
    if not latest['complete'] and not reasons:reasons=['闭包未完成']
    ok=bool(latest['complete']) and not gaps
    return {'publishable':ok,'sealed':ok,'manifest_version':version,'reasons':reasons,
            'resource_count':len([i for i in mine if i['kind']=='resource'])}
