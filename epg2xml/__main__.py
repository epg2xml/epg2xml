import sys
import json
import socket
import logging
from logging.handlers import RotatingFileHandler
from concurrent.futures import ThreadPoolExecutor, as_completed

if sys.version_info[:2] < (3, 6):
    sys.exit("python 3.6+에서 실행하세요.")

from epg2xml.config import Config
from epg2xml.providers import load_providers, load_channels
from epg2xml import __version__, __title__

############################################################
# INIT
############################################################

# logging
log_fmt = "%(asctime)-15s %(levelname)-8s %(name)-7s %(lineno)4d: %(message)s"
formatter = logging.Formatter(log_fmt, datefmt='%Y/%m/%d %H:%M:%S')
rootLogger = logging.getLogger()
rootLogger.setLevel(logging.INFO)

# suppress modules logging
logging.getLogger('requests').setLevel(logging.ERROR)
logging.getLogger('urllib3.connectionpool').setLevel(logging.ERROR)

# logging to console, stderr by default
consolehandler = logging.StreamHandler()
consolehandler.setFormatter(formatter)
rootLogger.addHandler(consolehandler)

# load initial config
conf = Config()

if conf.settings['logfile'] is not None:
    # logging to file
    filehandler = RotatingFileHandler(
        conf.settings['logfile'],
        maxBytes=1024 * 1024 * 2,
        backupCount=5,
        encoding='utf-8'
    )
    filehandler.setFormatter(formatter)
    rootLogger.addHandler(filehandler)

# set configured log level
rootLogger.setLevel(conf.settings['loglevel'])

# load config file
conf.load()

# logger
log = rootLogger.getChild("MAIN")

############################################################
# MAIN
############################################################


def main():
    log.debug('Loading providers ...')
    providers = load_providers(conf.configs)
    try:
        log.debug('Trying to load cached channels from json')
        with open(conf.settings['channelfile'], 'r', encoding='utf-8') as fp:
            channeljson = json.load(fp)
    except (json.decoder.JSONDecodeError, FileNotFoundError) as e:
        log.debug(f'Failed to load cached channels from json: {str(e)}')
        channeljson = {}

    if conf.args['cmd'] == 'run':
        # redirecting stdout to ...
        if conf.settings['xmlfile']:
            try:
                sys.stdout = open(conf.settings['xmlfile'], 'w', encoding='utf-8')
            except FileNotFoundError as e:
                log.error(str(e))
                sys.exit(1)
        elif conf.settings['xmlsock']:
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(conf.settings['xmlsock'])
                sys.stdout = sock.makefile('w')
            except socket.error as e:
                log.error(str(e))
                sys.exit(1)

        log.debug('Loading service channels ...')
        load_channels(providers, conf, channeljson=channeljson)

        log.debug('Loading MY_CHANNELS ...')
        for p in providers:
            p.load_my_channels()

        log.info('Writing xmltv.dtd header ...')
        print('<?xml version="1.0" encoding="UTF-8"?>')
        print('<!DOCTYPE tv SYSTEM "xmltv.dtd">\n')
        print(f'<tv generator-info-name="{__title__} v{__version__}">')

        log.debug('Writing channel headers ...')
        for p in providers:
            p.write_channel_headers()

        log.debug('Getting EPG ...')
        if conf.settings['parallel']:
            with ThreadPoolExecutor() as exe:
                f2p = {exe.submit(p.get_programs, lazy_write=True): p for p in providers}
                for future in as_completed(f2p):
                    p = f2p[future]
                    p.write_programs()
        else:
            for p in providers:
                if p.req_channels:
                    p.get_programs()

        print('</tv>')
        log.info('Done.')
        sys.exit(0)
    elif conf.args['cmd'] == 'update_channels':
        load_channels(providers, conf, channeljson=channeljson)
    else:
        log.error("Unknown command.")
        sys.exit(1)


if __name__ == "__main__":
    main()
