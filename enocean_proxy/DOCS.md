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

## Status page

**EnOcean Proxy** in the sidebar (or **Open web UI** on the app page) shows each gateway:
connected or not (with the last error), the programs connected and whether they use replay,
telegrams from and to the gateway, queue, dropped, replayed, refused and stalled clients,
how full the replay buffer is, the allowed clients and the last 50 frames live. Filter the
frames by radio telegrams, answers or other, or pause the list.

## Capture (e.g. PCT14 and FAM14)

**Capture** on the status page writes every frame in both directions, with time, gateway,
kind and the sending program, to `/share/enocean_proxy/capture-<time>-<gateway>.jsonl`. The
files are listed on the page for download (and are reachable through the Samba or SSH app).
A capture stops by itself at 50 MB.

To record what the PCT14 configuration software says to a FAM14:

1. Connect the FAM14 to the Home Assistant machine and add it to the app (`device` its
   `/dev/serial/by-id/...` path, `baudrate` 57600, `protocol` esp2).
2. In the app's **Network** section, give that gateway's port (e.g. 9100) a host port, and put
   the Windows PC's address into `allowed_clients`.
3. On the Windows PC, create a virtual COM port that connects over TCP to
   `<Home Assistant address>:9100` (e.g. HW VSP3 from HW group, or com0com with hub4com), and
   pick that COM port in PCT14.
4. On the status page, choose the FAM14 gateway, pick the PC's address under **Only this
   program may send** and start the capture. Home Assistant keeps receiving but cannot send
   to this gateway meanwhile, so it does not disturb the bus memory access.
5. Read or write the devices in PCT14, stop the capture, download the file.

Whether PCT14 tolerates the extra delay of the TCP detour is not tested yet.

## Air time

EnOcean shares the 868 MHz band: a sender may be on air for at most 1 % of the time. The app
estimates the air time of every telegram sent through a gateway (three subtelegrams each) and
shows the last hour's share of that 1 % on the status page; at 80 % it logs a warning.
Normal use stays far below; a burst of many covers or a misbehaving automation shows up here.

## Tools

- **Gateway test** reads the base ID 20 times and reports how many answers came and how long
  they took. Nothing is sent over the air.
- **Send frame** sends one complete ESP2 or ESP3 frame typed in hex and shows the gateway's
  answer. It is really sent: actuators react.

## Network and security

The app also answers a read-only status on port 9199 (version, programs, counters, air time;
no frames, no actions) inside the Supervisor network; the EnOcean Eltako integration shows
it on its gateway cards. Status page actions accept JSON requests only.

By default the ports are not opened on the host: Home Assistant reaches the app inside the
Supervisor network. To connect programs elsewhere in your LAN (eo_man, a second Home Assistant),
set host ports in the app's **Network** section and limit them with `allowed_clients`: anyone
who can reach a port can send telegrams to your actuators.

## Limits

- Replayed telegrams keep their reception time only in clients that ask for it (the EnOcean
  Eltako integration does since app 0.4.0); others get the plain frames.
- Commands for the FAM14 bus memory (ORG F0 and up) and their answers reach every program.
- A serial port that disappears is reopened every few seconds; programs stay connected.
