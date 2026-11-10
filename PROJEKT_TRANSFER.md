# SHM2 Shelly Pro 3EM Emulator – Projektdokumentation & Transfer-Leitfaden

Diese Datei enthält alle relevanten Informationen, Architekturentscheidungen, Dateiübersichten und Schritt-für-Schritt-Anleitungen, um dieses Projekt auf einen anderen Computer oder direkt in ein neues Home Assistant System zu übertragen und weiterzuentwickeln.

---

## 1. Projektübersicht & Zweck

### Das Problem
Die **Anker SOLIX Solarbank 3 Pro** (sowie viele andere moderne Heimspeicher- und Wechselrichtersysteme) unterstützt als externen Smart Meter zur dynamischen Nulleinspeisung und Lade-/Entladesteuerung den **Shelly Pro 3EM (Gen 2)**.
Wenn im Haus bereits ein **SMA Sunny Home Manager 2.0 (SHM2)** oder ein **SMA Energy Meter** im Zählerschrank verbaut ist, wäre ein zusätzlicher Shelly-Zähler reine Geld- und Platzverschwendung.

### Die Lösung
Dieses Projekt ist ein **Home Assistant Add-on** (auch als eigenständiger Docker-Container oder Python-Dienst lauffähig), das:
1. Die Live-Messdaten des SMA SHM2 über **SMA Speedwire Multicast** (`239.12.255.254:9522`, Protokoll `0x6069`) in Echtzeit (ca. 1x pro Sekunde) empfängt und dekodiert.
2. Alternativ Daten aus bestehenden **Home Assistant Sensoren** per Supervisor REST-API pollt (falls Multicast über VLANs blockiert ist).
3. Diese Messdaten im lokalen Netzwerk als vollständig emulierter **Shelly Pro 3EM (Gen 2)** bereitstellt:
   - **HTTP JSON-RPC** auf Port `80` (sowohl `POST /rpc` als auch `GET /rpc/<Method>?<params>`).
   - **UDP JSON-RPC** auf Port `1010`.
   - **mDNS / Zeroconf** Ankündigung (`_shelly._tcp` und `_http._tcp`), damit die Anker App das Gerät automatisch im WLAN findet.
   - **Ingress Web-Dashboard** (Port `8099`) in Home Assistant mit Live-Leistungsanzeige und Request-Monitor.

---

## 2. Vollständige Dateistruktur

```text
Shelly3PM-HA-Emulator/
├── config.yaml              # Home Assistant Add-on Konfiguration & Options-Schema
├── Dockerfile               # Multi-Arch Container-Definition (Alpine 3.20 + Python)
├── run.sh                   # Entrypoint-Startskript für den Container
├── requirements.txt         # Produktiv-Abhängigkeiten (aiohttp, zeroconf)
├── requirements-dev.txt     # Entwicklungs- & Test-Abhängigkeiten
├── .dockerignore            # Ausgeschlossene Dateien für den Docker-Build
├── .gitignore               # Git-Ausschlussregeln
├── README.md                # Zweisprachige Kurzdokumentation (DE / EN)
├── PROJEKT_TRANSFER.md      # Dieses vollständige Übergabedokument
│
├── src/                     # Quellcode der Anwendung
│   ├── __init__.py          # Python-Paket-Initialisierung
│   ├── main.py              # Hauptprogramm, Lifecycle- & Task-Orchestrierung
│   ├── models.py            # Datenmodelle (MeterData, Phasen, OBIS-IDs, Einheiten)
│   ├── speedwire.py         # SMA Multicast Socket Listener & OBIS-Parser
│   ├── ha_client.py         # Home Assistant REST-API Client (Alternative Datenquelle)
│   ├── shelly_service.py    # Shelly Gen2 JSON-RPC Logik, Stale-Watchdog & History
│   ├── udp_rpc.py           # Asynchroner UDP JSON-RPC Server (Port 1010)
│   ├── mdns_service.py      # Zeroconf mDNS Advertiser (_shelly._tcp / _http._tcp)
│   └── web_ui.py            # HTTP JSON-RPC Server (Port 80) & Ingress Dashboard
│
└── tests/                   # Automatisierte Tests (100% grün)
    ├── __init__.py
    ├── sample_packets.py    # Echte, aufgezeichnete Hex-Dumps von SMA EM & SHM2
    ├── test_speedwire.py    # Tests für Speedwire-Parser, OBIS-Decoding & Seriennummern
    ├── test_shelly_rpc.py   # Tests für alle Shelly Gen2 RPC-Endpunkte & Stale-Watchdog
    └── test_ha_client.py    # Tests für HA-Fallback-Berechnungen (P=U*I)
```

