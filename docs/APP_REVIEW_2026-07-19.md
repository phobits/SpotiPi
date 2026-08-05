# SpotiPi App Review – Performance, Security und UI

**Stand:** SpotiPi v1.12.7 (`a6049e3`)  
**Datum:** 2026-07-19  
**Scope:** Flask-Backend, Spotify-Integration, Preact-Frontend, ausgelieferte Assets und lokale Browser-Prüfung.  
**Auftrag:** Review und Dokumentation; keine Codeänderungen.

## Gesamturteil

SpotiPi hat für eine Raspberry-Pi-Appliance ein überdurchschnittlich gutes Fundament: zentrale HTTP-Sessions mit Timeouts und Retries, asynchrone Snapshots, Cache- und Thread-Safety-Mechanismen, Security-Header, Same-Origin-Prüfung, ein kleines Frontend-Bundle sowie eine responsive und tastaturbedienbare Oberfläche.

Die größten Risiken liegen nicht in der Grundarchitektur, sondern an drei Systemgrenzen:

1. Mehrere personenbezogene Spotify-GET-APIs umgehen die Schutzliste für nicht-lokale Clients.
2. Ein nicht authentifizierter bzw. fehlerhaft authentifizierter Spotify-Zustand erzeugt dauerhaft neue Token-Refresh-Versuche und Fehlerlogs.
3. Ein Wecker kann serverseitig als aktiv gespeichert oder geladen werden, obwohl kein Lautsprecher konfiguriert ist; die UI zeigt diesen nicht startbereiten Zustand nicht deutlich genug.

### Bewertung

| Bereich | Bewertung | Kurzurteil |
|---|---:|---|
| Security | 6/10 | Gute Basisschutzmechanismen, aber eine relevante Lücke in der GET-Routenabdeckung und kein Host-Allowlisting. |
| Performance | 7/10 | Bundle und Backend-Caching sind stark; Auth-Fehlerschleife, Vollbaum-Re-Renders und Doppel-Polling kosten auf dem Pi unnötig Ressourcen. |
| UI/UX | 7/10 | Visuell kohärent, responsive und gut zugänglich; Statusklarheit, Alarm-Readiness und Feedback-Hierarchie bleiben die Hauptlücken. |

Das beabsichtigte LAN-Trust-Modell (`SPOTIPI_TRUST_PRIVATE_NETWORK=True`) wurde ausdrücklich **nicht** als Fehler bewertet. Die Security-Befunde betreffen entweder nicht-lokale Clients oder Umgehungen der vorgesehenen Browser-/Host-Grenze.

## Priorisierte Befunde

### P1 – Schutzbedürftige Spotify-GET-APIs sind für nicht-lokale Clients nicht geschützt

**Bereich:** Security  
**Schwere:** Hoch, falls der Dienst über das private LAN hinaus erreichbar ist

`is_protected_request()` schützt bei sicheren Methoden nur eine kleine Prefix-/Exact-Liste (`src/utils/request_security.py:18-38, 306-314`). Nicht enthalten sind unter anderem:

- `/api/dashboard/status`
- `/api/devices` und `/api/spotify/devices`
- `/api/music-library` und `/api/music-library/sections`
- `/api/music-search`
- `/api/artist-albums/<id>` und `/api/artist-top-tracks/<id>`
- `/api/playback/queue`
- `/api/spotify/auth-status`
- `/api/perf/metrics`

Diese Routen liefern Alarmzeiten, Lautsprecher-/Geräteinformationen, aktuelle Wiedergabe, Queue, private Bibliotheksdaten oder Betriebsmetriken. Die Bibliotheksrouten sind beispielsweise in `src/routes/music.py:134-229`, Dashboard und Auth-Status in `src/routes/health.py:202-358, 430-450`, Geräte in `src/routes/devices.py:75-164` und die Queue in `src/routes/playback.py:151-175` definiert.

Der Routen-Inventurtest bestätigte, dass diese GET-Pfade als `PUBLIC` klassifiziert werden. Ein gesetztes Admin-Passwort hilft für diese Pfade nicht, weil die Authentifizierung nur für `is_protected_request()==True` ausgeführt wird (`src/app.py:215-245`).

