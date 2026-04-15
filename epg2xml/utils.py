import json
import logging
import re
import sys
import threading
import time
import xml.etree.ElementTree as ET
from datetime import timedelta
from functools import wraps
from math import floor
from pathlib import Path
from typing import Any, Callable, Optional

from bs4 import BeautifulSoup, FeatureNotFound

log = logging.getLogger("UTILS")


def dump_json(path: Path | str, data: Any):
    txt = json.dumps(data, ensure_ascii=False, indent=2)
    # for compact form of channellist in json files
    txt = re.sub(r",\n\s{8}\"", ', "', txt)
    txt = re.sub(r"\s{6}{\s+(.*)\s+}", r"      { \g<1> }", txt)
    return Path(path).write_text(txt, encoding="utf-8")


def strip_json_comments(text: str) -> str:
    result = []
    in_string = False
    in_line_comment = False
    in_block_comment = False
    escaped = False
    index = 0

    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""

        if in_line_comment:
            if char == "\n":
                in_line_comment = False
                result.append(char)
            index += 1
            continue

        if in_block_comment:
            if char == "*" and next_char == "/":
                in_block_comment = False
                index += 2
                continue
            if char in "\r\n":
                result.append(char)
            index += 1
            continue

        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            result.append(char)
            index += 1
            continue

        if char == "/" and next_char == "/":
            in_line_comment = True
            index += 2
            continue

        if char == "/" and next_char == "*":
            in_block_comment = True
            index += 2
            continue

        result.append(char)
        index += 1

    return "".join(result)


def load_json(path: Path | str):
    txt = Path(path).read_text(encoding="utf-8")
    return json.loads(strip_json_comments(txt))


