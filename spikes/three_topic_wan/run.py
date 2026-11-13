#!/usr/bin/env python3
"""Use the existing frozen-input, private-network container lifecycle."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "instance_churn_reconnect"))
from run import main


if __name__ == "__main__":
    main(contract="three_topic")
