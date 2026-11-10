# Home Assistant Add-on: SHM2 Shelly Pro 3EM Emulator

[![Open your Home Assistant instance and show the add-on store with a specific repository url filled in.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fmzler%2Fshm2-shelly-emulator)

[English](#english) | [Deutsch](#deutsch)

---

<a name="deutsch"></a>
## Deutsch

Offizielles Home Assistant Add-on Repository zur Emulation eines **Shelly Pro 3EM (Gen 2)** unter Verwendung von Echtzeit-Netzmessdaten eines **SMA Sunny Home Manager 2.0 (SHM2)** oder **SMA Energy Meter** per **Speedwire-Multicast** (oder alternativ aus bestehenden Home Assistant Sensoren).

### Das Ziel
Moderne Solar- und Speichersysteme wie die **Anker SOLIX Solarbank 3 Pro** unterstützen in der Hersteller-App den **Shelly Pro 3EM** als Smart Meter für die dynamische Nulleinspeisung und die präzise Batteriesteuerung. Wenn du bereits einen SMA Sunny Home Manager 2.0 oder ein SMA Energy Meter besitzt, ermöglicht dir dieses Add-on, den SHM2 direkt als Shelly Pro 3EM im Netzwerk bereitzustellen – ohne zusätzliche Zähler-Hardware!

---

### Installation in Home Assistant

#### Methode 1: 1-Klick Installation via My Home Assistant (Empfohlen)

Klicke einfach auf den folgenden Button, um das Repository direkt zu deinem Home Assistant hinzuzufügen:

[![Repository zu Home Assistant hinzufügen](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fmzler%2Fshm2-shelly-emulator)

#### Methode 2: Manuell im Add-on Store hinzufügen

1. Öffne dein **Home Assistant**.
2. Gehe zu **Einstellungen** &rarr; **Add-ons** &rarr; **Add-on Store** (unten rechts).
3. Klicke oben rechts auf das **Drei-Punkte-Menü** &rarr; **Repositories**.
4. Füge folgende URL ein und klicke auf **Hinzufügen**:
   ```text
   https://github.com/mzler/shm2-shelly-emulator
   ```
5. Schließe das Dialogfeld und lade die Seite einmal neu.
6. Im Store erscheint die neue Kategorie **SHM2 Shelly Emulator Repository**.
7. Klicke auf **SHM2 Shelly Emulator** &rarr; **Installieren**.
8. Klicke auf **Starten** (aktiviere optional „Beim Booten starten“ und „In der Seitenleiste anzeigen“).

---

### Features & Highlights

* **Echter SMA Speedwire Empfänger**:
  * Lauscht auf Multicast `239.12.255.254:9522` (SMA EMETER Protokoll `0x6069`).
  * Dynamischer OBIS-Parser für Wirkleistung (gesamt und pro Phase L1–L3), Scheinleistung, Spannung, Strom, Netzfrequenz (50 Hz), Power Factor und kumulierte Energiezähler (Wh/kWh).
  * Optionaler Seriennummern-Filter (`shm2_serial`).
* **Alternative Home Assistant Entitätsquelle**:
  * Kann Werte aus bestehenden Sensoren (`sensor.grid_power` etc.) per Home Assistant API abfragen.
* **Vollständige Shelly Pro 3EM (Gen 2) Emulation**:
  * **HTTP JSON-RPC** auf Port 80 (`POST /rpc` und `GET /rpc/<Method>?<params>`).
  * **UDP JSON-RPC** auf Port 1010.
  * Unterstützte Methoden: `Shelly.GetDeviceInfo`, `Shelly.GetStatus`, `Shelly.GetConfig`, `EM.GetStatus` (id: 0), `EM.GetConfig`, `EMData.GetStatus` (id: 0), `Sys.GetStatus`, `Wifi.GetStatus` uvm.
* **Automatische mDNS/Zeroconf Erkennung**:
  * Sendet `_shelly._tcp` und `_http._tcp` mit realistischen TXT-Records (`model=SPEM-003CEBEU`, `gen=2`, `app=Pro3EM`), damit die Anker App das Gerät sofort im WLAN findet.
* **Ingress Web-Dashboard (Port 8099)**:
  * Integriertes Dashboard direkt in der Home Assistant Seitenleiste mit Live-Leistungsanzeige und Request-Logger für unbekannte Anfragen.

---

### Einrichtung in der Anker App (SOLIX Solarbank 3 Pro)

1. Vergewissere dich, dass das Add-on läuft und im Log Live-Messwerte anzeigt.
2. Öffne die **Anker App** auf deinem Smartphone (im selben WLAN).
3. Öffne deine **Solarbank 3 Pro** &rarr; **Einstellungen** &rarr; **Smart Meter**.
4. Wähle **Shelly Pro 3EM** aus.
5. Die App findet den emulierten Zähler automatisch über mDNS.
6. Falls eine IP-Abfrage erfolgt: Gib die **IP-Adresse deines Home Assistant Hosts** ein.
7. Die Solarbank liest ab sofort im Sekundentakt die phasengenaue Wirkleistung (`EM.GetStatus`) für die Nulleinspeisung aus.

---

<a name="english"></a>
## English

Home Assistant Add-on repository that emulates a **Shelly Pro 3EM (Gen 2)** from real-time **SMA Sunny Home Manager 2.0 (SHM2)** or **SMA Energy Meter** Speedwire multicast packets (or from existing Home Assistant sensor entities).

### Purpose
Devices such as the **Anker SOLIX Solarbank 3 Pro** natively support the **Shelly Pro 3EM** grid meter in their app for zero-feed-in regulation and dynamic battery charging control. If you have an SMA Home Manager installed, this add-on enables you to reuse your existing SMA meter without purchasing additional Shelly hardware.

### Installation
Add `https://github.com/mzler/shm2-shelly-emulator` to your Home Assistant Add-on Store repositories, or click:

[![Add repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fmzler%2Fshm2-shelly-emulator)