---

## 3. Funktionsweise der Module (`src/`)

### `src/models.py`
- Definiert `MeterData` und `PhaseMeasurement` (Spannung `V`, Strom `A`, Wirkleistung `W`, Scheinleistung `VA`, Power Factor `pf`, Netzfrequenz `Hz`, Zählerstände Import/Export `Wh`).
- **Verifizierte SMA OBIS-Kennzahlen**:
  - Gesamtwirkleistung Bezug (`0x00010400`) / Einspeisung (`0x00020400`) in `0.1 W`.
  - Phasenspezifische Wirkleistung: L1 (`0x00150400`/`0x00160400`), L2 (`0x00290400`/`0x002A0400`), L3 (`0x003D0400`/`0x003E0400`).
  - Phasenspannungen: `0x00200400`, `0x00340400`, `0x00480400` in `mV`.
  - Phasenströme: `0x001F0400`, `0x00330400`, `0x00470400` in `mA`.
  - Gesamtenergiezähler: Bezug (`0x00010800`) / Einspeisung (`0x00020800`) in `Ws` (`Wh = Ws / 3600`).
- Enthält `to_em_status()` und `to_emdata_status()` zur exakten Formatierung gemäß der offiziellen Shelly Pro 3EM Gen 2 RPC-Spezifikation.
- Unterstützt `invert_sign`, falls das nachgelagerte System Vorzeichen invertiert erwartet.

### `src/speedwire.py`
- Öffnet einen Low-Level UDP-Multicast-Socket auf `239.12.255.254:9522`.
- Verifiziert Magic Bytes (`SMA\x00`), Protokoll-ID `0x6069` und den Telegramm-Header.
- Parst dynamisch alle OBIS-Felder und filtert optional nach der SHM2-Seriennummer.

### `src/ha_client.py`
- Fallback-Datenquelle: Pollt Entitäten über die Home Assistant REST API (`/api/states/<entity_id>`).
- Berechnet fehlende Ströme oder Scheinleistungen automatisch aus $P = U \cdot I$.

### `src/shelly_service.py`
- Verwaltet den Zustand des emulierten Geräts (MAC-Adresse, Firmware-Version `1.4.4`, Modell `SPEM-003CEBEU`).
- Implementiert alle Shelly Gen 2 RPC-Methoden:
  - `Shelly.GetDeviceInfo`, `Shelly.GetStatus`, `Shelly.GetConfig`
  - `EM.GetStatus` (id: 0), `EM.GetConfig`
  - `EMData.GetStatus` (id: 0)
  - `Sys.GetStatus`, `Sys.GetConfig`
  - `Wifi.GetStatus`, `Eth.GetStatus`
  - `Shelly.ListMethods`
- **Stale-Watchdog**: Bleiben Messpakete für länger als $N$ Sekunden (Standard: 10 s) aus, werden Fehlerflags gesetzt (`power_meter_failure`, `data_stale`), um zu verhindern, dass die Solarbank mit veralteten Werten regelt.
- Führt einen Ringspeicher für eingehende Anfragen (`debug_requests`), der im Web-Dashboard angezeigt wird.

### `src/udp_rpc.py`
- Asynchroner UDP-Server auf Port `1010` für JSON-RPC Anfragen.

### `src/mdns_service.py`
- Verwendet `zeroconf`, um das Gerät per Multicast-DNS anzukündigen:
  - Typen: `_shelly._tcp.local.` und `_http._tcp.local.`
  - TXT-Records: `gen=2`, `app=Pro3EM`, `ver=1.4.4`, `id=shellypro3em-<mac>`, `model=SPEM-003CEBEU`.

### `src/web_ui.py`
- Basiert auf `aiohttp.web`.
- Bedient HTTP JSON-RPC auf Port 80 (`/rpc` via POST und GET).
- Liefert das interaktive Web-Dashboard für Home Assistant Ingress auf Port 8099.

### `src/main.py`
- Lädt die Konfiguration aus `/data/options.json` (Home Assistant) bzw. Umgebungsvariablen.
- Startet alle Dienste nebenläufig mit `asyncio.gather`.
- Gibt alle 10 Sekunden eine kompakte Live-Statuszeile ins Log aus.

