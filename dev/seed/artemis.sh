#!/bin/sh
set -eu

marker=/seed-state/artemis-v1
if [ -f "$marker" ]; then
    echo "Artemis samples already seeded."
    exit 0
fi

for folder in /messages/*; do
    queue=$(basename "$folder")
    for file in "$folder"/*.json; do
        message=$(tr -d '\r\n' < "$file")
        /opt/artemis/bin/artemis producer \
            --url tcp://artemis:61616 \
            --user "$ARTEMIS_USER" --password "$ARTEMIS_PASSWORD" \
            --destination "queue://$queue" \
            --message-count 1 --message "$message"
    done
done

touch "$marker"
echo "Artemis samples seeded: 3 orders, 2 payments, 1 notification."
