FROM ubuntu:24.04

LABEL org.opencontainers.image.source="https://github.com/Shashkist/Learning_factory" \
      org.opencontainers.image.description="Learning Factory terminal lab image (Ubuntu + ttyd)" \
      lf="1"

ARG TTYD_VERSION=1.7.7
ARG TTYD_SHA256=8a217c968aba172e0dbf3f34447218dc015bc4d5e59bf51db2f2cd12b7be4f55

# ubuntu:24.04 is minimized: man pages are excluded by dpkg and /usr/bin/man is a stub.
# sudo is installed, but there is deliberately no sudoers entry for student.
RUN rm -f /etc/dpkg/dpkg.cfg.d/excludes \
 && if dpkg-divert --list /usr/bin/man | grep -q .; then \
      dpkg-divert --quiet --remove --rename /usr/bin/man; \
    fi \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates tini sudo nano vim less tree curl dnsutils iputils-ping traceroute \
      iproute2 net-tools procps file man-db \
 && apt-get install -y --no-install-recommends --reinstall \
      coreutils findutils grep sed tar gzip bash util-linux passwd procps \
 && rm -rf /var/lib/apt/lists/* \
 && curl -fsSL -o /usr/local/bin/ttyd \
      "https://github.com/tsl0922/ttyd/releases/download/${TTYD_VERSION}/ttyd.x86_64" \
 && echo "${TTYD_SHA256}  /usr/local/bin/ttyd" | sha256sum -c - \
 && chmod 0755 /usr/local/bin/ttyd \
 && useradd --create-home --home-dir /home/student --shell /bin/bash student \
 && mkdir -p /var/log/lf \
 && chown root:root /var/log/lf \
 && chmod 0700 /var/log/lf

COPY entrypoint.sh /usr/local/bin/lf-entrypoint
RUN chmod 0755 /usr/local/bin/lf-entrypoint

ENV LF_BASE_PATH=/
EXPOSE 7681
# ttyd runs as root on purpose: `su - student` needs it, and the log dir is root-only.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/lf-entrypoint"]
