# EnOcean Proxy

The app keeps the connection to your EnOcean gateways and offers each of them on a TCP
port. Any number of programs can connect at the same time: the
[EnOcean Eltako integration](https://github.com/bertel2020/HA-Enocean-Eltako), the old
`eltako` integration, eo_man or a capture tool. Every telegram from the gateway reaches every
program; telegrams the programs send go to the gateway one at a time, never interleaved.

While Home Assistant restarts, the app keeps listening. The EnOcean Eltako integration can
ask for the telegrams it missed and gets them in their original order, so cover positions,
meter readings and switch states stay right.

## Configuration

```yaml
gateways:
  - name: FAM-USB
    device: /dev/serial/by-id/usb-EnOcean_GmbH_EnOcean_Programmer_V3.2_XXXX-if01-port0
    baudrate: 9600
    protocol: esp2
    port: 9100
  - name: MGW
    device: tcp://192.168.1.50:5100
    protocol: esp3
    port: 9101
send_gap_ms: 10
replay_size: 1000
replay_max_age_s: 900
allowed_clients: []
log_level: info
```

- **device**: the serial port (best the stable `/dev/serial/by-id/...` path) or
  `tcp://host:port` for ser2net or a LAN gateway.
- **baudrate**: 9600 for FAM-USB and FAM14, 57600 for FGW14-USB, USB300 and most ESP3 sticks.
- **protocol**: `esp2` for FAM-USB, FGW14-USB and FAM14; `esp3` for USB300, MGW-ETH and others.
- **port**: 9100 to 9103, one per gateway.
- **send_gap_ms**: minimum gap between two telegrams sent to a gateway.
- **replay_size**: how many radio telegrams are kept while Home Assistant is away.
- **replay_max_age_s**: older telegrams are not replayed (default 15 minutes).
- **allowed_clients**: addresses or networks (e.g. `192.168.1.20`, `192.168.1.0/24`) that may
  connect; empty = everyone who can reach the port.

## In the EnOcean Eltako integration

After the app starts, each gateway shows up under **Settings → Devices & services** as
discovered (**EnOcean Proxy**). Confirm it and pick the model; replay is switched on. Only one
program may use a serial port: remove the port from every other program and let them connect
to the proxy instead.

## Network and security

By default the ports are not opened on the host: Home Assistant reaches the app inside the
Supervisor network. To connect programs elsewhere in your LAN (eo_man, a second Home Assistant),
set host ports in the app's **Network** section and limit them with `allowed_clients`: anyone
who can reach a port can send telegrams to your actuators.

## Limits

- Replayed telegrams arrive with the time of the replay, not of their reception.
- Commands for the FAM14 bus memory (ORG F0 and up) and their answers reach every program.
- A serial port that disappears is reopened every few seconds; programs stay connected.