**Auswirkung:** Bei Port-Forwarding, Reverse-Proxy-Fehlkonfiguration, VPN-Exposition oder direkter öffentlicher Erreichbarkeit können externe Clients Spotify- und Alarm-Metadaten ohne Admin-Authentifizierung lesen.

**Empfehlung:** Die sensitiven GET-APIs in die Schutzklassifizierung aufnehmen und Tests ergänzen, die für `REMOTE_ADDR=8.8.8.8` einen 403, für private Adressen weiterhin 200 erwarten. So bleibt das LAN-Trust-Verhalten unverändert.

### P1 – Ein aktiver Alarm kann nicht startbereit sein

**Bereich:** UI/UX, Zuverlässigkeit  
**Schwere:** Hoch für die Kernfunktion „Wecker“

Der geprüfte Zustand zeigte einen aktivierten Wecker ohne Lautsprecher und ohne Musik (`config/development.json:2-6`). Die UI zeigt dabei einen eingeschalteten Toggle und lediglich die neutralen Texte „No speaker“/„No music“. Der Ausführungspfad bricht bei fehlendem Gerät korrekt ab (`src/core/alarm.py:317-334`), aber zu diesem Zeitpunkt klingelt der Wecker bereits nicht.

Die Frontend-Aktivierung verhindert zwar interaktiv einen leeren Lautsprecher (`frontend/src/app.tsx:1923-1935`), der Service persistiert `enabled=True` jedoch ohne entsprechende Readiness-Regel (`src/services/alarm_service.py:102-167`). Dadurch bleiben manuell bearbeitete, migrierte oder direkt per API gespeicherte inkonsistente Zustände möglich.

**Auswirkung:** Die Oberfläche vermittelt „Wecker aktiv“, obwohl die zentrale Funktion sicher fehlschlagen wird.

**Empfehlung:** Eine serverseitig abgeleitete `ready`-/`readiness_errors`-Information in Status und Save-Response aufnehmen. In der Alarmkarte einen deutlich sichtbaren Warnzustand „Nicht startbereit – Lautsprecher fehlt“ anzeigen und Aktivierung serverseitig ablehnen oder automatisch deaktivieren. Fehlende Musik sollte ebenfalls als explizite Entscheidung („aktuelle Wiedergabe fortsetzen“) statt als neutraler Leerzustand modelliert werden.

### P1 – Auth-Fehler erzeugen eine dauerhafte Refresh- und Log-Schleife

**Bereich:** Performance, Betriebsstabilität  
**Schwere:** Hoch auf Pi Zero W bei fehlenden/ungültigen Credentials

Das Frontend pollt sichtbar alle 4 Sekunden, im Low-Power-Modus alle 6 Sekunden (`frontend/src/hooks/useDashboardPolling.ts:101-150`). Sobald der Dashboard-Snapshot abgelaufen ist, plant die API einen neuen Hintergrund-Refresh (`src/routes/health.py:202-245`), der immer `get_access_token()` aufruft (`src/routes/health.py:119-133`).

Ohne gültigen Cache startet jeder Zugriff erneut den Refresh-Pfad (`src/utils/token_cache.py:100-129, 177-210`). Es gibt kein negatives Ergebnis-Caching oder einen Fehler-Backoff auf Cache-Ebene. Bei fehlenden Credentials wird der Fehler intern abgefangen, beim nächsten Poll aber erneut versucht (`src/api/spotify.py:363-377, 486-502`).

**Live-Evidenz:** Im lokalen Offline/Auth-required-Lauf erschien zu praktisch jedem Dashboard-Poll erneut `Token refresh failed - no token returned`. Das erzeugte über mehrere Minuten kontinuierlich Fehlerlogs, obwohl sich der Auth-Zustand nicht ändern konnte.

**Auswirkung:** Unnötige Threads, CPU, Lock-Contention und Log-I/O; bei ungültigem Refresh-Token zusätzlich wiederholte Requests an Spotify. Auf einer SD-Karte ist der dauerhafte Log-Write besonders ungünstig.

**Empfehlung:** Fehlerarten negativ cachen: `missing_credentials` bis zur Credential-Änderung, `auth_required` mit längerem TTL und Netzwerkfehler mit exponentiellem Backoff/Jitter. Wiederholte identische Logs drosseln oder aggregieren. Ein expliziter „Reconnect/Retry“-Impuls darf den Backoff zurücksetzen.

