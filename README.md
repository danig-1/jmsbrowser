# JMS Browser

Python curses queue browser for **IBM MQ** and **Apache Artemis**, implementing
option 3's payload-reader layout. Browse messages without removing them from queues.
Compose and explicitly send TextMessages to configured queues on either provider.
The right panel shows **MessageId, Type, Prio, Size, Body** and a selected
message's body, headers and properties below the table.

![Implemented adjustable panels, focus styling and body reader with offline sample data](docs/designs/body-wrap-implemented.png)

## Install and run

Python 3.11+ is required. Real broker connections use JMS provider clients through
JPype and require a JVM. The optional `java` extra supplies a private runtime
inside the virtual environment; an existing Java 21+ installation with `JAVA_HOME`
also works. Offline demo mode does not start Java or access a broker.

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m jmsbrowser --install-clients
.\.venv\Scripts\python.exe run.py --demo
```

Linux/macOS:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m jmsbrowser --install-clients
python run.py --demo
```

`requirements.txt` explicitly lists the runtime packages, including the bundled
Java runtime and a Windows-only marker for `windows-curses`. Run the application
from this checkout with `python run.py`. For an installed `jmsbrowser` command,
also run `python -m pip install -e .`. If you use your own Java 21+ installation,
you can omit the `jdk4py` line from the requirements.

Start the broker containers separately from the project directory:

```sh
docker compose up -d
```

Or start the same broker fixtures with the Podman Compose file:

```sh
podman compose --file compose.podman.yaml up -d
```

Start the browser directly with Python on Windows:

```powershell
.\.venv\Scripts\python.exe run.py
```

With an activated virtual environment (Windows, Linux or macOS):

```sh
python run.py
```

`run.py` loads the local Compose configuration and development credentials,
preserves credentials already set in the environment, and starts only the browser.
It does not start or stop containers. Additional browser options work as usual,
for example `python run.py --connection "Local IBM MQ" --queue DEV.ORDERS`.

For another configuration, set its credentials and invoke the module directly:

```powershell
$env:ARTEMIS_PASSWORD = 'artemis-local-123'
$env:MQ_APP_PASSWORD = 'app-local-123'
.\.venv\Scripts\python.exe -m jmsbrowser --config config.example.yaml
```

On Linux/macOS, export the same two environment variables, then use
`python -m jmsbrowser --config config.example.yaml`. Select a server and press Enter to
connect. Tab or a mouse click moves between the three panels. In expanded message
detail, Tab / Shift+Tab cycles Headers, Properties and Body; these tabs are also
clickable in both detail layouts. Headers is selected by default. The message
list and inline detail each occupy half of section 3's height (rounded to whole
terminal rows). Minimum terminal
size is 120 columns × 28 rows; 160 × 46 leaves more room for message contents.

Drag either vertical divider to resize its adjacent panels. The small double
arrow marks the drag area; the bordering cells also accept a drag. Widths are
kept for the current session, with minimum sizes that preserve all message
columns. Double-click either divider to reset the widths; Escape cancels a drag.
Terminal resizing clamps the layout to the available space. The message
list/detail height split remains 50/50.

The active panel has a bright title and an arrow on its selected row. Selections
in other panels stay visible with a quieter background. Mouse movement alone
does not change keyboard focus.

Both Body views support soft wrapping and JSON/XML syntax colors, enabled by
default. Click the Wrap / Color controls or press **w** / **s** while Body is
selected in the message panel. Wrapped continuations use `>` instead of repeating
the line number; scrolling counts the displayed rows. Disable wrapping to scroll
long lines horizontally with **[** / **]**. JSON keeps its existing display
formatting; XML retains its line structure and receives token colors. Plain text
and invalid JSON remain readable text. These display settings apply across
messages for the session and leave the message body, headers and properties
unchanged.

