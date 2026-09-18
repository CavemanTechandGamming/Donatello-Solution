"""Package entry: ``python -m src``."""

from src.core.logging_setup import get_logger, install_excepthook, setup_logging
from src.ui.app import run


def main() -> None:
    setup_logging()
    install_excepthook()
    get_logger("main").info("Starting Donatello Solution")
    run()


if __name__ == "__main__":
    main()
