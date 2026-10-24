# The GitHub dependency heartbeat. See ../../docs/github-heartbeat.md.
#
# gh and jq are the point of this image: the classifier is the tuned bash + jq
# logic from suhlig/dotfiles, and both tools are absent from the Home Assistant
# container. mosquitto-clients publishes the verdict. Alpine is only the runtime.
FROM alpine:3.22

RUN apk add --no-cache \
      bash \
      ca-certificates \
      github-cli \
      jq \
      mosquitto-clients \
    && mkdir -p /run/secrets /var/lib/github-heartbeat \
    && chown 65534:65534 /var/lib/github-heartbeat

COPY heartbeat.sh /usr/local/bin/github-heartbeat
RUN chmod 0755 /usr/local/bin/github-heartbeat

# gh wants a writable home for its config even when GH_TOKEN is set.
ENV HOME=/var/lib/github-heartbeat \
    GH_NO_UPDATE_NOTIFIER=1 \
    MQTT_BASE_TOPIC=github/heartbeat

USER 65534:65534
ENTRYPOINT ["/usr/local/bin/github-heartbeat"]
