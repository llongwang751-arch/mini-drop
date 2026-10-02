"""Server-owned, read-only measurement report. Never accepts a browser path."""
import json
import os
from pathlib import Path
from .business_acceptance import canonical_hash


def get_business_acceptance():
    path=Path(os.getenv('MINI_DROP_BUSINESS_ACCEPTANCE_VIEW',
        '/workspace-source/reports/business-acceptance/latest-view.json'))
    try:
        if path.stat().st_size>1_000_000:raise ValueError('oversized report')
        value=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value,dict) or value.get('schema')!='mini-drop.business-view.v1':raise ValueError('invalid schema')
        if value.get('status')!='AVAILABLE' or not isinstance(value.get('cases'),list):raise ValueError('invalid cases')
        if canonical_hash({k:v for k,v in value.items() if k!='report_sha256'})!=value.get('report_sha256'):raise ValueError('report hash mismatch')
        for row in value['cases']:
            if row.get('ai_root_cause_verified') is not False:raise ValueError('local measurements cannot certify AI roots')
            if row['comparison']['outcome'] not in ('IMPROVEMENT_VERIFIED','DEGRADED_AVAILABLE','REJECTED','INCOMPARABLE'):raise ValueError('invalid outcome')
        return value
    except FileNotFoundError:
        return {'status':'NOT_RUN','cases':[],'message':'尚未运行业务验收'}
    except (OSError,ValueError,TypeError,KeyError):
        return {'status':'INVALID','cases':[],'message':'业务验收报告损坏或格式不符，不能确认结果'}
