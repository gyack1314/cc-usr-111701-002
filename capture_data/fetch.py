"""从 IIIF Presentation 3 清单走到画布、注释页与资源，留下可回放的版本闭包。

相对地址按各自来源文档的最终地址解释，重定向逐跳留证，分页沿 next 续抓；
令牌只发往登记过的来源边界，刷新令牌后必须复核清单版本，不得跨版本拼接。
"""
import base64, hashlib, json
from urllib.parse import urljoin, urlsplit
from . import journal

REDIRECTS = {301, 302, 303, 307, 308}
GONE = {404, 410}
DENIED = {401, 403}
MAX_HOPS = 8

class FetchError(ValueError):
    """采集无法继续。"""

class VersionDrift(FetchError):
    """刷新令牌后清单版本已变，拒绝悄悄拼出跨版本资料。"""

def _header(headers, name):
    return headers.get(name) or headers.get(name.lower()) or ''

def origin_of(url):
    u = urlsplit(url)
    if u.scheme not in ('http', 'https') or not u.netloc: raise FetchError('地址非法:' + url)
    return u.scheme + '://' + u.netloc

def version_of(res):
    """清单版本身份：优先 ETag，否则取正文字节摘要。"""
    etag = _header(res['headers'], 'ETag').strip()
    if etag: return 'etag:' + etag.removeprefix('W/').strip('"')
    return 'sha256:' + hashlib.sha256(res['body']).hexdigest()

def gap_reason(status):
    if status in GONE: return 'withdrawn:%d' % status
    if status in DENIED: return 'unauthorized:%d' % status
    return 'unavailable:%d' % status

class TokenStore:
    """按来源边界保存令牌；任何请求都不得携带越界令牌。"""
    def __init__(self): self._scopes = {}
    def register(self, label, token, origins):
        if not label or not token or not origins: raise ValueError('令牌登记非法')
        self._scopes[label] = {'token': token, 'origins': frozenset(origins), 'generation': 0}
    def refresh(self, label, token):
        if label not in self._scopes: raise FetchError('未知令牌范围:' + label)
        scope = self._scopes[label]; scope['token'] = token; scope['generation'] += 1
        return scope['generation']
    def for_url(self, url):
        origin = origin_of(url)
        for label, scope in self._scopes.items():
            if origin in scope['origins']:
                return {'label': label, 'token': scope['token'], 'generation': scope['generation']}
        return None

def body_bytes(row):
    if 'body_base64' in row: return base64.b64decode(row['body_base64'])
    if 'body_text' in row: return row['body_text'].encode('utf-8')
    body = row.get('body', {})
    if isinstance(body, bytes): return body
    return json.dumps(body, ensure_ascii=False, sort_keys=True).encode('utf-8')

class ScriptedClient:
    """冻结会话客户端：按地址返回预定响应并记录每次请求，不访问真实站点。"""
    def __init__(self, session, strict=False):
        self.session = session; self.strict = strict; self.requests = []
    def request(self, method, url, headers=None):
        self.requests.append({'method': method, 'url': url, 'headers': dict(headers or {})})
        row = self.session.get(url)
        if row is None:
            if self.strict: raise FetchError('回放外请求:' + url)
            return {'status': 404, 'headers': {}, 'body': b''}
        return {'status': row['status'], 'headers': dict(row.get('headers', {})), 'body': body_bytes(row)}

