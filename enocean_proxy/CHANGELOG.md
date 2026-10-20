# Changelog

## 0.2.0

- Home Assistant finds the gateways by itself (Supervisor discovery): set up from the
  "discovered" card, replay switched on.
- Ports are no longer opened on the host by default; Home Assistant reaches the app inside
  the Supervisor network. Optional `allowed_clients` when you open them.
- Replay keeps radio telegrams only (no FAM14 bus polling) and none older than
  `replay_max_age_s` (default 15 minutes).
- The gateway's answer to a command goes to the client that sent it.
- A client that stops reading is disconnected instead of filling the memory.
- Serial ports are opened exclusively; a health check restarts a hanging app; ports outside
  9100 to 9103 are refused.

## 0.1.0

- First version: one proxy per gateway (serial port or TCP), any number of TCP clients,
  client telegrams sent one at a time, replay of telegrams missed during a Home Assistant
  restart for the enocean_eltako integration.
