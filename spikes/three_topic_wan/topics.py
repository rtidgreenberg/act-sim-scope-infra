"""Original-topic routing for this bounded transport prototype, not the production catalog."""

TOPIC_CLASSES = {
    "ContactReport": "Contacts",
    "ControlCommand": "Event",
    "PlatformCommandAck": "Event",
    "RouterHealth": "Status",
}

COMPARISON_CASES = ("baseline", "bands_all", "bands_last", "none_all", "mixed_last",
                    "bands_no_suppression", "mixed_all", "mixed_no_suppression",
                    "split_keep_all")

TRADEOFF_CASES = ("mixed_all", "split_keep_all", "bands_all",
                 "mixed_no_suppression", "bands_no_suppression")


def topic_class(original_topic):
    try:
        return TOPIC_CLASSES[original_topic]
    except KeyError as error:
        raise ValueError(f"Unmapped prototype topic: {original_topic}") from error
