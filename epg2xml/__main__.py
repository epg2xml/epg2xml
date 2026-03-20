import logging
import socket
import sys
from contextlib import ExitStack

from epg2xml.config import Config, ConfigHelpRequested, ConfigLoadError, ConfigUpgradeRequired
from epg2xml.providers import EPGHandler

log = logging.getLogger("MAIN")


def run():
    conf = Config()
    conf.load()

    log.debug("Loading providers...")
    h = EPGHandler(conf.configs)

    if (cmd := conf.args["cmd"]) in ["run", "fromdb"]:
        with ExitStack() as stack:
            xml_output = sys.stdout
            if xmlfile := conf.settings["xmlfile"]:
                xml_output = stack.enter_context(open(xmlfile, "w", encoding="utf-8"))
            elif xmlsock := conf.settings["xmlsock"]:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(xmlsock)
                xml_output = stack.enter_context(sock.makefile("w"))

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
            h.to_xml(writer=xml_output)

            log.info("Done")
    elif cmd == "update_channels":
        h.load_channels(conf.settings["channelfile"], conf.settings["parallel"])
    else:
        raise NotImplementedError(f"Unknown command: {cmd}")


def main():
    try:
        run()
    except (ConfigHelpRequested, ConfigUpgradeRequired):
        return 0
    except (ConfigLoadError, FileNotFoundError, ImportError):
        return 1
    except KeyboardInterrupt:
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
