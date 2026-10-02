"""The replacement demo must have real negative evidence, never manufactured green scores."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import pytest
from scripts import build_engineering_case_index as catalog
from scripts.reproduce_parallel_timing_defect import reproduce


def test_real_catalog_grades_engineering_defects_separately_from_ai_roots():
    result = catalog.generate(check=True)
    assert len(result['cases']) == 4
    assert result['model_auto_root_cause'] == 'NOT_EVALUATED'
    assert result['legacy_fault_catalog'] == {'count':21, 'preserved':True, 'affects_this_score':False}
    assert [case['before']['failed'] for case in result['cases']] == [1,2,1,1]
    assert [case['after']['passed'] for case in result['cases']] == [1,9,1,1]


def test_exact_frozen_code_reproduces_parallel_overlap_defect():
    root = catalog.ROOT/'reports/quality/engineering-cases-20261001'
    result = reproduce(root/'timing-before.py', root/'timing-after.py')
    assert result['before']['actual_retrieval_ms'] == 0
    assert result['after']['actual_retrieval_ms'] == result['expected_retrieval_ms'] == 2000


@pytest.mark.parametrize('body,count', [('',1), ('<testcase classname="x" name="a"/>',2),
    ('<testcase classname="x" name="a"/><testcase classname="x" name="a"/>',2)])
def test_missing_and_duplicate_tests_are_rejected(body,count):
    with pytest.raises(ValueError, match='missing or duplicate'):
        catalog.junit(('<testsuites>'+body+'</testsuites>').encode(), {'classname':'x'}, count)


def isolated_case(tmp_path):
    old = b'<testsuites><testcase classname="x" name="a"><failure/></testcase></testsuites>'
    new = b'<testsuites><testcase classname="x" name="a"/></testsuites>'
    example = deepcopy(json.loads(catalog.PLAN.read_text(encoding='utf-8'))['cases'][2])
    example['selector'] = {'classname':'x'}
    example['evidence'] = {}
    for role, raw in [('before',old),('after',new)]:
        path = tmp_path/(role+'.xml')
        path.write_bytes(raw)
        example['evidence'][role] = {'path':path.name,'sha256':hashlib.sha256(raw).hexdigest()}
    return example


def test_hash_tampering_is_rejected(tmp_path):
    case = isolated_case(tmp_path)
    (tmp_path/'after.xml').write_text('<testsuites/>')
    with pytest.raises(ValueError,match='hash mismatch'):
        catalog.evaluate_case(case,tmp_path)


@pytest.mark.parametrize('element',['<skipped/>','<failure/>','<error/>'])
def test_after_skips_and_failures_cannot_be_displayed_as_fixed(tmp_path,element):
    case = isolated_case(tmp_path)
    raw = f'<testsuites><testcase classname="x" name="a">{element}</testcase></testsuites>'.encode()
    (tmp_path/'after.xml').write_bytes(raw)
    case['evidence']['after']['sha256'] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError,match='fully regressed'):
        catalog.evaluate_case(case,tmp_path)


def test_generated_index_and_downloads_must_match_pinned_evidence(tmp_path):
    output = tmp_path/'index.json'
    catalog.generate(output=output)
    catalog.generate(output=output,check=True)
    artifact = next(path for path in tmp_path.rglob('*.xml'))
    artifact.write_bytes(b'<testsuites/>')
    with pytest.raises(ValueError,match='artifact drift'):
        catalog.generate(output=output,check=True)
    catalog.generate(output=output)
    output.write_text('{}')
    with pytest.raises(ValueError,match='index drift'):
        catalog.generate(output=output,check=True)


def test_traversal_cannot_read_outside_evidence_root(tmp_path):
    with pytest.raises(ValueError,match='outside workspace'):
        catalog.evidence(tmp_path, {'path':'../outside','sha256':'0'*64})
