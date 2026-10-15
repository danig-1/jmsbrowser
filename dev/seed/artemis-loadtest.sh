#!/bin/sh
set -eu

marker=/seed-state/artemis-loadtest-v1
if [ -f "$marker" ]; then
    echo "Artemis DEV.LOADTEST already seeded."
    exit 0
fi

# Artemis auto-creates this durable queue. Commit the 1,000-message fixture together.
# The CLI supplies a distinct JMS message ID and count property (0..999) per message.
message=$(tr -d '\r\n' < /loadtest-message.json)
/opt/artemis/bin/artemis producer \
    --url tcp://artemis:61616 \
    --user "$ARTEMIS_USER" --password "$ARTEMIS_PASSWORD" \
    --destination queue://DEV.LOADTEST \
    --message-count 1000 --commit-interval 1000 --message "$message"

touch "$marker"
echo "Artemis DEV.LOADTEST seeded with 1000 persistent test messages."
