# Paper research dashboard

The dashboard monitors continuous paper research with full audits every 48 hours, daily summaries at 09:00 and weekly summaries on Monday at 09:00 Europe/Bucharest. Values come only from the report endpoint; empty states never imply trading success. It is a read-only observation surface.

## Color

Use these CSS tokens exclusively: ink `#172b2a`, muted `#5c6b68`, paper `#f3f5f1`, surface `#ffffff`, line `#dbe2db`, accent `#25766a`, comparison `#917345`, dark `#152d29`, on-dark `#f7faf5`, on-dark-muted `#becdc4`, soft `#e9f0e9`, warning `#74511d`, warning-background `#fff1d6`, danger `#a23c3c`, grid `#e8ece6`. The accent identifies baseline chart history and the active navigation item. The comparison chart uses bronze, distinguished additionally by a dashed line.

## Typography

System sans-serif stack. Tabular figures on financial values and time. Large editorial heading, compact uppercase metadata, 14–16px body, no type below 12px. Financial values always display their USDT unit.

## Layout

Fixed 232px sidebar on desktop, fluid content capped at 1500px. Five genuine navigation anchors: overview, comparison, discovery, activity and reports. At 980px, navigation becomes a wrapping top row. At 600px, cards stack and table regions scroll locally. The body never requires horizontal scrolling at 320px.

## Components

White bordered cards with 16px corners, modest spacing, a dark experiment header. KPI cards distinguish the two independently funded simulation accounts. The SVG chart plots actual observations only; a single observation becomes a dot. Comparison tables do not claim improvement without outcomes. Tables have explicit headings and local overflow. Status, empty, stale, offline, error, completed and expired states are first-class. No trade controls or fabricated search/avatar elements.

## Behavior and accessibility

Fetch `/api/report` every 60 seconds; the report snapshot cadence is 30 minutes. Countdown updates every second and clamps at zero. Continuous research counts down to the next 48-hour audit; reaching it never implies the experiment ended. Explicit paused, halted and frozen statuses remain visible. Legacy bounded reports still display Ended when their deadline passes. Fetch `/api/audits` independently every 60 seconds; archive failures show an inline warning and preserve portfolio reporting and previously loaded links. Report links must remain on this origin under `/audits/`. Requests time out and never overlap. Failed refreshes preserve the last data with an offline warning. Snapshot and tick age warnings are independent. All variable text is inserted using `textContent`; SVG elements use DOM APIs. Keyboard-visible focus, a skip link, live status announcements, descriptive chart text, and reduced-motion compatibility are required. All timestamps display Europe/Bucharest explicitly.
