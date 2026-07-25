"""Built-in providers for Javis — user plugins override these."""
from tools.provider_loader import ProviderLoader, ProviderProfile

ProviderConfig = ProviderProfile
ProviderPlugin = ProviderProfile

__all__ = [
    "ProviderLoader",
    "ProviderProfile",
    "ProviderConfig",
    "ProviderPlugin",
]

# User can create providers/<name>.py with same interface to override
