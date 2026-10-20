# Implementation notes

The selected design is **3 — Payload reader**, with a message table replacing
the original strip. Columns are **MessageId, Datetime, Size, Body**.
Datetime uses the JMS timestamp in local time; type and priority remain in metadata.
Overlay windows and composer file-path dialogs have clickable X close controls.
Narrow connection and queue panels leave the remaining width for that table
and selected-message detail. `docs/designs/04-implemented-reader.png` exports the
implemented character-grid renderer with real Artemis sample messages.
The message list and inline detail split section 3 equally in height, excluding
the shared separator and rounding to whole terminal rows. Both detail layouts
default to Headers, with tab order Headers, Properties, Body.
The Size label replaces Message size. In-app help and its ? shortcut have been removed.
The queue table includes sortable consumer counts, refreshed together with depth.
Artemis uses ConsumerCount; IBM MQ uses open-input handles. Panel titles have no
number prefixes, and the connection-count summary is omitted.

The r shortcut in the queue panel updates all queue depths and consumer counts.
Enter refreshes the selected queue's statistics and loads its messages, retaining
queue focus. Lists use Up / Down, with no Left / Right navigation. Detail
tabs are clickable in both layouts, and Tab / Shift+Tab cycles them while a
message is expanded. Queue routing labels and the breadcrumb row are omitted.
Detail tabs use fixed labels and highlight backgrounds without changing their
positions. Selected queue inquiries replace the depth number with `...` while
pending. IBM reuses its inquiry connection/PCF agent; the local Jolokia URL uses
IPv4 loopback to avoid the measured Windows IPv6 connection fallback delay.

The queue panel has no Refresh or Consumers buttons; shortcuts remain in the
bottom bar. Consumer display option 2 is a modal queue inspector, available
via c even on empty queues. Press c again in the inspector to refresh consumers;
r has no action there. It offers explicit refresh,
pagination, full consumer/connection details and keyboard/mouse navigation.
Asynchronous consumer inquiries have their own generation checks; permission
errors show Unavailable without interrupting message browsing.
Headers and Properties use right-aligned keys and left-aligned values in a table.
JMS timestamps retain raw milliseconds plus a local datetime with UTC offset.
[Headers screenshot](designs/headers-implemented.png) and
[Properties screenshot](designs/properties-implemented.png) use actual Artemis metadata.

## Structure

- `config.py`: strict YAML validation and environment credentials.
- `domain.py`: queue/message data, original bodies, metadata and byte sizes.
- `outgoing.py`: validated TextMessage drafts, typed JMS values and draft files.
- `composer.py`, `composer_screen.py`: modal editing, file operations, explicit
  review/send and result display, shared between curses and PNG exports.
- `brokers.py`: real Artemis and IBM adapters using JMS QueueBrowser enumeration.
- `runtime.py`, `client-lock.json`: pinned provider libraries and JVM setup.
- `worker.py`, `client.py`: persistent per-connection subprocesses, JSON protocol,
  isolated JVM classpaths, request deadlines and connection-loss events.
- `state.py`: asynchronous dispatch and checks that discard obsolete results.
- `screen.py`: character-grid rendering, filtering, pagination, mouse hit testing,
  Unicode/control-character handling and an ASCII fallback.
- `ui.py`: curses output, keyboard/mouse controls, resizing and detail scrolling.
- `cli.py`: interactive startup, configuration/runtime checks, JSON and PNG export.
- `tools/sample_data.py`: fixtures for tests and developer screenshot tools.

## Provider behavior

Both providers use `javax.jms.QueueBrowser`. The application never receives or
acknowledges business messages. The composer sends TextMessages through a JMS
producer only after explicit review/send. Browser handles close after enumeration;
producer handles close after sending. Other applications can change queue contents.

Artemis discovers queues and depths through authenticated Jolokia search/read.
IBM uses PCF inquiries; temporary PCF reply queues are excluded from the UI.
Artemis consumer queries exclude browse-only handles and enrich queue metadata
with the broker's filtered consumer view and matching session view when authorized.
The consumer list shows session user and session consumer/producer counts,
remote address and consumer creation time. The inspector uses only X to close.
IBM consumer inquiries
use queue handle status filtered to input handles; process/thread fields are
shown without claiming a unique consumer ID or selector that MQ does not expose.
The development app account has display and command-request/reply permissions
without object-administration permissions. An explicit IBM `queues` list avoids
PCF discovery and uses ordinary queue inquiry. PCF's internal request/reply
traffic is separate from business-message browsing.

Each connection owns a Python worker process and JVM. Blocking calls stay off
the terminal thread and provider classpaths remain isolated. A request deadline
terminates a stuck worker; the user can reconnect. ExceptionListener events
clear connected status after provider-detected connection loss. Pending results
are discarded after disconnects or queue selection changes.
Send jobs preserve their original destination and submitted payload, prevent
concurrent duplicate dispatch, and report uncertain delivery after a timeout or
connection loss instead of automatically retrying. Success refreshes the selected
queue's depth and message view.

Text preserves the original body and displays formatted JSON when possible.
Text size is UTF-8 payload bytes; byte messages use getBodyLength and show hex.
Map and stream values display as JSON with unknown encoded sizes. ObjectMessage
contents are not deserialized. Sizes exclude header and transport overhead.
Preview truncation retains the full text/byte payload-size value.

## Validation

Unit tests cover configuration, all columns at multiple terminal sizes, keyboard
and mouse selection, filters, scrolling, clipped IBM IDs, Unicode/control
characters, empty queues, ASCII mode, deadlines, connection loss and stale
asynchronous results. Payload tests cover text/byte/map/stream/object handling,
UTF-8 sizes, partial byte reads and avoiding object deserialization.

Optional integration tests use actual application workers against both Compose
brokers. They browse all sample queues twice, compare IDs/content/priorities/sizes,
check empty queues and limits, exclude internal reply queues, and compare depths
before and after. The provider-tool smoke test remains in tools/verify_brokers.py.
Composer tests cover text editing, modal isolation, keyboard/mouse controls,
draft files, validation, typed setters and single explicit dispatch. Sending
integration tests create fresh UUID queues on both providers, validate headers,
all eight property types, persistence, priority and expiry, and remove only those
new test queues after closing their handles. Existing fixture depths are preserved.
Windows curses startup, rendering, resizing and navigation were also exercised
in a terminal. Linux/macOS terminal behavior has not been validated on those OSes.

## Local environment

Compose runs pinned Artemis 2.57.0 and IBM MQ 9.4.5.0 developer images, four
matching sample queues, health checks and producers for six JSON messages per
broker. IBM also creates standard development queues. Artemis additionally seeds
DEV.LOADTEST with 1000 persistent messages, and
the local example browse limit is 1000. The generic application default is 100.
The three original consumer mockups are in CONSUMER_OPTIONS.md; option 2 is implemented.
Named volumes preserve data and seed markers; published ports bind to loopback. See README.md for
installation, launching, permissions, credentials, resetting and testing.

## Later work

The TextMessage composer is implemented. Object deserialization is
outside the browser's scope. XML text currently displays without reformatting.
Three proposed additions are illustrated in README.md: JMS selector filtering,
queue-depth history, and a TextMessage composer with send preview. The generator
tools/render_feature_options.py renders their mockups with example data and no
broker operations; selector filtering and queue history remain proposals.
