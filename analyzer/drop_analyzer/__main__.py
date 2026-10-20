from .config import Config
from . import __version__
from .runtime import ConfigurationError, emit
from .worker import run


def main(argv=None):
    try:
        run(Config.load(argv))
    except ConfigurationError:
        emit(
            "drop-analyzer",
            __version__,
            "configuration_invalid",
            level="error",
            stage="startup",
            error_code="CONFIGURATION_INVALID",
        )
        return 1
    except Exception:
        emit(
            "drop-analyzer",
            __version__,
            "worker_startup_failed",
            level="error",
            stage="startup",
            error_code="STARTUP_FAILED",
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
