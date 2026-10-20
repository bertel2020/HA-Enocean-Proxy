# EnOcean Proxy

Eine Home-Assistant-App (früher Add-on), die EnOcean-Gateways mit mehreren Programmen teilt
und Telegramme nachliefert, die während eines Neustarts von Home Assistant ankamen. Sie gehört
zur [EnOcean-Eltako-Integration](https://github.com/bertel2020/HA-Enocean-Eltako) (Phase 6)
und funktioniert mit allem, was rohes ESP2 oder ESP3 über TCP spricht.

> Braucht Home Assistant OS oder Supervised (Apps laufen unter dem Supervisor). Noch nicht mit
> echter Hardware getestet.

## Installation

[![Repository zu My Home Assistant hinzufügen](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fbertel2020%2FHA-Enocean-Proxy)

1. Dieses Repository über den Button hinzufügen, oder in Home Assistant unter
   **Einstellungen → Apps → App-Store → ⋮ → Repositories** mit
   `https://github.com/bertel2020/HA-Enocean-Proxy`.
2. **EnOcean Proxy** installieren, die Gateways in der Konfiguration eintragen und starten.
3. Die Gateways erscheinen in Home Assistant als entdeckt (**EnOcean Proxy**): bestätigen und
   Modell wählen.

Details: [enocean_proxy/DOCS.md](enocean_proxy/DOCS.md).

## Funktionsweise

- Ein Proxy je Gateway hält den seriellen Port oder die TCP-Verbindung und verbindet sich bei
  Abbruch selbst neu.
- Jedes Telegramm vom Gateway geht an jedes verbundene Programm.
- Telegramme der Programme werden an Rahmengrenzen getrennt und einzeln gesendet.
- Die Antwort des Gateways auf einen Befehl geht an das Programm, das ihn gesendet hat.
- Ein Programm, das direkt nach dem Verbinden `EOPX REPLAY` sendet, bekommt zuerst die
  Funktelegramme, die ankamen, während kein solches Programm verbunden war, in ihrer Reihenfolge
  (bis `replay_size`, keine älter als `replay_max_age_s`).
- Eine Statusseite in der Seitenleiste (Ingress) zeigt Verbindungen, Programme, Zähler,
  den Nachlieferungs-Puffer, die Funkbelegung gegenüber dem 1-%-Limit und die letzten Rahmen
  live; sie nimmt Mitschnitte auf (z. B. PCT14 mit FAM14, auf Wunsch mit dem Gateway nur für
  ein Programm), testet das Gateway und sendet einzelne Rahmen.
- Die Nachlieferung kann die Empfangszeit jedes Telegramms mitschicken (`EOPX REPLAY TS`).
- Home Assistant findet die Gateways selbst (Supervisor-Discovery). Die Ports bleiben im
  Supervisor-Netz, solange sie nicht freigegeben sind; `allowed_clients` begrenzt, wer sich
  verbinden darf.

## Entwicklung

```bash
python -m pip install -r requirements_test.txt
ruff check .
mypy
python -m pytest
```

Die CI veröffentlicht für jede neue Version auf `main` fertige Images
(`ghcr.io/bertel2020/{amd64,aarch64}-enocean-proxy`). Die App wird bei der Installation noch
lokal gebaut, bis die Pakete öffentlich sind und `config.yaml` sie als `image` nennt.

## Lizenz

MIT, siehe [LICENSE](LICENSE).
