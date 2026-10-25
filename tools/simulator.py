#!/usr/bin/env python3
"""Synthetic local receiver, not a record of a journey. Python 3.10+ stdlib."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import contextlib
import datetime as dt
import ipaddress
import math
import secrets
import time

from protocol import (DISCOVERY_PORT, TCP_PORT, ProtocolError, StreamDecoder,
                      control, local_ipv4, parse_control, sentence)


def coordinate(degrees: float, latitude: bool) -> str:
    whole = int(abs(degrees))
    return f"{whole:0{2 if latitude else 3}d}{(abs(degrees) - whole) * 60:07.4f}"


def make_epoch(tick: int, origin: dt.datetime) -> bytes:
    """A synthetic moving loop about the standard NMEA textbook point."""
    utc = origin + dt.timedelta(milliseconds=tick * 200)
    stamp = utc.strftime("%H%M%S.") + f"{utc.microsecond // 1000:03d}"
    phase = tick * 0.02
    latitude = 48.1173 + 0.00018 * math.sin(phase)
    longitude = 11.5167 + 0.00027 * (1 - math.cos(phase))
    bearing = math.degrees(math.atan2(math.sin(phase), math.cos(phase))) % 360
    lat, lon = coordinate(latitude, True), coordinate(longitude, False)
    bodies = [f"GPGGA,{stamp},{lat},N,{lon},E,1,08,0.9,545.4,M,46.9,M,,",
              f"GPGST,{stamp},1.0,2.0,1.5,0.0,1.5,2.0,3.0",
              f"GPRMC,{stamp},A,{lat},N,{lon},E,3.8877,{bearing:.2f},{utc:%d%m%y},,,A"]
    if tick % 5 == 0:
        bodies.extend([
            "GPGSA,A,3,04,05,09,12,15,18,24,29,,,,,1.5,0.9,1.2",
            "GPGSV,2,1,08,04,77,123,42,05,55,045,40,09,42,210,38,12,30,300,36",
            "GPGSV,2,2,08,15,65,160,41,18,25,080,35,24,15,250,32,29,10,340,30",
        ])
    return b"".join(sentence(body) for body in bodies)


class DeviceSession:
    """The same bounded input path is exercised by run_vectors.py."""

    def __init__(self, boot: str = "00000001"):
        self.decoder = StreamDecoder()
        self.accepted = False
        self.boot = boot
        self.seq = 0
        self.received = Counter()

    def receive(self, data: bytes) -> list[bytes]:
        replies = []
        for line in self.decoder.feed(data):
            self.received[line.name] += 1
            if line.name != "PCGBO":
                continue  # Never echo or forward a client's navigation data.
            try:
                verb, fields = parse_control(line)
                if verb == "ACCEPT":
                    known = set(fields["use"].split("+")) & {
                        "nmea", "ubx_rx", "imu_pcgbi", "wheel", "batt", "time", "udp_push"}
                    if known != {"nmea"}:
                        replies.append(control("ERROR", code="UNSUPPORTED_CAPABILITY"))
                    else:
                        self.accepted = True
            except ProtocolError as exc:
                # An error does not enable any extended traffic or UART access.
                code = "UNSUPPORTED_VERSION" if exc.rule == "version" else "BAD_REQUEST"
                replies.append(control("ERROR", code=code))
        return replies

    def heartbeat(self, uptime_ms: int, fresh_fix: bool = False) -> bytes | None:
        if not self.accepted:
            return None
        wire = control("HB", boot=self.boot, seq=self.seq, ms=uptime_ms & 0xFFFFFFFF, fix=int(fresh_fix))
        self.seq = (self.seq + 1) & 0xFFFFFFFF
        return wire


def discovery_reply(data: bytes, device_id: str, tcp_port: int = TCP_PORT) -> bytes | None:
    decoder = StreamDecoder()
    lines = decoder.feed(data)
    decoder.finish()
    if decoder.errors or len(lines) != 1:
        return None
    try:
        verb, fields = parse_control(lines[0])
        if verb == "DISCOVER":
            return control("OFFER", pv="1.0", nonce=fields["nonce"], id=device_id,
                           tcp=tcp_port, caps="nmea", name="Synthetic-GNSS")
    except ProtocolError:
        pass
    return None


class Discovery(asyncio.DatagramProtocol):
    def __init__(self, host: str, prefix: int, device_id: str, tcp_port: int):
        self.network = ipaddress.IPv4Network(f"{host}/{prefix}", strict=False)
        self.device_id = device_id
        self.tcp_port = tcp_port
        self.transport = None
        self.last_reply: dict[str, float] = {}

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if len(data) > 514 or ipaddress.IPv4Address(addr[0]) not in self.network:
            return
        now = time.monotonic()
        if now - self.last_reply.get(addr[0], -100) < 0.2:
            return
        reply = discovery_reply(data, self.device_id, self.tcp_port)
        if reply:
            # Bound state even when many local peers send discovery probes.
            if len(self.last_reply) >= 32:
                self.last_reply.clear()
            self.last_reply[addr[0]] = now
            self.transport.sendto(reply, addr)


class Simulator:
    def __init__(self, args):
        self.args = args
        self.boot = secrets.token_hex(4)
        self.started = time.monotonic()
        self.clients: set[asyncio.Queue] = set()
        self.connections: set[asyncio.Task] = set()

    async def client(self, reader, writer):
        peer = writer.get_extra_info("peername")
        network = ipaddress.IPv4Network(f"{self.args.host}/{self.args.prefix_length}", strict=False)
        if len(self.clients) >= 2 or ipaddress.IPv4Address(peer[0]) not in network:
            writer.close()
            await writer.wait_closed()
            return
        queue: asyncio.Queue = asyncio.Queue(maxsize=3)
        self.clients.add(queue)
        task = asyncio.current_task()
        self.connections.add(task)
        state = DeviceSession(self.boot)
        last_epoch_sent = 0.0
        print("client connected; HELLO=" + ("0" if self.args.nmea_only else "1"), flush=True)
        jobs = []
        try:
            if not self.args.nmea_only:
                writer.write(control("HELLO", pv="1.0", id=self.args.device_id, boot=self.boot,
                                     caps="nmea", name="Synthetic-GNSS", maker="Example", fw="1.0.0", talkers="GP"))
                await asyncio.wait_for(writer.drain(), 1)

            async def read_controls():
                while True:
                    data = await reader.read(1024)
                    if not data:
                        return
                    if not self.args.nmea_only:
                        for reply in state.receive(data):
                            # A control response is a complete line, like an epoch item.
                            enqueue(reply)

            def enqueue(packet: bytes):
                if queue.full():
                    queue.get_nowait()  # Discard an entire old epoch/item.
                queue.put_nowait((time.monotonic(), packet))

            async def write_epochs():
                nonlocal last_epoch_sent
                while True:
                    measured, packet = await queue.get()
                    if time.monotonic() - measured > 1:
                        continue
                    writer.write(packet)
                    # Disconnect a blocked writer rather than delivering a stale backlog.
                    await asyncio.wait_for(writer.drain(), 0.5)
                    if packet.startswith(b"$GPGGA,"):
                        last_epoch_sent = time.monotonic()

            async def heartbeats():
                while True:
                    await asyncio.sleep(1)
                    hb = state.heartbeat(round((time.monotonic() - self.started) * 1000),
                                         time.monotonic() - last_epoch_sent <= 1.5)
                    if hb is not None:
                        enqueue(hb)

            jobs = [asyncio.create_task(read_controls()), asyncio.create_task(write_epochs()),
                    asyncio.create_task(heartbeats())]
            done, _ = await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
            for job in done:
                job.result()
        except (ConnectionError, OSError, asyncio.TimeoutError):
            pass
        finally:
            self.clients.discard(queue)
            self.connections.discard(task)
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()
            print("client disconnected", flush=True)

    async def produce(self):
        origin = dt.datetime.now(dt.timezone.utc)
        start = time.monotonic()
        tick = 0
        while True:
            packet = make_epoch(tick, origin)
            measured = time.monotonic()
            for queue in tuple(self.clients):
                if queue.full():
                    queue.get_nowait()
                queue.put_nowait((measured, packet))
            tick = max(tick + 1, int((time.monotonic() - start) * 5) + 1)
            await asyncio.sleep(max(0, start + tick * 0.2 - time.monotonic()))

    async def run(self):
        loop = asyncio.get_running_loop()
        udp, _ = await loop.create_datagram_endpoint(
            lambda: Discovery(self.args.host, self.args.prefix_length, self.args.device_id, self.args.port),
            local_addr=(self.args.host, DISCOVERY_PORT))
        server = await asyncio.start_server(self.client, self.args.host, self.args.port, limit=2048)
        producer = asyncio.create_task(self.produce())
        print(f"synthetic receiver ready; TCP {self.args.port}; UDP {DISCOVERY_PORT}; 5 Hz", flush=True)
        try:
            async with server:
                if self.args.duration:
                    await asyncio.sleep(self.args.duration)
                else:
                    await server.serve_forever()
        finally:
            producer.cancel()
            for job in tuple(self.connections):
                job.cancel()
            await asyncio.gather(producer, *tuple(self.connections), return_exceptions=True)
            udp.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="selected local IPv4 interface; never a wildcard")
    parser.add_argument("--prefix-length", type=int, default=24, choices=range(1, 33), metavar="1..32")
    parser.add_argument("--port", type=int, default=TCP_PORT)
    parser.add_argument("--device-id", default="demo-simulator")
    parser.add_argument("--nmea-only", action="store_true", help="disable HELLO/ACCEPT/HB for baseline-only peers")
    parser.add_argument("--duration", type=float, default=0, help="optional bounded run in seconds")
    args = parser.parse_args()
    try:
        local_ipv4(args.host)
        if not 1 <= args.port <= 65535 or args.duration < 0:
            raise ValueError("invalid port or duration")
        # Reuse the public field validator before opening any socket.
        from protocol import parse_sentence
        parse_control(parse_sentence(control("HELLO", pv="1.0", id=args.device_id,
                                            boot="00000001", caps="nmea")[:-2]))
        asyncio.run(Simulator(args).run())
    except KeyboardInterrupt:
        pass
    except (ValueError, OSError) as exc:
        parser.exit(2, f"simulator: {exc}\n")


if __name__ == "__main__":
    main()
