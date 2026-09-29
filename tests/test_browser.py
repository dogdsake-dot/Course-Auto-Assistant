"""Synthetic pages only. Every browser request is intercepted; no live courses."""
import io
import os
import queue
import threading
import wave
from dataclasses import asdict

import pytest
from playwright.sync_api import sync_playwright

from course_auto_assistant.browser import BrowserWorker
from course_auto_assistant.core import CENTER, Cancelled, Listing, NeedsUser, save_json, starter_config
from course_auto_assistant.site import inspect_page, read_listing, require_no_attention, locate_video

pytestmark = pytest.mark.browser
BASE = 'http://127.0.0.1:8765'
CENTER = BASE + '/center'
SPEC = Listing('#list', '.card', 'a', '.status', ('已完成',), ('未完成',), 'none', '', '')


def card(status='未完成', href='/__fixture__/course'):
    return f'<section id="list"><div class="card"><a href="{href}">示例课程</a><span class="status">{status}</span></div></section>'



@pytest.fixture(autouse=True)
def fixture_site(monkeypatch):
    # Production host validation is tested separately. Browser fixtures use ONLY
    # loopback so these tests cannot hit an enterprise training server.
    from urllib.parse import urljoin, urlsplit
    import course_auto_assistant.core as core
    import course_auto_assistant.site as site
    import course_auto_assistant.browser as browser_module
    def local_only(value, base=CENTER):
        target = urljoin(base if base.startswith('http') else CENTER, value)
        if urlsplit(target).netloc != '127.0.0.1:8765' or value.startswith(('javascript:', '#')):
            raise NeedsUser('fixture URL rejected')
        return target
    monkeypatch.setattr(core, 'CENTER', CENTER)
    monkeypatch.setattr(site, 'allowed_url', local_only)
    monkeypatch.setattr(browser_module, 'allowed_url', local_only)

@pytest.fixture
def context():
    with sync_playwright() as pw:
        opts = {'headless': True, 'args': ['--autoplay-policy=no-user-gesture-required']}
        # This testing flag is never used by the production application.
        exe = os.environ.get('CAA_TEST_CHROMIUM')
        if exe:
            opts['executable_path'] = exe
        elif os.name == 'nt':
            opts['channel'] = 'msedge'
        browser = pw.chromium.launch(**opts)
        ctx = browser.new_context()
        ctx.route('**/*', lambda route: route.abort())
        yield ctx
        browser.close()


def page_with(context, html):
    page = context.new_page()
    page.set_content(html)
    return page


def test_list_and_noncomplete(context):
    page = page_with(context, card())
    rows = read_listing(page, SPEC)
    assert rows[0].status == 'pending'
    assert rows[0].url == BASE + '/__fixture__/course'


@pytest.mark.parametrize('html', [card('未知'), '<section id="list"></section>',
    card(href='javascript:go()'), card() + card(), '<input type="password">'])
def test_ambiguous_list_stops(context, html):
    page = page_with(context, html)
    with pytest.raises(NeedsUser):
        read_listing(page, SPEC)


def test_manual_dialog_blocks(context):
    page = page_with(context, '<div role="dialog">请确认本人正在学习</div>')
    with pytest.raises(NeedsUser):
        require_no_attention(page)


def test_abnormal_playback_blocks(context):
    page = page_with(context, '<h2>播放行为异常</h2>')
    with pytest.raises(NeedsUser):
        require_no_attention(page)


def test_iframe_video_discovery(context):
    page_with(context, '<iframe srcdoc="&lt;video controls&gt;&lt;/video&gt;"></iframe>')
    page, video = locate_video(context)
    assert video.evaluate('v => v.tagName') == 'VIDEO'
    context.new_page().set_content('<video controls></video>')
    with pytest.raises(NeedsUser):
        locate_video(context)


def test_diagnostic_excludes_secrets(context):
    page = page_with(context, '<input value="PASSWORD_SECRET"><a href="/x?token=PRIVATE_TOKEN">test@example.com</a>')
    report = str(inspect_page(page, 'center'))
    assert 'PASSWORD_SECRET' not in report
    assert 'PRIVATE_TOKEN' not in report
    assert 'test@example.com' not in report


