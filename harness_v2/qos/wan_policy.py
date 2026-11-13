"""Resolve the system timeout into environment substitutions for static QoS XML."""

import os


def resolve_wan_policy(environment):
    def positive_integer(name, default):
        value = environment.get(name, str(default))
        if not value.isascii() or not value.isdigit() or not 0 < int(value) <= 2_147_483_647:
            raise ValueError(f"{name} must be an integer from 1 to 2147483647")
        return int(value)

    timeout = positive_integer("WAN_TIMEOUT_SEC", 300)
    heartbeat = positive_integer("WAN_HB_PERIOD_SEC", 3)
    retries = (timeout + heartbeat - 1) // heartbeat
    if "WAN_HB_RETRIES" in environment and positive_integer("WAN_HB_RETRIES", retries) != retries:
        raise ValueError("WAN_HB_RETRIES is derived: ceil(WAN_TIMEOUT_SEC / WAN_HB_PERIOD_SEC)")
    return {"WAN_TIMEOUT_SEC": str(timeout), "WAN_HB_PERIOD_SEC": str(heartbeat),
            "WAN_HB_RETRIES": str(retries)}


def configure_wan_policy():
    policy = resolve_wan_policy(os.environ)
    os.environ.update(policy)
    return policy


if __name__ == "__main__":
    for name, value in configure_wan_policy().items():
        print(f"export {name}={value}")