| Key | Action |
| --- | --- |
| Tab / Shift+Tab | Change panel focus; in expanded detail, cycle detail tabs |
| Mouse click | Focus panels, select rows, activate detail tabs or inspector controls |
| Mouse drag on a vertical divider | Resize the adjacent panels |
| Double-click a divider / Escape during drag | Reset widths / cancel the drag |
| Up / Down | Select connection, queue or message |
| Left / Right | No action in connection, queue or message lists |
| Home / End | First / last matching row |
| Enter | Connect server; in queues refresh only the selected depth; in messages expand / restore detail |
| d | Disconnect selected server |
| PgUp / PgDn, mouse wheel over detail | Scroll payload / headers / properties |
| [ / ] | Scroll long detail lines horizontally; disable Body wrapping first |
| w / s in Body | Toggle soft wrapping / syntax colors, in either detail layout |
| h | Cycle Headers / Properties / Body |
| /, Enter | Edit and apply a filter for the focused panel |
| Escape | Return from full screen or clear focused filter |
| r in queues | Refresh depths for all queues without reloading messages |
| r in connections/messages | Refresh queues and messages |
| c | Open consumer inspector for the selected queue, including empty queues |
| n | Open the TextMessage composer for the selected queue |
| Inspector Up / Down, PgUp / PgDn | Select consumers; scroll when viewing consumer details |
| Inspector Enter | Show full consumer, connection, session and selector details |
| Inspector c / Refresh consumers | Refresh only consumer metadata; r has no action here |
| Inspector Tab / Shift+Tab | Focus list, Refresh consumers or Close |
| Inspector Escape / Close | Return from details to list, then close; Close closes immediately |
| q | Quit |

The green dot follows a live provider connection. Each connection has an isolated
worker process, so networking and Java calls do not block terminal navigation.
Requests have a configurable deadline; a stuck worker is terminated and can be
reconnected. Delayed results for a previously selected queue are discarded.
Queue-depth refreshes keep focus and message selection in place. Selecting a
queue with Up / Down still updates its message preview; Enter on a queue queries
only that queue's depth and keeps the queue panel focused. Routing labels and the
connection/queue breadcrumb line are omitted from the screen.
The selected depth cell displays `...` while its inquiry is pending. Detail-tab
labels retain fixed positions and widths; the active tab uses a background highlight.
The queue panel has no Refresh or Consumers buttons; use the `r refresh` and
`c consumers` shortcuts shown in the bottom bar. In the consumer inspector,
press `c` again to refresh the consumer list.

Local refresh timing was checked before and after the latest changes. Windows
tried IPv6 `::1` for `localhost` before falling back to the IPv4-only Compose port,
adding about two seconds per HTTP connection. The example Jolokia URL now uses
`127.0.0.1`. Median selected-queue requests fell from about 2,063 ms to 7 ms;
all-queue requests fell from 4,146 ms to 26 ms. IBM now reuses its inquiry connection
and PCF agent; selected inquiries fell from about 22 ms to 4 ms. These are five-sample
local request timings, excluding JVM startup and time waiting behind other requests.

The consumer inspector implements [consumer display option 2](docs/CONSUMER_OPTIONS.md).
It preserves panel focus, queue/message selection and detail tabs on return.
Artemis lists attached consumers, excluding browse-only sessions, with client,
endpoint, user, protocol, selector and connection/session details when available.
IBM lists **input handles**, with application, endpoint, user, channel and process/thread
details. Those handles do not necessarily represent separate application instances;
selectors and unique handle IDs are not available through the queue-status inquiry.
The inspector distinguishes loading, zero attached and unavailable metadata.
Permissions failures remain in the inspector without disconnecting message browsing.
Consumer metadata refresh is explicit and does not receive business messages.

## Message composer

Select a connected server and queue, then press **n**. The destination stays bound
to that connection and queue while composing. Use Tab / Shift+Tab or click to move
between fields and controls; arrow keys edit text inside the composer. Enter adds
a newline in Body, moves to the next text field, or activates a focused button.
Typing q, c, r or n in a text field enters text.

Set priority (0–9), persistent/non-persistent delivery, TTL in milliseconds
(0 means no expiry), JMSType and JMSCorrelationID. Add or remove typed properties;
click a property's Type or focus it and press Enter/Space to cycle String, Boolean,
Byte, Short, Int, Long, Float and Double. Names, duplicate names and numeric ranges
are validated before sending. Reserved JMS property names use the dedicated
header fields. Body and metadata together are limited to 4 MiB in UTF-8.

- **F2 / Preview send:** review the destination, body, headers and properties.
  No message is sent at this step. Use Up/Down or PgUp/PgDn to review long content.
  Tab/click **Send 1 message**, then Enter/click to send once.
