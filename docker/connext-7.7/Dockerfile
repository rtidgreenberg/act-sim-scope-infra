FROM ubuntu:22.04

ARG CONNEXT_VERSION=7.7.0
ARG ACT_REPO_URL=https://github.com/rtidgreenberg/act-sim-scope-infra.git
ARG ACT_REPO_REF=

ENV DEBIAN_FRONTEND=noninteractive \
    NDDSHOME=/opt/rti.com/rti_connext_dds-${CONNEXT_VERSION} \
    RTI_LICENSE_FILE=/shared/rti_license.dat

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ca-certificates \
        git \
        gcc \
        g++ \
        make \
        cmake \
        iproute2 \
        libglib2.0-0 \
        libgtk2.0-0 \
        openjdk-11-jre-headless \
        python3 \
        python3-aiohttp \
        python3-pip \
        tshark \
    && rm -rf /var/lib/apt/lists/*

COPY --from=connext-host bin /opt/rti.com/rti_connext_dds-7.7.0/bin
COPY --from=connext-host doc /opt/rti.com/rti_connext_dds-7.7.0/doc
COPY --from=connext-host include /opt/rti.com/rti_connext_dds-7.7.0/include
COPY --from=connext-host lib /opt/rti.com/rti_connext_dds-7.7.0/lib
COPY --from=connext-host resource /opt/rti.com/rti_connext_dds-7.7.0/resource
COPY --from=connext-host third_party /opt/rti.com/rti_connext_dds-7.7.0/third_party
COPY --from=connext-host rti_versions.xml /opt/rti.com/rti_connext_dds-7.7.0/rti_versions.xml

RUN python3 -m pip install --no-cache-dir \
    "$NDDSHOME"/resource/python_api/rti_connext-${CONNEXT_VERSION}-cp310-*.whl

RUN printf '%s\n' \
    'export NDDSHOME=/opt/rti.com/rti_connext_dds-7.7.0' \
    'export RTI_LICENSE_FILE=/shared/rti_license.dat' \
    'export PATH="$NDDSHOME/bin:$PATH"' \
    > /etc/profile.d/connext.sh

RUN if [ -n "$ACT_REPO_REF" ]; then \
        git clone --depth 1 --branch "$ACT_REPO_REF" "$ACT_REPO_URL" /opt/act-sim-scope-infra; \
    else \
        git clone --depth 1 "$ACT_REPO_URL" /opt/act-sim-scope-infra; \
    fi \
    && rm -rf /opt/act-sim-scope-infra/.git

WORKDIR /opt/act-sim-scope-infra
VOLUME ["/shared", "/workspace"]

CMD ["sleep", "infinity"]