### P2 – Same-Origin-Prüfung ist gegen DNS-Rebinding/ungeprüfte Hosts nicht robust

**Bereich:** Security  
**Schwere:** Mittel bis hoch

Die Same-Origin-Prüfung vergleicht `Origin` bzw. `Referer` direkt mit `request.host` (`src/utils/request_security.py:321-349`). Ein explizites Host-Allowlisting oder `TRUSTED_HOSTS` ist in `_configure_app()` nicht gesetzt (`src/app.py:76-117`).

Bei DNS-Rebinding kann eine Angreifer-Domain nach dem Laden auf die private Pi-Adresse zeigen. Der Browser sendet dann weiterhin `Host: attacker.example` und `Origin: http://attacker.example`; der aktuelle Vergleich akzeptiert dies als same-origin. Gleichzeitig überspringt ein privater Client die Admin-Authentifizierung vorzeitig (`src/app.py:221-223`).

**Auswirkung:** Ein besuchtes bösartiges Webangebot kann – abhängig von Browser-/Private-Network-Access-Verhalten – die CSRF-Schranke zum lokalen Gerät umgehen.

**Empfehlung:** Erwartete Hosts explizit konfigurieren und unbekannte `Host`-Werte vor LAN-Trust/Auth ablehnen. Zulässig sollten nur Loopback, die konfigurierte IP/Hostname-Kombination und bewusst gesetzte Reverse-Proxy-Hosts sein. Das ändert den LAN-Trust-Default nicht.

### P2 – Keine Obergrenze für Request Bodies

**Bereich:** Security, Performance  
**Schwere:** Mittel

Alle zustandsändernden Endpunkte erwarten kleine JSON- oder Form-Payloads. In `_configure_app()` gibt es jedoch kein `MAX_CONTENT_LENGTH` und keine anwendungsspezifische 413-Behandlung (`src/app.py:76-117`). Rate-Limiting schützt die Anzahl der Requests, nicht deren Größe.

**Auswirkung:** Ein einzelner großer Body kann auf dem speicherarmen Pi unnötig RAM oder temporären Speicher belegen. Das gilt insbesondere im absichtlich vertrauensvollen LAN-Modell, in dem lokale Clients POSTs ohne Admin-Auth senden dürfen.

**Empfehlung:** Eine kleine globale Grenze (z. B. 64–256 KiB) setzen und 413 als standardisierte API-Response liefern. Für die vorhandenen Endpunkte ist kein großer Upload erforderlich.

### P2 – Rate-Limiter behält alte Client-Buckets unbegrenzt

**Bereich:** Performance, DoS-Resilienz  
**Schwere:** Mittel

Der Limiter speichert `(client_ip, rule)` dauerhaft in `_state` (`src/utils/rate_limiting.py:46-58, 118-147`). Abgelaufene Fenster setzen nur den Zähler zurück; Einträge werden nicht entfernt. Bereinigt wird ausschließlich beim manuellen Reset (`src/utils/rate_limiting.py:189-195`).

**Auswirkung:** Über lange Laufzeit wächst der Speicher mit jeder neuen Client-IP und Regel. Das ist im Heim-LAN meist klein, bei öffentlicher/VPN-Exposition oder großen IPv6-Adressräumen aber ein vermeidbarer Memory-DoS-Vektor.

**Empfehlung:** Periodische oder opportunistische Eviction abgelaufener, nicht blockierter Einträge und eine harte Obergrenze mit LRU/ältestem Zeitstempel einführen.

### P2 – Jeder Dashboard-Poll rendert den gesamten App-Baum neu

**Bereich:** Performance, UI  
**Schwere:** Mittel

`mergeDashboard()` erzeugt für jede erfolgreiche Antwort ein neues Objekt, auch wenn sich der fachliche Zustand nicht geändert hat (`frontend/src/hooks/useDashboardPolling.ts:32-49, 62-70`). Der komplette UI-Zustand sitzt in einer 3.365-Zeilen-`App`-Komponente (`frontend/src/app.tsx:1369-3365`).

Zusätzlich setzt `usePlaybackActions` die lokale Slider-Position bei jeder Änderung des gesamten `dashboard`- oder `settings`-Objekts zurück (`frontend/src/hooks/usePlaybackActions.ts:80-86`). Damit kann ein Poll während einer Lautstärkeinteraktion gegen den Nutzer arbeiten.