- **F3 / Load file:** load a UTF-8 text file (an optional UTF-8 BOM is removed).
- **F4 / Save draft:** save body, settings, typed properties and destination as
  JSON. Defaults to the ignored local drafts directory. Credentials are excluded;
  existing files are not overwritten. Enter applies a path; Ctrl+U clears it.
- **F5 / Load draft:** restore a saved draft for the same configured destination.
- **F6 / + Property:** add a property row. Tab or mouse wheel reveals additional
  rows on smaller terminals; the minus control removes a row.
- **Escape / Cancel:** leave the review screen for editing, or close the composer.

![Implemented composer](docs/designs/composer-implemented.png)

[Send review screenshot](docs/designs/composer-preview.png).

Sending runs in the broker worker and is not retried automatically. The result
shows the provider-assigned message ID, timestamp and expiry. The selected queue's
depth and message list refresh after success. Browse limits still apply, so a new
message may be outside the loaded portion of a large queue.
If the worker times out or loses the connection during sending, delivery may be
unknown: the result directs you to inspect the queue before sending again and
offers saving the draft instead of an automatic resend. Provider failures before
send dispatch keep the draft available for editing. The composer stays open while
the request is pending. Demo mode simulates sends only in its offline worker.
On restricted brokers, sending additionally requires put/send authority on the
target queue; the local Compose accounts already have it.

## Configuration and message columns

Copy `config.example.yaml` to `config.yaml` or pass `--config`. Configuration is
validated with `--check-config`; passwords are resolved from environment variables
when connecting, and are never saved to YAML. Connection types are `artemis` and
`ibm`; default startup is disconnected (`auto_connect: false`).

- **MessageId:** JMS ID, shortened in the table with the ending retained; available
  in full in the detail view and Headers tab.
- **Type:** JMS body type, such as TextMessage, BytesMessage or MapMessage. The
  optional application-defined JMSType is available in Headers.
- **Prio:** JMS priority from 0 through 9.
- **Size:** payload bytes, excluding headers and transport overhead. Text
  is measured in UTF-8 and bytes messages use their actual body length. Map,
  stream and serialized-object encoded sizes are `n/a`, because portable JMS
  does not expose those byte counts. The full size is retained when preview
  content is truncated.
- **Body:** a single-line body preview; the selected payload is shown below.

Headers and Properties display as key/value tables with right-aligned keys and
left-aligned values. Horizontal scrolling moves values while keeping keys fixed.
`JMSTimestamp` retains its raw milliseconds and also shows local datetime with
milliseconds and UTC offset, for example `1791981234567 (2026-10-14 14:33:54.567+02:00)`.
A zero JMS timestamp is marked `not set`. Raw metadata stays unchanged in JSON exports.

JSON text is formatted in the preview while retaining the original raw text.
Bytes are shown in hex; maps and primitive streams are shown as JSON. Object
messages are identified without deserializing their contents. `max_body_bytes`
caps payload bytes included in a preview (hex formatting expands the display);
headers and properties are separate. JMS text providers materialize text
before truncation, so this is not a broker-side read-size limit. Workers use a
256 MiB Java heap. Browse results are a changing view rather than a locked
snapshot; other clients and expiry can change queue contents independently.

Artemis queue discovery uses authenticated Jolokia (`management_url`) and JMS
handles message browsing. Separate management credentials and a CA file can be
configured. IBM uses PCF inquiry and JMS browsing; local development grants the
app account display permission and access to its temporary PCF reply queues.
Those internal request/reply messages are separate from business-queue browsing.
On restricted IBM installations, configure an explicit `queues` list to use
ordinary queue inquiry without PCF discovery. Both paths need browse/inquire
authority on the intended queues. The IBM consumer inspector additionally needs
PCF command/reply access and display authority for queue-status inquiries, even
when an explicit queue list is configured. The local Compose account already has
these permissions. Artemis uses the queue's `listConsumersAsJSON` and optionally
the broker's `listConsumers` management operation to supply connection details;
denied optional enrichment leaves the consumer list visible with a notice.

Optional TLS settings: `enabled`, `trust_store`, `trust_store_password_env`,
`key_store`, `key_store_password_env`, and IBM `cipher_suite`. Store paths should
be absolute. Use Java trust/key stores; management HTTPS uses the system CAs or
`management_ca_file`. `timeout_seconds` defaults to 15 per request.

