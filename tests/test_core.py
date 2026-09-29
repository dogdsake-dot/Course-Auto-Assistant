import json
from pathlib import Path

import pytest

from course_auto_assistant.core import (CENTER, InstanceLock, Listing, NeedsUser,
    SiteConfig, allowed_url, load_json, public_text, public_url, save_json, starter_config)


def listing():
    return Listing('#list', '.card', 'a', '.status', ('已完成', '100%'),
                   ('未完成', '0%', '50%'), 'none', '', '')


def test_defaults_block_automatic_actions():
    cfg = SiteConfig.parse(starter_config())
    with pytest.raises(NeedsUser, match='尚未核实'):
        cfg.validate_run()


@pytest.mark.parametrize('text, expected', [('未完成', 'pending'), ('已完成', 'complete'),
    (' 100% ', 'complete'), ('未完成100%', 'unknown'), ('', 'unknown'),
    ('10%', 'unknown'), ('已完成 还有考试', 'unknown')])
def test_status_exact_match(text, expected):
    assert listing().classify(text) == expected


@pytest.mark.parametrize('url', ['https://evil.example/x', 'http://qy.yingsheng.com/x',
    'https://qy.yingsheng.com.evil.example/x', 'javascript:alert(1)', '#', '',
    'https://u:p@qy.yingsheng.com/x', 'https://qy.yingsheng.com:444/x'])
def test_navigation_rejects_unconfirmed_targets(url):
    with pytest.raises(NeedsUser):
        allowed_url(url)


def test_relative_url_and_query_preserved_for_local_navigation_only():
    assert allowed_url('/course/a?id=123#x') == 'https://qy.yingsheng.com/course/a?id=123'
    assert public_url('https://u:p@qy.yingsheng.com/a?token=secret#x') == 'https://qy.yingsheng.com/a'
    assert public_url('data:text/html,secret') == 'data:[省略]'


def test_personal_text_redaction():
    value = public_text('test@example.com 13812345678')
    assert 'test@' not in value and '13812345678' not in value


def test_atomic_storage_and_unicode(tmp_path):
    path = tmp_path / 'config.json'
    save_json(path, {'标题': '中文'})
    assert load_json(path) == {'标题': '中文'}
    with pytest.raises(ValueError):
        save_json(path, {'bad': float('nan')})
    assert load_json(path) == {'标题': '中文'}
    assert not list(tmp_path.glob('.caa-*'))


def test_two_instances_cannot_share_profile(tmp_path):
    one, two = InstanceLock(tmp_path), InstanceLock(tmp_path)
    one.acquire()
    try:
        with pytest.raises(NeedsUser):
            two.acquire()
    finally:
        one.close()
    two.acquire()
    two.close()


def test_cannot_enable_empty_adapter_by_changing_flag_only():
    raw = starter_config()
    raw['verified'], raw['evidence'] = True, '用户核实'
    with pytest.raises(NeedsUser):
        SiteConfig.parse(raw).validate_run()


def test_wrong_tenant_rejected():
    raw = starter_config()
    raw['course_center_url'] = CENTER.replace('26058', '1')
    with pytest.raises(ValueError):
        SiteConfig.parse(raw)


def test_pagination_must_be_explicit():
    raw = starter_config()['courses']
    raw.update(ready='#list', items='.card', link='a', status='.s', complete=['完成'], pending=['未完成'])
    with pytest.raises(NeedsUser, match='翻页'):
        Listing.parse(raw).validate()


def test_overlapping_status_rejected():
    raw = starter_config()['courses']
    raw.update(ready='#list', items='.card', link='a', status='.s', complete=['完成'], pending=['完成'], pagination='none')
    with pytest.raises(ValueError):
        Listing.parse(raw).validate()