**Auswirkung:** Vollständige Preact-Reconciliation alle 4/6 Sekunden und potenziell springender Volume-Slider. Das fällt auf Desktop kaum auf, ist aber für einen dauerhaft geöffneten Pi-Zero-Browser unnötig.

**Empfehlung:** Unveränderte Dashboard-Antworten gegen relevante Felder vergleichen und bei Gleichheit die alte Referenz zurückgeben. Player, Alarmkarte und Sheets in stabile Teilkomponenten zerlegen. Den Slider nur mit dem Serverwert synchronisieren, wenn der Nutzer gerade nicht interagiert oder der Serverwert tatsächlich geändert wurde.

### P2 – Dynamische Statusänderungen sind für Screenreader weitgehend stumm

**Bereich:** UI/Accessibility  
**Schwere:** Mittel

`StatusPill` ist ein normales `span` ohne Live-Region (`frontend/src/app.tsx:328-335`), obwohl der Inhalt durch Polling wechselt. Der zentrale Play/Pause-Button hat einen statischen Namen und kein `aria-pressed` (`frontend/src/app.tsx:2449-2457`).

Die E2E-axe-Prüfung findet deshalb keine statische serious/critical-Verletzung, deckt aber die fehlende Ansage nach Statuswechseln nicht ab.

**Auswirkung:** Screenreader-Nutzer erfahren nicht zuverlässig, ob Wiedergabe gestartet/pausiert wurde, die Verbindung verloren ging oder eine Spotify-Neuanmeldung nötig ist.

**Empfehlung:** Eine dedizierte, visually-hidden `role="status"`-Region für echte Statusübergänge verwenden. Der Play/Pause-Name sollte die nächste Aktion benennen („Pause“ bzw. „Wiedergabe starten“) und den Zustand über `aria-pressed` oder gleichwertige Semantik spiegeln.

### P2 – Der prominenteste Player-CTA ist im häufigen Idle-State ein No-op

**Bereich:** UI/UX  
**Schwere:** Mittel

Ohne aktiven Spotify-Player bleibt der zentrale Button visuell grün und leuchtend, ist aber deaktiviert (`frontend/src/app.tsx:2449-2457`). Der eigentliche Einstieg liegt in der separaten „Play now“-Karte. Im Live-Review war dies der stärkste visuelle CTA, hatte aber keine Aktion.

**Auswirkung:** Die visuelle Hierarchie verspricht eine primäre Aktion, die im häufigen First-Run-/Idle-Zustand nicht verfügbar ist.

**Empfehlung:** Im Idle-State den zentralen Button als echten Einstieg in „Play now“ verwenden oder deutlich neutraler darstellen und eine kurze Erklärung anbieten.

### P3 – Queue-Polling läuft separat zum Dashboard-Polling

**Bereich:** Performance  
**Schwere:** Niedrig bis mittel

Neben dem 4/6-Sekunden-Dashboard-Poll existiert ein eigener 15-Sekunden-Queue-Poll (`frontend/src/app.tsx:1612-1684`). Sichtbarkeit und Auth-State werden berücksichtigt, dennoch entsteht bei aktiver Wiedergabe ein zweiter periodischer Spotify-Pfad.

**Empfehlung:** Queue nur bei Trackwechsel, beim Öffnen der relevanten Fläche oder als Teil eines gemeinsamen Snapshots aktualisieren.

### P3 – Low-Power-Modus reduziert die teuersten CSS-Effekte nicht

**Bereich:** Performance, UI  
**Schwere:** Niedrig bis mittel

Der Low-Power-Wert steuert das Polling, nicht aber das Rendering. Mehrere große Flächen verwenden `backdrop-filter: blur(20px)` (`frontend/src/styles.css:103-114`). Die frühere Endlos-Animation wurde in v1.12.6 sinnvoll auf sechs Durchläufe begrenzt (`frontend/src/styles.css:376-409`), aber der Blur bleibt beim Scrollen und bei Status-Repaints teuer.

**Empfehlung:** `data-low-power` am App-Root ausgeben und Blur/Shadow/Animationen dort gezielt reduzieren. Das Design kann über solide halbtransparente Flächen praktisch gleich bleiben.

