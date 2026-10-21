# EnOcean Proxy

A Home Assistant app (formerly add-on) that shares EnOcean gateways with several programs
and replays telegrams missed while Home Assistant restarts. It belongs to the
[EnOcean Eltako integration](https://github.com/bertel2020/HA-Enocean-Eltako) (phase 6) and
works with anything that speaks raw ESP2 or ESP3 over TCP.

> Needs Home Assistant OS or Supervised (apps run under the Supervisor). Not tested on real
> hardware yet.

## Installation

[![Add the repository to My Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fbertel2020%2FHA-Enocean-Proxy)

1. Add this repository with the button, or in Home Assistant under **Settings → Apps → App
   store → ⋮ → Repositories** with `https://github.com/bertel2020/HA-Enocean-Proxy`.
2. Install **EnOcean Proxy**, enter your gateways in its configuration and start it.
3. The gateways show up in Home Assistant as discovered (**EnOcean Proxy**): confirm and pick
   the model.

Details: [enocean_proxy/DOCS.md](enocean_proxy/DOCS.md).

## How it works

- One proxy per gateway keeps the serial port or TCP connection and reconnects on its own.
- Every telegram from the gateway goes to every connected program.
- Telegrams from the programs are cut at frame boundaries and sent one at a time.
- The gateway's answer to a command goes to the program that sent it.
- A program that sends `EOPX REPLAY` right after connecting first gets the radio telegrams
  that arrived while no such program was connected, in order (up to `replay_size`, none
  older than `replay_max_age_s`).
- A status page in the sidebar (ingress), one gateway at a time in four tabs: an
  **overview** that explains what needs attention (connection, programs, replay buffer, air
  time against the 1 % duty cycle), the **telegrams** live with sender IDs, search and
  filters, **captures** (e.g. PCT14 with a FAM14, optionally with the gateway reserved for
  one program) and **tools** (gateway test, sending a single frame with checksum check).
- Replay can carry each telegram's reception time (`EOPX REPLAY TS`).
- Home Assistant finds the gateways itself (Supervisor discovery). The ports stay inside the
  Supervisor network unless you open them; `allowed_clients` limits who may connect.

## Development

```bash
python -m pip install -r requirements_test.txt
ruff check .
mypy
python -m pytest
```

CI publishes prebuilt images for every new version on `main`
(`ghcr.io/bertel2020/{amd64,aarch64}-enocean-proxy`); `config.yaml` names them as `image`,
so the Supervisor pulls them instead of building. Bump `version` with every change: the
publish job skips a version that already exists.

## License

MIT, see [LICENSE](LICENSE).
