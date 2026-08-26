"""Find every address this server is reachable on, and answer to `scout.local`.

Typing an IP into six phones is the worst part of setup, so the server
enumerates its own addresses, prints them, offers a QR page, and answers mDNS
queries for a friendly hostname.
"""
import socket
import struct
import subprocess
import threading
import time

MDNS_ADDR = "224.0.0.251"
MDNS_PORT = 5353
HOSTNAME = "scout"          # -> scout.local
# Windows Mobile Hotspot always hands out this gateway; worth advertising loudly.
WINDOWS_HOTSPOT = "192.168.137.1"
# Every hotspot a team is likely to run, whether the laptop is the access point
# or just another client on someone's phone. The laptop is often on two networks
# at once - venue wifi for the API keys, hotspot for the scouts - and only the
# hotspot address is reachable from the phones, so these sort to the top.
HOTSPOT_PREFIXES = (
    "192.168.137.",   # Windows Mobile Hotspot
    "192.168.2.",     # macOS Internet Sharing
    "172.20.10.",     # iPhone Personal Hotspot
    "192.168.43.",    # Android tethering
)
IP_RECHECK_SECS = 15      # how often the responder re-reads its own address


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
    """Catch hotspot interfaces the other two methods miss (they usually do)."""
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


def _is_private(ip):
    """RFC 1918 only. 172. alone would sweep in public space that starts 172."""
    if ip.startswith("192.168.") or ip.startswith("10."):
        return True
    if ip.startswith("172."):
        try:
            return 16 <= int(ip.split(".")[1]) <= 31
        except (IndexError, ValueError):
            return False
    return False


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
        # hotspot addresses first, then private ranges, then anything else
        if ip == WINDOWS_HOTSPOT:
            return 0
        if ip.startswith(HOTSPOT_PREFIXES):
            return 1
        if ip == d:
            return 2
        if _is_private(ip):
            return 3
        return 4

    return sorted(set(usable), key=lambda ip: (score(ip), ip))


def urls(port):
    return [f"http://{ip}:{port}" for ip in local_ipv4s()]


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
    lines.append(f"  Settings:  open  http://localhost:{port}/       on this screen (API keys live here)")
    lines.append("")
    lines.append("  Windows: if phones cannot connect, allow Python through")
    lines.append("  Windows Firewall on PRIVATE networks (it prompts on first run).")
    lines.append("")
    return "\n".join(lines)


class MDNSResponder(threading.Thread):
    """Minimal responder: answers A queries for `scout.local` only.

    Not a full mDNS implementation - it advertises nothing and answers nothing
    else, which is all we need and keeps it from misbehaving on a venue network.
    """

    daemon = True

    def __init__(self, ip=None, name=HOSTNAME):
        super().__init__(name="mdns")
        self.ip = ip              # None means "whatever this machine has now"
        self.qname = (name + ".local").lower()
        self._stop = threading.Event()
        self.sock = None
        self._next_check = 0.0

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

    def _refresh_ip(self):
        """Follow the address this machine has *now*, not the one it booted on.

        Starting the server on house wifi and joining the hotspot at the venue is
        the normal Saturday, not the exception. Answering scout.local with the
        address from breakfast points every phone at nothing, which is worse than
        not answering at all. The socket goes with it: the multicast membership
        was joined on the old interface and does not follow by itself.
        """
        now = time.monotonic()
        if now < self._next_check:
            return
        self._next_check = now + IP_RECHECK_SECS
        ips = local_ipv4s()
        ip = ips[0] if ips else None
        if ip and ip != self.ip:
            self.ip = ip
            self._close()

    def _close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
        self.sock = None

    def run(self):
        while not self._stop.is_set():
            self._refresh_ip()
            if self.sock is None:
                try:
                    self.sock = self._open()
                except Exception:
                    # mDNS is a convenience; never take the server down for it,
                    # and never give up on it either - the interface it needs may
                    # only appear once the hotspot does.
                    self._stop.wait(5)
                    continue
            try:
                data, addr = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except Exception:
                self._close()
                continue
            try:
                reply = self._maybe_reply(data)
                if reply:
                    self.sock.sendto(reply, (MDNS_ADDR, MDNS_PORT))
            except Exception:
                pass

    def stop(self):
        self._stop.set()
        self._close()

    def _maybe_reply(self, data):
        if not self.ip or len(data) < 12:
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
