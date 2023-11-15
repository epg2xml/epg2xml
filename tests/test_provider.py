import logging
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from random import shuffle
from timeit import default_timer as timer

from epg2xml import __title__, __version__
from epg2xml.providers import load_providers

cfg = {
    "ENABLED": True,
    "FETCH_LIMIT": 2,
    "ID_FORMAT": "{ServiceId}.{Source.lower()}",
    "ADD_REBROADCAST_TO_TITLE": False,
    "ADD_EPNUM_TO_TITLE": True,
    "ADD_DESCRIPTION": True,
    "ADD_XMLTV_NS": False,
    "GET_MORE_DETAILS": False,
    "ADD_CHANNEL_ICON": True,
    "HTTP_PROXY": None,
    "MY_CHANNELS": "*",
}

# logging
log_fmt = "%(asctime)-15s %(levelname)-8s %(name)-7s %(lineno)4d: %(message)s"
formatter = logging.Formatter(log_fmt, datefmt="%Y/%m/%d %H:%M:%S")
rootLogger = logging.getLogger()
rootLogger.setLevel(logging.DEBUG)

# suppress modules logging
logging.getLogger("requests").setLevel(logging.ERROR)
logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)

# logging to console, stderr by default
consolehandler = logging.StreamHandler()
consolehandler.setFormatter(formatter)
rootLogger.addHandler(consolehandler)

# logger
log = rootLogger.getChild("TEST")

provider_name = sys.argv[1]
provider = load_providers({provider_name.upper(): cfg})[0]

if provider_name.lower() == "daum":
    cfg["ID_FORMAT"] = "{No}.{Source.lower()}"

stime = timer()
provider.load_svc_channels()
etime_ch = timer() - stime

provider.load_req_channels()

if len(sys.argv) > 2:
    rch = provider.req_channels
    if len(sys.argv) > 3 and sys.argv[3] == "--shuffle":
        log.info("Shuffling requested channels...")
        shuffle(rch)
    if sys.argv[2].isdecimal():
        log.info(f"Using {sys.argv[2]} of them...")
        num_rch = int(sys.argv[2])
    else:
        per_rch = float(sys.argv[2]) * 100
        log.info(f"Using {per_rch:3.1f}% of them...")
        num_rch = int(len(rch) * float(sys.argv[2]))
    rch = rch[: max(10, num_rch)]
    provider.req_channels = rch

stime = timer()
provider.get_programs()
etime_prog = timer() - stime
num_rch = len(provider.req_channels)

provider.req_channels = [x for x in provider.req_channels if x.programs]
num_gch = len(provider.req_channels)

# status
log.info(f"To load service channels: {etime_ch:.2f}s")
log.info(f"To get EPG: {etime_prog:.2f}s/{num_rch:d} = {etime_prog/num_rch:.2f}s")
log.info(f"Requested: {num_rch} / Alive: {num_gch}")

if not provider.req_channels:
    sys.exit(0)

xmlfile = Path.cwd().joinpath(f"xmltv_{provider.provider_name.lower()}.xml")
with open(xmlfile, "w", encoding="utf-8") as f:
    with redirect_stdout(f):
        print('<?xml version="1.0" encoding="UTF-8"?>')
        print('<!DOCTYPE tv SYSTEM "xmltv.dtd">\n')
        print(f'<tv generator-info-name="{__title__} v{__version__}">')

        provider.write_channels()
        provider.write_programs()

        print("</tv>")

log.info(f"Average size: {xmlfile.stat().st_size/num_gch/1000.:.2f} kbyte/ch")

try:
    subprocess.run(["tv_validate_file", xmlfile], check=True)
except subprocess.CalledProcessError as e:
    sys.exit(1)
