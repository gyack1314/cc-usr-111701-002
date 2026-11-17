import json
def decode(raw, manifest):
    doc=json.loads(raw.decode('utf-8'))
    if doc.get('type')!='Manifest':raise ValueError('不是 Manifest')
    records=[{'kind':'manifest','id':doc['id'],'version':'received','refs':[],
              'payload':{'source_url':manifest['source_url']}}]
    for canvas in doc['items']:
        if canvas.get('type')!='Canvas':raise ValueError('画布类型非法')
        records.append({'kind':'canvas','id':canvas['id'],'version':'received','refs':[doc['id']],
                        'payload':{'pages':[x['id'] for x in canvas.get('items',[])]}})
        for page in canvas.get('items',[]):
            if page.get('type')!='AnnotationPage':raise ValueError('注释页类型非法')
            records.append({'kind':'annotation','id':page['id'],'version':'received','refs':[canvas['id']],
                            'payload':{'items':[x['id'] for x in page.get('items',[])]}})
            for ann in page.get('items',[]):
                records.append({'kind':'resource','id':ann['id'],'version':'received','refs':[page['id']],
                                'payload':{'target':ann.get('target'),'body':ann.get('body'),
                                'motivation':ann.get('motivation')}})
    return {'protocol':manifest['protocol'],'collection':manifest['collection'],'records':records}
