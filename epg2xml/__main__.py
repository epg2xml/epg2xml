import logging
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack

from epg2xml import __title__, __version__
from epg2xml.config import Config
from epg2xml.providers import load_channels, load_providers

############################################################
# INIT
############################################################

# load initial config
conf = Config()

# load config file
conf.load()

# logger
log = logging.getLogger("MAIN")

############################################################
# MAIN
############################################################


def main():
    log.debug("Loading providers...")
    providers = load_providers(conf.configs)

    if conf.args["cmd"] == "run":
        with ExitStack() as stack:
            # redirecting stdout to...
            if conf.settings["xmlfile"]:
                sys.stdout = stack.enter_context(open(conf.settings["xmlfile"], "w", encoding="utf-8"))
            elif conf.settings["xmlsock"]:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(conf.settings["xmlsock"])
                sys.stdout = stack.enter_context(sock.makefile("w"))

            log.debug("Loading service channels...")
            load_channels(providers, conf.settings["channelfile"], conf.settings["parallel"])

            log.debug("Loading requested channels...")
            for p in providers:
                p.load_req_channels()

            log.info("Writing xmltv.dtd header...")
            print('<?xml version="1.0" encoding="UTF-8"?>')
            print('<!DOCTYPE tv SYSTEM "xmltv.dtd">\n')
            print(f'<tv generator-info-name="{__title__} v{__version__}">')
            stack.callback(print, "</tv>")

            log.debug("Writing channels...")
            for p in providers:
                p.write_channels()

            log.debug("Getting EPG...")
            if conf.settings["parallel"]:
                with ThreadPoolExecutor() as exe:
                    for p in providers:
                        exe.submit(p.get_programs)
            else:
                for p in providers:
                    p.get_programs()

            log.debug("Writing programs...")
            for p in providers:
                p.write_programs()

            log.info("Done")
    elif conf.args["cmd"] == "update_channels":
        load_channels(providers, conf.settings["channelfile"], conf.settings["parallel"])
    else:
        raise NotImplementedError(f"Unknown command: {conf.args['cmd']}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