---

## 4. Projekt auf einen anderen PC übertragen

### Methode A: Projektordner direkt kopieren (Empfohlen)
Kopiere einfach den gesamten Ordner `Shelly3PM-HA-Emulator/` via:
- USB-Stick / Externe Festplatte
- AirDrop / Lokale Netzwerkfreigabe (SMB)
- ZIP-Archiv

> [!IMPORTANT]
> Achte beim Entpacken auf einem Linux- oder Mac-System darauf, dass das Startskript ausführbar bleibt:
> ```bash
> chmod +x run.sh
> ```
> Falls die Dateien über ein Windows-System liefen, stelle sicher, dass `run.sh` mit Unix-Zeilenenden (`LF`) gespeichert ist. (Das im `Dockerfile` integrierte `sed -i 's/\r$//' /run.sh` fängt dies zusätzlich automatisch ab).

### Methode B: Per Git
Falls du das Projekt versionieren oder auf GitHub/GitLab teilen möchtest:
```bash
cd "Shelly3PM-HA-Emulator"
git init
git add .
git commit -m "Initial commit of SHM2 Shelly Pro 3EM Emulator"
git remote add origin <DEINE_REPO_URL>
git push -u origin main
```
Auf dem Ziel-PC anschließend einfach klonen:
```bash
git clone <DEINE_REPO_URL>
```

---

## 5. Lokale Entwicklung & Tests auf dem PC

Du kannst das gesamte Projekt lokal auf jedem PC (Mac, Linux, Windows) testen, ohne Home Assistant starten zu müssen.

