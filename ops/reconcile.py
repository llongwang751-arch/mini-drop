"""在固定批次内对账 S3 与 SQL，不把凭据或 profile 内容写入日志。

删除前再次检查数据库保护范围，避免将仍在上传、持有 RAW 授权或活跃作业前缀下的对象误判为孤儿。
"""
from datetime import datetime, timedelta, timezone
import hashlib

PREFIXES = ('raw/', 'intermediate/', 'result/', 'results/', 'logs/', 'manifest/', 'binary/')


def reconcile(repo, storage, *, now=None, limit=100, hash_budget=64 * 1024 * 1024, orphan_grace=timedelta(days=1), multipart_grace=timedelta(days=1)):
    """执行一轮有限批次的过期清理、摘要检查、孤儿删除及旧 multipart 终止，返回计数。

    SQL 与 S3 不能同事务提交：删除先记 deleting，实际删除并复核后才记 expired，失败可在后续重试。
    孤儿扫描使用持久游标，并在删除前再查保护范围；单个对象失败只计入 errors，不吞掉整轮可恢复工作。
    """
    now = now or datetime.now(timezone.utc)
    stats = dict(expired=0, missing=0, quarantined=0, checked=0, orphans=0, multipart_aborted=0, errors=0)
    for item in repo.expired(limit):
        try:
            repo.set_status(item, 'deleting')
            storage.remove(item['object_key'])
            if storage.stat(item['object_key']) is not None:
                raise RuntimeError('Deletion verification failed')
            repo.set_status(item, 'expired')
            repo.audit('ARTIFACT_EXPIRED', str(item['id']))
            stats['expired'] += 1
        except Exception:
            stats['errors'] += 1
    for item in repo.candidates(limit):
        try:
            key = item['object_key']; actual_size = storage.stat(key)
            if actual_size is None:
                repo.set_status(item, 'missing'); repo.audit('ARTIFACT_MISSING', str(item['id'])); stats['missing'] += 1
                continue
            corrupt = actual_size != item['size']
            if not corrupt and actual_size <= hash_budget:
                digest = hashlib.sha256(); measured = 0
                for chunk in storage.chunks(key):
                    measured += len(chunk)
                    if measured > actual_size or measured > hash_budget:
                        corrupt = True; break
                    digest.update(chunk)
                corrupt = corrupt or measured != actual_size or digest.hexdigest() != item['sha256']
                hash_budget -= min(measured, hash_budget)
                if hasattr(repo, 'mark_checked'): repo.mark_checked(item)
            if corrupt:
                repo.set_status(item, 'quarantined'); repo.audit('ARTIFACT_INTEGRITY_FAILURE', str(item['id'])); stats['quarantined'] += 1
            stats['checked'] += 1
        except Exception:
            stats['errors'] += 1
    # 对象名只作为存储标识符，不能解析成宿主文件路径。
    cursor = repo.object_cursor() if hasattr(repo,'object_cursor') else ''
    listing = storage.list(cursor) if hasattr(repo,'object_cursor') else storage.list()
    last_key = cursor
    for index, (key, modified) in enumerate(listing):
        if index >= 10000 or stats['orphans'] >= limit: break
        last_key = key
        if not key.startswith(PREFIXES) or not modified or modified > now - orphan_grace: continue
        try:
            # 保护范围包括已签发 RAW 授权与所有活跃作业前缀，必须紧邻删除前再次查询。
            if not repo.referenced(key):
                storage.remove(key)
                repo.audit('ORPHAN_OBJECT_DELETED', hashlib.sha256(key.encode()).hexdigest())
                stats['orphans'] += 1
        except Exception:
            stats['errors'] += 1
    else:
        last_key = ''
    if hasattr(repo,'save_object_cursor'): repo.save_object_cursor(last_key)
    if hasattr(storage,'multipart'):
        for index,(key,upload_id,initiated) in enumerate(storage.multipart()):
            if index >= 10000 or stats['multipart_aborted'] >= limit: break
            if not key.startswith(PREFIXES) or not initiated or initiated > now-multipart_grace: continue
            try:
                storage.abort(key,upload_id)
                repo.audit('ABANDONED_MULTIPART_ABORTED',hashlib.sha256((key+upload_id).encode()).hexdigest())
                stats['multipart_aborted'] += 1
            except Exception: stats['errors'] += 1
    return stats
