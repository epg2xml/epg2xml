import argparse
import json
import logging
import os
import sys
import errno
from copy import copy
from pathlib import Path

from epg2xml.utils import dump_json
from epg2xml import __version__, __title__, __description__, __url__

logger = logging.getLogger("CONFIG")


class Singleton(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)

        return cls._instances[cls]


class Config:
    __metaclass__ = Singleton

    base_config = {
        'GLOBAL': {
            'ENABLED': True,
            'FETCH_LIMIT': 2,
            'ID_FORMAT': '{ServiceId}.{Source.lower()}',
            'ADD_REBROADCAST_TO_TITLE': False,
            'ADD_EPNUM_TO_TITLE': True,
            'ADD_DESCRIPTION': True,
            'ADD_XMLTV_NS': False,
            'GET_MORE_DETAILS': False,
        },
        'KT': {
            'MY_CHANNELS': [],
        },
        'LG': {
            'MY_CHANNELS': [],
        },
        'SK': {
            'MY_CHANNELS': [],
        },
        'DAUM': {
            'MY_CHANNELS': [],
        },
        'NAVER': {
            'MY_CHANNELS': [],
        },
        'WAVVE': {
            'MY_CHANNELS': [],
        },
        'TVING': {
            'MY_CHANNELS': [],
        },
    }

    base_settings = {
        'config': {
            'argv': '--config',
            'env': 'EPG2XML_CONFIG',
            'default': str(Path.cwd().joinpath("epg2xml.json"))
        },
        'logfile': {
            'argv': '--logfile',
            'env': 'EPG2XML_LOGFILE',
            'default': None
        },
        'loglevel': {
            'argv': '--loglevel',
            'env': 'EPG2XML_LOGLEVEL',
            'default': 'INFO'
        },
        'channelfile': {
            'argv': '--channelfile',
            'env': 'EPG2XML_CHANNELFILE',
            'default': str(Path.cwd().joinpath("Channel.json"))
        },
        'xmlfile': {
            'argv': '--xmlfile',
            'env': 'EPG2XML_XMLFILE',
            'default': None
        },
        'xmlsock': {
            'argv': '--xmlsock',
            'env': 'EPG2XML_XMLSOCK',
            'default': None
        },
        'parallel': {
            'argv': '--parallel',
            'env': 'EPG2XML_PARALLEL',
            'default': False
        },
    }

    def __init__(self):
        """Initializes config"""
        # Args and settings
        self.args = self.parse_args()
        self.settings = self.get_settings()
        # Configs
        self.configs = None

    @property
    def default_config(self):
        """reserved for adding extra fields"""
        cfg = copy(self.base_config)
        return cfg

    def __inner_upgrade(self, settings1, settings2, key=None, overwrite=False):
        sub_upgraded = False
        merged = copy(settings2)

        if isinstance(settings1, dict):
            for k, v in settings1.items():
                # missing k
                if k not in settings2:
                    merged[k] = v
                    sub_upgraded = True
                    if not key:
                        logger.info("Added %r config option: %s", str(k), str(v))
                    else:
                        logger.info("Added %r to config option %r: %s", str(k), str(key), str(v))
                    continue

                # iterate children
                if isinstance(v, dict) or isinstance(v, list):
                    merged[k], did_upgrade = self.__inner_upgrade(
                        settings1[k], settings2[k], key=k, overwrite=overwrite
                    )
                    sub_upgraded = did_upgrade if did_upgrade else sub_upgraded
                elif settings1[k] != settings2[k] and overwrite:
                    merged = settings1
                    sub_upgraded = True
        elif isinstance(settings1, list) and key:
            for v in settings1:
                if v not in settings2:
                    merged.append(v)
                    sub_upgraded = True
                    logger.info("Added to config option %r: %s", str(key), str(v))
                    continue

        return merged, sub_upgraded

    def upgrade_configs(self, currents):
        fields_env = {}

        # ENV gets priority: ENV > config.json
        for name, data in self.base_config.items():
            if name in os.environ:
                # Use JSON decoder to get same behaviour as config file
                fields_env[name] = json.JSONDecoder().decode(os.environ[name])
                logger.debug("setting from ENV   --%s=%s", name, fields_env[name])

        # Update in-memory config with environment settings
        currents.update(fields_env)

        # Do inner upgrade
        upgraded_configs, upgraded = self.__inner_upgrade(self.base_config, currents)
        return upgraded_configs, upgraded

    def load_with_hidden(self, cfg_old):
        cfg_new = copy(cfg_old)
        for p in cfg_new:
            # push items in GLOBAL as defaults
            for k, v in cfg_old['GLOBAL'].items():
                if k not in cfg_new[p]:
                    cfg_new[p][k] = v
        del cfg_new['GLOBAL']
        self.configs = cfg_new

    def load(self):
        logger.debug("Loading config...")
        if not Path(self.settings["config"]).exists():
            logger.info("No config file found. Creating a default one...")
            self.save(self.default_config)

        try:
            with open(self.settings['config'], 'r', encoding='utf-8') as fp:
                cfg, upgraded = self.upgrade_configs(json.load(fp))

                # Save config if upgraded
                if upgraded:
                    self.save(cfg)
                    exit(0)

            self.load_with_hidden(cfg)
        except json.decoder.JSONDecodeError:
            logger.exception('Please check your config here: %s', self.settings['config'])
            exit(1)

    def save(self, cfg, exitOnSave=True):
        dump_json(self.settings['config'], cfg)
        if exitOnSave:
            logger.info(
                "Your config was upgraded. You may check the changes here: %r",
                self.settings['config']
            )

        if exitOnSave:
            exit(0)

    def get_settings(self):
        setts = {}
        for name, data in self.base_settings.items():
            # Argrument priority: cmd < environment < default
            try:
                value = None
                # Command line argument
                if self.args[name]:
                    value = self.args[name]
                    logger.debug("setting from ARG   --%s=%s", name, value)

                # Envirnoment variable
                elif data['env'] in os.environ:
                    value = os.environ[data['env']]
                    logger.debug("setting from ENV   --%s=%s" % (data['env'], value))

                # Default
                else:
                    value = data['default']
                    logger.debug("setting by default %s=%s" % (data['argv'], value))

                setts[name] = value

            except Exception:
                logger.exception("Exception raised on setting value: %r" % name)

        # checking existance of important files' dir
        for argname in ['config', 'logfile', 'channelfile']:
            filepath = setts[argname]
            if filepath is not None and not Path(filepath).parent.exists():
                logger.error(FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), filepath))
                sys.exit(1)

        return setts

    # Parse command line arguments
    def parse_args(self):
        parser = argparse.ArgumentParser(
            prog=__title__,
            description=__description__,
            epilog=f'Online help: <{__url__}>',
            formatter_class=argparse.RawTextHelpFormatter
        )

        # Mode
        parser.add_argument(
            'cmd',
            metavar='command',
            choices=('run', 'update_channels'),
            help=(
                '"run": XML 형식으로 출력\n'
                '"update_channels": 채널 정보 업데이트'
            )
        )

        # Display version info
        parser.add_argument(
            '-v', '--version',
            action='version',
            version='{} v{}'.format(__title__, __version__)
        )

        # Config file
        parser.add_argument(
            self.base_settings['config']['argv'],
            nargs='?',
            const=None,
            help='config file path (default: %s)' % self.base_settings['config']['default']
        )

        # Log file
        parser.add_argument(
            self.base_settings['logfile']['argv'],
            nargs='?',
            const=None,
            help='log file path (default: %s)' % self.base_settings['logfile']['default']
        )

        # Log level
        parser.add_argument(
            self.base_settings['loglevel']['argv'],
            choices=('DEBUG', 'INFO', 'WARNING', 'ERROR'),
            help='loglevel (default: %s)' % self.base_settings['loglevel']['default']
        )

        # Channel file
        parser.add_argument(
            self.base_settings['channelfile']['argv'],
            nargs='?',
            const=None,
            help='channel file path (default: %s)' % self.base_settings['channelfile']['default']
        )

        # XML file
        parser.add_argument(
            self.base_settings['xmlfile']['argv'],
            nargs='?',
            const=None,
            help='write output to file if specified'
        )

        # XML socket
        parser.add_argument(
            self.base_settings['xmlsock']['argv'],
            nargs='?',
            const=None,
            help='send output to unix socket if specified'
        )

        # Run in Parallel
        parser.add_argument(
            self.base_settings['parallel']['argv'],
            action='store_true',
            help='run in parallel (experimental)'
        )

        # Print help by default if no arguments
        if len(sys.argv) == 1:
            parser.print_help()

            sys.exit(0)

        else:
            return vars(parser.parse_args())
