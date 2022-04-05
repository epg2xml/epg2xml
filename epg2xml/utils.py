import re
import sys
import json
import time
import logging
from xml.sax.saxutils import escape as _escape

from requests import Session

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


def request_data(url, params, method="GET", output="html", session=None, ret=""):
    # TODO: retry on failure
    # https://findwork.dev/blog/advanced-usage-python-requests-timeouts-retries-hooks/
    sess = Session() if session is None else session
    try:
        if method == "GET":
            r = sess.get(url, params=params)
        elif method == "POST":
            r = sess.post(url, data=params)
        else:
            raise ValueError(f"Unexpected method: {method}")
        r.raise_for_status()
        if output.lower() == "html":
            ret = r.text
        elif output.lower() == "json":
            ret = r.json()
        else:
            raise ValueError(f"Unexpected output type: {output}")
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


def escape(s):
    return _escape(_illegal_xml_chars_RE.sub(" ", s))


class PrefixLogger(logging.LoggerAdapter):
    def __init__(self, logger, prefix):
        super().__init__(logger, {})
        self.prefix = prefix

    def process(self, msg, kwargs):
        return f"{self.prefix} {msg}", kwargs
