import json
import logging
import re
import sys
import time
import xml.etree.ElementTree as ET

import requests

ua = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/77.0.3865.90 Safari/537.36"
)
req_sleep = 1

log = logging.getLogger("UTILS")


def dump_json(file_path, data):
    with open(file_path, "w", encoding="utf-8") as f:
        txt = json.dumps(data, ensure_ascii=False, indent=2)
        # for compact form of channellist in json files
        txt = re.sub(r",\n\s{8}\"", ', "', txt)
        txt = re.sub(r"\s{6}{\s+(.*)\s+}", r"      { \g<1> }", txt)
        f.write(txt)


def request_data(url, method="GET", session=None, **kwargs):
    ret = ""
    with session or requests.Session() as sess:
        try:
            r = sess.request(method=method, url=url, **kwargs)
            r.raise_for_status()
            try:
                ret = r.json()
            except (json.decoder.JSONDecodeError, ValueError):
                ret = r.text
        except requests.exceptions.HTTPError as e:
            log.error("요청 중 에러: %s", e)
        except Exception:
            log.exception("요청 중 에러:")
    time.sleep(req_sleep)
    return ret


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
