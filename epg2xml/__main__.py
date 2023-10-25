import json
import logging
import socket
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack

from epg2xml import __title__, __version__
from epg2xml.config import Config, setup_root_logger
from epg2xml.providers import load_channels, load_providers

############################################################
# INIT
############################################################

# logging
setup_root_logger()

# load initial config
conf = Config()

if conf.settings["logfile"] is not None:
    from logging.handlers import RotatingFileHandler

    # logging to file
    fileHandler = RotatingFileHandler(conf.settings["logfile"], maxBytes=2 * 1024**2, backupCount=5, encoding="utf-8")
    setup_root_logger(handler=fileHandler)

# set configured log level
logging.getLogger().setLevel(conf.settings["loglevel"])

# load config file
conf.load()

# logger
log = logging.getLogger("MAIN")

############################################################
# MAIN
############################################################


def main():
    log.debug("Loading providers ...")
    providers = load_providers(conf.configs)
    try:
        log.debug("Trying to load cached channels from json")
        with open(conf.settings["channelfile"], "r", encoding="utf-8") as fp:
            channeljson = json.load(fp)
    except (json.decoder.JSONDecodeError, ValueError, FileNotFoundError) as e:
        log.debug("Failed to load cached channels from json: %s", e)
        channeljson = {}

    if conf.args["cmd"] == "run":
        with ExitStack() as stack:
            # redirecting stdout to ...
            if conf.settings["xmlfile"]:
                sys.stdout = stack.enter_context(open(conf.settings["xmlfile"], "w", encoding="utf-8"))
            elif conf.settings["xmlsock"]:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(conf.settings["xmlsock"])
                sys.stdout = stack.enter_context(sock.makefile("w"))
            stack.callback(print, "</tv>")

            log.debug("Loading service channels ...")
            load_channels(providers, conf, channeljson)

            log.debug("Loading MY_CHANNELS ...")
            for p in providers:
                p.load_my_channels()

            log.info("Writing xmltv.dtd header ...")
            print('<?xml version="1.0" encoding="UTF-8"?>')
            print('<!DOCTYPE tv SYSTEM "xmltv.dtd">\n')
            print(f'<tv generator-info-name="{__title__} v{__version__}">')

            log.debug("Writing channel headers ...")
            for p in providers:
                p.write_channel_headers()

            log.debug("Getting EPG ...")
            if conf.settings["parallel"]:
                with ThreadPoolExecutor() as exe:
                    f2p = {exe.submit(p.get_programs, lazy_write=True): p for p in providers}
                    for future in as_completed(f2p):
                        p = f2p[future]
                        p.write_programs()
            else:
                for p in providers:
                    if p.req_channels:
                        p.get_programs()

            log.info("Done.")
    elif conf.args["cmd"] == "update_channels":
        load_channels(providers, conf, channeljson)
    else:
        raise NotImplementedError(f"Unknown command: {conf.args['cmd']}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
