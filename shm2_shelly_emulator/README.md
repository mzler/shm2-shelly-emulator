# SHM2 Shelly Pro 3EM Emulator

[English](#english) | [Deutsch](#deutsch)

---

<a name="english"></a>
## English

Home Assistant add-on that reads real-time grid measurements from an **SMA Sunny Home Manager 2.0 (SHM2)** or **SMA Energy Meter** via **Speedwire Multicast** (or from existing Home Assistant entities) and exposes them on your local network as a fully emulated **Shelly Pro 3EM (Gen 2)**.

This enables devices like the **Anker SOLIX Solarbank 3 Pro** (and other solar/battery storage systems supporting Shelly Pro 3EM) to use your existing SMA Sunny Home Manager 2.0 as their grid power meter for dynamic zero-feed-in regulation and battery charging control without buying extra smart meter hardware.

---

### Key Features

* **Direct SMA Speedwire Multicast Receiver**:
  * Listens on `239.12.255.254:9522` for standard SMA EMETER datagrams (`0x6069`).
  * Dynamic OBIS parser: extracts active import/export power (totals & per phase L1–L3), apparent power, voltage, current, power factor, grid frequency (50 Hz), and energy counters (Wh/kWh).
  * Optional serial number filter (`shm2_serial`) to ignore packets from other SMA devices.
* **Alternative Home Assistant Entity Bridge**:
  * Can poll existing Home Assistant power/voltage sensors via the Supervisor API if multicast cannot cross network subnets.
* **Full Shelly Pro 3EM (Gen 2) Emulation**:
  * **HTTP JSON-RPC on port 80**: Supports both `POST /rpc` (JSON-RPC 2.0 frames) and `GET /rpc/<Method>?<params>` (e.g. `GET /rpc/EM.GetStatus?id=0`).
  * **UDP RPC on port 1010**: Listens for JSON-RPC frames over UDP and responds to client datagrams.
  * Methods implemented:
    * `Shelly.GetDeviceInfo` (Model `SPEM-003CEBEU`, Gen 2, App `Pro3EM`)
    * `Shelly.GetStatus` (Bundled component status)
    * `Shelly.GetConfig` (Full configuration tree)
    * `EM.GetStatus` (id: 0, live per-phase & total metrics: power, current, voltage, pf, freq)
    * `EM.GetConfig` (id: 0, CT configuration)
    * `EMData.GetStatus` (id: 0, accumulated active energy in Wh)
    * `Sys.GetStatus` & `Sys.GetConfig`
    * `Wifi.GetStatus` & `Eth.GetStatus`
    * `Shelly.ListMethods`
* **Sign Convention**:
  * Standard convention: Positive (+) = Grid Import / Consumption, Negative (-) = Grid Export / PV Feed-in.
  * Configurable `invert_sign` option to invert if your downstream inverter requires reversed signs.
* **Stale Data Protection**:
  * Returns last known values for high responsiveness (~1 s update rate).
  * If no telegram arrives for `stale_timeout` seconds (default: 10 s), it returns an explicit error flag (`power_meter_failure`, `data_stale`) and logs a warning instead of reporting outdated/fake measurements.
* **mDNS / Zeroconf Announcement**:
  * Advertises `_shelly._tcp.local.` and `_http._tcp.local.` with matching TXT records (`gen=2`, `app=Pro3EM`, `ver=1.4.4`, `id=shellypro3em-<mac>`, `model=SPEM-003CEBEU`).
* **Debug & Discovery Monitor**:
  * Logs all incoming HTTP and UDP requests (client IP, method, path, params, body, response) to console and into a ring buffer.
  * Returns clean JSON-RPC errors for unknown methods instead of crashing.
* **Home Assistant Ingress Web Dashboard**:
  * Modern web UI (accessible in Home Assistant sidebar) showing live power dials, per-phase metrics, energy counters, device identity, and a live request monitor.

---

### Installation in Home Assistant

#### Method 1: Local Add-on (Recommended for Pair Programming / Custom Repos)
1. In Home Assistant, install the **Samba Share** or **SSH & Web Terminal** add-on.
2. Navigate to your Home Assistant `addons/` directory.
3. Create a folder named `shm2_shelly_emulator` (or copy this entire repository directory into `/addons/shm2_shelly_emulator`).
4. In Home Assistant, open **Settings** &rarr; **Add-ons** &rarr; **Add-on Store**.
5. Click the top-right menu (three dots) &rarr; **Check for updates**.
6. Under "Local add-ons", select **SHM2 Shelly Emulator** and click **Install**.
7. In the add-on configuration tab, configure your options (or keep defaults) and click **Start**.

#### Method 2: Git Repository in Add-on Store
1. Open **Settings** &rarr; **Add-ons** &rarr; **Add-on Store**.
2. Click the three dots menu in the top right &rarr; **Repositories**.
3. Add the URL of your repository and click **Add**.
4. Refresh and install **SHM2 Shelly Emulator**.

---

### Configuration Options (`config.yaml`)

| Option | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `data_source` | string | `speedwire` | Data source: `speedwire` (direct multicast) or `homeassistant` (entity polling). |
| `shm2_serial` | string | `""` | Optional serial number of your SHM2 (e.g. `3002850058` or `0xb2fbdb0a`). If empty, accepts all SMA EM datagrams. |
| `network_interface` | string | `""` | Optional network interface (e.g. `eth0`, `en0`). Leave empty for all interfaces. |
| `multicast_ip` | string | `239.12.255.254` | SMA Speedwire multicast IP. |
| `multicast_port` | integer | `9522` | SMA Speedwire UDP port. |
| `http_port` | integer | `80` | Port for the Shelly HTTP API (must be `80` for standard Shelly discovery). |
| `udp_port` | integer | `1010` | Port for Shelly UDP RPC. |
| `ingress_port` | integer | `8239` | Internal port used by Home Assistant Ingress for the Web UI. |
| `emulated_mac` | string | `""` | Custom MAC address (e.g. `34:98:7A:5B:CD:12`). If empty, a stable MAC is automatically generated and preserved. |
| `device_name` | string | `Shelly Pro 3EM` | Display name for the emulated device. |
| `firmware_version` | string | `1.4.4` | Emulated firmware version reported to apps. |
| `invert_sign` | boolean | `false` | Invert active power signs and swap import/export energy counters. |
| `stale_timeout` | integer | `10` | Seconds without data before flagging measurements as stale. |
| `log_level` | string | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `debug_requests` | boolean | `true` | Log every incoming HTTP/UDP request for discovery & debugging. |
| `ha_entities` | mapping | *(see below)* | Entity mapping when `data_source: homeassistant`. |

---

### Multicast, IGMP Snooping, and VLAN Notes

* **`host_network: true` is required**:
  The add-on runs with `host_network: true` so Docker does not isolate the network. This allows receiving multicast UDP frames on `239.12.255.254:9522`, sending mDNS announcements on port `5353`, and binding to ports 80 and 1010 directly.
* **Network Switches & IGMP Snooping**:
  Some managed network switches drop multicast packets if IGMP snooping is enabled and no IGMP querier is active. If the add-on logs `Waiting for first grid telemetry packet`, temporarily disable IGMP snooping on your switch or verify that multicast forwarding is permitted.
* **Same Subnet / VLAN Recommendation**:
  Keep the SMA Sunny Home Manager 2.0, the Home Assistant host, and the Anker SOLIX Solarbank on the **same LAN / subnet**. Multicast and mDNS do not cross routers/VLANs unless a multicast proxy/reflector (e.g. `avahi-daemon` or `smcroute`) is explicitly configured on your router.

---

### Connecting in the Anker App (SOLIX Solarbank 3 Pro)

1. Ensure the add-on is running and reports live grid telemetry in the logs.
2. Open the **Anker** app on your smartphone (connected to the same Wi-Fi network).
3. Navigate to your **Solarbank 3 Pro** &rarr; **Settings** &rarr; **Grid Meter / Smart Meter**.
4. Select **Shelly Pro 3EM** (or search for local smart meters).
5. The Anker app will discover the emulated Shelly via mDNS (`_shelly._tcp`).
6. If the app prompts for an IP address, enter the **IP address of your Home Assistant host**.
7. The Anker app will query `EM.GetStatus` and verify communication.
8. If the app sends unexpected RPC calls, check the **Incoming Request Log** in the add-on dashboard or add-on logs to see the exact request and response.

---

### Testing & Verification with `curl`

Test the emulated endpoints from any computer on your network:

```bash
# 1. Check real-time instantaneous measurements
curl http://<HOME_ASSISTANT_IP>/rpc/EM.GetStatus?id=0

# 2. Check device metadata and generation
curl http://<HOME_ASSISTANT_IP>/rpc/Shelly.GetDeviceInfo

# 3. Check accumulated energy counters (Wh)
curl http://<HOME_ASSISTANT_IP>/rpc/EMData.GetStatus?id=0

# 4. Check full status bundle
curl http://<HOME_ASSISTANT_IP>/rpc/Shelly.GetStatus

# 5. Test JSON-RPC 2.0 POST frame
curl -X POST -H "Content-Type: application/json" \
  -d '{"id": 1, "method": "EM.GetStatus", "params": {"id": 0}}' \
  http://<HOME_ASSISTANT_IP>/rpc
```

---

<a name="deutsch"></a>
## Deutsch

Home Assistant Add-on, das die Echtzeit-Netzmessdaten eines **SMA Sunny Home Manager 2.0 (SHM2)** oder **SMA Energy Meter** per **Speedwire-Multicast** (oder alternativ aus bestehenden Home Assistant Entitäten) ausliest und als vollständig emulierten **Shelly Pro 3EM (Gen 2)** im lokalen Netzwerk bereitstellt.

Dadurch kann z. B. die **Anker SOLIX Solarbank 3 Pro** (sowie andere Speicher- und Wechselrichtersysteme mit Shelly-Unterstützung) den vorhandenen SMA SHM2 direkt als Smart Meter für die dynamische Nulleinspeisung und Batteriesteuerung nutzen, ohne dass ein zusätzlicher Shelly-Zähler im Sicherungskasten verbaut werden muss.

---

### Funktionsumfang

* **Direkter SMA Speedwire Multicast Empfänger**:
  * Verbindet sich mit Multicast-Gruppe `239.12.255.254:9522` für SMA EMETER-Telegramme (`0x6069`).
  * Dynamischer OBIS-Parser: Liest Wirkleistung Einspeisung/Bezug (Summe und Phasen L1–L3), Scheinleistung, Spannung, Stromstärke, Leistungsfaktor, Netzfrequenz (50 Hz) und Energiezähler (Wh/kWh) aus.
  * Optionaler Seriennummernfilter (`shm2_serial`), um bei mehreren SMA-Geräten gezielt den gewünschten Zähler auszuwählen.
* **Alternative Home Assistant Entitäten-Brücke**:
  * Liest alternativ konfigurierte Home Assistant Sensoren über die Supervisor API aus, falls Multicast über Subnetzgrenzen hinweg nicht weitergeleitet wird.
* **Vollständige Shelly Pro 3EM (Gen 2) Emulation**:
  * **HTTP JSON-RPC auf Port 80**: Unterstützt `POST /rpc` (JSON-RPC 2.0) und `GET /rpc/<Method>?<params>` (z. B. `GET /rpc/EM.GetStatus?id=0`).
  * **UDP RPC auf Port 1010**: Empfängt JSON-RPC Pakete über UDP und antwortet an den Absender.
  * Implementierte Methoden:
    * `Shelly.GetDeviceInfo` (Modell `SPEM-003CEBEU`, Gen 2, App `Pro3EM`)
    * `Shelly.GetStatus` (Gesamtstatus aller Komponenten)
    * `Shelly.GetConfig` (Vollständige Konfiguration)
    * `EM.GetStatus` (id: 0, Echtzeit-Messwerte aller 3 Phasen und Summen)
    * `EM.GetConfig` (id: 0, Wandler-Konfiguration)
    * `EMData.GetStatus` (id: 0, aufgelaufene Energiezähler in Wh)
    * `Sys.GetStatus` & `Sys.GetConfig`
    * `Wifi.GetStatus` & `Eth.GetStatus`
    * `Shelly.ListMethods`
* **Vorzeichen-Konvention**:
  * Standard: Positiv (+) = Netzbezug, Negativ (-) = Netzeinspeisung.
  * Option `invert_sign` kehrt die Vorzeichen um, falls das Zielgerät eine umgekehrte Logik erwartet.
* **Schutz vor veralteten Daten (Stale Timeout)**:
  * Aktualisierungsrate ca. 1 Sekunde; gibt die zuletzt bekannten Messwerte zurück.
  * Bleiben Messdaten für mehr als `stale_timeout` Sekunden (Standard: 10 s) aus, wird eine Warnung protokolliert und das Fehlerflag `power_meter_failure` / `data_stale` gesetzt.
* **mDNS / Zeroconf Erkennung**:
  * Veröffentlicht `_shelly._tcp.local.` und `_http._tcp.local.` mit TXT-Records (`gen=2`, `app=Pro3EM`, `ver=1.4.4`, `id=shellypro3em-<mac>`, `model=SPEM-003CEBEU`), damit die Anker App den Zähler automatisch erkennt.
* **Debug- & Discovery-Modus**:
  * Protokolliert jede eingehende HTTP- und UDP-Anfrage (Quell-IP, Methode, Pfad, Body, Antwort) im Log und in einem Ringspeicher.
  * Unbekannte Methoden führen nicht zum Absturz, sondern erhalten eine standardkonforme JSON-RPC Fehlermeldung (`-32601`).
* **Home Assistant Ingress Web-Dashboard**:
  * Modernes Web-Interface in der Home Assistant Seitenleiste mit Live-Leistungsanzeige, Phasenwerten, Energiezählern und interaktiver Anfrage-Tabelle.

---

### Installation als lokales Add-on in Home Assistant

1. In Home Assistant das **Samba Share** oder **Terminal & SSH** Add-on öffnen.
2. In den Ordner `/addons/` navigieren.
3. Einen Unterordner namens `shm2_shelly_emulator` anlegen und alle Dateien dieses Repositories hineinkopieren.
4. In Home Assistant zu **Einstellungen** &rarr; **Add-ons** &rarr; **Add-on Store** wechseln.
5. Oben rechts auf das Drei-Punkte-Menü klicken &rarr; **Nach Updates suchen**.
6. Unter "Lokale Add-ons" das **SHM2 Shelly Emulator** Add-on auswählen und auf **Installieren** klicken.
7. Unter **Konfiguration** ggf. Einstellungen anpassen und das Add-on **Starten**.

---

### Visual Automation Builder & Intelligente Solarbank-Steuerung (v1.1.0)

Da die offizielle Anker Smartphone-App das Hinzufügen von Shelly Smart Metern ausschließlich über einen Allterco Shelly Cloud OAuth-Login erlaubt (wodurch Software-Emulatoren in der App blockiert sind), bietet dieses Add-on ab Version 1.1.0 eine **voll integrierte visuelle Steuerung**:

Über das integrierte Ingress-Web-Dashboard steuert das Add-on die Solarbank direkt über die in Home Assistant installierte Anker-Integration (`ha-anker-solix` oder Modbus TCP) – unter direkter Nutzung der SMA SHM2 Speedwire-Echtzeitdaten!

#### Die 4 Tabs im Web-Dashboard:
1. **Live-Monitor (SMA Telemetrie)**:
   * Große Anzeige der Netzwirkleistung (+ Bezug / − Einspeisung) mit Richtungsanzeige und Aktualisierungsalter.
   * Phasengenaue Kacheln für L1, L2 und L3 mit Wirkleistung (W), Spannung (V), Strom (A), Power Factor ($\cos \phi$), Netzfrequenz (Hz) sowie getrennten Zählerständen für Bezug und Einspeisung (kWh).
   * Live-Status der angebundenen Anker Solarbank (Akkustand %, Solarladung W, aktuelle Abgabe W).
2. **Automations-Builder**:
   * Master-Schalter zum Aktivieren/Pausieren der Regelung per Klick.
   * Auswahl der intelligenten Regelungsmodi (Nulleinspeisung, Spitzenlastkappung, Nacht-Grundlast, Akku-Priorität).
   * Automatische Entitätserkennung aus Home Assistant mit Vorfilterung für Anker Solarbank Entitäten (`number.*preset_discharge_power`, `sensor.*battery_percentage` etc.).
   * Einstellbare Parameter: Mindest- und Maximalleistung (0–800 W), Hysterese/Totband (W), Aktualisierungsintervall (s), Mindest-Akkustand (Min SoC %) zum Schutz der Batterie.
3. **Regelungs-Protokoll**:
   * Live-Tabelle der letzten 50 Steuerungsentscheidungen mit Zeitstempel, Modus, Netzleistung, Akku %, Solarleistung, altem und neuem Sollwert sowie verständlicher Begründung.
4. **Shelly RPC Monitor**:
   * Live-Inspektor aller eingehenden HTTP- und UDP-Anfragen an die Shelly-Schnittstelle zur Fehlerdiagnose.

#### Die 4 Optimierungs-Modi im Detail:
* **Dynamische Nulleinspeisung (Grid Following)**: Passt die Entladeleistung der Solarbank kontinuierlich so an, dass der Netzbezug am SMA Home Manager genau 0 W beträgt ($P_{\text{Ziel}} = P_{\text{aktuell}} + P_{\text{Netzbezug}}$).
* **Spitzenlastkappung (Peak Shaving)**: Die Solarbank speist nur dann ein, wenn der Hausverbrauch einen definierten Schwellenwert (z. B. 1000 W) übersteigt, um Lastspitzen abzufedern und den Akku für die teuersten Verbrauchsphasen zu schonen.
* **Nacht-Grundlast (Constant Base Load)**: Speist während der Nachtstunden (wenn keine PV-Erzeugung anliegt) eine feste Grundlast (z. B. 150 W) ein.
* **Akku-Priorität (Solar Priority)**: Entlädt den Akku erst dann, wenn er mindestens bis zu einem gewünschten Ziel-Akkustand (z. B. 80 %) aufgeladen wurde.

---

### Wichtige Hinweise zu Multicast, IGMP-Snooping und VLANs

* **`host_network: true` ist zwingend erforderlich**:
  Das Add-on benötigt direkten Zugriff auf das Host-Netzwerk, um Multicast-Pakete auf Port 9522 zu empfangen und mDNS auf Port 5353 auszusenden.
* **Managed Switches & IGMP-Snooping**:
  Bei manchen Switches (z. B. UniFi, TP-Link, Netgear) führt aktiviertes IGMP-Snooping ohne IGMP-Querier dazu, dass Multicast-Pakete nach wenigen Sekunden verworfen werden. Falls das Add-on keine Pakete empfängt, IGMP-Snooping testweise deaktivieren.
* **Gleiches Subnetz**:
  Der SMA SHM2, der Home Assistant Host und die Anker Solarbank sollten sich im **gleichen Netzwerk/Subnetz** befinden, da Multicast-Telegramme ohne speziellen Relay-Dienst (mDNS-Reflector / PIM) nicht über Router oder getrennte VLANs geroutet werden.

---

### Einbindung in der Anker App (SOLIX Solarbank 3 Pro)

1. Add-on starten und im Log prüfen, ob Messwerte empfangen werden (`[LIVE SPEEDWIRE] Net: ...`).
2. Die **Anker** App auf dem Smartphone im selben WLAN öffnen.
3. Zur **Solarbank 3 Pro** navigieren &rarr; **Einstellungen** &rarr; **Netzzähler / Smart Meter hinzufügen**.
4. **Shelly Pro 3EM** auswählen.
5. Der emulierte Shelly wird über mDNS automatisch im Netzwerk gefunden.
6. Falls nach einer IP-Adresse gefragt wird, die **IP-Adresse des Home Assistant Hosts** eingeben.
7. Die Anker Solarbank fragt nun sekündlich `EM.GetStatus` ab und passt die Einspeisung dynamisch an den aktuellen Hausverbrauch an.
8. Im Add-on Web-Dashboard unter **Incoming Request Log** kann der Datenverkehr live mitverfolgt werden.

---

### Testen & Prüfen mit `curl`

Von einem beliebigen Rechner im lokalen Netzwerk ausführen:

```bash
# 1. Momentanwerte aller Phasen abfragen
curl http://<HOME_ASSISTANT_IP>/rpc/EM.GetStatus?id=0

# 2. Geräte-Metadaten abfragen
curl http://<HOME_ASSISTANT_IP>/rpc/Shelly.GetDeviceInfo

# 3. Zählerstände abfragen (Wh)
curl http://<HOME_ASSISTANT_IP>/rpc/EMData.GetStatus?id=0

# 4. JSON-RPC POST Test
curl -X POST -H "Content-Type: application/json" \
  -d '{"id": 1, "method": "EM.GetStatus", "params": {"id": 0}}' \
  http://<HOME_ASSISTANT_IP>/rpc
```
