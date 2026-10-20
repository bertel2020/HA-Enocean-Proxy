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
3. In der EnOcean-Eltako-Integration das Gateway über das Netzwerk anlegen: Host `127.0.0.1`,
   Port 9100 (erstes Gateway).

Details: [enocean_proxy/DOCS.md](enocean_proxy/DOCS.md).

## Funktionsweise

- Ein Proxy je Gateway hält den seriellen Port oder die TCP-Verbindung und verbindet sich bei
  Abbruch selbst neu.
- Jedes Telegramm vom Gateway geht an jedes verbundene Programm.
- Telegramme der Programme werden an Rahmengrenzen getrennt und einzeln gesendet.
- Ein Programm, das direkt nach dem Verbinden `EOPX REPLAY` sendet, bekommt zuerst die
  Telegramme, die ankamen, während kein solches Programm verbunden war, in ihrer Reihenfolge
  (bis `replay_size`).

## Entwicklung

```bash
python -m pip install -r requirements_test.txt
ruff check .
mypy
python -m pytest
```

## Lizenz

MIT, siehe [LICENSE](LICENSE).
