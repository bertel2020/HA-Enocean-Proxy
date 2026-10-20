# Changelog

## 0.5.2

- Discovery: all gateways go to Home Assistant in one message. The Supervisor keeps one
  discovery message per app, so with several gateways only the last one used to appear.
  Needs the EnOcean Eltako integration from 2026-10-20 or newer; older versions see the
  first gateway only.

## 0.5.1

- Status page reviewed: four main figures plus an "issues" line instead of eight tiles
  full of zeros; "connecting" instead of "disconnected" before the first error; one
  sticky bar for live/pause and the frame filter instead of buttons in every card that
  acted on all of them; frames as a stacked list on phones; labels for screen readers;
  sending a frame asks for confirmation; send and test are refused while a capture
  reserves the gateway; clearer error messages; no polling in hidden tabs; reduced motion.

## 0.5.0

- Read-only status on port 9199 inside the Supervisor network: the EnOcean Eltako
  integration shows the app's version, connected programs, air time and an active send
  lock on its gateway cards. No frames, no actions, port not opened on the host.
- Actions of the status page accept JSON only (no cross-site form posts).

## 0.4.0

- Capture: every frame in both directions to `/share/enocean_proxy`, download and delete on
  the status page; optionally only one program may send meanwhile (PCT14 with a FAM14).
- Replay with reception times ("EOPX REPLAY TS"), used by the EnOcean Eltako integration.
- Air time of sent telegrams against the 1 % duty cycle, warning at 80 %.
- Tools on the status page: gateway test (base ID round trips) and sending one raw frame.

## 0.3.0

- Status page in Home Assistant's sidebar (ingress): per gateway the connection, the
  programs connected (with replay), counters, the replay buffer, allowed clients and the
  last 50 frames live, filterable by kind; light and dark, phone friendly.
- Stopping the app no longer hangs while programs are still connected.

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
