"""固定的集成契约回归，模型生成的补丁不能修改此文件。"""
from crm.services.brand_palette import derive, ramp


def test_python_collection_types_and_mutable_isolation():
    first = derive('#123456')
    assert type(first) is dict
    assert type(first['ramp']) is list
    assert type(first['warnings']) is list
    first['ramp'][0] = 'mutated-by-another-caller'
    first['warnings'].append('private-caller-value')
    second = derive('#123456')
    assert second['ramp'][0] != 'mutated-by-another-caller'
    assert 'private-caller-value' not in second['warnings']


def test_public_ramp_contract_is_a_fresh_list():
    first = ramp('#123456')
    assert type(first) is list
    first[0] = 'mutated-by-another-caller'
    second = ramp('#123456')
    assert type(second) is list
    assert second[0] != 'mutated-by-another-caller'


def test_preview_limit_and_identity_remain_enforced():
    from fastapi.testclient import TestClient
    from crm.main import create_app
    from crm.config import Settings
    from crm.auth import Identity
    from crm.seed import DEMO_USER, DEMO_ORG, create_demo_repository
    # 只使用合成内存数据；重复缓存色值，降低该回归本身的资源成本。
    identity = Identity(DEMO_USER, DEMO_ORG, 'viewer')
    app = create_app(Settings(environment='test', repository_backend='memory'), create_demo_repository(), lambda _: identity)
    with TestClient(app) as client:
        codes = [client.post('/api/v1/ui/branding/preview', json={'accent_hex': '#123456'}).status_code for _ in range(61)]
        assert codes[:60] == [200] * 60
        assert codes[60] == 429
