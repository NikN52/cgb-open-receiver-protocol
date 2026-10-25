# Python reference tools

Python 3.10 or newer, standard library only. Run commands from the repository
root. These tools use synthetic data; they do not capture a real journey or
write commands to a GNSS receiver.

Start a local reference device in one terminal:

```sh
python tools/simulator.py --host 127.0.0.1 --duration 60
```

In another terminal:

```sh
python tools/check_device.py 127.0.0.1 --seconds 30 --profile extended
python tools/run_vectors.py
```

The simulator sends GGA, GST and RMC at 5 Hz and GSA/GSV at 1 Hz. Its moving
loop is generated about the standard NMEA textbook point, not from a real
track. GNSS UTC advances from the current PC UTC. One HELLO is sent on each
connection. NMEA flows before ACCEPT; only a valid ACCEPT enables 1 Hz HB.
Use `--nmea-only` to disable HELLO, ACCEPT and HB for a legacy client. Discovery
uses UDP 28496, unicast replies and the request's nonce.

On a PC where TCP 28495 is reserved or already used, select a free local port
and pass it through `--port` to both simulator and checker. The device's
normative default remains 28495.
OFFER advertises the port actually selected by the simulator.

For a receiver on a real local Wi-Fi/AP network, replace the placeholders:

```sh
python tools/check_device.py RECEIVER_LOCAL_IP --interface YOUR_LOCAL_IP --prefix-length 24
```

The interface is required outside loopback. Only a selected loopback, RFC1918
or IPv4 link-local address/subnet is allowed; wildcard/global binding is
rejected. Select the actual subnet prefix rather than assuming `/24`.
The simulator can likewise bind `--host YOUR_LOCAL_IP --prefix-length 24`.
Neither tool creates a firewall rule or an Internet-facing service.

The checker sends a unicast DISCOVER to the specified device, connects TCP,
accepts advertised capabilities it can parse (excluding UDP push), and reads
for 30 seconds by default. Use `--profile baseline` to omit ACCEPT/HB
requirements. `--json REPORT.json` saves a report without the raw location
stream. The report distinguishes:

- `PASS`: observed without a violation during this run;
- `FAIL`: an observed or missing required property;
- `SKIP`: not exercised, including physical UART behavior, broadcast discovery,
  Bluetooth, UDP push, loss transitions and invalid-fix fault injection.

The final PASS and exit code 0 mean all observed MUST rules passed. SHOULD
violations are still printed as FAIL but do not change that exit code. Exit 1
means a MUST failure; exit 2 means invalid arguments or a connection error.
The checker checks both measurement UTC progression and arrival cadence, with
documented scheduling tolerance. It cannot prove that a malicious device did
not relabel old measurements with new UTC. Ending an observation mid-line is
not a peer framing error; an actual EOF with a partial frame is an error.

The simulator limits clients to two, buffers three complete epochs/items per
client, drops whole old queue entries, and disconnects a stalled writer after
a bounded drain. This is laboratory backpressure behavior, not a claim about
the firmware's physical UART or Wi-Fi behavior.

`protocol.py` supplies shared bounded wire primitives. `run_vectors.py`
replays every stream fixture through **both** `simulator.DeviceSession.receive`
and `check_device.StreamAudit.feed`; it also checks IMU segmentation and the
reference UDP anti-replay/lease window. This is shared-code regression testing,
not an independent certification or proof of bridge support for extensions.
`generate_vectors.py` regenerates synthetic fixtures and explicit expected
outcomes. Do not replace expected failures automatically with current parser
results; review a fixture and its expectation together.
