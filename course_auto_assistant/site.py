"""Read-only diagnostics and the verified-selector adapter. No learning APIs."""
from __future__ import annotations

from dataclasses import dataclass
import time
from playwright.sync_api import Error as PlaywrightError

from .core import Listing, NeedsUser, allowed_url, public_text, public_url


@dataclass(frozen=True)
class Entry:
    title: str
    url: str
    status: str


def visible_matches(page, selector: str):
    found = []
    for frame in page.frames:
        try:
            loc = frame.locator(selector)
            for i in range(loc.count()):
                if loc.nth(i).is_visible():
                    found.append(loc.nth(i))
        except PlaywrightError as exc:
            raise NeedsUser("页面或选择器发生变化，需要重新核实适配。") from exc
    return found


def single(page, selector: str):
    found = visible_matches(page, selector)
    if len(found) != 1:
        raise NeedsUser(f"入口匹配到 {len(found)} 个可见元素，需要恰好一个；自动操作已停止。")
    return found[0]


def require_no_attention(page, selectors=()) -> None:
    # Fail closed for common visible dialogs. This is a safety net, not a
    # claim to recognise every custom site challenge. Unknown stalls also stop.
    defaults = ('[role="dialog"]:visible', 'dialog[open]',
                'input[type="password"]:visible')
    for selector in (*defaults, *selectors):
        if visible_matches(page, selector):
            raise NeedsUser("页面出现登录、弹窗或待人工处理内容。请在浏览器中检查后再启动队列。")
    for frame in page.frames:
        try:
            if frame.get_by_text("播放行为异常", exact=False).first.is_visible():
                raise NeedsUser("英盛提示播放行为异常，已停止自动操作；请人工处理。")
        except PlaywrightError:
            continue


def read_listing(page, spec: Listing) -> list[Entry]:
    """One page only. Empty/missing/ambiguous status is not 'unfinished'."""
    containers = visible_matches(page, spec.ready)
    if len(containers) != 1:
        raise NeedsUser("列表尚未就绪或登录已失效；请确认页面后重试。")
    cards = visible_matches(page, spec.items)
    if not cards:
        raise NeedsUser("没有识别到课程/章节，不能把它当作全部完成。")
    entries = []
    seen = set()
    for card in cards:
        anchor = card.locator(spec.link)
        status = card.locator(spec.status)
        if anchor.count() != 1 or status.count() != 1:
            raise NeedsUser("课程卡片的链接/状态数量不唯一，需要重新适配。")
        href = anchor.get_attribute("href")
        if not href:
            raise NeedsUser("发现无 href 的课程按钮；需要确认其打开方式，暂不猜测。")
        url = allowed_url(href, page.url)
        title = public_text(anchor.inner_text()) or "未命名课程/章节"
        value = spec.classify(status.inner_text())
        if value == "unknown":
            raise NeedsUser("页面出现未确认的进度标记；不会擅自归类为已完成或未完成。")
        if url in seen:
            raise NeedsUser("当前页出现重复课程链接，需确认列表选择器。")
        seen.add(url)
        entries.append(Entry(title, url, value))
    return entries


def next_list_page(page, spec: Listing) -> bool:
    if spec.pagination == "none":
        return False
    if visible_matches(page, spec.next_disabled):
        return False
    button = single(page, spec.next_button)
    button.click(timeout=10000)
    return True


def locate_video(context, selector="video", timeout=10.0):
    deadline = time.monotonic() + timeout
    while True:
        matches = []
        pages = [p for p in context.pages if not p.is_closed()]
        for page in pages:
            for loc in visible_matches(page, selector):
                matches.append((page, loc))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1 or not pages or time.monotonic() >= deadline:
            raise NeedsUser(f"识别到 {len(matches)} 个可见视频；需要确认具体播放器，已暂停。")
        pages[0].wait_for_timeout(200)


def video_state(video) -> dict:
    return video.evaluate("""v => ({
        current: Number.isFinite(v.currentTime) ? v.currentTime : null,
        duration: Number.isFinite(v.duration) ? v.duration : null,
        paused: v.paused, ended: v.ended, rate: v.playbackRate,
        ready: v.readyState, error: v.error ? v.error.code : null
    })""")


def inspect_page(page, stage: str) -> dict:
    """Never read inputs, cookies, storage, network headers or media addresses."""
    report = {"stage": stage, "url_without_query": public_url(page.url), "frames": [],
              "privacy": "仅本地保存。仍可能含课程名、ID、路径，分享前必须检查。"}
    for frame in page.frames[:20]:
        try:
            nodes = frame.locator("a,button,video,iframe,[role=button],select").evaluate_all("""els =>
              els.slice(0, 350).map(e => ({
                tag: e.tagName.toLowerCase(), id: e.id,
                classes: typeof e.className === 'string' ? e.className : '',
                text: e.tagName === 'SELECT' ? '' : (e.innerText || '').slice(0, 160),
                href: e.tagName === 'A' ? e.getAttribute('href') : null,
                parent: e.parentElement ? {tag:e.parentElement.tagName.toLowerCase(),
                    id:e.parentElement.id, classes:typeof e.parentElement.className === 'string'
                    ? e.parentElement.className : ''} : null
              }))""")
            for node in nodes:
                node["text"] = public_text(node["text"])
                if node["href"]:
                    from urllib.parse import urljoin
                    node["href"] = public_url(urljoin(frame.url, node["href"]))
            report["frames"].append({"url_without_query": public_url(frame.url), "elements": nodes})
        except PlaywrightError:
            report["frames"].append({"unavailable": True})
    return report
