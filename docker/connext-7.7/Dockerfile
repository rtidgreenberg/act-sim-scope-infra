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
        curl \
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

RUN curl -fsSL -o /usr/share/keyrings/rti-official-archive.gpg \
        https://packages.rti.com/deb/official/repo.key \
    && printf 'deb [arch=%s signed-by=%s] %s %s main\n' \
        "$(dpkg --print-architecture)" \
        /usr/share/keyrings/rti-official-archive.gpg \
        https://packages.rti.com/deb/official \
        "$(. /etc/os-release && echo "$VERSION_CODENAME")" \
        > /etc/apt/sources.list.d/rti.list \
    && printf '%s\n' \
        'rti-connext-dds-7.7.0-common rti-connext-dds-7.7.0/license/accepted select true' \
        | debconf-set-selections \
    && apt-get update \
    && apt-get install --no-install-recommends -y rti-connext-dds-7.7.0 \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir \
    "rti.connext==${CONNEXT_VERSION}"

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