### 1. Python-Umgebung einrichten
Voraussetzung: Python 3.9+ (ideal Python 3.11 oder 3.12).
```bash
cd Shelly3PM-HA-Emulator

# Virtuelle Umgebung anlegen und aktivieren
python3 -m venv venv
source venv/bin/activate  # Auf Windows: venv\Scripts\activate

# Abhängigkeiten installieren
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

### 2. Unittests ausführen
Alle 20 Tests verifizieren den Speedwire-Parser gegen echte SMA-Hex-Pakete, prüfen die Sign-Invertierung und testen die Shelly-RPC-Endpunkte:
```bash
python3 -m unittest discover tests
```
Erwartete Ausgabe:
```text
Ran 20 tests in 0.011s
OK
```

### 3. Emulator lokal starten (Testbetrieb)
```bash
export PYTHONPATH="."
python3 -m src.main
```
*(Hinweis: Port 80 benötigt auf manchen Systemen Root-Rechte. Für lokale Tests kann in `src/config.py` oder via ENV temporär ein anderer Port z. B. 8080 gewählt werden).*

---

## 6. Installation als Home Assistant Add-on

### Schritt-für-Schritt-Anleitung:

1. **Samba-Share auf Home Assistant aktivieren**:
   - Installiere in Home Assistant das offizielle Add-on **Samba share** (falls noch nicht vorhanden).
2. **Ordner übertragen**:
   - Verbinde dich mit der Netzwerkfreigabe deines Home Assistant Servers.
   - Öffne den Ordner `addons/`.
   - Kopiere den kompletten Ordner `Shelly3PM-HA-Emulator` hinein und benenne ihn idealerweise `shm2_shelly_emulator`.
   - Der Pfad auf Home Assistant lautet dann:
     `/addons/shm2_shelly_emulator/`
3. **Add-on Store aktualisieren**:
   - Öffne in Home Assistant **Einstellungen** > **Add-ons** > **Add-on Store**.
   - Klicke oben rechts auf die **drei Punkte** > **Nach Aktualisierungen suchen** (Check for updates).
4. **Add-on installieren**:
   - Unter "Lokale Add-ons" erscheint **SHM2 Shelly Emulator**.
   - Klicke darauf und wähle **Installieren**.
   - Der Build dauert auf Basis von Alpine 3.20 nur ca. 5–10 Sekunden.
5. **Konfiguration anpassen**:
   - Öffne den Reiter **Konfiguration** des Add-ons.
   - Wähle als Datenquelle `speedwire` (Standard).
   - Falls du mehrere SMA-Geräte im Netz hast, trage optional die Seriennummer deines SHM2 ein.
   - Klicke auf **Speichern**.
6. **Starten & Überwachen**:
   - Klicke auf **Starten** und aktiviere "Beim Booten starten" sowie "In der Seitenleiste anzeigen".
   - Prüfe den Reiter **Protokoll**: Dort erscheint alle 10 Sekunden der aktuelle Zählerstand und die Phasensaldierung.
   - Klicke auf **Web-UI öffnen**, um das Live-Dashboard aufzurufen.

---

## 7. Wichtige Home Assistant Architektur-Details

> [!IMPORTANT]
> Bei Anpassungen an `config.yaml` und `Dockerfile` müssen zwingend folgende Regeln beachtet werden:

1. **`host_network: true`**:
   - Ist zwingend erforderlich, damit der Container die UDP-Multicast-Pakete (`239.12.255.254:9522`) und mDNS (`5353`) ohne NAT/Bridge empfangen kann.
2. **KEINE `ports:`-Sektion in `config.yaml`**:
   - Wenn `host_network: true` gesetzt ist, **verbietet** der Home Assistant Supervisor eine `ports:`-Mapping-Sektion (`Cannot use host_network with ports`). Die Ports (80, 1010, 8099) sind durch Host-Networking automatisch direkt auf dem Host offen.
3. **`init: false` in `config.yaml`**:
   - Verhindert, dass der Supervisor seinen eigenen `s6-overlay`-Manager injectet, was sonst zu Abstürzen (`can only run as pid 1`) führt.
4. **KEINE `build.yaml` verwenden**:
   - Modernes Home Assistant (BuildKit) benötigt kein `build.yaml`. Das `Dockerfile` nutzt direkt `FROM alpine:3.20` mit den nativen Paketen `py3-aiohttp` und `py3-zeroconf`.

---

## 8. Einbindung in die Anker App (SOLIX Solarbank 3 Pro)

1. **Voraussetzung**: Smartphone, Home Assistant und Solarbank müssen im selben WLAN / Subnetz sein.
2. Öffne die **Anker App** und wähle die **Solarbank 3 Pro** aus.
3. Gehe zu **Einstellungen** > **Smart Meter / Zähler hinzufügen**.
4. Wähle **Shelly Pro 3EM**.
5. Die App entdeckt den Emulator automatisch via mDNS. Falls eine manuelle IP-Abfrage erfolgt, gib die IP-Adresse deines Home Assistant Hosts ein.
6. Die Solarbank liest ab sofort im Sekundentakt die phasengenaue Wirkleistung (`EM.GetStatus`) für die Nulleinspeisung aus.

### Schnelltest per Terminal (`curl`)
Von jedem beliebigen Rechner im selben Heimnetzwerk:
```bash
# Live-Werte (Leistung L1-L3, Gesamtleistung, Spannung, Strom)
curl http://<HOME_ASSISTANT_IP>/rpc/EM.GetStatus?id=0

# Geräte-Informationen
curl http://<HOME_ASSISTANT_IP>/rpc/Shelly.GetDeviceInfo

# Energiezähler (Import/Export in Wh)
curl http://<HOME_ASSISTANT_IP>/rpc/EMData.GetStatus?id=0
```

---

## 9. Fehlerbehebung (Troubleshooting)

| Problem | Ursache | Lösung |
| :--- | :--- | :--- |
| **"Waiting for first grid telemetry packet..."** | Multicast wird vom Netzwerk-Switch blockiert | Im Switch **IGMP Snooping** testweise deaktivieren oder sicherstellen, dass SHM2 und HA im selben VLAN/Subnetz sind. Alternativ `data_source: homeassistant` wählen. |
| **"Error updating / build failed"** | Supervisor hängt am alten Build-Cache fest | Add-on in Home Assistant **Deinstallieren**, Seite neu laden, und sauber auf **Installieren** klicken. |
| **"can only run as pid 1"** | s6-overlay Konflikt | Sicherstellen, dass `init: false` in `config.yaml` steht. |
| **Solarbank regelt verkehrt herum** | Vorzeichen-Konvention | In den Add-on Optionen `invert_sign: true` setzen. |
| **Unbekannte Anfragen der Anker App** | Anker nutzt neue RPC-Methoden | `debug_requests: true` aktivieren. Im Ingress-Dashboard oder im Log wird jede Anfrage mit Pfad, Body und Quell-IP geloggt. Fehlende Methoden können in `shelly_service.py` ergänzt werden. |
