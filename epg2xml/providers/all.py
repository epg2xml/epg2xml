from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    class_name: str


PROVIDERS = (
    ProviderSpec("kt", "KT"),
    ProviderSpec("lg", "LG"),
    ProviderSpec("sk", "SK"),
    ProviderSpec("daum", "DAUM"),
    ProviderSpec("naver", "NAVER"),
    ProviderSpec("tving", "TVING"),
    ProviderSpec("wavve", "WAVVE"),
    ProviderSpec("spotv", "SPOTV"),
    ProviderSpec("kbs", "KBS"),
    ProviderSpec("mbc", "MBC"),
    ProviderSpec("sbs", "SBS"),
)


PROVIDERS_BY_NAME = {provider.name: provider for provider in PROVIDERS}


def get_provider_spec(name: str) -> ProviderSpec:
    key = name.strip()
    return PROVIDERS_BY_NAME.get(key.lower())
