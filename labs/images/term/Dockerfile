FROM ubuntu:24.04

LABEL org.opencontainers.image.source="https://github.com/Shashkist/Learning_factory" \
      org.opencontainers.image.description="Learning Factory terminal lab image (Ubuntu + ttyd)" \
      lf="1"

# Space-separated Ubuntu mirrors, written with priority:1 so apt tries them before archive.ubuntu.com (the given mirrors are tried in random order among themselves, then archive.ubuntu.com); empty keeps the stock apt sources (#545).
ARG APT_MIRRORS=""
ARG TTYD_VERSION=1.7.7
ARG TTYD_SHA256=8a217c968aba172e0dbf3f34447218dc015bc4d5e59bf51db2f2cd12b7be4f55

# ubuntu:24.04 is minimized: man pages are excluded by dpkg and /usr/bin/man is a stub.
# sudo is installed, but there is deliberately no sudoers entry for student.
# apt retries and times out instead of hanging when a mirror stops answering (#227, #235).
RUN printf 'Acquire::Retries "3";\nAcquire::http::Timeout "30";\n' > /etc/apt/apt.conf.d/80-lf-retries \
 && rm -f /etc/dpkg/dpkg.cfg.d/excludes \
 && if dpkg-divert --list /usr/bin/man | grep -q .; then \
      dpkg-divert --quiet --remove --rename /usr/bin/man; \
    fi \
 && if [ -n "$APT_MIRRORS" ]; then \
      cp /etc/apt/sources.list.d/ubuntu.sources /etc/apt/ubuntu.sources.lf-orig \
      && ( \
        set -f; \
        for m in $APT_MIRRORS; do \
          printf '%s\tpriority:1\n' "$m"; \
        done \
      ) > /etc/apt/lf-mirrors.txt \
      && echo http://archive.ubuntu.com/ubuntu/ >> /etc/apt/lf-mirrors.txt \
      && sed -i 's|^URIs:.*|URIs: mirror+file:/etc/apt/lf-mirrors.txt|' /etc/apt/sources.list.d/ubuntu.sources; \
    fi \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates tini sudo nano vim less tree curl dnsutils iputils-ping traceroute \
      iproute2 net-tools procps file man-db \
 && apt-get install -y --no-install-recommends --reinstall \
      coreutils findutils grep sed tar gzip bash util-linux passwd procps \
 && if [ -f /etc/apt/ubuntu.sources.lf-orig ]; then \
      mv /etc/apt/ubuntu.sources.lf-orig /etc/apt/sources.list.d/ubuntu.sources; \
    fi \
 && rm -f /etc/apt/lf-mirrors.txt \
 && rm -rf /var/lib/apt/lists/* \
 && curl -fsSL --retry 3 --max-time 120 -o /usr/local/bin/ttyd \
      "https://github.com/tsl0922/ttyd/releases/download/${TTYD_VERSION}/ttyd.x86_64" \
 && echo "${TTYD_SHA256}  /usr/local/bin/ttyd" | sha256sum -c - \
 && chmod 0755 /usr/local/bin/ttyd \
 && useradd --uid 1001 --create-home --home-dir /home/student --shell /bin/bash student \
 && groupadd team \
 && usermod -aG team student \
 && mkdir -p /var/log/lf \
 && chown root:root /var/log/lf \
 && chmod 0700 /var/log/lf

COPY entrypoint.sh /usr/local/bin/lf-entrypoint
RUN chmod 0755 /usr/local/bin/lf-entrypoint

ENV LF_BASE_PATH=/
EXPOSE 7681
# ttyd runs as root on purpose: `su - student` needs it, and the log dir is root-only.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/lf-entrypoint"]