class Walker:
    """沿清单-画布-注释页-资源步行采集，逐项封存证据。"""
    def __init__(self, client, tokens, db, collection, refresher=None):
        self.client = client; self.tokens = tokens; self.db = db
        self.collection = collection; self.refresher = refresher
        self.manifest_url = None; self.manifest_version = None; self._revalidating = False

    def _once(self, url):
        grant = self.tokens.for_url(url) if self.tokens else None
        headers = {'Authorization': 'Bearer ' + grant['token']} if grant else {}
        return self.client.request('GET', url, headers), (grant['label'] if grant else '')

    def _fetch(self, url):
        """跟随重定向并逐跳留证；401 时先刷新令牌、复核清单版本，再原址重试一次。"""
        chain = []; current = url; scope = ''; retried = False
        while True:
            resp, scope = self._once(current)
            status = resp['status']
            location = _header(resp['headers'], 'Location')
            if status in REDIRECTS and location:
                chain.append({'url': current, 'status': status, 'location': location, 'auth_scope': scope})
                if len(chain) >= MAX_HOPS: raise FetchError('重定向过多:' + url)
                current = urljoin(current, location); continue
            if status == 401 and not retried and scope and self.refresher and not self._revalidating:
                retried = True
                chain.append({'url': current, 'status': status, 'auth_scope': scope})
                try: self.refresher(self.tokens, scope)
                except FetchError: break
                self._revalidate()
                continue
            break
        digest = hashlib.sha256(resp['body']).hexdigest()
        evidence_id = hashlib.sha256(json.dumps([url, current, status, digest], sort_keys=True).encode('utf-8')).hexdigest()
        journal.put_evidence(self.db, self.collection, {'evidence_id': evidence_id, 'url': url,
            'final_url': current, 'chain': chain, 'status': status, 'headers': resp['headers'],
            'body': resp['body'], 'body_sha256': digest, 'auth_scope': scope})
        return {'ok': status == 200, 'status': status, 'url': url, 'final_url': current,
                'headers': resp['headers'], 'body': resp['body'], 'body_sha256': digest,
                'auth_scope': scope, 'evidence_id': evidence_id}

    def _revalidate(self):
        """刷新令牌后复核清单版本；版本漂移即中止，不得跨版本拼接。"""
        if self._revalidating or not self.manifest_url: return
        self._revalidating = True
        try: res = self._fetch(self.manifest_url)
        finally: self._revalidating = False
        if not res['ok']: raise FetchError('清单复核失败:%d' % res['status'])
        other = version_of(res)
        if other != self.manifest_version:
            raise VersionDrift('清单版本漂移:%s -> %s，拒绝跨版本拼接' % (self.manifest_version, other))

    def _item(self, kind, item_id, res, refs, fragment=None, payload=None):
        if res is not None:
            digest, evidence_id, final_url, scope = res['body_sha256'], res['evidence_id'], res['final_url'], res['auth_scope']
        else:
            raw = json.dumps(fragment, ensure_ascii=False, sort_keys=True).encode('utf-8')
            digest, evidence_id, final_url, scope = hashlib.sha256(raw).hexdigest(), '', '', ''
        journal.put_item(self.db, self.collection, {'kind': kind, 'id': item_id, 'version': self.manifest_version,
            'status': 'captured', 'gap': '', 'digest': digest, 'final_url': final_url,
            'auth_scope': scope, 'evidence_id': evidence_id, 'refs': refs, 'payload': payload or {}})

    def _gap(self, kind, item_id, res, gaps, reason, refs=None, payload=None):
        gaps.append({'kind': kind, 'id': item_id, 'reason': reason})
        journal.put_item(self.db, self.collection, {'kind': kind, 'id': item_id, 'version': self.manifest_version,
            'status': 'gap', 'gap': reason, 'digest': '', 'final_url': res['final_url'],
            'auth_scope': res['auth_scope'], 'evidence_id': res['evidence_id'], 'refs': refs or [], 'payload': payload or {}})

    def _materialize(self, ref, base, expect, kind, gaps, refs):
        """含 items 的视为内嵌；否则按自身地址抓取，其内部相对地址以最终地址为基准。"""
        if isinstance(ref, str): ref = {'id': ref}
        if not isinstance(ref, dict) or not ref.get('id'): raise FetchError(kind + ' 引用非法')
        rid = urljoin(base, ref['id'])
        if 'items' in ref:
            if ref.get('type') != expect: raise FetchError(kind + ' 类型非法:' + rid)
            return ref, base, None
        res = self._fetch(rid)
        if not res['ok']:
            self._gap(kind, rid, res, gaps, gap_reason(res['status']), refs=refs)
            return None, base, res
        try: doc = json.loads(res['body'].decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError): raise FetchError(kind + ' 不是合法JSON:' + rid)
        if doc.get('type') != expect: raise FetchError(kind + ' 类型非法:' + rid)
        return doc, res['final_url'], res

    def _doc_id(self, doc, base, res, kind):
        if doc.get('id'): return urljoin(base, doc['id'])
        if res is not None: return res['final_url']
        raise FetchError(kind + ' 身份非法')

    def walk(self, manifest_url):
        """从清单走完全部画布、注释页与资源，返回闭包摘要。"""
        self.manifest_url = manifest_url
        mres = self._fetch(manifest_url)
        if not mres['ok']: raise FetchError('清单不可用:%d %s' % (mres['status'], manifest_url))
        try: doc = json.loads(mres['body'].decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError): raise FetchError('清单不是合法JSON')
        if doc.get('type') != 'Manifest': raise FetchError('不是 Manifest')
        self.manifest_version = version_of(mres)
        base = mres['final_url']
        mid = urljoin(base, doc.get('id') or manifest_url)
        gaps = []
        self._item('manifest', mid, mres, [], payload={'source_url': manifest_url})
        for c_ref in doc.get('items') or []:
            self._walk_canvas(c_ref, base, mid, gaps)
        complete = not gaps
        journal.put_closure(self.db, self.collection, manifest_url, self.manifest_version, complete, gaps)
        return {'collection': self.collection, 'manifest_url': manifest_url,
                'manifest_version': self.manifest_version, 'complete': complete, 'gaps': gaps}

    def _walk_canvas(self, c_ref, base, mid, gaps):
        canvas, cbase, cres = self._materialize(c_ref, base, 'Canvas', 'canvas', gaps, [mid])
        if canvas is None: return
        cid = self._doc_id(canvas, cbase, cres, 'canvas')
        self._item('canvas', cid, cres, [mid], fragment=canvas)
        for p_ref in canvas.get('items') or []:
            self._walk_pages(p_ref, cbase, cid, gaps)

    def _walk_pages(self, p_ref, base, cid, gaps):
        seen = set(); ref = p_ref
        while ref is not None:
            page, pbase, pres = self._materialize(ref, base, 'AnnotationPage', 'page', gaps, [cid])
            if page is None: return
            pid = self._doc_id(page, pbase, pres, 'page')
            if pid in seen: raise FetchError('分页循环:' + pid)
            seen.add(pid)
            self._item('page', pid, pres, [cid], fragment=page)
            for a in page.get('items') or []:
                self._walk_annotation(a, pbase, pid, gaps)
            nxt = page.get('next')
            if isinstance(nxt, dict) and nxt.get('id'): ref, base = nxt, pbase
            elif isinstance(nxt, str) and nxt: ref, base = {'id': nxt}, pbase
            else: ref = None

    def _walk_annotation(self, a, pbase, pid, gaps):
        if not isinstance(a, dict) or not a.get('id'): raise FetchError('注释身份非法')
        if a.get('type', 'Annotation') != 'Annotation': raise FetchError('注释类型非法')
        aid = urljoin(pbase, a['id'])
        payload = {'target': a.get('target'), 'motivation': a.get('motivation')}
        self._item('annotation', aid, None, [pid], fragment=a, payload=payload)
        bodies = a.get('body')
        if bodies is None: return
        if isinstance(bodies, dict): bodies = [bodies]
        for b in bodies:
            self._walk_body(aid, b, pbase, gaps)

    def _walk_body(self, aid, b, pbase, gaps):
        if not isinstance(b, dict): raise FetchError('资源实体非法')
        if not b.get('id') and isinstance(b.get('source'), dict): b = {**b, 'id': b['source'].get('id')}
        payload = {'format': b.get('format'), 'type': b.get('type')}
        if b.get('id'):
            bid = urljoin(pbase, b['id'])
            res = self._fetch(bid)
            if res['ok']: self._item('resource', bid, res, [aid], payload=payload)
            else: self._gap('resource', bid, res, gaps, gap_reason(res['status']), refs=[aid], payload=payload)
        elif b.get('value') is not None:
            digest = hashlib.sha256(str(b['value']).encode('utf-8')).hexdigest()
            journal.put_item(self.db, self.collection, {'kind': 'resource', 'id': aid + '#body',
                'version': self.manifest_version, 'status': 'captured', 'gap': '', 'digest': digest,
                'final_url': '', 'auth_scope': '', 'evidence_id': '', 'refs': [aid],
                'payload': {**payload, 'inline': True}})
        else: raise FetchError('资源实体缺少身份')

def replay_client(db, collection):
    """由账本证据重建冻结会话；任何未记录的请求都会暴露走样。"""
    session = {}
    for url, final_url, chain, status, headers, body in db.execute(
            'SELECT url,final_url,chain,status,headers,body FROM evidence WHERE collection=?', (collection,)):
        for hop in json.loads(chain):
            session[hop['url']] = {'status': hop['status'],
                                   'headers': {'Location': hop['location']} if hop.get('location') else {}, 'body': b''}
        session[final_url] = {'status': status, 'headers': json.loads(headers), 'body': body}
    return ScriptedClient(session, strict=True)
