import argparse
import errno
import json
import logging
import os
import sys
from copy import deepcopy
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Union

from epg2xml import __description__, __title__, __url__, __version__
from epg2xml.providers.all import PROVIDERS
from epg2xml.utils import dump_json, load_json

# suppress modules logging
logging.getLogger("requests").setLevel(logging.ERROR)
logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)
logging.getLogger("curl_cffi").setLevel(logging.ERROR)

logger = logging.getLogger("CONFIG")


class ConfigHelpRequested(Exception):
    pass


class ConfigUpgradeRequired(Exception):
    pass


class ConfigLoadError(Exception):
    pass


def setup_root_logger(
    *,
    handler: logging.Handler = None,
    formatter: logging.Formatter = None,
    level: Union[int, str] = None,
) -> None:
    if level is None:
        level = logging.INFO

    if handler is None:
        # logging to console, stderr by default
        handler = logging.StreamHandler()

    if formatter is None:
        log_fmt = "%(asctime)-15s %(levelname)-8s %(name)-7s: %(message)s"
        formatter = logging.Formatter(log_fmt, datefmt="%Y/%m/%d %H:%M:%S")

    handler.setFormatter(formatter)
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(level)


class Config:

    base_config = {
        "GLOBAL": {
            "ENABLED": True,
            "FETCH_LIMIT": 2,
            "ID_FORMAT": "{No}.{Source.lower()}",
            "ADD_REBROADCAST_TO_TITLE": False,
            "ADD_EPNUM_TO_TITLE": True,
            "ADD_DESCRIPTION": True,
            "ADD_XMLTV_NS": False,
            "ADD_CHANNEL_ICON": True,
            "HTTP_PROXY": None,
        },
        **{provider.name.upper(): {"MY_CHANNELS": []} for provider in PROVIDERS},
    }

    base_settings = {
        "config": {
            "argv": ["--config"],
            "env": "EPG2XML_CONFIG",
            "default": str(Path.cwd().joinpath("epg2xml.json")),
            "help": "config file path",
            "argparse": {"nargs": "?", "const": None},
        },
        "logfile": {
            "argv": ["--logfile"],
            "env": "EPG2XML_LOGFILE",
            "default": None,
            "help": "log file path",
            "argparse": {"nargs": "?", "const": None},
        },
        "loglevel": {
            "argv": ["--loglevel"],
            "env": "EPG2XML_LOGLEVEL",
            "default": "INFO",
            "help": "loglevel",
            "argparse": {"choices": ("DEBUG", "INFO", "WARNING", "ERROR")},
        },
        "channelfile": {
            "argv": ["--channelfile"],
            "env": "EPG2XML_CHANNELFILE",
            "default": str(Path.cwd().joinpath("Channel.json")),
            "help": "channel file path",
            "argparse": {"nargs": "?", "const": None},
        },
        "xmlfile": {
            "argv": ["--xmlfile"],
            "env": "EPG2XML_XMLFILE",
            "default": None,
            "help": "write output to file if specified",
            "argparse": {"nargs": "?", "const": None},
        },
        "xmlsock": {
            "argv": ["--xmlsock"],
            "env": "EPG2XML_XMLSOCK",
            "default": None,
            "help": "send output to unix socket if specified",
            "argparse": {"nargs": "?", "const": None},
        },
        "parallel": {
            "argv": ["--parallel"],
            "env": "EPG2XML_PARALLEL",
            "default": False,
            "help": "run in parallel",
            "argparse": {"action": "store_true"},
        },
        "dbfile": {
            "argv": ["--dbfile"],
            "env": "EPG2XML_DBFILE",
            "default": None,
            "help": "export/import data to/from db",
            "argparse": {"nargs": "?", "const": None},
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
        return deepcopy(self.base_config)

    def __inner_upgrade(self, settings1, settings2, key=None, overwrite=False):
        sub_upgraded = False
        merged = deepcopy(settings2)

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
                if isinstance(v, (dict, list)):
                    merged[k], did_upgrade = self.__inner_upgrade(
                        settings1[k], settings2[k], key=k, overwrite=overwrite
                    )
                    sub_upgraded = did_upgrade or sub_upgraded
                elif settings1[k] != settings2[k] and overwrite:
                    merged = deepcopy(settings1)
                    sub_upgraded = True
        elif isinstance(settings1, list) and key:
            for v in settings1:
                if v not in settings2:
                    merged.append(deepcopy(v))
                    sub_upgraded = True
                    logger.info("Added to config option %r: %s", str(key), str(v))
                    continue

        return merged, sub_upgraded

    def upgrade_configs(self, currents):
        fields_env = {}

        # ENV gets priority: ENV > config.json
        for name, _ in self.base_config.items():
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
        cfg_new = deepcopy(cfg_old)
        for p in cfg_new:
            # push items in GLOBAL as defaults
            for k, v in cfg_old["GLOBAL"].items():
                if k not in cfg_new[p]:
                    cfg_new[p][k] = deepcopy(v)
        del cfg_new["GLOBAL"]
        self.configs = cfg_new

    def load(self):
        logger.debug("Loading config...")
        if not Path(self.settings["config"]).exists():
            logger.info("No config file found. Creating a default one...")
            self.save(self.default_config)
            raise ConfigUpgradeRequired(self.settings["config"])

        try:
            cfg, upgraded = self.upgrade_configs(load_json(self.settings["config"]))

            # Save config if upgraded
            if upgraded:
                self.save(cfg)
                raise ConfigUpgradeRequired(self.settings["config"])

            self.load_with_hidden(cfg)
        except (json.decoder.JSONDecodeError, ValueError) as exc:
            logger.exception("Please check your config here: %s", self.settings["config"])
            raise ConfigLoadError(self.settings["config"]) from exc

    def save(self, cfg):
        dump_json(self.settings["config"], cfg)
        logger.info("Your config was upgraded. You may check the changes here: %r", self.settings["config"])

    def get_settings(self):
        setts = {}
        initial_loglevel = (
            self.args.get("loglevel")
            or os.environ.get(self.base_settings["loglevel"]["env"])
            or self.base_settings["loglevel"]["default"]
        )
        logging.getLogger().setLevel(initial_loglevel)
        for name, data in self.base_settings.items():
            # setting priority: arg -> env -> default
            try:
                if (value := self.args.get(name)) not in (None, False):
                    logger.debug("setting from ARG   %s=%s", data["argv"][-1], value)
                elif data["env"] in os.environ:
                    value = os.environ[data["env"]]
                    logger.debug("setting from ENV   %s=%s", data["env"], value)
                else:
                    value = data["default"]
                    logger.debug("setting by default %s=%s", name, value)

                setts[name] = value

            except KeyError:
                logger.exception("Exception raised on setting value: %r", name)

        # checking existance of important files' dir
        for argname in ["config", "logfile", "channelfile", "dbfile"]:
            filepath = setts[argname]
            if filepath is not None and not Path(filepath).parent.exists():
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), filepath)

        # handling of boolean args
        for argname in ["parallel"]:
            if isinstance(setts[argname], str):
                setts[argname] = setts[argname].lower() in ("y", "yes", "t", "true", "on", "1")

        # logging to file
        if setts["logfile"] is not None:
            fileHandler = RotatingFileHandler(setts["logfile"], maxBytes=2 * 1024**2, backupCount=5, encoding="utf-8")
            setup_root_logger(handler=fileHandler)

        # set configured log level
        logging.getLogger().setLevel(setts["loglevel"])

        return setts

    # Parse command line arguments
    def parse_args(self):
        parser = argparse.ArgumentParser(
            prog=__title__,
            description=__description__,
            epilog=f"Online help: <{__url__}>",
            formatter_class=argparse.RawTextHelpFormatter,
        )

        # Mode
        parser.add_argument(
            "cmd",
            metavar="command",
            choices=("run", "fromdb", "update_channels"),
            help="\n".join(
                (
                    '"run": XML 형식으로 출력',
                    '"fromdb": dbfile로부터 불러오기',
                    '"update_channels": 채널 정보 업데이트',
                )
            ),
        )

        # Display version info
        parser.add_argument(
            "-v",
            "--version",
            action="version",
            version=f"{__title__} v{__version__}",
        )

        for name, data in self.base_settings.items():
            help_text = data["help"]
            if data["default"]:
                help_text += f" (default: {data['default']})"
            kwargs = {"dest": name, "help": help_text, **data.get("argparse", {})}
            parser.add_argument(*data["argv"], **kwargs)

        # Print help by default if no arguments
        if len(sys.argv) == 1:
            parser.print_help()
            raise ConfigHelpRequested()

        return vars(parser.parse_args())


# logging
setup_root_logger()
