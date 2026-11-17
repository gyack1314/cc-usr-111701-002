"""封存已确认的来源和记录版本，不替外部协议判断完整性。"""
import hashlib,json,sqlite3
from .io import asset
from .adapter import decode
SCHEMA="CREATE TABLE IF NOT EXISTS sources(collection TEXT,digest TEXT,raw BLOB,PRIMARY KEY(collection,digest));CREATE TABLE IF NOT EXISTS records(collection TEXT,kind TEXT,record_id TEXT,version TEXT,source_digest TEXT,refs TEXT,payload TEXT,PRIMARY KEY(collection,kind,record_id,version),FOREIGN KEY(collection,source_digest) REFERENCES sources(collection,digest));CREATE TABLE IF NOT EXISTS evidence(collection TEXT,evidence_id TEXT,url TEXT,final_url TEXT,chain TEXT,status INTEGER,headers TEXT,body BLOB,body_sha256 TEXT,auth_scope TEXT,PRIMARY KEY(collection,evidence_id));CREATE TABLE IF NOT EXISTS items(collection TEXT,kind TEXT,item_id TEXT,manifest_version TEXT,status TEXT,gap TEXT,digest TEXT,final_url TEXT,auth_scope TEXT,evidence_id TEXT,refs TEXT,payload TEXT,PRIMARY KEY(collection,kind,item_id,manifest_version));CREATE TABLE IF NOT EXISTS closures(collection TEXT,manifest_url TEXT,manifest_version TEXT,complete INTEGER,gaps TEXT,PRIMARY KEY(collection,manifest_version));"
def connect(path):
 c=sqlite3.connect(path);c.execute('PRAGMA foreign_keys=ON');c.executescript(SCHEMA);return c
def receive(c,manifest,root):
 raw=asset(root,manifest['assets'][0]);digest=hashlib.sha256(raw).hexdigest();doc=decode(raw,manifest) if manifest['assets'][0]['path'] in ('protocol-source.bin','conflict-source.bin') else json.loads(raw.decode('utf-8'))
 if doc.get('collection')!=manifest['collection'] or doc.get('protocol')!=manifest['protocol']:raise ValueError('来源身份不匹配')
 with c:
  c.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?)',(doc['collection'],digest,raw))
  for row in doc['records']:
   refs=json.dumps(row.get('refs',[]),ensure_ascii=False);payload=json.dumps(row.get('payload',{}),ensure_ascii=False,sort_keys=True)
   old=c.execute('SELECT source_digest,payload FROM records WHERE collection=? AND kind=? AND record_id=? AND version=?',(doc['collection'],row['kind'],row['id'],row['version'])).fetchone()
   if old and old!=(digest,payload):raise ValueError('版本冲突')
   c.execute('INSERT OR IGNORE INTO records VALUES(?,?,?,?,?,?,?)',(doc['collection'],row['kind'],row['id'],row['version'],digest,refs,payload))
 return digest

def put_evidence(c,collection,ev):
 """封存一次原始响应；同一证据身份不得对应不同字节。"""
 row=(collection,ev['evidence_id'],ev['url'],ev['final_url'],json.dumps(ev['chain'],ensure_ascii=False),ev['status'],json.dumps(ev['headers'],ensure_ascii=False,sort_keys=True),ev['body'],ev['body_sha256'],ev['auth_scope'])
 with c:
  old=c.execute('SELECT body_sha256 FROM evidence WHERE collection=? AND evidence_id=?',(collection,ev['evidence_id'])).fetchone()
  if old and old[0]!=ev['body_sha256']:raise ValueError('证据冲突')
  c.execute('INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?)',row)

def put_item(c,collection,it):
 """把一项纳入某清单版本的闭包；同身份不同内容即版本冲突，重试幂等。"""
 refs=json.dumps(it['refs'],ensure_ascii=False);payload=json.dumps(it['payload'],ensure_ascii=False,sort_keys=True)
 key=(collection,it['kind'],it['id'],it['version'])
 with c:
  old=c.execute('SELECT status,gap,digest,final_url FROM items WHERE collection=? AND kind=? AND item_id=? AND manifest_version=?',key).fetchone()
  if old and (old[0],old[1],old[2],old[3])!=(it['status'],it['gap'],it['digest'],it['final_url']):raise ValueError('版本冲突:'+it['id'])
  c.execute('INSERT OR IGNORE INTO items VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',key+(it['status'],it['gap'],it['digest'],it['final_url'],it['auth_scope'],it['evidence_id'],refs,payload))

def put_closure(c,collection,url,version,complete,gaps):
 """登记一次版本闭包；同版本重试必须得到同一结论。"""
 gj=json.dumps(gaps,ensure_ascii=False,sort_keys=True)
 with c:
  old=c.execute('SELECT manifest_url,complete,gaps FROM closures WHERE collection=? AND manifest_version=?',(collection,version)).fetchone()
  if old and (old[0],old[1],old[2])!=(url,1 if complete else 0,gj):raise ValueError('闭包冲突')
  c.execute('INSERT OR IGNORE INTO closures VALUES(?,?,?,?,?)',(collection,url,version,1 if complete else 0,gj))
