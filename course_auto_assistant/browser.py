"""All Playwright calls live on one worker thread; Tk only exchanges messages."""
from __future__ import annotations

import queue
import shutil
import threading
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError, sync_playwright

from .core import (CENTER, Cancelled, NeedsUser, SiteConfig, allowed_url,
                   load_json, public_url, save_json)
from .site import (inspect_page, locate_video, next_list_page, read_listing,
                   require_no_attention, video_state)


class BrowserWorker(threading.Thread):
    def __init__(self, root: Path, events: queue.Queue):
        super().__init__(daemon=True, name="playwright-worker")
        self.root, self.events = root, events
        self.commands = queue.Queue()
        self.stop_job = threading.Event()
        self.pause_job = threading.Event()
        self.shutdown = threading.Event()
        self.context = None
        self.active_page = None
        self.playwright = None
        self.observed_seconds = 0.0
        self.observations = []

    def emit(self, kind: str, value):
        self.events.put((kind, value))

    def send(self, command: str, value=None):
        self.commands.put((command, value))

    def live_pages(self):
        return [p for p in self.context.pages if not p.is_closed()] if self.context else []

    def pages_update(self):
        self.emit("pages", [(i, public_url(p.url)) for i, p in enumerate(self.live_pages())])

    def open_browser(self):
        if not self.context or not self.live_pages():
            self.close_browser()
            try:
                self.context = self.playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self.root / "edge-profile"), channel="msedge",
                    headless=False, viewport=None, accept_downloads=False,
                )
            except PlaywrightError as exc:
                raise NeedsUser("无法启动 Microsoft Edge。请确认本机 Edge 可正常启动，且没有另一个助手占用专用浏览器。") from exc
            self.context.set_default_timeout(10000)
            self.context.on("page", lambda page: self.emit("log", "浏览器打开了新标签页；采集前可刷新标签页列表。"))
            self.active_page = self.context.pages[0] if self.context.pages else self.context.new_page()
        try:
            self.active_page.goto(CENTER, wait_until="domcontentloaded", timeout=30000)
        finally:
            self.pages_update()
        self.emit("log", "请在专用 Edge 内人工登录。登录资料仅保存在本机专用目录，不读取日常 Edge 配置。")

    def close_browser(self):
        if self.context:
            try:
                self.context.close()
            except PlaywrightError:
                pass
        self.context, self.active_page = None, None

    def pause_videos(self):
        for page in self.live_pages():
            for frame in page.frames:
                try:
                    frame.locator("video").evaluate_all("vs => vs.forEach(v => v.pause())")
                except PlaywrightError:
                    pass

    def checkpoint(self, video=None):
        if self.stop_job.is_set() or self.shutdown.is_set():
            raise Cancelled()
        if not self.live_pages():
            raise NeedsUser("浏览器已关闭，队列停止。")
        if self.pause_job.is_set():
            self.pause_videos()
            self.emit("status", "已暂停")
            while self.pause_job.is_set():
                if self.stop_job.is_set() or self.shutdown.is_set():
                    raise Cancelled()
                if not self.live_pages():
                    raise NeedsUser("浏览器已关闭。")
                self.live_pages()[0].wait_for_timeout(200)
            self.emit("status", "运行中")
            return True
        return False

    def navigate(self, url):
        self.checkpoint()
        url = allowed_url(url)
        self.pause_videos()
        page = self.active_page
        if page is None or page.is_closed():
            page = self.context.new_page()
            self.active_page = page
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        allowed_url(page.url)  # redirects to another host require manual inspection
        return page

    def scan(self, page, spec, cfg):
        entries, seen_pages, seen_urls = [], set(), set()
        for _ in range(100):  # guardrail, never silently truncate a long list
            self.checkpoint()
            require_no_attention(page, cfg.attention_selectors)
            # Wait on the confirmed ready marker, not a guessed network-idle delay.
            deadline = time.monotonic() + 10
            while True:
                try:
                    current = read_listing(page, spec)
                    break
                except NeedsUser:
                    if time.monotonic() >= deadline:
                        raise
                    self.checkpoint()
                    page.wait_for_timeout(250)
            signature = tuple((e.url, e.status) for e in current)
            if signature in seen_pages:
                raise NeedsUser("列表翻页没有产生新内容，已停止以避免循环。")
            seen_pages.add(signature)
            for entry in current:
                if entry.url in seen_urls:
                    raise NeedsUser("不同列表页出现相同课程，需检查分页配置。")
                seen_urls.add(entry.url)
                entries.append(entry)
            if not next_list_page(page, spec):
                return entries
            # Wait for a changed list signature. Some sites update without navigation.
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                self.checkpoint()
                page.wait_for_timeout(250)
                try:
                    newer = tuple((e.url, e.status) for e in read_listing(page, spec))
                    if newer != signature:
                        break
                except NeedsUser:
                    pass
            else:
                raise NeedsUser("翻页等待超时，未继续扫描。")
        raise NeedsUser("列表超过100页安全上限，需人工确认，未宣称扫描完整。")

    def play_lesson(self, cfg):
        page, video = locate_video(self.context, cfg.video_selector)
        allowed_url(page.url)
        start_url = page.url
        require_no_attention(page, cfg.attention_selectors)
        state = video_state(video)
        if abs(state["rate"] - 1.0) > 0.001:
            raise NeedsUser("当前播放器不是1倍速；请手动恢复网站正常播放状态。")
        # Normal HTMLMediaElement playback. Never seek or manufacture ended events.
        try:
            if not state["ended"]:
                video.evaluate("v => v.play()")
        except PlaywrightError as exc:
            raise NeedsUser("浏览器未允许自动播放，请人工点击播放后重新启动队列。") from exc
        last_t, last_media = time.monotonic(), state["current"]
        last_progress = last_t
        while True:
            resumed = self.checkpoint(video)
            require_no_attention(page, cfg.attention_selectors)
            if page.url != start_url:
                raise NeedsUser("播放页面已跳转；必须先确认上一课时的完成状态，停止自动推进。")
            if resumed and not video_state(video)["ended"]:
                # Resume only after checking for a new site dialog.
                video.evaluate("v => v.play()")
            state = video_state(video)
            now = time.monotonic()
            if resumed:
                last_t, last_progress = now, now
            if state["error"]:
                raise NeedsUser("播放器报告加载/解码错误，已停止。")
            if abs(state["rate"] - 1) > 0.001:
                raise NeedsUser("播放速度发生变化，已停止。")
            media = state["current"]
            if media is not None and last_media is not None and media > last_media:
                if not state["paused"] and not resumed:
                    self.observed_seconds += min(now - last_t, 2.0)
                last_progress = now
            self.emit("progress", dict(current=media, duration=state["duration"],
                                       seconds=self.observed_seconds))
            if state["ended"]:
                return
            if state["paused"]:
                raise NeedsUser("视频被暂停，可能需要人工确认；不会反复自动点击继续。")
            if now - last_progress > 90:
                raise NeedsUser("视频90秒无进展，可能是网络、登录或在场确认问题；请人工检查。")
            last_t, last_media = now, media
            page.wait_for_timeout(500)

    def automatic_queue(self):
        cfg = SiteConfig.parse(load_json(self.root / "site-config.json"))
        cfg.validate_run()  # before any browser action
        if not self.context:
            raise NeedsUser("请先打开浏览器并人工登录。")
        if len(self.live_pages()) != 1:
            raise NeedsUser("开始自动队列前请仅保留一个课程中心标签页，避免操作到其他视频。")
        self.active_page = self.live_pages()[0]
        self.observed_seconds, self.observations = 0.0, []
        page = self.navigate(cfg.center)
        courses = self.scan(page, cfg.courses, cfg)
        pending = [e for e in courses if e.status == "pending"]
        self.emit("queue", [asdict(e) for e in courses])
        self.emit("log", f"完整扫描到 {len(courses)} 门课程，其中 {len(pending)} 门未完成。")
        for course in pending:
            self.emit("status", "课程：" + course.title)
            page = self.navigate(course.url)
            lessons = self.scan(page, cfg.lessons, cfg)
            for lesson in [e for e in lessons if e.status == "pending"]:
                self.emit("status", "课时：" + lesson.title)
                self.navigate(lesson.url)
                self.play_lesson(cfg)
                self.observations.append({"course": course.title, "lesson": lesson.title,
                                          "result": "video_ended_only"})
                save_json(self.root / "last-run.json", self.observations)
                # ended != platform-completed. Re-read the authoritative visible list.
                confirmed = False
                for _ in range(3):
                    page = self.navigate(course.url)
                    actual = self.scan(page, cfg.lessons, cfg)
                    match = [e for e in actual if e.url == lesson.url]
                    if len(match) == 1 and match[0].status == "complete":
                        confirmed = True
                        break
                    page.wait_for_timeout(2000)
                if not confirmed:
                    raise NeedsUser("视频已结束，但平台尚未显示完成。已停在待确认状态，不会算成学分或完成课程。")
                self.observations[-1]["result"] = "platform_displayed_complete"
                save_json(self.root / "last-run.json", self.observations)
                self.emit("log", "平台已显示课时完成：" + lesson.title)
            # Verify course-level completion too, e.g. exam requirements must not be skipped.
            page = self.navigate(cfg.center)
            actual_courses = self.scan(page, cfg.courses, cfg)
            match = [e for e in actual_courses if e.url == course.url]
            if len(match) != 1 or match[0].status != "complete":
                raise NeedsUser("课时处理结束，但课程未显示完成；可能另有考试或任务，请人工确认。")
        self.emit("log", "本次扫描队列处理结束。这里只记录平台页面状态，不换算或申报学分。")

    def run(self):
        try:
            with sync_playwright() as pw:
                self.playwright = pw
                while not self.shutdown.is_set():
                    try:
                        command, value = self.commands.get_nowait()
                    except queue.Empty:
                        if self.live_pages():
                            try:
                                self.live_pages()[0].wait_for_timeout(150)
                            except PlaywrightError:
                                self.close_browser()
                        else:
                            self.shutdown.wait(0.15)
                        continue
                    self.emit("busy", True)
                    self.stop_job.clear()
                    self.pause_job.clear()
                    try:
                        if command == "open":
                            self.open_browser()
                        elif command == "pages":
                            self.pages_update()
                        elif command == "inspect":
                            index, stage = value
                            pages = self.live_pages()
                            if index < 0 or index >= len(pages):
                                raise NeedsUser("请先打开浏览器，再刷新并选择标签页。")
                            path = self.root / "reports" / f"{stage}-{datetime.now():%Y%m%d-%H%M%S}.json"
                            save_json(path, inspect_page(pages[index], stage))
                            self.emit("log", f"已保存页面结构：{path}\n分享前请打开检查，仍可能包含课程名或ID。")
                        elif command == "auto":
                            self.automatic_queue()
                        elif command == "close":
                            self.close_browser()
                        elif command == "clear_login":
                            self.close_browser()
                            profile = self.root / "edge-profile"
                            if profile.exists():
                                shutil.rmtree(profile)
                            self.emit("log", "已删除助手专用的本地登录资料；不会影响日常 Edge。")
                        self.emit("status", "就绪")
                    except Cancelled:
                        self.pause_videos()
                        self.emit("status", "已停止")
                    except (NeedsUser, ValueError, OSError, PlaywrightError) as exc:
                        self.pause_videos()
                        self.emit("status", "需要人工处理")
                        # Do not print Playwright call logs: they may contain private URLs.
                        message = str(exc) if isinstance(exc, (NeedsUser, ValueError)) else (
                            f"操作失败（{type(exc).__name__}）。请检查网络、浏览器或本地文件权限后重试。")
                        self.emit("notice", message)
                    except Exception as exc:
                        self.pause_videos()
                        self.emit("notice", f"发生未处理异常（{type(exc).__name__}），自动操作已停止。")
                    finally:
                        self.emit("busy", False)
                self.close_browser()
        except Exception as exc:
            self.emit("notice", f"浏览器引擎启动失败（{type(exc).__name__}），请重新安装发布包。")
            self.emit("busy", False)
