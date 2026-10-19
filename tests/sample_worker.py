"""Test-only entry point for exercising the real worker protocol with fixtures."""
from unittest.mock import patch

from jmsbrowser.worker import main
from tools.sample_data import SampleAdapter


if __name__ == "__main__":
    with patch("jmsbrowser.brokers.create_adapter", SampleAdapter):
        main()