### P3 – Settings-Hierarchie priorisiert seltene Setup-Aufgaben

**Bereich:** UI/UX  
**Schwere:** Niedrig bis mittel

Auf 360×800 belegt der Spotify-Credential-Block den gesamten ersten sichtbaren Settings-Bereich und mehr als zwei Viewport-Höhen; Sprache, Lautstärke und OLED-Modus liegen darunter (`frontend/src/app.tsx:3013-3344`). Das Layout hat keinen horizontalen Overflow, aber die Informationshierarchie entspricht nicht der Nutzungshäufigkeit.

**Empfehlung:** Alltagspräferenzen zuerst zeigen; Credential-Management in einen einklappbaren „Spotify-Verbindung“-Bereich verschieben. Bei verbundenem Konto genügt zunächst Status, Profil und „Verbindung verwalten“.

### P3 – Alarm-Autosave hat kein positives Feedback

**Bereich:** UI/UX  
**Schwere:** Niedrig bis mittel

Zeit, Wochentage, Gerät, Lautstärke und Toggles speichern automatisch (`frontend/src/app.tsx:2618-2750`). Der Success-Pfad aktualisiert nur den Dashboard-State und zeigt keine Bestätigung (`frontend/src/app.tsx:1963-1975`), während Settings-Änderungen jeweils einen Erfolgstoast ausgeben (`frontend/src/hooks/useSettingsMutations.ts:78-91`).

**Auswirkung:** Bei Pi-/Netzwerklatenz bleibt unklar, ob eine Änderung gespeichert wurde; zugleich ist das Feedback-Modell zwischen Flächen inkonsistent.

**Empfehlung:** Einen ruhigen Inline-Status „Gespeichert“/„Speichert…“ im Sheet verwenden, nicht für jede Änderung einen Toast. Fehler weiterhin assertiv toasten.

### P3 – Lokale Geräte-Metadaten sind in einer getrackten Development-Config enthalten

**Bereich:** Security/Data Hygiene  
**Schwere:** Niedrig bis mittel

`config/development.json` ist getrackt und enthält echte Lautsprechernamen sowie Spotify-Geräte-IDs (`config/development.json:12-27`). Im aktuell getrackten Tree wurden keine Spotify-Secrets oder Tokens gefunden; die Geräteinformationen sind trotzdem unnötige persönliche Betriebsdaten.

**Empfehlung:** Getrackte Development-Config auf synthetische Beispieldaten reduzieren und Runtime-Caches ausschließlich außerhalb des Repositories halten. Falls das Repository öffentlich oder breit geteilt ist, die Git-Historie separat auf frühere Secrets/PII prüfen.

### P3 – Python-Deployment ist nicht reproduzierbar gepinnt

**Bereich:** Supply Chain, Betriebsstabilität  
**Schwere:** Niedrig bis mittel

`requirements.txt` enthält nur Mindestversionen (`>=`) und keine Lockdatei oder Hashes. Ein Fresh Install kann daher zu unterschiedlichen Zeitpunkten andere Major-/Minor-Stände installieren.

**Empfehlung:** Einen regelmäßig aktualisierten, getesteten Constraints-/Lock-Stand für das Pi-Deployment pflegen und Updates bewusst automatisieren.

## Was bereits stark ist

### Security

- Proxy-Header werden standardmäßig nicht vertraut; vertrauenswürdige Proxies müssen explizit konfiguriert werden.
- Admin-Passwörter werden konstantzeitlich geprüft; Passwort-Hashes werden unterstützt.
- Same-Origin-Prüfung trennt Wildcard-CORS korrekt von CSRF-Freigaben.
- CSP, `nosniff`, Frame-Schutz, Referrer Policy und CORP werden zentral gesetzt (`src/app.py:257-292`).
- Spotify-Credentials liegen in `~/.spotipi/.env`, der Pfad wird auf 0700/0600 abgesichert; API-Antworten maskieren Secrets.
- Spotify-HTTP-Zugriffe verwenden zentrale, thread-lokale Sessions mit Timeouts, Retries nur für geeignete Methoden und `trust_env=False`.

### Performance

