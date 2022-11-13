__title__ = "epg2xml"
__description__ = "웹 상의 소스를 취합하여 EPG를 만드는 프로그램"
__url__ = "https://github.com/epg2xml/epg2xml"

try:
    from ._version import version
except ImportError:
    try:
        from setuptools_scm import get_version

        version = get_version(version_scheme="release-branch-semver")
    except Exception:
        version = "2.5.0.dev0"

__version__ = version
