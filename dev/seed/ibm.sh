#!/bin/bash
set -euo pipefail

marker=/seed-state/ibm-v1
if [ -f "$marker" ]; then
    echo "IBM MQ samples already seeded."
    exit 0
fi

password=$(tr -d '\r\n' < /run/secrets/mqAppPassword)
for folder in /messages/*; do
    queue=$(basename "$folder")
    for file in "$folder"/*.json; do
        message=$(tr -d '\r\n' < "$file")
        # amqsputc prompts for the app password before reading message lines.
        output=$(printf '%s\n%s\n\n' "$password" "$message" | \
            /opt/mqm/samp/bin/amqsputc "$queue" QM1)
        printf '%s\n' "$output"
        # MQ sample programs can print MQ errors without a non-zero exit code.
        if printf '%s\n' "$output" | grep -Eq 'reason code|MQ[A-Z]+ ended'; then
            echo "Failed to seed $queue" >&2
            exit 1
        fi
    done
done

touch "$marker"
echo "IBM MQ samples seeded: 3 orders, 2 payments, 1 notification."