- Das ausgelieferte Frontend liegt deutlich unter Budget: JS 25,2 KiB gzip und CSS 6,1 KiB gzip.
- Dashboard-/Playback-/Device-Snapshots werden asynchron aktualisiert; Request-Threads blockieren nicht auf Spotify.
- Visibility-aware Polling reduziert Hintergrundaktivität.
- Cache-, Token- und Config-Zugriffe sind thread-safe; Dateiupdates erfolgen an wichtigen Stellen atomar.
- Die Idle-Equalizer-Animation endet nach sechs Durchläufen statt dauerhaft zu laufen.

### UI/UX

- Desktop- und Mobile-Layout sind visuell kohärent; bei 360×800 gab es keinen horizontalen Overflow.
- Das mobile Settings-Target war 48×44 px; die jüngsten Touch-/Gutter-Fixes greifen.
- Sheets sperren den Dokument-Scroll, haben internen Scroll, Fokus-Trap, Escape-Close und Fokus-Restore.
- Custom Listboxes unterstützen Tastaturbedienung; Toasts verwenden passende Live-Regionen.
- `prefers-reduced-motion`, Safe-Area-Insets und responsive Breakpoints sind vorhanden.
- Empty-, Loading-, Offline-, Auth-required- und Error-Zustände sind breit abgedeckt.

## Empfohlene Reihenfolge

### Sofort / vor externer Exposition

1. Sensitive GET-APIs in die Schutzklassifizierung aufnehmen.
2. Host-Allowlisting gegen DNS-Rebinding ergänzen.
3. Alarm-Readiness serverseitig definieren und in der UI deutlich anzeigen.
4. Token-Refresh-Fehler negativ cachen und Backoff/Log-Throttling einführen.

### Nächster Performance-/UX-Zyklus

1. Unveränderte Dashboard-Updates bailen lassen; Volume-Interaktion vom Poll entkoppeln.
2. Request-Body-Limit und Rate-Limiter-Eviction ergänzen.
3. Player-Idle-CTA, dynamische A11y-Ansagen und Alarm-Save-Feedback verbessern.
4. Queue-Polling konsolidieren und Low-Power-CSS reduzieren.

### Danach

1. Settings-Hierarchie neu ordnen.
2. Development-Config bereinigen und Python-Abhängigkeiten locken.
3. PWA-Versprechen klären: Es existiert ein Manifest, aber kein Service Worker; ein echter Offline-Cold-Start ist daher nicht möglich.

## Verifikation

| Check | Ergebnis |
|---|---|
| Git-Status vor Review | sauber |
| `npm run typecheck` | bestanden |
| `npm run budget:check` | bestanden – JS 83,7 KiB raw / 25,2 KiB gzip; CSS 26,3 KiB raw / 6,1 KiB gzip |
| `npm run test:e2e` | 69/69 bestanden (Mobile, Tablet, Desktop; axe + Runtime-Budget) |
| `pytest -q` | Collection-Fehler: `spotipy` fehlt im lokalen venv |
| `pytest -q --ignore=tests/test_generate_token.py` | 240 bestanden, 2 bewusst übersprungen |
| `pip check` | keine kaputten installierten Abhängigkeiten |
| Browser-Review | Desktop sowie 360×800; Dashboard, Settings- und Alarm-Sheet interaktiv geprüft |
| Code-/Config-/Asset-Änderungen | keine |

Der anfängliche E2E-Lauf innerhalb der Sandbox konnte Chromium nicht starten; derselbe Testlauf außerhalb dieser Prozessbeschränkung bestand vollständig. `npm run build` wurde bewusst nicht ausgeführt, da der Auftrag keine Quellcode-/Bundle-Änderungen erlaubt und die committed Assets bereits separat per Budget und E2E geprüft wurden.

## Grenzen des Reviews

- Keine echte Pi-Zero-W-Hardwaremessung (FPS, CPU, Temperatur, SD-I/O); Performance-Schwere basiert auf Codepfaden und lokal beobachtetem Verhalten.
- Kein Live-Spotify-Test und kein Alarm-Fire-Test; Netzwerkzugriffe waren im Review-Server deaktiviert.
- Kein externer npm-Advisory-Audit: Dieser würde Dependency-Metadaten an die öffentliche Registry übertragen und wurde ohne explizite Freigabe nicht ausgeführt.
- Keine Prüfung der vollständigen Git-Historie auf frühere Secrets oder personenbezogene Daten.