JMS JAR versions and SHA-256 checksums are locked in `jmsbrowser/client-lock.json`.
`--install-clients` downloads and verifies them in `~/.cache/jmsbrowser/clients`;
`JMSBROWSER_CLIENT_DIR` overrides this location. `--doctor` checks runtime/client
availability. Sending is available only through the interactive composer's
explicit review/send flow; JSON and PNG exports remain non-consuming reads.

For non-interactive browsing and exporting the application screen:

```sh
jmsbrowser --config config.example.yaml --connection "Local IBM MQ" --queue DEV.ORDERS --dump
jmsbrowser --config config.example.yaml --queue DEV.ORDERS --screenshot screen.png
```

PNG export requires Pillow (`pip install '.[dev]'`) and renders the same grid as
curses from an actual browse result. It closes the connection after exporting.

## Proposed next features

The original proposals use example data. Options 1 and 2 remain proposals;
option 3 is now implemented in the composer described above.

1. **JMS selector filtering — recommended first:** browse matching headers and
   properties before applying the result limit, making large queues easier to
   investigate. [Screenshot](docs/designs/feature-1-selector.png).
2. **Queue depth history:** sample and plot queue depths over time, showing backlog
   changes, minimum and maximum. Depth changes alone do not establish send/receive
   rates. [Screenshot](docs/designs/feature-2-history.png).
3. **Message composer — implemented:** write or load a TextMessage, edit priority, persistence,
   expiry and typed properties, then preview the destination before explicitly
   sending. [Screenshot](docs/designs/feature-3-composer.png).

