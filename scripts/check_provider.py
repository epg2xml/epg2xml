import logging
import subprocess
import sys
from contextlib import redirect_stdout
from copy import deepcopy
from importlib import import_module
from pathlib import Path
from random import shuffle
from timeit import default_timer as timer

from epg2xml import __title__, __version__
from epg2xml.config import Config
from epg2xml.providers.all import get_provider_spec


def setup_logging():
    log_fmt = "%(asctime)-15s %(levelname)-8s %(name)-7s: %(message)s"
    formatter = logging.Formatter(log_fmt, datefmt="%Y/%m/%d %H:%M:%S")
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    logging.getLogger("requests").setLevel(logging.ERROR)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)
    logging.getLogger("curl_cffi").setLevel(logging.ERROR)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    return root_logger.getChild("CHECK")


def build_provider(provider_name: str):
    spec = get_provider_spec(provider_name)
    if spec is None:
        raise ImportError(f"No such provider found: '{provider_name}'")

    cfg = deepcopy(Config.base_config["GLOBAL"])
    cfg["MY_CHANNELS"] = "*"

    module = import_module(f"epg2xml.providers.{spec.name}")
    provider = getattr(module, spec.class_name)(cfg)
    return provider


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 3:
        print("usage: python -m scripts.check_provider <provider> <count_or_ratio> <shuffle:true|false>")
        return 2

    provider_name, limit_arg, shuffle_arg = argv
    log = setup_logging()
    provider = build_provider(provider_name)

    stime = timer()
    provider.load_svc_channels()
    etime_ch = timer() - stime

    provider.load_req_channels()
    req_channels = provider.req_channels

    if shuffle_arg == "true":
        log.info("Shuffling requested channels...")
        shuffle(req_channels)

    if limit_arg.isdecimal() and (num_req := int(limit_arg)) > 0:
        log.info("Using %d of them...", num_req)
        req_channels = req_channels[: max(1, num_req)]
    elif (percent_req := float(limit_arg) * 100) > 0.0:
        log.info("Using %3.1f%% of them...", percent_req)
        num_req = int(len(req_channels) * percent_req / 100)
        req_channels = req_channels[: max(10, num_req)]

    provider.req_channels = req_channels

    stime = timer()
    provider.get_programs()
    etime_prog = timer() - stime
    num_req = len(provider.req_channels)

    provider.req_channels = [x for x in provider.req_channels if x.programs]
    num_live = len(provider.req_channels)

    log.info("To load service channels: %.2fs", etime_ch)
    log.info("To get EPG: %.2fs/%d = %.2fs", etime_prog, num_req, etime_prog / num_req)
    log.info("Requested: %d / Alive: %d", num_req, num_live)

    if not provider.req_channels:
        return 0

    xmlfile = Path.cwd().joinpath(f"xmltv_{provider.provider_name.lower()}.xml")
    with open(xmlfile, "w", encoding="utf-8") as f:
        with redirect_stdout(f):
            print('<?xml version="1.0" encoding="UTF-8"?>')
            print('<!DOCTYPE tv SYSTEM "xmltv.dtd">\n')
            print(f'<tv generator-info-name="{__title__} v{__version__}">')
            provider.write_channels()
            provider.write_programs()
            print("</tv>")

    log.info("Average size: %.2f kbyte/ch", xmlfile.stat().st_size / num_live / 1000.0)

    try:
        subprocess.run(["tv_validate_file", xmlfile], check=True)
    except subprocess.CalledProcessError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
