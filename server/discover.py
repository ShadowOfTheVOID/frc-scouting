"""Find every address this server is reachable on, and answer to `scout.local`.

Typing an IP into six phones is the worst part of setup, so the server
enumerates its own addresses, prints them, offers a QR page, and answers mDNS
queries for a friendly hostname.
"""
import platform
import socket
import struct
import subprocess
import threading
import time

MDNS_ADDR = "224.0.0.251"
MDNS_PORT = 5353
HOSTNAME = "scout"          # -> scout.local
# Windows Mobile Hotspot always hands out this gateway. Only ever seen when
# practising at home - team access points are not allowed in a venue - but it
# is the whole network when it is there, so it sorts first.
WINDOWS_HOTSPOT = "192.168.137.1"


def _default_route_ip():
    """Address of the interface that would carry outbound traffic. No packet sent."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        s.close()


def _hostname_ips():
    out = set()
    try:
        for fam, _, _, _, sa in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            out.add(sa[0])
    except Exception:
        pass
    return out


def _scraped_ips():
    """Catch the interfaces the other two methods miss (they usually do)."""
    out = set()
    for cmd in (["ifconfig"], ["ip", "-4", "addr"], ["ipconfig"]):
        try:
            txt = subprocess.run(cmd, capture_output=True, text=True, timeout=4).stdout
        except Exception:
            continue
        if not txt:
            continue
        import re
        for m in re.finditer(r"(?:inet |IPv4 Address[.\s]*:\s*)(\d+\.\d+\.\d+\.\d+)", txt):
            out.add(m.group(1))
        break
    return out


def local_ipv4s():
    """All plausible LAN addresses, best candidate first."""
    found = set()
    d = _default_route_ip()
    if d:
        found.add(d)
    found |= _hostname_ips()
    found |= _scraped_ips()
    usable = [ip for ip in found
              if not ip.startswith("127.") and not ip.startswith("169.254.")]

    def score(ip):
        # hotspot gateway first, then private ranges, then anything else
        if ip == WINDOWS_HOTSPOT:
            return 0
        if ip.startswith("192.168.137."):
            return 1
        if ip == d:
            return 2
        if ip.startswith(("192.168.", "10.")) or ip.startswith("172."):
            return 3
        return 4

    return sorted(set(usable), key=lambda ip: (score(ip), ip))


# Enumerating the interfaces forks ifconfig/ip/ipconfig with a four second
# timeout. /api/diag calls this, and the dashboard used to fetch /api/diag every
# thirty seconds, so the hub laptop was spawning a process a minute for a list
# of addresses that changes when somebody moves the laptop to another network -
# not twice a minute for eight hours.
_URLS_TTL = 300.0
_urls_cache = {}
_urls_lock = threading.Lock()


def urls(port, max_age=_URLS_TTL):
    with _urls_lock:
        hit = _urls_cache.get(port)
        if hit and time.time() - hit[0] < max_age:
            return list(hit[1])
    out = [f"http://{ip}:{port}" for ip in local_ipv4s()]
    with _urls_lock:
        _urls_cache[port] = (time.time(), list(out))
    return out


def banner(port):
    lines = [""]
    lines.append("  FRC 2026 REBUILT scouting server")
    lines.append("  " + "-" * 52)
    us = urls(port)
    if not us:
        lines.append("  No LAN address found - only reachable on this machine.")
    for i, u in enumerate(us):
        tag = "  <- try this first" if i == 0 else ""
        lines.append(f"    {u}{tag}")
    lines.append(f"    http://{HOSTNAME}.local:{port}   (if the device supports mDNS)")
    lines.append("")
    lines.append(f"  Scouts:    open  {us[0] if us else 'http://localhost:%d' % port}/scout")
    lines.append(f"  Dashboard: open  {us[0] if us else 'http://localhost:%d' % port}/dashboard")
    lines.append(f"  Join QR:   open  http://localhost:{port}/join   on this screen and let scouts scan it")
    # Named for what it is. "Settings" is where an event key and eight API keys
    # are set from, and the first question anybody asked was where it was.
    lines.append(f"  Admin:     open  http://localhost:{port}/       on this screen "
                 f"(event, API keys, mirror - press UNLOCK)")
    lines.append("")
    # Only where it is true. On Windows the hub offers to add the rule itself a
    # few lines further down the same window (see server/firewall.py), so this
    # is the sentence that explains what that question is about.
    if platform.system() == "Windows":
        lines.append("  Windows blocks phones from reaching this laptop until it is allowed")
        lines.append("  through the firewall. That is the question below, and the Windows")
        lines.append("  popup that may also appear: allow it on PRIVATE networks.")
        lines.append("")
    return "\n".join(lines)


class MDNSResponder(threading.Thread):
    """Minimal responder: answers A queries for `scout.local` only.

    Not a full mDNS implementation - it advertises nothing and answers nothing
    else, which is all we need and keeps it from misbehaving on a venue network.
    """

    daemon = True

    def __init__(self, ip, name=HOSTNAME):
        super().__init__(name="mdns")
        self.ip = ip
        self.qname = (name + ".local").lower()
        self._stop = threading.Event()
        self.sock = None

    def _open(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        s.bind(("", MDNS_PORT))
        mreq = struct.pack("4s4s", socket.inet_aton(MDNS_ADDR), socket.inet_aton("0.0.0.0"))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        s.settimeout(1.0)
        return s

    def run(self):
        try:
            self.sock = self._open()
        except Exception:
            return  # mDNS is a convenience; never take the server down for it
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except Exception:
                return
            try:
                reply = self._maybe_reply(data)
                if reply:
                    self.sock.sendto(reply, (MDNS_ADDR, MDNS_PORT))
            except Exception:
                pass

    def stop(self):
        self._stop.set()

    def _maybe_reply(self, data):
        if len(data) < 12:
            return None
        tid, flags, qd, *_ = struct.unpack("!6H", data[:12])
        if flags & 0x8000 or qd < 1:
            return None  # a response, not a query
        pos, labels = 12, []
        while pos < len(data):
            n = data[pos]
            pos += 1
            if n == 0:
                break
            if n & 0xC0:
                return None  # compression in a question: ignore
            labels.append(data[pos:pos + n].decode("ascii", "ignore"))
            pos += n
        if pos + 4 > len(data):
            return None
        qtype, qclass = struct.unpack("!HH", data[pos:pos + 4])
        if qtype not in (1, 255) or ".".join(labels).lower() != self.qname:
            return None

        name = b"".join(bytes([len(l)]) + l.encode() for l in labels) + b"\x00"
        header = struct.pack("!6H", tid, 0x8400, 0, 1, 0, 0)
        rr = name + struct.pack("!HHIH", 1, 0x8001, 120, 4) + socket.inet_aton(self.ip)
        return header + rr