Selectors are supported by the
[JMS QueueBrowser API](https://docs.oracle.com/javaee/7/api/javax/jms/QueueSession.html).
The send controls correspond to
[JMS producer options](https://docs.oracle.com/javaee/7/api/javax/jms/MessageProducer.html).
Regenerate the three proposals with `python tools/render_feature_options.py`.

## Original design alternatives

All three proposals use the same 144-column character grid, sample data and
three-panel flow: connections → queues → messages. They can be implemented with
Python `curses`, with Tab / Shift+Tab and mouse clicks changing panel focus.
Green dots indicate connected servers; hollow dots indicate disconnected servers.
The message preview stays inside the right panel.

| Option | Screenshot | Interaction emphasis |
| --- | --- | --- |
| 1 — Classic inspector | [View](docs/designs/01-classic.png) | Framed panels, queue metadata, message table with preview below. |
| 2 — Compact workbench | [View](docs/designs/02-compact.png) | Single-row lists, minimal borders, more queues/messages visible at once. |
| 3 — Payload reader | [View](docs/designs/03-reader.png) | Selected; implemented with the requested message table replacing the strip. |

These are static, reproducible UI mockups, not screenshots of a running application.
See [the implementation notes](docs/PLAN.md) and the
[YAML configuration](config.example.yaml).

## Local brokers

Use Docker Desktop running Linux containers (or Docker Engine) with Docker
Compose, or Podman with a configured Compose provider. The images are pinned to
verified published tags and use fully qualified registry names. IBM MQ uses the
**Advanced for Developers** image. Both Compose files set `LICENSE=accept`, so
starting either accepts the IBM MQ developer license. Use this setup for local
development.

```sh
docker compose up -d
docker compose ps -a
docker compose logs artemis-seed ibm-seed artemis-loadtest-seed
python tools/verify_brokers.py
```

For Podman, use [compose.podman.yaml](compose.podman.yaml), located beside the
Docker `compose.yaml`. It defines the same brokers, health checks, sample queues,
seeding services, secrets and named volumes:

```sh
podman compose --file compose.podman.yaml up -d
podman compose --file compose.podman.yaml ps -a
podman compose --file compose.podman.yaml logs artemis-seed ibm-seed artemis-loadtest-seed
podman compose --file compose.podman.yaml down
```

Install Podman 4.7+ and [set up a Compose provider](https://podman-desktop.io/docs/compose/setting-up-compose)
first. On Windows/macOS, create and start a Podman machine (Podman Desktop can do
this). [Podman Compose](https://docs.podman.io/en/latest/markdown/podman-compose.1.html)
delegates to an installed provider such as Docker Compose or podman-compose.
Run these commands from the project directory. Run either Docker or Podman at a
time; both use the same host ports. Each engine keeps its own broker volumes and
seed markers.
The fixture verification script above currently uses Docker Compose.

`up` waits for both brokers to become healthy before starting the sample
producers. The three seed services should finish with **Exited (0)**; both brokers stay
running. The verification script waits for seeding, checks every fixture, and
browses each queue twice to verify unchanged message IDs, bodies and depths.
It uses only Python's standard library and tools already inside the images.

The same queues exist on both brokers:

| Queue | Initial messages |
| --- | ---: |
| DEV.ORDERS | 3 |
| DEV.PAYMENTS | 2 |
| DEV.NOTIFICATIONS | 1 |
| DEV.EMPTY | 0 |

Artemis additionally has **DEV.LOADTEST**, with **1,000 persistent TextMessages**.
Each has a unique JMS message ID and a `count` property from 0 to 999. Its shared
JSON body is in `dev/loadtest-message.json`. The local example configuration uses
`browse_limit: 1000` so all test messages are visible; the generic UI default remains
100. The additional seed service also works with existing broker volumes and skips
completed seeding on later starts.

Sample JSON messages live in `dev/messages/`. These producers belong to the
development fixtures; composer sends are additional user messages.

| Broker | Client connection | Console | Local credentials |
| --- | --- | --- | --- |
| Artemis | localhost:61616 | http://localhost:8161/console | artemis / artemis-local-123 |
| IBM MQ | localhost:1414, QM1, DEV.APP.SVRCONN | https://localhost:9443/ibmmq/console | app / app-local-123; console: admin / admin-local-123 |

Published ports bind to loopback. IBM's development console uses a self-signed
certificate. The checked-in secret files contain deliberately public development
passwords, supplied to IBM through Compose secrets. Keep real credentials outside
the YAML; the browser config references environment variables.

Data and seed markers live in named volumes in the selected engine. Ordinary
`docker compose down` or `podman compose --file compose.podman.yaml down` preserves
messages, and subsequent startup skips completed seeding. Seed markers
make normal restarts repeatable; a failure between sending messages and saving
the marker can leave partial fixtures. For this disposable environment, reset all
three volumes together to recreate a clean sample set:

```sh
docker compose down --volumes
docker compose up -d
```

The reset deletes this Compose project's broker data and sample-seeding state.
`verify_brokers.py` expects the initial fixture counts, so intentional sending or
consumption by another application requires a reset before running it again.

## Re-render the proposals

```sh
python -m pip install -r tools/requirements-mockups.txt
python tools/render_mockups.py
python tools/render_design_options.py
python tools/render_ui_improvements.py
```

The renderer finds Consolas on Windows or DejaVu Sans Mono on Linux, or accepts
`--font /path/to/font.ttf`. Pillow is used only to render the proposals.

The six later design proposals and their tradeoffs are in
[docs/DESIGN_OPTIONS.md](docs/DESIGN_OPTIONS.md). Options 1, 2 and 4 are now
implemented. The screenshot scripts use illustrative data and do not connect to
brokers. The implemented-UI exporter includes normal/minimum widths and expanded
JSON/XML body examples.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[java,dev]'
.\.venv\Scripts\python.exe -m pytest -q
$env:JMSBROWSER_INTEGRATION = '1'
.\.venv\Scripts\python.exe -m pytest -m integration -q
```

The integration tests use the application's real JMS adapters, repeat browsing
on both providers, validate every JSON fixture and requested message column,
test empty queues and preview/count limits, and compare queue depths before and
after. Run them with the pristine Compose fixtures. Composer integration tests
create fresh UUID queues on each provider, validate sending and expiration, then
delete only those test queues. Existing broker queues, messages and volumes are
preserved. Existing IBM volumes created before the browser implementation need
the new inquiry permissions applied once:

```powershell
Get-Content dev/ibm/20-queues.mqsc | docker compose exec -T ibm runmqsc QM1
```

This has already been applied to the local test broker in this workspace.

## Reference documentation

- [Official Apache Artemis Docker configuration](https://artemis.apache.org/components/artemis/documentation/latest/docker.html)
- [IBM MQ container usage and custom MQSC files](https://github.com/ibm-messaging/mq-container/blob/master/docs/usage.md)
- [IBM developer defaults and password secrets](https://github.com/ibm-messaging/mq-container/blob/master/docs/developer-config.md)
