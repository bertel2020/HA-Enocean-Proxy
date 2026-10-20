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
log_level: info
```

- **device**: the serial port (best the stable `/dev/serial/by-id/...` path) or
  `tcp://host:port` for ser2net or a LAN gateway.
- **baudrate**: 9600 for FAM-USB and FAM14, 57600 for FGW14-USB, USB300 and most ESP3 sticks.
- **protocol**: `esp2` for FAM-USB, FGW14-USB and FAM14; `esp3` for USB300, MGW-ETH and others.
- **port**: 9100 to 9103, one per gateway.
- **send_gap_ms**: minimum gap between two telegrams sent to a gateway.
- **replay_size**: how many telegrams are kept while Home Assistant is away.

## In the EnOcean Eltako integration

Add the gateway with its model (e.g. FAM-USB) and the connection **network**: host
`127.0.0.1`, the port from above. In the gateway's options, switch on **replay over EnOcean
Proxy**. Only one app or integration may use a serial port: remove the port from every other
program and let them connect to the proxy instead.

## Limits

- Answers to commands (e.g. reading the base ID) reach every program, as on a shared bus.
- A serial port that disappears is reopened every few seconds; programs stay connected.
