"""Configuration, local-only storage and conservative progress interpretation."""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

CENTER = "https://qy.yingsheng.com/26058/courselist/"
HOST = "qy.yingsheng.com"


class NeedsUser(RuntimeError):
    """Stop automated actions until the user or the site adapter resolves ambiguity."""


class Cancelled(RuntimeError):
    pass


def data_dir() -> Path:
    override = os.environ.get("CAA_DATA_DIR")  # tests/development only
    root = Path(override) if override else Path(
        os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share"))
    ) / "CourseAutoAssistant"
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_json(path: Path, value: object) -> None:
    """Atomic replacement; never truncate the previous file on partial write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".caa-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8-sig") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("配置必须是 JSON 对象。")
    return value


def normal_text(value: str) -> str:
    return " ".join(value.split())


def public_text(value: str) -> str:
    """Best-effort report redaction, NOT a guarantee of anonymisation."""
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[邮箱已隐藏]", value)
    value = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[手机号已隐藏]", value)
    return normal_text(value)[:160]


def public_url(value: str) -> str:
    """No credentials, query values or fragments in diagnostic exports."""
    try:
        u = urlsplit(value)
        if u.scheme not in ("http", "https"):
            return f"{u.scheme or 'unknown'}:[省略]"
        return urlunsplit((u.scheme, u.hostname or "", u.path, "", ""))
    except ValueError:
        return "[地址已隐藏]"


def allowed_url(value: str, base: str = CENTER) -> str:
    u = urlsplit(urljoin(base, value))
    if (u.scheme != "https" or u.hostname != HOST or u.username or u.password
            or u.port not in (None, 443)):
        raise NeedsUser("自动导航已停止：目标不在已确认的英盛企业站点内。")
    if not value.strip() or value.strip().startswith(("#", "javascript:")):
        raise NeedsUser("该入口不是可直接打开的课程链接，需要确认页面的点击逻辑。")
    return urlunsplit((u.scheme, u.netloc, u.path, u.query, ""))


@dataclass(frozen=True)
class Listing:
    ready: str
    items: str
    link: str
    status: str
    complete: tuple[str, ...]
    pending: tuple[str, ...]
    pagination: str
    next_button: str
    next_disabled: str

    @classmethod
    def parse(cls, raw: dict) -> "Listing":
        if not isinstance(raw, dict):
            raise ValueError("列表配置格式错误。")
        def string(key: str) -> str:
            s = raw.get(key, "")
            if not isinstance(s, str) or len(s) > 500:
                raise ValueError(f"{key} 必须是短字符串。")
            return s.strip()
        def tokens(key: str) -> tuple[str, ...]:
            seq = raw.get(key, [])
            if not isinstance(seq, list) or not all(isinstance(s, str) and s.strip() for s in seq):
                raise ValueError(f"{key} 必须是非空文本的数组。")
            return tuple(normal_text(s) for s in seq)
        return cls(*(string(k) for k in ("ready", "items", "link", "status")),
                   tokens("complete"), tokens("pending"),
                   *(string(k) for k in ("pagination", "next_button", "next_disabled")))

    def validate(self) -> None:
        if not all((self.ready, self.items, self.link, self.status, self.complete, self.pending)):
            raise NeedsUser("缺少列表入口、状态文字或列表就绪标记；不能猜测哪些课程已完成。")
        if set(self.complete) & set(self.pending):
            raise ValueError("完成和未完成标记不能重叠。")
        if self.pagination not in ("none", "next_button"):
            raise NeedsUser("需要先确认列表是单页还是翻页，避免漏掉课程。")
        if self.pagination == "next_button" and not all((self.next_button, self.next_disabled)):
            raise NeedsUser("翻页配置缺少下一页入口或末页禁用标记。")

    def classify(self, text: str) -> str:
        text = normal_text(text)
        # Exact matching avoids treating '未完成' or '未学习100%' as complete.
        if text in self.complete:
            return "complete"
        if text in self.pending:
            return "pending"
        return "unknown"


@dataclass(frozen=True)
class SiteConfig:
    verified: bool
    evidence: str
    center: str
    courses: Listing
    lessons: Listing
    video_selector: str
    attention_selectors: tuple[str, ...]

    @classmethod
    def parse(cls, raw: dict) -> "SiteConfig":
        if raw.get("schema_version") != 1 or raw.get("site") != "yingsheng":
            raise ValueError("仅支持 schema_version=1 的英盛适配配置。")
        if not isinstance(raw.get("verified"), bool):
            raise ValueError("verified 必须是布尔值。")
        if raw.get("course_center_url") != CENTER:
            raise ValueError("首版仅支持已确认的 26058 企业课程中心。")
        selectors = raw.get("attention_selectors", [])
        if not isinstance(selectors, list) or not all(isinstance(x, str) and 0 < len(x) < 500 for x in selectors):
            raise ValueError("attention_selectors 格式错误。")
        video = raw.get("video_selector", "video")
        if not isinstance(video, str) or not video.strip() or len(video) > 500:
            raise ValueError("video_selector 格式错误。")
        evidence = raw.get("evidence", "")
        if not isinstance(evidence, str):
            raise ValueError("evidence 必须说明适配验证依据。")
        return cls(raw["verified"], evidence, CENTER, Listing.parse(raw.get("courses", {})),
                   Listing.parse(raw.get("lessons", {})), video, tuple(selectors))

    def validate_run(self) -> None:
        if not self.verified or not self.evidence.strip():
            raise NeedsUser("英盛实站适配尚未核实。请先采集课程中心、章节列表、播放器三个页面的结构；不会猜测入口后自动点击。")
        self.courses.validate()
        self.lessons.validate()


def starter_config() -> dict:
    def listing() -> dict:
        return dict(ready="", items="", link="", status="", complete=[], pending=[],
                    pagination="unknown", next_button="", next_disabled="")
    return dict(schema_version=1, site="yingsheng", verified=False, evidence="",
                course_center_url=CENTER, courses=listing(), lessons=listing(),
                video_selector="video", attention_selectors=[])


class InstanceLock:
    """Protect the dedicated browser profile from concurrent application instances."""
    def __init__(self, root: Path):
        self.path = root / ".instance.lock"
        self.stream = None

    def acquire(self) -> None:
        self.stream = self.path.open("a+b")
        self.stream.seek(0, 2)
        if not self.stream.tell():
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            self.stream = None
            raise NeedsUser("助手已经在运行，请勿同时打开两个实例。") from exc

    def close(self) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None
