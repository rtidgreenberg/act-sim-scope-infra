"""Experimental logical-topic priorities, applied to writes and lifecycle samples."""

DEFAULT_PRIORITIES = {"Mission": 100, "PlatformCommandAck": 100, "ContactReport": 10}
TOPIC_PRIORITY_MODES = ("sample_priority", "priority_channels", "priority_bands",
                        "priority_bands_batch", "priority_bands_repair", "priority_bands_last1")
PRIORITY_MODES = ("production", "paced_fifo", *TOPIC_PRIORITY_MODES)


def topic_priorities(overrides):
    priorities = dict(DEFAULT_PRIORITIES)
    for override in overrides:
        topic, separator, value = override.partition("=")
        if (not separator or topic not in priorities or not value.isascii()
                or not value.isdigit() or not 1 <= int(value) <= 2_147_483_647):
            raise ValueError("Priority must be a supported original topic name=positive 32-bit integer")
        priorities[topic] = int(value)
    return priorities
