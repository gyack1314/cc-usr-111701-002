"""区分画布的引用闭包和资源字节闭包。"""
def assess(rows):
    resources=[r for r in rows if r['kind']=='resource']
    absent=[r['payload']['body']['id'] for r in resources if r['payload'].get('body',{}).get('id') and not r['payload'].get('body_verified')]
    return {'publishable':not absent,'reasons':['图片字节尚未核验:'+url for url in absent],
            'resource_count':len(resources)}