def norm_text(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def time_to_td(value) -> Optional[timedelta]:
    text = norm_text(value)
    if text is None:
        return None

    normalized = text.replace(":", "")
    if not normalized.isdigit():
        return None

    if len(normalized) == 4:
        hour = int(normalized[:2])
        minute = int(normalized[2:4])
        second = 0
    elif len(normalized) in (6, 8):
        hour = int(normalized[:2])
        minute = int(normalized[2:4])
        second = int(normalized[4:6])
    else:
        return None

    if minute > 59 or second > 59:
        return None
    return timedelta(hours=hour, minutes=minute, seconds=second)


# https://stackoverflow.com/a/22273639
_illegal_unichrs = [
    (0x00, 0x08),
    (0x0B, 0x0C),
    (0x0E, 0x1F),
    (0x7F, 0x84),
    (0x86, 0x9F),
    (0xFDD0, 0xFDDF),
    (0xFFFE, 0xFFFF),
]
if sys.maxunicode >= 0x10000:  # not narrow build
    _illegal_unichrs.extend(
        [
            (0x1FFFE, 0x1FFFF),
            (0x2FFFE, 0x2FFFF),
            (0x3FFFE, 0x3FFFF),
            (0x4FFFE, 0x4FFFF),
            (0x5FFFE, 0x5FFFF),
            (0x6FFFE, 0x6FFFF),
            (0x7FFFE, 0x7FFFF),
            (0x8FFFE, 0x8FFFF),
            (0x9FFFE, 0x9FFFF),
            (0xAFFFE, 0xAFFFF),
            (0xBFFFE, 0xBFFFF),
            (0xCFFFE, 0xCFFFF),
            (0xDFFFE, 0xDFFFF),
            (0xEFFFE, 0xEFFFF),
            (0xFFFFE, 0xFFFFF),
            (0x10FFFE, 0x10FFFF),
        ]
    )
_illegal_ranges = [rf"{chr(low)}-{chr(high)}" for (low, high) in _illegal_unichrs]
_illegal_xml_chars_RE = re.compile("[" + "".join(_illegal_ranges) + "]")


class Element(ET.Element):
    def __init__(self, *args, **kwargs):
        attrib = kwargs.pop("attrib", {})
        super().__init__(args[0], attrib=attrib, **kwargs)
        if len(args) > 1:
            self.text = args[1]

    def indent(self, space="  ", level=0):
        if level < 0:
            raise ValueError(f"Initial indentation level must be >= 0, got {level}")
        if len(self) == 0:
            return

        # Reduce the memory consumption by reusing indentation strings.
        indentations = ["\n" + level * space]

        def _indent_children(elem, level):
            # Start a new indentation level for the first child.
            child_level = level + 1
            try:
                child_indentation = indentations[child_level]
            except IndexError:
                child_indentation = indentations[level] + space
                indentations.append(child_indentation)

            if not elem.text or not elem.text.strip():
                elem.text = child_indentation

            for child in elem:
                if len(child):
                    _indent_children(child, child_level)
                if not child.tail or not child.tail.strip():
                    child.tail = child_indentation

            # Dedent after the last child by overwriting the previous indentation.
            if not child.tail.strip():  # pylint: disable=undefined-loop-variable
                child.tail = indentations[level]  # pylint: disable=undefined-loop-variable

        _indent_children(self, 0)

    def tostring(self, space="  ", level=0):
        self.indent(space=space, level=level)
        return _illegal_xml_chars_RE.sub("", space * level + ET.tostring(self, encoding="unicode"))


class PrefixLogger(logging.LoggerAdapter):
    def __init__(self, logger, prefix):
        super().__init__(logger, {})
        self.prefix = prefix

    def process(self, msg, kwargs):
        return f"{self.prefix} {msg}", kwargs


class ParserBeautifulSoup(BeautifulSoup):
    """A ``bs4.BeautifulSoup`` that picks the first available parser."""

    def insert_before(self, *args):
        pass

    def insert_after(self, *args):
        pass

    def __init__(self, markup, **kwargs):
        # pick the first parser available
        for parser in ["lxml", "html.parser"]:
            try:
                super().__init__(markup, parser, **kwargs)
                return
            except FeatureNotFound:
                pass

        raise FeatureNotFound


class RateLimiter:
    """original implementation by tomasbasham/ratelimit"""

    try:
        now: Callable = time.monotonic  # Use monotonic time if available
    except AttributeError:
        now: Callable = time.time  # otherwise fall back to the system clock

    def __init__(self, calls: int = 15, period: float = 900.0, tps: float = None):
        if tps is not None:
            if tps <= 0.0:
                raise ValueError("tps must be positive")
            calls, period = 1, 1 / tps
        self.max_calls = max(1, min(sys.maxsize, floor(calls)))
        self.period = period

        # Initialise the decorator state.
        self.last_reset = self.now()
        self.num_calls = 0

        # Add thread safety.
        self.lock = threading.RLock()

    def __call__(self, func: Callable) -> Callable:
        """
        Return a wrapped function that prevents further function invocations if
        previously called within a specified period of time.
        """

        @wraps(func)
        def wrapper(*args, **kargs):
            """
            Extend the behaviour of the decorated function, forwarding function
            invocations previously called no sooner than a specified period of
            time. The decorator will raise an exception if the function cannot
            be called so the caller may implement a retry strategy such as an
            exponential backoff.
            """
            with self.lock:
                period_remaining = self.__period_remaining()

                # If the time window has elapsed then reset.
                if period_remaining <= 0:
                    self.num_calls = 0
                    self.last_reset = self.now()

                # Increase the number of attempts to call the function.
                self.num_calls += 1

                # If the number of attempts to call the function exceeds the maximum
                if self.num_calls > self.max_calls:
                    self.last_reset = self.now() + period_remaining  # for future call
                    time.sleep(period_remaining)
                    return func(*args, **kargs)
            return func(*args, **kargs)

        return wrapper

    def __period_remaining(self) -> float:
        elapsed = self.now() - self.last_reset
        return self.period - elapsed
