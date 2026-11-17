"""返回本地已确认记录与引用缺口。"""
import json
from .publication import assess,assess_closure
def overview(c,collection):
 rows=c.execute('SELECT kind,record_id,version,source_digest,refs,payload FROM records WHERE collection=? ORDER BY kind,record_id,version',(collection,)).fetchall()
 ids={r[1] for r in rows};missing=sorted({ref for r in rows for ref in json.loads(r[4]) if ref not in ids})
 business_rows=[{'kind':r[0],'id':r[1],'version':r[2],'refs':json.loads(r[4]),'payload':json.loads(r[5])} for r in rows]
 business=assess(business_rows) if any('position' not in r['payload'] for r in business_rows) else {'publishable':False,'reasons':['通用来源尚无协议核验']}
 return {'business':business,'collection':collection,'records':[{'kind':r[0],'id':r[1],'version':r[2],'source_digest':r[3]} for r in rows],'missing_refs':missing,'locally_closed':not missing}

def closure_view(c,collection):
 """逐项给出原始响应、最终地址、授权范围、内容摘要与清单版本，并核查重复与串版。"""
 ev={}
 for e in c.execute('SELECT evidence_id,url,final_url,status,headers,body_sha256,auth_scope,chain FROM evidence WHERE collection=?',(collection,)):
  ev[e[0]]={'url':e[1],'final_url':e[2],'status':e[3],'headers':json.loads(e[4]),'body_sha256':e[5],'auth_scope':e[6],'chain':json.loads(e[7])}
 items=[]
 for r in c.execute('SELECT kind,item_id,manifest_version,status,gap,digest,final_url,auth_scope,evidence_id,refs,payload FROM items WHERE collection=? ORDER BY kind,item_id',(collection,)):
  items.append({'kind':r[0],'id':r[1],'manifest_version':r[2],'status':r[3],'gap':r[4],'digest':r[5],
                'final_url':r[6],'auth_scope':r[7],'refs':json.loads(r[9]),'payload':json.loads(r[10]),'evidence':ev.get(r[8])})
 closures=[{'manifest_url':r[0],'manifest_version':r[1],'complete':bool(r[2]),'gaps':json.loads(r[3])}
           for r in c.execute('SELECT manifest_url,manifest_version,complete,gaps FROM closures WHERE collection=? ORDER BY rowid',(collection,))]
 counts={}
 for it in items:counts[(it['kind'],it['id'],it['manifest_version'])]=counts.get((it['kind'],it['id'],it['manifest_version']),0)+1
 duplicates=sorted('%s/%s@%s'%k for k,n in counts.items() if n>1)
 versions={}
 for it in items:versions.setdefault((it['kind'],it['id']),set()).add(it['manifest_version'])
 mixing=sorted('%s/%s'%k for k,v in versions.items() if len(v)>1)
 ids={it['id'] for it in items}
 missing=sorted({ref for it in items for ref in it['refs'] if ref not in ids})
 return {'collection':collection,'closures':closures,'items':items,'missing_refs':missing,
         'verify':{'duplicates':duplicates,'version_mixing':mixing},'business':assess_closure(closures,items)}