def test_unverified_adapter_never_navigates(tmp_path):
    worker = BrowserWorker(tmp_path, queue.Queue())
    save_json(tmp_path / 'site-config.json', starter_config())
    with pytest.raises(NeedsUser, match='尚未核实'):
        worker.automatic_queue()
    assert worker.context is None


def fixture_cfg():
    raw = starter_config()
    raw.update(verified=True, evidence='SYNTHETIC TEST ONLY, not live-site verification')
    raw['courses'] = raw['lessons'] = asdict(SPEC)
    # JSON converts tuples to arrays.
    return raw


def test_offline_full_queue_and_platform_confirmation(context, tmp_path):
    """Real browser playback of a short local WAV, plus a synthetic completion label."""
    done = {'value': False}
    wav = io.BytesIO()
    with wave.open(wav, 'wb') as f:
        f.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        f.writeframes(b'\x00\x00' * 4000)
    seen = []
    def handler(route):
        url = route.request.url
        seen.append(url)
        if url.endswith('/audio.wav'):
            route.fulfill(body=wav.getvalue(), content_type='audio/wav')
        elif url.endswith('/__fixture__/done'):
            done['value'] = True
            route.fulfill(body='ok')
        elif url == CENTER:
            route.fulfill(body=card('已完成' if done['value'] else '未完成'), content_type='text/html; charset=utf-8')
        elif url.endswith('/__fixture__/course'):
            route.fulfill(body=card('已完成' if done['value'] else '未完成', '/__fixture__/lesson'), content_type='text/html; charset=utf-8')
        elif url.endswith('/__fixture__/lesson'):
            route.fulfill(body='<video controls src="/__fixture__/audio.wav" onended="fetch(\'/__fixture__/done\')"></video>', content_type='text/html; charset=utf-8')
        else:
            route.abort()
    context.route('**/*', handler)
    worker = BrowserWorker(tmp_path, queue.Queue())
    worker.context = context
    worker.active_page = context.new_page()
    save_json(tmp_path / 'site-config.json', fixture_cfg())
    worker.automatic_queue()
    assert done['value']
    assert worker.observations[-1]['result'] == 'platform_displayed_complete'
    assert all(x.startswith(BASE) for x in seen)


def test_stop_checkpoint(context, tmp_path):
    page_with(context, '<h1>demo</h1>')
    worker = BrowserWorker(tmp_path, queue.Queue())
    worker.context = context
    worker.stop_job.set()
    with pytest.raises(Cancelled):
        worker.checkpoint()


def test_pagination_collects_all_pages(context, tmp_path):
    from course_auto_assistant.core import SiteConfig
    page = page_with(context, card() + '<button id="next" onclick="document.querySelector(\'a\').href=\'/__fixture__/course2\';this.disabled=true">下一页</button>')
    spec = Listing('#list', '.card', 'a', '.status', ('已完成',), ('未完成',),
                   'next_button', '#next', '#next[disabled]')
    worker = BrowserWorker(tmp_path, queue.Queue())
    worker.context = context
    cfg = SiteConfig.parse(starter_config())
    entries = worker.scan(page, spec, cfg)
    assert len(entries) == 2
    assert entries[0].url != entries[1].url


def test_ended_alone_never_completes_lesson(context, tmp_path, monkeypatch):
    from course_auto_assistant.site import Entry
    page = page_with(context, card())
    worker = BrowserWorker(tmp_path, queue.Queue())
    worker.context, worker.active_page = context, page
    save_json(tmp_path / 'site-config.json', fixture_cfg())
    monkeypatch.setattr(worker, 'navigate', lambda url: page)
    monkeypatch.setattr(worker, 'scan', lambda p, s, c: [Entry('fixture', BASE + '/__fixture__/lesson', 'pending')])
    monkeypatch.setattr(worker, 'play_lesson', lambda cfg: None)
    with pytest.raises(NeedsUser, match='尚未显示完成'):
        worker.automatic_queue()
    assert worker.observations[-1]['result'] == 'video_ended_only'
