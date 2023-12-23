import logging
import socket
import sys
from contextlib import ExitStack

from epg2xml.config import Config
from epg2xml.providers import EPGHandler

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
    h = EPGHandler(conf.configs)

    if (cmd := conf.args["cmd"]) in ["run", "fromdb"]:
        with ExitStack() as stack:
            # redirecting stdout to...
            if xmlfile := conf.settings["xmlfile"]:
                sys.stdout = stack.enter_context(open(xmlfile, "w", encoding="utf-8"))
            elif xmlsock := conf.settings["xmlsock"]:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(xmlsock)
                sys.stdout = stack.enter_context(sock.makefile("w"))

            if cmd == "fromdb":
                log.debug("Importing from dbfile...")
                h.from_db(conf.settings["dbfile"])
            else:
                log.debug("Loading service channels...")
                h.load_channels(conf.settings["channelfile"], conf.settings["parallel"])

                log.debug("Loading requested channels...")
                h.load_req_channels()

                log.debug("Getting EPG...")
                h.get_programs(conf.settings["parallel"])

                if (dbfile := conf.settings["dbfile"]) is not None:
                    log.debug("Exporting to dbfile...")
                    h.to_db(dbfile)

            log.info("Writing xmltv.dtd header...")
            h.to_xml()

            log.info("Done")
    elif cmd == "update_channels":
        h.load_channels(conf.settings["channelfile"], conf.settings["parallel"])
    else:
        raise NotImplementedError(f"Unknown command: {cmd}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
